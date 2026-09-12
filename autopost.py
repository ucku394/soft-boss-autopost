import os
import sys
import json
import random
import logging
import urllib.parse
import html
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
        logging.FileHandler("autopost.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# =============================================================================
# ENV
# =============================================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

if not all([
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
    GEMINI_API_KEY,
    UNSPLASH_ACCESS_KEY
]):
    logger.error(
        "❌ Не все переменные заданы! Проверь .env:"
    )
    logger.error(
        "TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, "
        "GEMINI_API_KEY, UNSPLASH_ACCESS_KEY"
    )
    sys.exit(1)

# =============================================================================
# ФАЙЛЫ
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HISTORY_FILE = os.path.join(
    BASE_DIR,
    "content_history.json"
)

# =============================================================================
# НАСТРОЙКИ
# =============================================================================

MEMORY_DEPTH = 20

MIN_QUALITY_SCORE = 7.0

MAX_CAPTION_LENGTH = 1020

TEXT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
]

# =============================================================================
# РУБРИКИ
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
            "Чек-лист эффективного one-to-one",
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
# AI SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """
Ты — главный редактор современного Telegram-канала
о лидерстве, управлении командами, эффективности руководителей,
карьере и современных технологиях.

Аудитория:
руководители среднего звена, директора, собственники бизнеса,
руководители подразделений и специалисты, которые хотят стать сильнее
как управленцы.

Твоя задача — создавать контент, который:

1. хочется открыть;
2. хочется дочитать;
3. хочется сохранить;
4. хочется переслать коллеге;
5. вызывает желание высказать своё мнение.

СТИЛЬ:

- профессиональный;
- уверенный;
- современный;
- интеллектуальный;
- конкретный;
- живой;
- без инфоцыганства;
- без банальной мотивации;
- без канцелярита.

Пиши так, будто автор имеет реальный управленческий опыт.

ЗАПРЕЩЕНО:

- "успешный успех";
- "выход из зоны комфорта";
- "никогда не сдавайтесь";
- "поверьте в себя";
- очевидные советы;
- пустые мотивационные фразы;
- выдуманные исследования;
- выдуманные цифры;
- выдуманные цитаты;
- искусственный пафос.

Каждый пост должен содержать хотя бы одну конкретную мысль,
которую руководитель может применить на практике.

НЕ РАСТЯГИВАЙ ТЕКСТ.

Лучше 500 сильных символов, чем 1000 пустых.

ЗАГОЛОВОК должен вызывать интерес.

HOOK — первая мысль после заголовка.
Он должен заставить читать дальше.

ОСНОВНОЙ ТЕКСТ:
объясняет проблему и даёт конкретный вывод.

CTA:
естественный вопрос или призыв к действию.

НЕ ПОВТОРЯЙ предыдущие публикации.

Если тема похожа на предыдущий пост,
измени угол зрения.

Используй HTML:

<b>жирный</b>
<i>курсив</i>

Не используй Markdown.

Всегда возвращай ТОЛЬКО JSON.
"""

# =============================================================================
# ИСТОРИЯ
# =============================================================================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        logger.warning(
            f"⚠️ Ошибка чтения истории: {e}"
        )
        return []


def save_history(history):

    try:
        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                history[-100:],
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        logger.warning(
            f"⚠️ Ошибка сохранения истории: {e}"
        )


# =============================================================================
# ВЫБОР ТЕМЫ
# =============================================================================

def pick_topic(weekday):

    day_data = THEMES[weekday]

    pool = day_data["topics"]

    history = load_history()

    recent_topics = [
        item.get("topic", "")
        for item in history[-MEMORY_DEPTH:]
    ]

    candidates = [
        topic
        for topic in pool
        if topic not in recent_topics
    ]

    if not candidates:
        candidates = pool

    topic = random.choice(candidates)

    logger.info(
        f"🎯 Выбрана тема: {topic}"
    )

    return topic


# =============================================================================
# ПРОМПТ ГЕНЕРАЦИИ
# =============================================================================

def build_generation_prompt():

    weekday = datetime.now().weekday()

    day_data = THEMES[weekday]

    topic = pick_topic(weekday)

    history = load_history()

    recent_titles = [
        item.get("title", "")
        for item in history[-10:]
        if item.get("title")
    ]

    history_text = "\n".join(
        f"- {title}"
        for title in recent_titles
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

Последние заголовки канала:

{history_text}

Создай новую публикацию.

Она должна отличаться от предыдущих публикаций
не только формулировкой, но и углом зрения.

Верни JSON строго следующего формата:

{{
  "title": "короткий сильный заголовок",
  "hook": "цепляющая первая мысль",
  "body": "основной текст",
  "cta": "вопрос аудитории",
  "hashtags": ["#управление", "#лидерство"],
  "image_query": "короткий английский запрос для Unsplash",
  "rubric": "{day_data["rubric"]}"
}}

Ограничения:

title — желательно до 90 символов.

body + hook + cta + hashtags должны вместе
умещаться примерно в 900 символов.

Не используй выдуманные факты.

image_query пиши на английском языке.

Не добавляй никаких комментариев вне JSON.
"""

    return prompt, topic, day_data["rubric"]


# =============================================================================
# GEMINI
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
                        "text": SYSTEM_PROMPT
                        + "\n\n"
                        + prompt
                    }
                ]
            }
        ],

        "generationConfig": {

            "temperature": 0.75,

            "maxOutputTokens": 3000,

            "responseMimeType": "application/json",

            "responseSchema": schema
        }
    }

    try:

        logger.info(
            f"🤖 Запрашиваем Gemini: {model_name}"
        )

        response = requests.post(
            url,
            json=payload,
            timeout=(10, 90)
        )

        data = response.json()

        if response.status_code != 200:

            logger.warning(
                f"⚠️ Gemini {model_name}: "
                f"HTTP {response.status_code}"
            )

            logger.warning(
                json.dumps(
                    data,
                    ensure_ascii=False
                )[:1000]
            )

            return None

        candidates = data.get(
            "candidates",
            []
        )

        if not candidates:
            logger.warning(
                "⚠️ Gemini вернул пустой candidates"
            )
            return None

        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )

        if not parts:
            return None

        raw_text = parts[0].get(
            "text",
            ""
        ).strip()

        if not raw_text:
            return None

        logger.info(
            f"📦 Получен JSON: "
            f"{len(raw_text)} символов"
        )

        try:

            result = json.loads(raw_text)

        except json.JSONDecodeError:

            logger.warning(
                "⚠️ Gemini вернул некорректный JSON"
            )

            return None

        return result

    except requests.exceptions.Timeout:

        logger.warning(
            f"⏱️ Timeout Gemini {model_name}"
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

def edit_post(post):

    prompt = f"""
Ты получил черновик Telegram-публикации.

Проверь его как строгий главный редактор.

ЧЕРНОВИК:

Заголовок:
{post.get("title", "")}

Hook:
{post.get("hook", "")}

Текст:
{post.get("body", "")}

CTA:
{post.get("cta", "")}

Хэштеги:
{post.get("hashtags", [])}

Проверь:

1. Есть ли сильный hook?
2. Нет ли банальностей?
3. Есть ли конкретная польза?
4. Не повторяет ли пост очевидные советы?
5. Хороший ли заголовок?
6. Вызывает ли CTA желание ответить?
7. Нет ли выдуманных фактов?
8. Не слишком ли много текста?

Оценка от 1 до 10.

Верни JSON:

{{
  "quality_score": 8.5,
  "title": "...",
  "hook": "...",
  "body": "...",
  "cta": "...",
  "hashtags": ["#управление", "#лидерство"],
  "image_query": "...",
  "editor_comment": "короткий комментарий"
}}

Если текст можно улучшить —
ИСПРАВЬ его.

Не просто оценивай.

Верни только JSON.
"""

    result = None

    for model in TEXT_MODELS:

        result = call_gemini(
            model,
            prompt
        )

        if result:
            break

    if not result:
        logger.warning(
            "⚠️ AI Editor не смог обработать пост"
        )
        return post

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
        f"📝 Оценка редактора: {score}/10"
    )

    if score < MIN_QUALITY_SCORE:

        logger.warning(
            f"⚠️ Пост получил только {score}/10"
        )

        # Не блокируем публикацию полностью.
        # Иначе канал может остаться без поста.
        # Позже сделаем автоматическую регенерацию.
        result["quality_warning"] = True

    return result


# =============================================================================
# ФОРМИРОВАНИЕ TELEGRAM ТЕКСТА
# =============================================================================

def build_telegram_text(post):

    title = post.get(
        "title",
        ""
    ).strip()

    hook = post.get(
        "hook",
        ""
    ).strip()

    body = post.get(
        "body",
        ""
    ).strip()

    cta = post.get(
        "cta",
        ""
    ).strip()

    hashtags = post.get(
        "hashtags",
        []
    )

    if not isinstance(
        hashtags,
        list
    ):
        hashtags = []

    hashtags_text = " ".join(
        str(x).strip()
        for x in hashtags[:3]
        if str(x).strip()
    )

    parts = []

    if title:
        parts.append(
            f"<b>{html.escape(title)}</b>"
        )

    if hook:
        parts.append(
            html.escape(hook)
        )

    if body:
        parts.append(
            html.escape(body)
        )

    if cta:
        parts.append(
            f"<i>{html.escape(cta)}</i>"
        )

    if hashtags_text:
        parts.append(
            html.escape(hashtags_text)
        )

    text = "\n\n".join(parts)

    if len(text) > MAX_CAPTION_LENGTH:

        logger.warning(
            f"⚠️ Пост слишком длинный: "
            f"{len(text)} символов"
        )

        text = (
            text[:MAX_CAPTION_LENGTH]
            .rsplit(" ", 1)[0]
            + "…"
        )

    return text


# =============================================================================
# UNSPLASH
# =============================================================================

def generate_image(
    image_query
):

    if not image_query:
        image_query = "modern business leadership"

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
            f"🖼️ Ищем изображение: "
            f"{image_query}"
        )

        response = requests.get(
            url,
            headers={
                "Accept-Version": "v1"
            },
            timeout=(5, 20)
        )

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

        return None

    except Exception as e:

        logger.warning(
            f"⚠️ Ошибка Unsplash: {e}"
        )

        return None


# =============================================================================
# TELEGRAM
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
        "chat_id": TELEGRAM_CHANNEL_ID,
        "caption": text,
        "parse_mode": "HTML",
        "show_caption_above_media": False
    }

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=(10, 60)
        )

        result = response.json()

        if (
            response.status_code == 200
            and result.get("ok")
        ):

            message_id = (
                result["result"]["message_id"]
            )

            logger.info(
                f"✅ Пост опубликован. "
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


def publish_text(text):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:

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
                result["result"]["message_id"]
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

        "date": datetime.now().isoformat(),

        "topic": topic,

        "title": post.get(
            "title",
            ""
        ),

        "rubric": post.get(
            "rubric",
            ""
        ),

        "quality_score": post.get(
            "quality_score",
            None
        ),

        "image_query": post.get(
            "image_query",
            ""
        ),

        "message_id": message_id
    }

    history.append(record)

    save_history(history)

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

    for model in TEXT_MODELS:

        generated = call_gemini(
            model,
            prompt
        )

        if generated:
            logger.info(
                f"✅ Черновик создан через {model}"
            )
            break

    if not generated:

        logger.error(
            "❌ Не удалось создать пост"
        )

        return None, None

    # AI Editor
    edited = edit_post(
        generated
    )

    edited["rubric"] = rubric

    text = build_telegram_text(
        edited
    )

    logger.info(
        f"📏 Размер Telegram-поста: "
        f"{len(text)} символов"
    )

    return edited, topic


# =============================================================================
# MAIN
# =============================================================================

def main():

    logger.info(
        "========================================"
    )

    logger.info(
        "🚀 ЗАПУСК TELEGRAM AI EDITOR V2"
    )

    logger.info(
        "========================================"
    )

    post, topic = generate_post()

    if not post:

        logger.error(
            "❌ Генерация завершилась ошибкой"
        )

        sys.exit(1)

    logger.info(
        f"📰 Заголовок: "
        f"{post.get('title', '')}"
    )

    logger.info(
        f"🎯 Качество: "
        f"{post.get('quality_score', 'N/A')}/10"
    )

    telegram_text = build_telegram_text(
        post
    )

    image_query = post.get(
        "image_query",
        ""
    )

    image_bytes = generate_image(
        image_query
    )

    message_id = None

    if image_bytes:

        message_id = publish_photo(
            image_bytes,
            telegram_text
        )

    else:

        logger.warning(
            "⚠️ Изображение не получено."
        )

        message_id = publish_text(
            telegram_text
        )

    if not message_id:

        logger.error(
            "❌ Публикация не удалась"
        )

        sys.exit(1)

    save_post_history(
        post,
        topic,
        message_id
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "🎉 V2 УСПЕШНО ЗАВЕРШИЛА РАБОТУ"
    )

    logger.info(
        "========================================"
    )


if __name__ == "__main__":
    main()