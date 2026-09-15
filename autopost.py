import os
import re
import html
import json
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# ============================================================
# V7 — TELEGRAM AI EDITOR
# Надёжная генерация + failover Gemini + безопасный Telegram HTML
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

HISTORY_FILE = BASE_DIR / "topics_history.json"

PREFERRED_MODEL = os.getenv("GEMINI_MODEL", "").strip()

# Если GEMINI_MODEL задан, он идёт первым, но не является единственной моделью.
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

if PREFERRED_MODEL:
    GEMINI_MODELS = [PREFERRED_MODEL] + [
        m for m in GEMINI_MODELS if m != PREFERRED_MODEL
    ]

RETRYABLE_HTTP = {429, 500, 502, 503, 504}

GEMINI_TIMEOUT = 45
TELEGRAM_TIMEOUT = 40
UNSPLASH_TIMEOUT = 25

HISTORY_WEEKS = 8
MAX_CAPTION = 950  # запас до лимита Telegram 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("autopost-v7")


# ============================================================
# ТЕМАТИЧЕСКАЯ СЕТКА
# ============================================================

THEMES = {
    0: {
        "rubric": "Лидерство и управление",
        "topics": [
            "Как руководителю говорить с сильным сотрудником",
            "Почему сильные сотрудники перестают проявлять инициативу",
            "Как руководителю не стать узким местом команды",
            "Что делать, если сотрудник умнее руководителя в своей области",
            "Как отличать самостоятельность сотрудника от потери управляемости",
            "Почему команда перестаёт сообщать руководителю плохие новости",
        ],
    },
    1: {
        "rubric": "Практический инструмент",
        "topics": [
            "Как критиковать сотрудника и не разрушить мотивацию",
            "Простой шаблон постановки задачи без микроменеджмента",
            "Как проводить короткие встречи 1-на-1",
            "Матрица приоритетов для руководителя на перегруженной неделе",
            "Как делегировать задачу так, чтобы её действительно выполнили",
            "5 вопросов перед тем, как поручить новую задачу сотруднику",
        ],
    },
    2: {
        "rubric": "Психология руководителя",
        "topics": [
            "Почему руководитель начинает контролировать всё подряд",
            "Как страх ошибки превращает руководителя в микроменеджера",
            "Почему руководителю трудно признавать собственную ошибку",
            "Как сохранять спокойствие, когда команда ошиблась",
            "Что происходит с командой, когда руководитель постоянно раздражён",
            "Как не переносить рабочее напряжение на стиль управления",
        ],
    },
    3: {
        "rubric": "Кейс и разбор ситуации",
        "topics": [
            "Сотрудник постоянно спорит с руководителем: что делать",
            "Лучший специалист саботирует решения руководителя",
            "Команда выполняет задачи, но перестала думать самостоятельно",
            "Руководитель получил конфликт между двумя сильными сотрудниками",
            "Сотрудник говорит: «Это не входит в мои обязанности»",
            "Новый руководитель пришёл в сильную и самостоятельную команду",
        ],
    },
    4: {
        "rubric": "Решения и ошибки",
        "topics": [
            "Ошибка руководителя, которая незаметно убивает инициативу",
            "Почему постоянная срочность разрушает эффективность",
            "Когда контроль действительно необходим, а когда вреден",
            "Почему похвала иногда демотивирует сильного сотрудника",
            "Как руководитель сам создаёт очередь из согласований",
            "Что делать, если все задачи у команды внезапно стали срочными",
        ],
    },
    5: {
        "rubric": "Лёгкий формат",
        "topics": [
            "Один вопрос, который стоит задать себе перед понедельником",
            "Что сильный руководитель перестаёт делать со временем",
            "Мини-тест: вы руководите или постоянно тушите пожары",
            "3 признака здоровой команды",
            "Одна привычка, которая экономит руководителю часы",
            "Что команда замечает в руководителе раньше, чем он думает",
        ],
    },
    6: {
        "rubric": "Недельная рефлексия",
        "topics": [
            "Что руководителю стоит перестать делать на следующей неделе",
            "Какая задача на этой неделе действительно была важной",
            "Где руководитель потратил время впустую",
            "Как понять, что команда стала самостоятельнее",
            "Какую сложную беседу руководитель откладывает",
            "Один управленческий вывод, который стоит забрать в новую неделю",
        ],
    },
}


IMAGE_QUERIES = {
    "Лидерство и управление": [
        "business leader meeting team",
        "executive leadership team",
        "manager talking employee",
        "business team discussion",
    ],
    "Практический инструмент": [
        "business planning desk",
        "project planning meeting",
        "manager writing notes",
        "team workflow office",
    ],
    "Психология руководителя": [
        "business executive thinking",
        "manager reflection office",
        "leadership stress office",
        "professional thinking portrait",
    ],
    "Кейс и разбор ситуации": [
        "business conflict meeting",
        "team discussion office",
        "manager employee conversation",
        "business negotiation",
    ],
    "Решения и ошибки": [
        "business decision meeting",
        "executive decision office",
        "business problem solving",
        "manager strategy meeting",
    ],
    "Лёгкий формат": [
        "modern office team",
        "business coffee meeting",
        "creative team office",
        "professional team smiling",
    ],
    "Недельная рефлексия": [
        "business planning week",
        "executive desk notebook",
        "leadership reflection",
        "office planning calendar",
    ],
}


# ============================================================
# ИСТОРИЯ
# ============================================================

def load_history():
    if not HISTORY_FILE.exists():
        return []

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            return data.get("published", [])

    except Exception as exc:
        log.warning("Не удалось прочитать историю: %s", exc)

    return []


def save_history(items):
    payload = {"published": items[-300:]}

    HISTORY_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_topic(text):
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    stop = {
        "как", "что", "это", "для", "при", "если", "или", "из",
        "не", "и", "в", "на", "с", "по", "к", "у", "о", "а", "то",
        "же",
    }

    return {
        word
        for word in normalize_topic(text).split()
        if len(word) >= 4 and word not in stop
    }


def similarity_percent(a, b):
    ta, tb = tokens(a), tokens(b)

    if not ta or not tb:
        return 0

    return round(
        100 * len(ta & tb) / max(len(ta | tb), 1)
    )


def recent_history():
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=HISTORY_WEEKS)

    result = []

    for item in load_history():
        try:
            dt = datetime.fromisoformat(item["published_at"])

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if dt >= cutoff:
                result.append(item)

        except Exception:
            # Старые записи без даты тоже используем как защиту от повторов.
            result.append(item)

    return result


def choose_topic(candidates):
    history = recent_history()

    log.info(
        "🧠 История за последние %s недель: %s публикаций",
        HISTORY_WEEKS,
        len(history),
    )

    scored = []

    for topic in candidates:
        max_sim = 0

        for item in history:
            old_topic = item.get("topic", "")
            sim = similarity_percent(topic, old_topic)
            max_sim = max(max_sim, sim)

        scored.append((max_sim, topic))

        log.info(
            "🔎 Локальная проверка: %s → %s%%",
            topic,
            max_sim,
        )

    # 40% и выше считаем слишком похожим.
    acceptable = [
        item for item in scored
        if item[0] < 40
    ]

    if acceptable:
        selected = random.choice(acceptable)
    else:
        selected = min(scored, key=lambda x: x[0])

    log.info(
        "✅ Выбрана тема: %s | сходство: %s%%",
        selected[1],
        selected[0],
    )

    return selected[1]


def record_success(topic, rubric, model, text):
    items = load_history()

    items.append({
        "topic": topic,
        "rubric": rubric,
        "model": model,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": normalize_topic(text)[:300],
    })

    save_history(items)


# ============================================================
# TELEGRAM HTML
# ============================================================

ALLOWED_TAGS = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre)(?:\s[^<>]*)?>",
    re.IGNORECASE,
)


def sanitize_telegram_html(text):
    """
    Главный фикс V7.

    <br>, <br/>, <br /> превращаются в реальные переносы строк.
    Неподдерживаемые HTML-теги удаляются.
    Markdown fences удаляются.
    """

    if not text:
        return ""

    text = text.strip()

    # Удаляем markdown code fences.
    text = re.sub(
        r"```(?:html|HTML|text|TEXT)?",
        "",
        text,
    )
    text = text.replace("```", "")

    # КРИТИЧЕСКИЙ ФИКС ПРОБЛЕМЫ V6.
    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    # HTML entities -> обычный текст.
    text = html.unescape(text)

    # Сохраняем только разрешённые Telegram-теги.
    saved = []

    def stash(match):
        saved.append(match.group(0))
        return f"\x00TAG{len(saved)-1}\x00"

    text = ALLOWED_TAGS.sub(stash, text)

    # Удаляем остальные HTML-теги.
    text = re.sub(r"<[^>]*>", "", text)

    # Экранируем обычные < > &.
    text = html.escape(text, quote=False)

    # Возвращаем разрешённые теги.
    for index, tag in enumerate(saved):
        text = text.replace(
            f"\x00TAG{index}\x00",
            tag,
        )

    # На случай нарушения промпта моделью.
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<b>\1</b>",
        text,
        flags=re.DOTALL,
    )

    text = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)",
        r"<i>\1</i>",
        text,
    )

    # Нормализация пустых строк.
    text = re.sub(
        r"\n[ \t]+",
        "\n",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def strip_html(text):
    text = re.sub(
        r"</?[^>]+>",
        "",
        text,
    )

    return html.unescape(text)


def fit_caption(text, max_len=MAX_CAPTION):
    if len(text) <= max_len:
        return text

    log.warning(
        "⚠️ Пост длиннее лимита Telegram: %s символов",
        len(text),
    )

    # Безопасный аварийный вариант.
    plain = strip_html(text)
    plain = re.sub(r"\s+", " ", plain).strip()

    if len(plain) <= max_len:
        return html.escape(
            plain,
            quote=False,
        )

    cut = plain[: max_len - 1]

    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]

    return (
        html.escape(
            cut.rstrip(),
            quote=False,
        )
        + "…"
    )


def valid_basic_html(text):
    pairs = {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "code",
        "pre",
    }

    stack = []

    for match in re.finditer(
        r"</?([a-zA-Z]+)(?:\s[^<>]*)?>",
        text,
    ):
        full = match.group(0)
        tag = match.group(1).lower()

        if tag not in pairs:
            return False

        if full.startswith("</"):
            if not stack or stack[-1] != tag:
                return False

            stack.pop()

        elif not full.rstrip().endswith("/>"):
            stack.append(tag)

    return not stack


# ============================================================
# GEMINI
# ============================================================

def build_prompt(topic, rubric):
    return f"""
Ты — главный редактор Telegram-канала о лидерстве и управлении
«Лидерство без выгорания».

Рубрика: {rubric}
Тема: {topic}

Задача: создай один сильный Telegram-пост для руководителей.

Требования:
1. 600–850 символов.
2. Пост должен быть полностью законченным: не обрывай предложение, мысль или абзац.
3. Последняя содержательная строка должна заканчиваться нормальным знаком препинания.
4. Начни с сильного тезиса, наблюдения или вопроса.
3. Дай конкретную управленческую мысль, которую можно применить на практике.
4. Не пересказывай очевидности.
5. Не выдумывай статистику, исследования, цитаты, факты или имена.
6. Если приводишь пример — обозначай его как гипотетическую ситуацию.
7. Используй 2–3 уместных эмодзи, без перегруза.
8. В конце — короткий вопрос читателю или практическое действие.
9. Добавь 2–3 релевантных хэштега.
10. Используй короткие абзацы.

КРИТИЧЕСКИ ВАЖНО ДЛЯ TELEGRAM:
- НЕЛЬЗЯ использовать <br>, <br/>, <p>, <div>, <span>.
- Для переноса строки используй настоящий символ новой строки.
- Разрешены только:
  <b>...</b>
  <i>...</i>
  <u>...</u>
  <s>...</s>
  <code>...</code>
- Не используй Markdown **жирный**, *курсив*, ```код```.
- Не используй ссылки.
- Не добавляй пояснения от редактора.
- Верни ТОЛЬКО готовый текст поста.
""".strip()


def gemini_request(model, prompt):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model}:generateContent"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt}
                ],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 1800,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    return requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=GEMINI_TIMEOUT,
    )


def extract_gemini_text(data):
    try:
        parts = data["candidates"][0]["content"]["parts"]

        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict)
        )

        return text.strip()

    except (
        KeyError,
        IndexError,
        TypeError,
    ):
        return ""


def looks_like_complete_post(text):
    """
    Проверяет, не оборвалась ли генерация Gemini посреди предложения.
    Короткие/незаконченные ответы не отправляем в Telegram.
    """
    if not text:
        return False

    clean = sanitize_telegram_html(text)
    plain = strip_html(clean).strip()

    # Слишком короткий ответ — почти наверняка неполная генерация.
    if len(plain) < 450:
        return False

    # Пост должен содержать хотя бы один хэштег.
    if "#" not in plain:
        return False

    # Если последняя строка выглядит оборванной — не публикуем.
    last = plain.rstrip()

    # Типичные признаки обрыва генерации.
    if last.endswith(("-", "—", ",", ":", ";", "…")):
        return False

    # Последний символ должен выглядеть как нормальное завершение.
    if not re.search(r"[.!?)]$", last):
        return False

    return True


def generate_with_failover(topic, rubric):
    prompt = build_prompt(
        topic,
        rubric,
    )

    for model in GEMINI_MODELS:
        log.info(
            "🤖 Gemini генерация: %s",
            model,
        )

        for attempt in range(1, 4):
            try:
                response = gemini_request(
                    model,
                    prompt,
                )

                if response.status_code == 200:
                    text = extract_gemini_text(
                        response.json()
                    )

                    if text:
                        if looks_like_complete_post(text):
                            log.info(
                                "✅ Gemini успешно: %s | попытка %s | %s символов",
                                model,
                                attempt,
                                len(strip_html(sanitize_telegram_html(text))),
                            )

                            return text, model

                        log.warning(
                            "⚠️ Gemini вернул незавершённый/слишком короткий пост "
                            "| %s | попытка %s/3 — генерируем заново",
                            model,
                            attempt,
                        )

                        if attempt < 3:
                            time.sleep(
                                1.5 + random.uniform(0.5, 1.5)
                            )

                        continue

                    log.warning(
                        "⚠️ %s вернул пустой ответ",
                        model,
                    )

                    break

                if response.status_code in RETRYABLE_HTTP:
                    log.warning(
                        "⚠️ Gemini HTTP %s | %s | попытка %s/3",
                        response.status_code,
                        model,
                        attempt,
                    )

                    if attempt < 3:
                        delay = (
                            (2 ** (attempt - 1)) * 3
                            + random.uniform(0.5, 1.5)
                        )

                        time.sleep(delay)

                    continue

                # Постоянная ошибка: повторять тот же запрос нет смысла.
                log.error(
                    "❌ Gemini HTTP %s | %s: %s",
                    response.status_code,
                    model,
                    response.text[:500],
                )

                break

            except requests.RequestException as exc:
                log.warning(
                    "⚠️ Сетевая ошибка Gemini | %s | попытка %s/3: %s",
                    model,
                    attempt,
                    exc,
                )

                if attempt < 3:
                    time.sleep(
                        3 * attempt
                        + random.uniform(0.5, 1.5)
                    )

        log.warning(
            "➡️ Переключаемся с %s на следующую модель",
            model,
        )

    return None, None


# ============================================================
# ЛОКАЛЬНЫЙ FALLBACK
# ============================================================

def local_fallback(topic):
    templates = [
        (
            f"<b>{topic}</b>\n\n"
            "Иногда управленческая проблема начинается не с сотрудника, "
            "а с того, как руководитель формулирует ситуацию.\n\n"
            "Перед тем как давать оценку, задайте три вопроса: "
            "что произошло фактически, чего я ожидаю и что конкретно "
            "нужно изменить дальше.\n\n"
            "Так разговор превращается из критики в управленческое действие. 🎯\n\n"
            "Какой из этих трёх вопросов вы чаще всего пропускаете?\n\n"
            "#лидерство #управление #руководитель"
        ),
        (
            f"<b>{topic}</b>\n\n"
            "Сильный руководитель не обязан лично решать каждую проблему. "
            "Его задача — создать условия, в которых команда способна "
            "решать проблемы самостоятельно.\n\n"
            "Попробуйте сегодня вместо готового ответа спросить: "
            "«Какое решение ты считаешь лучшим и почему?»\n\n"
            "Один такой вопрос иногда развивает сотрудника сильнее, "
            "чем длинная инструкция. 💡\n\n"
            "Попробуете применить его на этой неделе?\n\n"
            "#лидерство #делегирование #команда"
        ),
    ]

    return random.choice(templates)


# ============================================================
# UNSPLASH
# ============================================================

def get_unsplash_photo(rubric):
    if not UNSPLASH_ACCESS_KEY:
        log.warning(
            "⚠️ UNSPLASH_ACCESS_KEY не задан"
        )

        return None

    queries = IMAGE_QUERIES.get(
        rubric,
        [
            "leadership business",
            "business team",
        ],
    )

    query = random.choice(queries)

    url = "https://api.unsplash.com/photos/random"

    params = {
        "query": query,
        "orientation": "landscape",
        "content_filter": "high",
    }

    headers = {
        "Authorization": (
            f"Client-ID {UNSPLASH_ACCESS_KEY}"
        ),
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=UNSPLASH_TIMEOUT,
        )

        if response.status_code != 200:
            log.warning(
                "⚠️ Unsplash HTTP %s: %s",
                response.status_code,
                response.text[:300],
            )

            return None

        data = response.json()

        image_url = (
            data.get("urls", {})
            .get("regular")
        )

        if not image_url:
            return None

        image = requests.get(
            image_url,
            timeout=UNSPLASH_TIMEOUT,
        )

        if image.status_code != 200:
            return None

        log.info(
            "🖼️ Unsplash: %s",
            query,
        )

        return {
            "bytes": image.content,
            "photo_url": (
                data.get("links", {})
                .get("html", "")
            ),
            "author": (
                data.get("user", {})
                .get("name", "Unsplash")
            ),
            "query": query,
        }

    except requests.RequestException as exc:
        log.warning(
            "⚠️ Ошибка Unsplash: %s",
            exc,
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )


def send_photo_to_telegram(
    image_bytes,
    caption,
):
    return requests.post(
        telegram_url("sendPhoto"),
        data={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={
            "photo": (
                "leadership.jpg",
                image_bytes,
                "image/jpeg",
            ),
        },
        timeout=TELEGRAM_TIMEOUT,
    )


def send_text_to_telegram(text):
    return requests.post(
        telegram_url("sendMessage"),
        data={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=TELEGRAM_TIMEOUT,
    )


def publish(caption, image):
    caption = sanitize_telegram_html(
        caption
    )

    caption = fit_caption(
        caption
    )

    if not valid_basic_html(caption):
        log.warning(
            "⚠️ HTML не прошёл проверку — plain text"
        )

        caption = strip_html(caption)

    if image:
        response = send_photo_to_telegram(
            image["bytes"],
            caption,
        )

        if response.status_code == 200:
            log.info(
                "✅ Пост с фото опубликован"
            )

            return True

        log.error(
            "❌ Telegram sendPhoto HTTP %s: %s",
            response.status_code,
            response.text[:700],
        )

        # Второй шанс: отправляем фото без HTML.
        plain = strip_html(caption)

        response = send_photo_to_telegram(
            image["bytes"],
            plain,
        )

        if response.status_code == 200:
            log.info(
                "✅ Пост с фото опубликован без HTML"
            )

            return True

    # Если фото не отправилось — сохраняем публикацию текста.
    response = send_text_to_telegram(
        caption
    )

    if response.status_code == 200:
        log.info(
            "✅ Текстовый пост опубликован"
        )

        return True

    log.error(
        "❌ Telegram sendMessage HTTP %s: %s",
        response.status_code,
        response.text[:700],
    )

    return False


# ============================================================
# MAIN
# ============================================================

def main():
    print("🚀 TELEGRAM AI EDITOR V7.1")
    print(
        "🤖 Gemini failover:",
        " → ".join(GEMINI_MODELS),
    )
    print(
        f"🧠 Анти-повтор: последние "
        f"{HISTORY_WEEKS} недель"
    )

    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHANNEL_ID": TELEGRAM_CHANNEL_ID,
        "GEMINI_API_KEY": GEMINI_API_KEY,
    }

    missing = [
        name
        for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные: "
            + ", ".join(missing)
        )

    now = datetime.now()
    weekday = now.weekday()

    day_data = THEMES[weekday]

    rubric = day_data["rubric"]

    base_topic = random.choice(
        day_data["topics"]
    )

    log.info(
        "🎯 Базовая тема дня: %s",
        base_topic,
    )

    log.info(
        "🗂️ Тем дня доступно: %s",
        len(day_data["topics"]),
    )

    topic = choose_topic(
        [base_topic]
        + [
            x
            for x in day_data["topics"]
            if x != base_topic
        ]
    )

    generated, model = generate_with_failover(
        topic,
        rubric,
    )

    if generated:
        caption = sanitize_telegram_html(
            generated
        )

        caption = fit_caption(
            caption
        )

        log.info(
            "🧠 Использована модель: %s",
            model,
        )

    else:
        log.error(
            "❌ Все Gemini-модели недоступны"
        )

        log.warning(
            "🛟 Используем локальный fallback"
        )

        caption = local_fallback(
            topic
        )

        model = "local-fallback"

    image = get_unsplash_photo(
        rubric
    )

    if publish(
        caption,
        image,
    ):
        record_success(
            topic,
            rubric,
            model,
            caption,
        )

        log.info(
            "🎉 V7 завершил работу успешно"
        )

        return 0

    log.error(
        "❌ Пост не опубликован"
    )

    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )