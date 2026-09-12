import os
import sys
import json
import random
import logging
import urllib.parse
import html
import re
from datetime import datetime

from dotenv import load_dotenv
import requests


# =============================================================================
# ЛОГИРОВАНИЕ
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(
            "autopost.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


# =============================================================================
# ЗАГРУЗКА ENV
# =============================================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHANNEL_ID = os.getenv(
    "TELEGRAM_CHANNEL_ID",
    ""
).strip()

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
).strip()

UNSPLASH_ACCESS_KEY = os.getenv(
    "UNSPLASH_ACCESS_KEY",
    ""
).strip()


# =============================================================================
# ПРОВЕРКА ENV
# =============================================================================

required_variables = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHANNEL_ID": TELEGRAM_CHANNEL_ID,
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "UNSPLASH_ACCESS_KEY": UNSPLASH_ACCESS_KEY,
}

missing_variables = [
    name
    for name, value in required_variables.items()
    if not value
]

if missing_variables:

    logger.error(
        "❌ Не заданы переменные:"
    )

    for variable in missing_variables:
        logger.error(
            f"   {variable}"
        )

    logger.error(
        "Проверь файл .env"
    )

    sys.exit(1)


# =============================================================================
# ПУТИ
# =============================================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

HISTORY_FILE = os.path.join(
    BASE_DIR,
    "content_history.json"
)


# =============================================================================
# НАСТРОЙКИ
# =============================================================================

MEMORY_DEPTH = 20

MAX_HISTORY_RECORDS = 100

# Telegram caption для фотографии ограничен 1024 символами.
# Оставляем запас.
MAX_CAPTION_LENGTH = 1000

# Минимальная оценка AI-редактора.
MIN_QUALITY_SCORE = 7.0


# =============================================================================
# МОДЕЛИ GEMINI
# =============================================================================

TEXT_MODELS = [
    "gemini-3.6-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
]


# =============================================================================
# ТЕМЫ ПО ДНЯМ НЕДЕЛИ
# =============================================================================

THEMES = {

    0: {
        "rubric": "🔥 Фокус недели",
        "format_hint": "короткий сильный управленческий пост",
        "topics": [
            "Одна управленческая метрика, на которую стоит смотреть всю неделю",
            "Привычка руководителя, от которой стоит отказаться",
            "Одно управленческое действие, которое стоит сделать до пятницы",
            "Что руководителю стоит перестать контролировать",
            "Как начать неделю без хаоса и десятков срочных задач"
        ]
    },

    1: {
        "rubric": "🛠 Инструмент руководителя",
        "format_hint": "практический инструмент с конкретными шагами",
        "topics": [
            "Матрица приоритетов руководителя",
            "Как правильно делегировать задачу",
            "Чек-лист эффективной встречи один на один",
            "Как давать негативную обратную связь",
            "Как контролировать задачу без микроменеджмента",
            "Как проводить сложный разговор с сотрудником"
        ]
    },

    2: {
        "rubric": "🧠 Управленческий инсайт",
        "format_hint": "короткий интеллектуальный пост",
        "topics": [
            "Фраза, которая выдаёт слабого руководителя",
            "Почему сотрудники перестают говорить руководителю правду",
            "Разница между занятостью и эффективностью",
            "Почему сильные сотрудники иногда начинают сопротивляться",
            "Как руководитель сам создаёт микроменеджмент"
        ]
    },

    3: {
        "rubric": "📊 Вопрос руководителям",
        "format_hint": "пост-вопрос для вовлечения аудитории",
        "topics": [
            "Что сложнее: нанять или уволить сотрудника?",
            "Можно ли быть хорошим руководителем без жёсткости?",
            "Что важнее: результат или атмосфера в команде?",
            "Нужно ли руководителю дружить с сотрудниками?",
            "Как вы поступаете, если команда не согласна с решением?"
        ]
    },

    4: {
        "rubric": "💣 Разбор ситуации",
        "format_hint": "реалистичная управленческая ситуация и практическое решение",
        "topics": [
            "Сотрудник систематически опаздывает",
            "Сильный сотрудник начал саботировать решения",
            "Руководитель потерял авторитет команды",
            "Сотрудник постоянно перекладывает ответственность",
            "Коллега публично спорит с руководителем",
            "Подчинённый не выполняет договорённости"
        ]
    },

    5: {
        "rubric": "📚 Полезное на выходные",
        "format_hint": "лёгкий, но полезный контент",
        "topics": [
            "Книга, которую стоит прочитать руководителю",
            "Привычки эффективных руководителей",
            "Инструмент, который экономит время менеджеру",
            "Ошибка руководителя, которую часто считают нормой",
            "Интересный управленческий кейс"
        ]
    },

    6: {
        "rubric": "💭 Рефлексия недели",
        "format_hint": "спокойный интеллектуальный пост",
        "topics": [
            "Главный управленческий урок недели",
            "Ошибка руководителя, из которой можно извлечь пользу",
            "Что руководителю стоит изменить на следующей неделе",
            "Вопрос, который стоит задать себе перед понедельником",
            "Что команда пытается сказать руководителю своим поведением"
        ]
    }
}


# =============================================================================
# ОЧИСТКА ТЕКСТА ОТ HTML
# =============================================================================

def clean_ai_text(text: str) -> str:
    """
    Полностью очищает текст, который пришёл от AI.

    ВАЖНО:
    Gemini может вернуть:
        <br>
        <br/>
        <br />
        <b>текст</b>
        <i>текст</i>

    Мы не позволяем этим тегам попасть в Telegram.
    """

    if text is None:
        return ""

    text = str(text)

    # ---------------------------------------------------------
    # Убираем Markdown code fences
    # ---------------------------------------------------------

    text = re.sub(
        r"```(?:html|markdown|text)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = text.replace(
        "```",
        ""
    )

    # ---------------------------------------------------------
    # Декодируем HTML entities
    #
    # &lt;br&gt; -> <br>
    # &amp; -> &
    # ---------------------------------------------------------

    text = html.unescape(text)

    # ---------------------------------------------------------
    # <br>, <br/>, <br /> -> перенос строки
    # ---------------------------------------------------------

    text = re.sub(
        r"<\s*br\s*/?\s*>",
        "\n",
        text,
        flags=re.IGNORECASE
    )

    # ---------------------------------------------------------
    # Удаляем HTML-теги
    # ---------------------------------------------------------

    text = re.sub(
        r"<[^>]*>",
        "",
        text
    )

    # ---------------------------------------------------------
    # Убираем лишние пробелы
    # ---------------------------------------------------------

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    # ---------------------------------------------------------
    # Убираем пробелы перед переносом
    # ---------------------------------------------------------

    text = re.sub(
        r" +\n",
        "\n",
        text
    )

    # ---------------------------------------------------------
    # Максимум 2 переноса подряд
    # ---------------------------------------------------------

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


# =============================================================================
# БЕЗОПАСНЫЙ HTML ДЛЯ TELEGRAM
# =============================================================================

def telegram_escape(text: str) -> str:
    """
    Экранирует пользовательский текст перед добавлением
    Telegram HTML-разметки.

    Благодаря этому символы < > & не будут превращаться
    в случайные HTML-теги.
    """

    if not text:
        return ""

    return html.escape(
        str(text),
        quote=False
    )


# =============================================================================
# ИСТОРИЯ
# =============================================================================

def load_history():

    if not os.path.exists(
        HISTORY_FILE
    ):
        return []

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(
            data,
            list
        ):
            return data

        return []

    except Exception as e:

        logger.warning(
            f"⚠️ Не удалось прочитать историю: {e}"
        )

        return []


def save_history(history):

    try:

        history = history[
            -MAX_HISTORY_RECORDS:
        ]

        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                history,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        logger.warning(
            f"⚠️ Не удалось сохранить историю: {e}"
        )


# =============================================================================
# ВЫБОР ТЕМЫ
# =============================================================================

def pick_topic(
    weekday: int
):

    day_data = THEMES[
        weekday
    ]

    pool = day_data[
        "topics"
    ]

    history = load_history()

    recent_topics = [
        item.get(
            "topic",
            ""
        )
        for item in history[
            -MEMORY_DEPTH:
        ]
    ]

    candidates = [
        topic
        for topic in pool
        if topic not in recent_topics
    ]

    if not candidates:
        candidates = pool

    topic = random.choice(
        candidates
    )

    logger.info(
        f"🎯 Тема: {topic}"
    )

    return topic


# =============================================================================
# СИСТЕМНЫЙ ПРОМПТ
# =============================================================================

SYSTEM_PROMPT = """
Ты — главный редактор современного Telegram-канала
о лидерстве, управлении командами, эффективности руководителей,
карьере и современных технологиях.

АУДИТОРИЯ:

Руководители среднего звена, директора, собственники бизнеса,
руководители подразделений и специалисты, которые хотят стать
сильнее как управленцы.

ГЛАВНАЯ ЦЕЛЬ:

Создавать публикации, которые:

1. хочется открыть;
2. хочется дочитать;
3. хочется сохранить;
4. хочется переслать коллеге;
5. хочется обсудить.

СТИЛЬ:

- профессиональный;
- уверенный;
- современный;
- интеллектуальный;
- конкретный;
- живой.

Пиши так, будто автор имеет реальный управленческий опыт.

НЕ ИСПОЛЬЗУЙ:

- инфоцыганство;
- "успешный успех";
- "выход из зоны комфорта";
- "никогда не сдавайтесь";
- "поверьте в себя";
- пустую мотивацию;
- банальные советы;
- искусственный пафос;
- канцелярит.

НЕ ВЫДУМЫВАЙ:

- факты;
- статистику;
- исследования;
- цифры;
- цитаты;
- названия компаний;
- события.

Если факт неизвестен — не утверждай его как факт.

КАЖДЫЙ ПОСТ ДОЛЖЕН ИМЕТЬ:

1. Сильный заголовок.
2. Hook — первая мысль, заставляющая читать дальше.
3. Основную полезную мысль.
4. Практический вывод.
5. Естественный вопрос аудитории.

ВАЖНО:

НЕ ИСПОЛЬЗУЙ HTML.

НЕ ИСПОЛЬЗУЙ:

<br>
<br/>
<br />
<b>
</b>
<i>
</i>
<u>
</u>
<s>
</s>

НЕ ИСПОЛЬЗУЙ Markdown.

Форматирование Telegram будет добавлено программой автоматически.

Возвращай обычный чистый текст внутри JSON.
"""


# =============================================================================
# ПРОМПТ ГЕНЕРАЦИИ
# =============================================================================

def build_generation_prompt():

    weekday = datetime.now().weekday()

    day_data = THEMES[
        weekday
    ]

    topic = pick_topic(
        weekday
    )

    history = load_history()

    recent_titles = [
        item.get(
            "title",
            ""
        )
        for item in history[
            -10:
        ]
        if item.get("title")
    ]

    if recent_titles:

        history_text = "\n".join(
            f"- {title}"
            for title in recent_titles
        )

    else:

        history_text = (
            "Пока публикаций нет."
        )

    prompt = f"""
Сегодня:
{datetime.now().strftime("%Y-%m-%d")}

Рубрика:
{day_data["rubric"]}

Формат:
{day_data["format_hint"]}

Тема:
{topic}

ПОСЛЕДНИЕ ЗАГОЛОВКИ КАНАЛА:

{history_text}

Создай новую публикацию.

Она должна отличаться от предыдущих
не только формулировкой, но и углом зрения.

СТРУКТУРА JSON:

{{
    "title": "сильный короткий заголовок",
    "hook": "цепляющая первая мысль",
    "body": "основной текст",
    "cta": "естественный вопрос аудитории",
    "hashtags": [
        "#управление",
        "#лидерство"
    ],
    "image_query": "short English search query",
    "rubric": "{day_data["rubric"]}"
}}

ТРЕБОВАНИЯ:

title:
до 90 символов.

hook:
1–2 предложения.

body:
содержательный текст.

Общий текст должен быть компактным.
Не растягивай мысль ради количества символов.

cta:
один естественный вопрос.

hashtags:
2–3 релевантных хэштега.

image_query:
короткий запрос на английском языке
для поиска фотографии на Unsplash.

НЕ ИСПОЛЬЗУЙ HTML.

НЕ ИСПОЛЬЗУЙ <br>, <b>, <i> и другие HTML-теги.

НЕ ДОБАВЛЯЙ НИКАКИХ ПОЯСНЕНИЙ ВНЕ JSON.
"""

    return (
        prompt,
        topic,
        day_data["rubric"]
    )


# =============================================================================
# ИЗВЛЕЧЕНИЕ JSON
# =============================================================================

def extract_json(
    raw_text: str
):

    if not raw_text:
        return None

    text = raw_text.strip()

    # ---------------------------------------------------------
    # Убираем markdown code block
    # ---------------------------------------------------------

    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^```\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    text = text.strip()

    # ---------------------------------------------------------
    # Первая попытка
    # ---------------------------------------------------------

    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError:
        pass

    # ---------------------------------------------------------
    # Если модель добавила текст до/после JSON,
    # ищем первый объект
    # ---------------------------------------------------------

    start = text.find(
        "{"
    )

    end = text.rfind(
        "}"
    )

    if start != -1 and end != -1 and end > start:

        candidate = text[
            start:end + 1
        ]

        try:

            return json.loads(
                candidate
            )

        except json.JSONDecodeError:
            pass

    logger.warning(
        "⚠️ Не удалось распознать JSON от Gemini"
    )

    return None


# =============================================================================
# GEMINI API
# =============================================================================

def call_gemini(
    model_name,
    prompt
):

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model_name}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )

    # ---------------------------------------------------------
    # Структура JSON
    # ---------------------------------------------------------

    schema = {

        "type": "OBJECT",

        "properties": {

            "title": {
                "type": "STRING"
            },

            "hook": {
                "type": "STRING"
            },

            "body": {
                "type": "STRING"
            },

            "cta": {
                "type": "STRING"
            },

            "hashtags": {

                "type": "ARRAY",

                "items": {
                    "type": "STRING"
                }
            },

            "image_query": {
                "type": "STRING"
            },

            "rubric": {
                "type": "STRING"
            }
        },

        "required": [
            "title",
            "hook",
            "body",
            "cta",
            "hashtags",
            "image_query",
            "rubric"
        ]
    }

    payload = {

        "contents": [

            {
                "parts": [

                    {
                        "text":
                            SYSTEM_PROMPT
                            + "\n\n"
                            + prompt
                    }
                ]
            }
        ],

        "generationConfig": {

            "temperature": 0.75,

            "maxOutputTokens": 3000,

            "responseMimeType":
                "application/json",

            "responseSchema":
                schema
        }
    }

    try:

        logger.info(
            f"🤖 Gemini: {model_name}"
        )

        response = requests.post(
            url,
            json=payload,
            timeout=(10, 90)
        )

        try:

            data = response.json()

        except Exception:

            logger.warning(
                "⚠️ Gemini вернул не JSON HTTP-ответ"
            )

            return None

        # -----------------------------------------------------
        # Ошибка API
        # -----------------------------------------------------

        if response.status_code != 200:

            logger.warning(
                f"⚠️ Gemini HTTP "
                f"{response.status_code}"
            )

            logger.warning(
                json.dumps(
                    data,
                    ensure_ascii=False
                )[:1500]
            )

            return None

        # -----------------------------------------------------
        # candidates
        # -----------------------------------------------------

        candidates = data.get(
            "candidates",
            []
        )

        if not candidates:

            logger.warning(
                "⚠️ Gemini: candidates пуст"
            )

            return None

        candidate = candidates[
            0
        ]

        content = candidate.get(
            "content",
            {}
        )

        parts = content.get(
            "parts",
            []
        )

        if not parts:

            logger.warning(
                "⚠️ Gemini: parts пуст"
            )

            return None

        raw_text = parts[
            0
        ].get(
            "text",
            ""
        ).strip()

        if not raw_text:

            logger.warning(
                "⚠️ Gemini вернул пустой текст"
            )

            return None

        logger.info(
            f"📦 Получено: "
            f"{len(raw_text)} символов"
        )

        # -----------------------------------------------------
        # JSON
        # -----------------------------------------------------

        result = extract_json(
            raw_text
        )

        if not result:

            return None

        # -----------------------------------------------------
        # ФИНАЛЬНАЯ ОЧИСТКА AI-ТЕКСТА
        # -----------------------------------------------------

        for field in [
            "title",
            "hook",
            "body",
            "cta"
        ]:

            if field in result:

                result[field] = clean_ai_text(
                    result[field]
                )

        # -----------------------------------------------------
        # Очистка hashtags
        # -----------------------------------------------------

        hashtags = result.get(
            "hashtags",
            []
        )

        if not isinstance(
            hashtags,
            list
        ):
            hashtags = []

        clean_hashtags = []

        for hashtag in hashtags[:3]:

            hashtag = clean_ai_text(
                str(hashtag)
            )

            hashtag = hashtag.replace(
                " ",
                ""
            )

            if hashtag:

                if not hashtag.startswith(
                    "#"
                ):
                    hashtag = "#" + hashtag

                clean_hashtags.append(
                    hashtag
                )

        result[
            "hashtags"
        ] = clean_hashtags

        # -----------------------------------------------------
        # Очистка image_query
        # -----------------------------------------------------

        result[
            "image_query"
        ] = clean_ai_text(
            result.get(
                "image_query",
                ""
            )
        )

        return result

    except requests.exceptions.Timeout:

        logger.warning(
            f"⏱️ Timeout Gemini: {model_name}"
        )

        return None

    except Exception as e:

        logger.warning(
            f"⚠️ Ошибка Gemini: {e}"
        )

        return None


# =============================================================================
# AI РЕДАКТОР
# =============================================================================

def edit_post(
    post
):

    prompt = f"""
Ты — строгий главный редактор Telegram-канала.

Проверь и улучши следующий пост.

ЗАГОЛОВОК:
{post.get("title", "")}

HOOK:
{post.get("hook", "")}

ТЕКСТ:
{post.get("body", "")}

CTA:
{post.get("cta", "")}

ХЭШТЕГИ:
{post.get("hashtags", [])}

Оцени:

1. Силу заголовка.
2. Силу первой фразы.
3. Конкретность.
4. Пользу для руководителя.
5. Оригинальность.
6. Читабельность.
7. Вероятность сохранения/пересылки.
8. Качество вопроса аудитории.

Оценка от 1 до 10.

ВАЖНО:

Не просто оценивай.

Если что-то слабое —
исправь.

НЕ ИСПОЛЬЗУЙ HTML.

НЕ ИСПОЛЬЗУЙ:

<br>
<br/>
<b>
<i>

Верни JSON:

{{
    "quality_score": 8.5,
    "title": "...",
    "hook": "...",
    "body": "...",
    "cta": "...",
    "hashtags": [
        "#управление",
        "#лидерство"
    ],
    "image_query": "...",
    "editor_comment": "..."
}}

Верни только JSON.
"""

    # ---------------------------------------------------------
    # Используем модели по очереди
    # ---------------------------------------------------------

    for model in TEXT_MODELS:

        result = call_gemini(
            model,
            prompt
        )

        if result:

            try:

                score = float(
                    result.get(
                        "quality_score",
                        0
                    )
                )

            except Exception:

                score = 0

            logger.info(
                f"📝 Оценка AI-редактора: "
                f"{score}/10"
            )

            if score < MIN_QUALITY_SCORE:

                logger.warning(
                    f"⚠️ Качество ниже "
                    f"порога {MIN_QUALITY_SCORE}"
                )

            return result

    logger.warning(
        "⚠️ AI Editor не смог обработать пост"
    )

    return post


# =============================================================================
# ПРОВЕРКА ПОСТА
# =============================================================================

def validate_post(
    post
):

    required_fields = [
        "title",
        "hook",
        "body",
        "cta"
    ]

    for field in required_fields:

        value = post.get(
            field,
            ""
        )

        if not str(value).strip():

            logger.warning(
                f"⚠️ Отсутствует поле: {field}"
            )

            return False

    return True


# =============================================================================
# ФОРМИРОВАНИЕ TELEGRAM-ТЕКСТА
# =============================================================================

def build_telegram_text(
    post
):

    # ---------------------------------------------------------
    # СНАЧАЛА ОЧИЩАЕМ
    # ---------------------------------------------------------

    title = clean_ai_text(
        post.get(
            "title",
            ""
        )
    )

    hook = clean_ai_text(
        post.get(
            "hook",
            ""
        )
    )

    body = clean_ai_text(
        post.get(
            "body",
            ""
        )
    )

    cta = clean_ai_text(
        post.get(
            "cta",
            ""
        )
    )

    # ---------------------------------------------------------
    # Экранируем
    # ---------------------------------------------------------

    title = telegram_escape(
        title
    )

    hook = telegram_escape(
        hook
    )

    body = telegram_escape(
        body
    )

    cta = telegram_escape(
        cta
    )

    # ---------------------------------------------------------
    # Хэштеги
    # ---------------------------------------------------------

    hashtags = post.get(
        "hashtags",
        []
    )

    if not isinstance(
        hashtags,
        list
    ):
        hashtags = []

    clean_hashtags = []

    for hashtag in hashtags[:3]:

        hashtag = clean_ai_text(
            str(hashtag)
        )

        hashtag = hashtag.replace(
            " ",
            ""
        )

        if hashtag:

            if not hashtag.startswith(
                "#"
            ):
                hashtag = "#" + hashtag

            clean_hashtags.append(
                hashtag
            )

    hashtags_text = " ".join(
        clean_hashtags
    )

    hashtags_text = telegram_escape(
        hashtags_text
    )

    # ---------------------------------------------------------
    # СОБИРАЕМ ПОСТ
    # ---------------------------------------------------------

    parts = []

    if title:

        parts.append(
            f"<b>{title}</b>"
        )

    if hook:

        parts.append(
            hook
        )

    if body:

        parts.append(
            body
        )

    if cta:

        parts.append(
            f"<i>{cta}</i>"
        )

    if hashtags_text:

        parts.append(
            hashtags_text
        )

    text = "\n\n".join(
        parts
    )

    # ---------------------------------------------------------
    # ФИНАЛЬНАЯ ЗАЩИТА
    # ---------------------------------------------------------

    # Если вдруг AI каким-то образом протащил
    # <br> или другие теги — удаляем.

    text = re.sub(
        r"<\s*br\s*/?\s*>",
        "\n",
        text,
        flags=re.IGNORECASE
    )

    # ---------------------------------------------------------
    # Ограничение длины
    # ---------------------------------------------------------

    if len(text) > MAX_CAPTION_LENGTH:

        logger.warning(
            f"⚠️ Пост длинный: "
            f"{len(text)} символов"
        )

        text = (
            text[:MAX_CAPTION_LENGTH]
            .rsplit(
                " ",
                1
            )[0]
            + "…"
        )

    return text.strip()


# =============================================================================
# UNSPLASH
# =============================================================================

def generate_image(
    image_query
):

    if not image_query:

        image_query = (
            "modern business leadership"
        )

    query = urllib.parse.quote(
        image_query
    )

    url = (
        "https://api.unsplash.com/photos/random"
        f"?query={query}"
        "&orientation=landscape"
        "&content_filter=high"
        "&client_id="
        f"{UNSPLASH_ACCESS_KEY}"
    )

    try:

        logger.info(
            f"🖼️ Unsplash запрос: "
            f"{image_query}"
        )

        response = requests.get(
            url,
            headers={
                "Accept-Version": "v1"
            },
            timeout=(5, 20)
        )

        if response.status_code == 401:

            logger.error(
                "❌ Неверный UNSPLASH_ACCESS_KEY"
            )

            return None

        if response.status_code == 429:

            logger.warning(
                "⚠️ Лимит Unsplash превышен"
            )

            return None

        if response.status_code != 200:

            logger.warning(
                f"⚠️ Unsplash HTTP "
                f"{response.status_code}"
            )

            return None

        data = response.json()

        image_url = (
            data
            .get("urls", {})
            .get("regular")
        )

        if not image_url:

            logger.warning(
                "⚠️ Unsplash не вернул URL изображения"
            )

            return None

        image_response = requests.get(
            image_url,
            timeout=(5, 30)
        )

        if (
            image_response.status_code == 200
            and len(image_response.content) > 1000
        ):

            photographer = (
                data
                .get("user", {})
                .get("name", "Unknown")
            )

            logger.info(
                f"✅ Фото получено. "
                f"Автор: {photographer}"
            )

            return image_response.content

        logger.warning(
            "⚠️ Не удалось скачать изображение"
        )

        return None

    except requests.exceptions.Timeout:

        logger.warning(
            "⏱️ Timeout Unsplash"
        )

        return None

    except Exception as e:

        logger.warning(
            f"⚠️ Ошибка Unsplash: {e}"
        )

        return None


# =============================================================================
# TELEGRAM — ФОТО
# =============================================================================

def publish_photo(
    image_bytes,
    text
):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    files = {

        "photo": (
            "cover.jpg",
            image_bytes,
            "image/jpeg"
        )
    }

    data = {

        "chat_id":
            TELEGRAM_CHANNEL_ID,

        "caption":
            text,

        "parse_mode":
            "HTML"
    }

    try:

        logger.info(
            "📤 Отправляем фото в Telegram..."
        )

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=(10, 60)
        )

        try:

            result = response.json()

        except Exception:

            logger.error(
                "❌ Telegram вернул "
                "некорректный ответ"
            )

            return None

        if (
            response.status_code == 200
            and result.get("ok")
        ):

            message_id = (
                result[
                    "result"
                ][
                    "message_id"
                ]
            )

            logger.info(
                f"✅ Фото-пост опубликован. "
                f"Message ID: {message_id}"
            )

            return message_id

        logger.error(
            "❌ Ошибка Telegram:"
        )

        logger.error(
            json.dumps(
                result,
                ensure_ascii=False
            )
        )

        return None

    except Exception as e:

        logger.error(
            f"❌ Ошибка отправки фото: {e}"
        )

        return None


# =============================================================================
# TELEGRAM — ТЕКСТ
# =============================================================================

def publish_text(
    text
):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHANNEL_ID,

        "text":
            text,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            False
    }

    try:

        logger.info(
            "📤 Отправляем текст в Telegram..."
        )

        response = requests.post(
            url,
            json=payload,
            timeout=(5, 30)
        )

        result = response.json()

        if (
            response.status_code == 200
            and result.get("ok")
        ):

            message_id = (
                result[
                    "result"
                ][
                    "message_id"
                ]
            )

            logger.info(
                f"✅ Текст опубликован. "
                f"Message ID: {message_id}"
            )

            return message_id

        logger.error(
            f"❌ Telegram error: {result}"
        )

        return None

    except Exception as e:

        logger.error(
            f"❌ Ошибка Telegram: {e}"
        )

        return None


# =============================================================================
# СОХРАНЕНИЕ ИСТОРИИ
# =============================================================================

def save_post_history(
    post,
    topic,
    message_id
):

    history = load_history()

    record = {

        "date":
            datetime.now().isoformat(),

        "topic":
            topic,

        "title":
            clean_ai_text(
                post.get(
                    "title",
                    ""
                )
            ),

        "rubric":
            post.get(
                "rubric",
                ""
            ),

        "quality_score":
            post.get(
                "quality_score",
                None
            ),

        "image_query":
            post.get(
                "image_query",
                ""
            ),

        "message_id":
            message_id
    }

    history.append(
        record
    )

    save_history(
        history
    )

    logger.info(
        "💾 История публикации сохранена"
    )


# =============================================================================
# ГЕНЕРАЦИЯ ПОСТА
# =============================================================================

def generate_post():

    prompt, topic, rubric = (
        build_generation_prompt()
    )

    generated = None

    # ---------------------------------------------------------
    # Генерируем
    # ---------------------------------------------------------

    for model in TEXT_MODELS:

        generated = call_gemini(
            model,
            prompt
        )

        if generated:

            logger.info(
                f"✅ Черновик создан через "
                f"{model}"
            )

            break

    if not generated:

        logger.error(
            "❌ Все модели Gemini "
            "не смогли создать пост"
        )

        return None, None

    # ---------------------------------------------------------
    # Проверяем
    # ---------------------------------------------------------

    if not validate_post(
        generated
    ):

        logger.error(
            "❌ Gemini создал неполный пост"
        )

        return None, None

    # ---------------------------------------------------------
    # AI редактор
    # ---------------------------------------------------------

    edited = edit_post(
        generated
    )

    if not edited:

        edited = generated

    edited[
        "rubric"
    ] = rubric

    # ---------------------------------------------------------
    # Ещё раз очищаем
    # ---------------------------------------------------------

    for field in [
        "title",
        "hook",
        "body",
        "cta"
    ]:

        edited[field] = clean_ai_text(
            edited.get(
                field,
                ""
            )
        )

    # ---------------------------------------------------------
    # Формируем Telegram
    # ---------------------------------------------------------

    telegram_text = build_telegram_text(
        edited
    )

    logger.info(
        f"📏 Размер поста: "
        f"{len(telegram_text)} символов"
    )

    return edited, topic


# =============================================================================
# MAIN
# =============================================================================

def main():

    logger.info(
        ""
    )

    logger.info(
        "=========================================="
    )

    logger.info(
        "🚀 TELEGRAM AI EDITOR V2"
    )

    logger.info(
        "=========================================="
    )

    # ---------------------------------------------------------
    # Генерация
    # ---------------------------------------------------------

    post, topic = generate_post()

    if not post:

        logger.error(
            "❌ Пост не создан"
        )

        sys.exit(1)

    # ---------------------------------------------------------
    # Информация
    # ---------------------------------------------------------

    logger.info(
        f"📰 Заголовок: "
        f"{post.get('title', '')}"
    )

    logger.info(
        f"📊 Качество: "
        f"{post.get('quality_score', 'N/A')}/10"
    )

    logger.info(
        f"🖼️ Image query: "
        f"{post.get('image_query', '')}"
    )

    # ---------------------------------------------------------
    # Telegram текст
    # ---------------------------------------------------------

    telegram_text = build_telegram_text(
        post
    )

    # ---------------------------------------------------------
    # Получаем фото
    # ---------------------------------------------------------

    image_bytes = generate_image(
        post.get(
            "image_query",
            ""
        )
    )

    # ---------------------------------------------------------
    # Публикация
    # ---------------------------------------------------------

    message_id = None

    if image_bytes:

        message_id = publish_photo(
            image_bytes,
            telegram_text
        )

    else:

        logger.warning(
            "⚠️ Фото не получено."
        )

        logger.warning(
            "Публикуем текстом."
        )

        message_id = publish_text(
            telegram_text
        )

    # ---------------------------------------------------------
    # Проверяем публикацию
    # ---------------------------------------------------------

    if not message_id:

        logger.error(
            "❌ Пост не опубликован"
        )

        sys.exit(1)

    # ---------------------------------------------------------
    # История
    # ---------------------------------------------------------

    save_post_history(
        post,
        topic,
        message_id
    )

    # ---------------------------------------------------------
    # ГОТОВО
    # ---------------------------------------------------------

    logger.info(
        "=========================================="
    )

    logger.info(
        "🎉 ПОСТ УСПЕШНО ОПУБЛИКОВАН"
    )

    logger.info(
        "=========================================="
    )


# =============================================================================
# START
# =============================================================================

if __name__ == "__main__":
    main()