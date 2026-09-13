import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Any, Optional

import requests
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash",
).strip()

HISTORY_FILE = "content_history.json"

# Не публиковать практически одинаковую тему
ANTI_REPEAT_DAYS = 56

# Сколько последних публикаций хранить
MAX_HISTORY_ITEMS = 200

# Сколько последних тем использовать для защиты от повторов
MEMORY_DEPTH = 40

# Одна попытка Gemini на одну публикацию
MAX_GEMINI_ATTEMPTS = 1

# Повтор Telegram/Unsplash при временных ошибках
MAX_HTTP_ATTEMPTS = 3

# Telegram caption limit
TELEGRAM_CAPTION_LIMIT = 1024

# Ограничения контента
MAX_TITLE_LENGTH = 140
MAX_HOOK_LENGTH = 260
MAX_BODY_LENGTH = 1500
MAX_CTA_LENGTH = 180

# Минимальная оценка качества от Gemini
QUALITY_THRESHOLD = 7

# Локальная защита от похожих тем
SIMILARITY_THRESHOLD = 0.70

# HTTP timeout
HTTP_TIMEOUT = 30

# Пауза между запросами
RETRY_BASE_DELAY = 3


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("autopost")


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "soft-boss-autopost/1.0",
    }
)


# ============================================================
# VALIDATION
# ============================================================

def validate_config() -> None:
    missing = []

    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_CHANNEL_ID:
        missing.append("TELEGRAM_CHANNEL_ID")

    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")

    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные: "
            + ", ".join(missing)
        )

    logger.info("Configuration OK")
    logger.info("Gemini model: %s", GEMINI_MODEL)


# ============================================================
# TIME
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Убираем markdown/code fencing
    text = text.replace("```json", "")
    text = text.replace("```", "")

    # Убираем лишние пробелы
    text = re.sub(r"[ \t]+", " ", text)

    # Максимум 2 перевода строки подряд
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def limit_text(text: str, max_length: int) -> str:
    text = clean_text(text)

    if len(text) <= max_length:
        return text

    shortened = text[:max_length].rstrip()

    # Не оставляем оборванное слово
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]

    return shortened.rstrip(".,;:!?-") + "…"


def normalize_for_similarity(text: str) -> str:
    text = clean_text(text).lower()

    text = re.sub(r"[^a-zа-яё0-9\s]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def tokenize(text: str) -> set[str]:
    normalized = normalize_for_similarity(text)

    words = normalized.split()

    # Убираем слишком короткие слова
    return {
        word
        for word in words
        if len(word) >= 4
    }


def similarity(a: str, b: str) -> float:
    a_tokens = tokenize(a)
    b_tokens = tokenize(b)

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)

    if union == 0:
        return 0.0

    return intersection / union


# ============================================================
# HISTORY
# ============================================================

def load_history() -> list[dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        logger.info("History file does not exist. Creating new history.")
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            logger.warning("History is not a list. Resetting.")
            return []

        return data

    except Exception as exc:
        logger.warning("Не удалось прочитать историю: %s", exc)
        return []


def save_history(history: list[dict[str, Any]]) -> None:
    history = history[-MAX_HISTORY_ITEMS:]

    temp_file = HISTORY_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            history,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    os.replace(temp_file, HISTORY_FILE)


def recent_topics(history: list[dict[str, Any]]) -> list[str]:
    topics = []

    cutoff = utc_now() - timedelta(days=ANTI_REPEAT_DAYS)

    for item in reversed(history):
        topic = clean_text(item.get("topic", ""))
        created_at = clean_text(item.get("created_at", ""))

        if not topic:
            continue

        try:
            dt = datetime.fromisoformat(created_at)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

        except Exception:
            dt = utc_now()

        if dt >= cutoff:
            topics.append(topic)

        if len(topics) >= MEMORY_DEPTH:
            break

    return topics


def is_topic_unique(
    topic: str,
    previous_topics: list[str],
) -> bool:
    topic = clean_text(topic)

    for previous in previous_topics:
        score = similarity(topic, previous)

        if score >= SIMILARITY_THRESHOLD:
            logger.warning(
                "Тема слишком похожа на предыдущую: %.2f | %s",
                score,
                previous,
            )
            return False

    return True


# ============================================================
# GEMINI
# ============================================================

def gemini_url() -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )


def extract_gemini_text(response_json: dict[str, Any]) -> str:
    candidates = response_json.get("candidates") or []

    if not candidates:
        prompt_feedback = response_json.get("promptFeedback")

        if prompt_feedback:
            logger.error(
                "Gemini promptFeedback: %s",
                json.dumps(
                    prompt_feedback,
                    ensure_ascii=False,
                ),
            )

        usage = response_json.get("usageMetadata")

        if usage:
            logger.info(
                "Gemini usageMetadata: %s",
                json.dumps(
                    usage,
                    ensure_ascii=False,
                ),
            )

        return ""

    candidate = candidates[0]

    finish_reason = candidate.get("finishReason")

    if finish_reason:
        logger.info(
            "Gemini finishReason: %s",
            finish_reason,
        )

    content = candidate.get("content") or {}
    parts = content.get("parts") or []

    texts = []

    for part in parts:
        text = part.get("text")

        if text:
            texts.append(text)

    result = "\n".join(texts).strip()

    usage = response_json.get("usageMetadata")

    if usage:
        logger.info(
            "Gemini usageMetadata: %s",
            json.dumps(
                usage,
                ensure_ascii=False,
            ),
        )

    return result


def extract_json(text: str) -> Optional[dict[str, Any]]:
    text = clean_text(text)

    if not text:
        return None

    # Сначала пробуем обычный JSON
    try:
        data = json.loads(text)

        if isinstance(data, dict):
            return data

    except json.JSONDecodeError:
        pass

    # Затем ищем объект внутри ответа
    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL,
    )

    if not match:
        return None

    try:
        data = json.loads(match.group(0))

        if isinstance(data, dict):
            return data

    except json.JSONDecodeError:
        return None

    return None


def call_gemini(prompt: str) -> Optional[dict[str, Any]]:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt,
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.9,
            "topP": 0.95,
            "maxOutputTokens": 1800,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "topic": {
                        "type": "STRING",
                    },
                    "title": {
                        "type": "STRING",
                    },
                    "hook": {
                        "type": "STRING",
                    },
                    "body": {
                        "type": "STRING",
                    },
                    "cta": {
                        "type": "STRING",
                    },
                    "hashtags": {
                        "type": "ARRAY",
                        "items": {
                            "type": "STRING",
                        },
                    },
                    "quality_score": {
                        "type": "INTEGER",
                    },
                },
                "required": [
                    "topic",
                    "title",
                    "hook",
                    "body",
                    "cta",
                    "hashtags",
                    "quality_score",
                ],
            },
        },
    }

    try:
        response = SESSION.post(
            gemini_url(),
            json=payload,
            timeout=HTTP_TIMEOUT,
        )

    except requests.RequestException as exc:
        logger.error(
            "Ошибка соединения с Gemini: %s",
            exc,
        )
        return None

    if response.status_code == 429:
        logger.error(
            "Gemini API error 429 — квота исчерпана."
        )

        try:
            error_data = response.json()

            logger.error(
                "Gemini 429 details: %s",
                json.dumps(
                    error_data,
                    ensure_ascii=False,
                ),
            )

        except Exception:
            logger.error(
                "Gemini 429 response: %s",
                response.text[:2000],
            )

        # ВАЖНО:
        # Не делаем быстрые повторные запросы.
        # При исчерпанной квоте они только тратят время.
        return None

    if response.status_code >= 500:
        logger.warning(
            "Gemini server error HTTP %s.",
            response.status_code,
        )
        return None

    if response.status_code != 200:
        logger.error(
            "Gemini API error %s: %s",
            response.status_code,
            response.text[:3000],
        )
        return None

    try:
        data = response.json()

    except ValueError:
        logger.error("Gemini вернул невалидный JSON HTTP-ответ.")
        return None

    text = extract_gemini_text(data)

    if not text:
        logger.error("Gemini вернул пустой текст.")
        return None

    result = extract_json(text)

    if result is None:
        logger.error(
            "Gemini вернул текст, но не JSON: %s",
            text[:2000],
        )
        return None

    return result


# ============================================================
# CONTENT GENERATION
# ============================================================

def build_content_prompt(
    previous_topics: list[str],
) -> str:
    if previous_topics:
        history_text = "\n".join(
            f"- {topic}"
            for topic in previous_topics[:MEMORY_DEPTH]
        )
    else:
        history_text = "- История пуста."

    return f"""
Ты — главный редактор Telegram-канала про бизнес, деньги,
продуктивность, карьеру, личную эффективность и развитие.

Нужно создать ОДНУ полностью готовую публикацию.

КРИТИЧЕСКИ ВАЖНО:
1. Не повторяй темы из истории.
2. Не делай перефразирование уже использованной темы.
3. Выбери свежий практический угол.
4. Пост должен давать конкретную пользу.
5. Не используй банальные мотивационные фразы.
6. Не начинай с "Сегодня поговорим о..."
7. Не пиши "подписывайтесь", если это не является естественным CTA.
8. Не используй непроверяемые цифры и факты.
9. Не придумывай исследования, компании или статистику.
10. Пиши на русском языке.
11. Текст должен быть естественным для Telegram.
12. Не используй markdown-разметку.
13. Не используй HTML.
14. Не добавляй пояснений вне JSON.

ИСТОРИЯ ПОСЛЕДНИХ ТЕМ:
{history_text}

СТРУКТУРА:

topic:
Короткая уникальная тема.

title:
Сильный заголовок, желательно до 100 символов.

hook:
1–2 предложения, которые заставляют читать дальше.

body:
Основная практическая часть.
Дай 3–5 конкретных идей, шагов, ошибок или наблюдений.
Разбивай текст на короткие абзацы.
Не делай огромную стену текста.

cta:
Короткий естественный призыв к действию.
Например вопрос читателю или предложение применить идею.

hashtags:
3–5 релевантных хэштегов.

quality_score:
Целое число от 1 до 10.
Оценивай собственный текст строго.
7+ означает действительно хороший готовый пост.

Верни ТОЛЬКО JSON.
""".strip()


def validate_generated_content(
    data: dict[str, Any],
    previous_topics: list[str],
) -> Optional[dict[str, Any]]:
    required = [
        "topic",
        "title",
        "hook",
        "body",
        "cta",
        "hashtags",
        "quality_score",
    ]

    for field in required:
        if field not in data:
            logger.error(
                "В ответе Gemini отсутствует поле: %s",
                field,
            )
            return None

    topic = limit_text(
        data.get("topic", ""),
        MAX_TITLE_LENGTH,
    )

    title = limit_text(
        data.get("title", ""),
        MAX_TITLE_LENGTH,
    )

    hook = limit_text(
        data.get("hook", ""),
        MAX_HOOK_LENGTH,
    )

    body = limit_text(
        data.get("body", ""),
        MAX_BODY_LENGTH,
    )

    cta = limit_text(
        data.get("cta", ""),
        MAX_CTA_LENGTH,
    )

    hashtags = data.get("hashtags", [])

    if not isinstance(hashtags, list):
        hashtags = []

    clean_hashtags = []

    for hashtag in hashtags:
        hashtag = clean_text(hashtag)

        if not hashtag:
            continue

        hashtag = re.sub(
            r"[^a-zA-Zа-яА-ЯёЁ0-9_#]",
            "",
            hashtag,
        )

        if not hashtag.startswith("#"):
            hashtag = "#" + hashtag

        if len(hashtag) > 40:
            continue

        if hashtag not in clean_hashtags:
            clean_hashtags.append(hashtag)

    try:
        quality_score = int(
            data.get("quality_score", 0)
        )
    except (TypeError, ValueError):
        quality_score = 0

    if not topic:
        logger.error("Пустая тема.")
        return None

    if not title:
        logger.error("Пустой заголовок.")
        return None

    if not body:
        logger.error("Пустое тело поста.")
        return None

    if not is_topic_unique(
        topic,
        previous_topics,
    ):
        logger.error(
            "Gemini сгенерировал повторяющуюся тему."
        )
        return None

    if quality_score < QUALITY_THRESHOLD:
        logger.warning(
            "Качество поста ниже порога: %s < %s",
            quality_score,
            QUALITY_THRESHOLD,
        )
        return None

    result = {
        "topic": topic,
        "title": title,
        "hook": hook,
        "body": body,
        "cta": cta,
        "hashtags": clean_hashtags[:5],
        "quality_score": quality_score,
    }

    return result


def generate_content(
    history: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    previous_topics = recent_topics(history)

    logger.info(
        "Generating complete post with one Gemini request."
    )

    prompt = build_content_prompt(previous_topics)

    for attempt in range(1, MAX_GEMINI_ATTEMPTS + 1):
        logger.info(
            "Gemini generation attempt %s/%s.",
            attempt,
            MAX_GEMINI_ATTEMPTS,
        )

        result = call_gemini(prompt)

        if result is None:
            continue

        validated = validate_generated_content(
            result,
            previous_topics,
        )

        if validated:
            logger.info(
                "Пост успешно сгенерирован. Quality score: %s",
                validated["quality_score"],
            )
            return validated

    return None


# ============================================================
# TELEGRAM HTML
# ============================================================

def telegram_escape(text: str) -> str:
    return html_escape(
        clean_text(text),
        quote=False,
    )


def render_telegram_post(
    content: dict[str, Any],
) -> str:
    title = telegram_escape(
        content["title"]
    )

    hook = telegram_escape(
        content["hook"]
    )

    body = telegram_escape(
        content["body"]
    )

    cta = telegram_escape(
        content["cta"]
    )

    hashtags = " ".join(
        telegram_escape(h)
        for h in content["hashtags"]
    )

    parts = []

    if title:
        parts.append(
            f"<b>{title}</b>"
        )

    if hook:
        parts.append(hook)

    if body:
        parts.append(body)

    if cta:
        parts.append(
            f"<i>{cta}</i>"
        )

    if hashtags:
        parts.append(hashtags)

    text = "\n\n".join(parts)

    return fit_telegram_caption(text)


def fit_telegram_caption(
    text: str,
) -> str:
    if len(text) <= TELEGRAM_CAPTION_LIMIT:
        return text

    logger.warning(
        "Telegram caption слишком длинный: %s. Обрезаем.",
        len(text),
    )

    # Оставляем место для многоточия
    limit = TELEGRAM_CAPTION_LIMIT - 1

    shortened = text[:limit]

    # Не режем HTML entity
    last_amp = shortened.rfind("&")
    last_semicolon = shortened.rfind(";")

    if last_amp > last_semicolon:
        shortened = shortened[:last_amp]

    # Не режем HTML tag
    last_open = shortened.rfind("<")
    last_close = shortened.rfind(">")

    if last_open > last_close:
        shortened = shortened[:last_open]

    return shortened.rstrip() + "…"


# ============================================================
# UNSPLASH
# ============================================================

def get_unsplash_image(
    query: str,
) -> Optional[str]:
    if not UNSPLASH_ACCESS_KEY:
        logger.info(
            "UNSPLASH_ACCESS_KEY не задан. Публикуем без изображения."
        )
        return None

    url = "https://api.unsplash.com/search/photos"

    params = {
        "query": query,
        "per_page": 10,
        "orientation": "landscape",
    }

    headers = {
        "Authorization": (
            f"Client-ID {UNSPLASH_ACCESS_KEY}"
        )
    }

    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            response = SESSION.get(
                url,
                params=params,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )

        except requests.RequestException as exc:
            logger.warning(
                "Unsplash connection error: %s",
                exc,
            )

            if attempt < MAX_HTTP_ATTEMPTS:
                time.sleep(
                    RETRY_BASE_DELAY * attempt
                )

            continue

        if response.status_code == 429:
            logger.warning(
                "Unsplash rate limit."
            )
            return None

        if response.status_code >= 500:
            if attempt < MAX_HTTP_ATTEMPTS:
                time.sleep(
                    RETRY_BASE_DELAY * attempt
                )
                continue

        if response.status_code != 200:
            logger.warning(
                "Unsplash HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        results = data.get("results") or []

        if not results:
            logger.info(
                "Unsplash не нашёл изображение."
            )
            return None

        # Выбираем случайное изображение из первых результатов
        selected = random.choice(
            results[: min(10, len(results))]
        )

        urls = selected.get("urls") or {}

        image_url = (
            urls.get("regular")
            or urls.get("full")
            or urls.get("small")
        )

        if image_url:
            return image_url

        return None

    return None


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_url(
    method: str,
) -> str:
    return (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )


def telegram_request(
    method: str,
    data: Optional[dict[str, Any]] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    """
    Возвращает:
      success  — Telegram точно подтвердил запрос
      failed   — Telegram точно ответил ошибкой
      unknown  — был timeout/неизвестный сетевой исход

    unknown нельзя автоматически повторять для sendPhoto,
    потому что Telegram мог уже принять сообщение.
    """

    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            response = SESSION.post(
                telegram_url(method),
                data=data or {},
                timeout=HTTP_TIMEOUT,
            )

        except requests.Timeout as exc:
            logger.error(
                "Telegram timeout: %s",
                exc,
            )

            return "unknown", None

        except requests.RequestException as exc:
            logger.error(
                "Telegram connection error: %s",
                exc,
            )

            if attempt < MAX_HTTP_ATTEMPTS:
                time.sleep(
                    RETRY_BASE_DELAY * attempt
                )
                continue

            return "unknown", None

        try:
            result = response.json()
        except ValueError:
            result = None

        if response.status_code == 200 and result:
            if result.get("ok"):
                return "success", result

        # Telegram 429
        if response.status_code == 429:
            retry_after = 10

            try:
                retry_after = int(
                    result.get(
                        "parameters",
                        {},
                    ).get(
                        "retry_after",
                        10,
                    )
                )
            except Exception:
                pass

            logger.warning(
                "Telegram rate limit. Ждём %s сек.",
                retry_after,
            )

            if attempt < MAX_HTTP_ATTEMPTS:
                time.sleep(
                    min(retry_after, 60)
                )
                continue

        # 5xx можно повторить
        if response.status_code >= 500:
            if attempt < MAX_HTTP_ATTEMPTS:
                time.sleep(
                    RETRY_BASE_DELAY * attempt
                )
                continue

        logger.error(
            "Telegram API error HTTP %s: %s",
            response.status_code,
            response.text[:2000],
        )

        return "failed", result

    return "failed", None


def publish_to_telegram(
    caption: str,
    image_url: Optional[str],
) -> str:
    # Если есть картинка — сначала sendPhoto.
    if image_url:
        logger.info(
            "Публикуем пост с изображением."
        )

        status, result = telegram_request(
            "sendPhoto",
            {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML",
            },
        )

        if status == "success":
            logger.info(
                "Telegram sendPhoto: success"
            )
            return "success"

        if status == "unknown":
            logger.error(
                "Telegram sendPhoto имеет неизвестный результат. "
                "Fallback на текст НЕ выполняем, чтобы не создать дубль."
            )
            return "unknown"

        logger.warning(
            "sendPhoto failed. Пробуем текстовую публикацию."
        )

    # Текстовая публикация.
    logger.info(
        "Публикуем текстовый пост."
    )

    status, _ = telegram_request(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
    )

    if status == "success":
        logger.info(
            "Telegram sendMessage: success"
        )
    else:
        logger.error(
            "Telegram sendMessage: %s",
            status,
        )

    return status


# ============================================================
# HISTORY RECORD
# ============================================================

def add_history_item(
    history: list[dict[str, Any]],
    content: dict[str, Any],
    telegram_status: str,
) -> None:
    history.append(
        {
            "created_at": iso_now(),
            "topic": content["topic"],
            "title": content["title"],
            "quality_score": content["quality_score"],
            "telegram_status": telegram_status,
        }
    )

    # Сохраняем только успешные публикации
    history[:] = history[-MAX_HISTORY_ITEMS:]


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    logger.info(
        "=================================================="
    )
    logger.info(
        "Starting Telegram autopost"
    )
    logger.info(
        "=================================================="
    )

    try:
        validate_config()
    except Exception as exc:
        logger.error(
            "Configuration error: %s",
            exc,
        )
        return 1

    history = load_history()

    logger.info(
        "History items: %s",
        len(history),
    )

    # --------------------------------------------------------
    # Генерируем пост
    # --------------------------------------------------------

    content = generate_content(history)

    if not content:
        logger.error(
            "Не удалось сгенерировать уникальный качественный пост."
        )
        return 1

    logger.info(
        "Selected topic: %s",
        content["topic"],
    )

    logger.info(
        "Quality score: %s",
        content["quality_score"],
    )

    # --------------------------------------------------------
    # Telegram text
    # --------------------------------------------------------

    caption = render_telegram_post(content)

    logger.info(
        "Final Telegram caption length: %s/%s",
        len(caption),
        TELEGRAM_CAPTION_LIMIT,
    )

    # --------------------------------------------------------
    # Image
    # --------------------------------------------------------

    image_query = content["topic"]

    image_url = get_unsplash_image(
        image_query
    )

    if image_url:
        logger.info(
            "Unsplash image found."
        )
    else:
        logger.info(
            "Image unavailable. Continuing without image."
        )

    # --------------------------------------------------------
    # Publish
    # --------------------------------------------------------

    telegram_status = publish_to_telegram(
        caption=caption,
        image_url=image_url,
    )

    if telegram_status != "success":
        logger.error(
            "Публикация не подтверждена Telegram. "
            "History НЕ обновляем."
        )

        return 1

    # --------------------------------------------------------
    # Save history
    # --------------------------------------------------------

    add_history_item(
        history,
        content,
        telegram_status,
    )

    save_history(history)

    logger.info(
        "История успешно обновлена."
    )

    logger.info(
        "=================================================="
    )
    logger.info(
        "POST PUBLISHED SUCCESSFULLY"
    )
    logger.info(
        "=================================================="
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.error("Interrupted.")
        sys.exit(130)
    except Exception as exc:
        logger.exception(
            "Fatal error: %s",
            exc,
        )
        sys.exit(1)