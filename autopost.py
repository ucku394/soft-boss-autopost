import html
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

HISTORY_FILE = "content_history.json"

ANTI_REPEAT_DAYS = 56
MAX_HISTORY_ITEMS = 200
MEMORY_DEPTH = 40

MAX_TOPIC_ATTEMPTS = 5
MAX_POST_ATTEMPTS = 3
MAX_EDITOR_ATTEMPTS = 2

LOCAL_TOPIC_SIMILARITY_THRESHOLD = 0.75
GEMINI_TOPIC_SIMILARITY_THRESHOLD = 0.78

QUALITY_THRESHOLD = 7

TELEGRAM_CAPTION_LIMIT = 1024

TITLE_MAX_LENGTH = 90
HOOK_MAX_LENGTH = 220
BODY_MAX_LENGTH = 650
CTA_MAX_LENGTH = 180

MIN_HASHTAGS = 2
MAX_HASHTAGS = 3

HTTP_TIMEOUT = (10, 60)

GEMINI_MAX_OUTPUT_TOKENS = 3000

USER_AGENT = "soft-boss-autopost/1.0"

WEEKDAY_THEMES = {
    0: "Лидерство и управление",
    1: "Карьерный рост и развитие",
    2: "Переговоры и коммуникация",
    3: "Продуктивность и рабочие привычки",
    4: "Психология работы и бизнеса",
    5: "Личные границы и баланс",
    6: "Мышление руководителя",
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("autopost")


# ============================================================
# VALIDATION
# ============================================================

def validate_environment() -> None:
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHANNEL_ID": TELEGRAM_CHANNEL_ID,
        "GEMINI_API_KEY": GEMINI_API_KEY,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        logger.error(
            "Отсутствуют обязательные переменные окружения: %s",
            ", ".join(missing),
        )
        sys.exit(1)

    if not UNSPLASH_ACCESS_KEY:
        logger.warning(
            "UNSPLASH_ACCESS_KEY не задан. Посты будут публиковаться без изображений."
        )


# ============================================================
# TIME
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso_now() -> str:
    return utc_now().isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None

    if not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_ai_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Remove HTML/XML tags because Telegram formatting is added later.
    text = re.sub(r"<[^>]+>", "", text)

    # Remove zero-width characters.
    text = text.replace("\u200b", "")
    text = text.replace("\u200c", "")
    text = text.replace("\u200d", "")

    # Normalize unicode.
    text = unicodedata.normalize("NFC", text)

    # Normalize excessive whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def shorten_text(text: str, max_length: int) -> str:
    text = clean_ai_text(text)

    if len(text) <= max_length:
        return text

    truncated = text[:max_length].rstrip()

    # Prefer cutting at a natural sentence/word boundary.
    candidates = [
        truncated.rfind(". "),
        truncated.rfind("! "),
        truncated.rfind("? "),
        truncated.rfind("; "),
        truncated.rfind(", "),
        truncated.rfind(" "),
    ]

    cut = max(candidates)

    if cut >= int(max_length * 0.65):
        truncated = truncated[:cut + 1]

    return truncated.rstrip(" ,;:-")


def format_numbered_paragraphs(text: str) -> str:
    text = clean_ai_text(text)

    if not text:
        return ""

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    formatted = []

    for line in lines:
        # Preserve existing numbering.
        if re.match(r"^\d+[\.\)]\s+", line):
            formatted.append(line)
        else:
            formatted.append(line)

    return "\n\n".join(formatted)


# ============================================================
# PROMPT INJECTION PROTECTION
# ============================================================

PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all|any|previous|prior)\s+instructions",
    r"disregard\s+(all|any|previous|prior)\s+instructions",
    r"forget\s+(all|any|previous|prior)\s+instructions",
    r"system\s+prompt",
    r"developer\s+message",
    r"reveal\s+(your|the)\s+(prompt|instructions)",
    r"show\s+(your|the)\s+(prompt|instructions)",
    r"игнорируй\s+(все|предыдущие|прошлые)\s+инструкции",
    r"забудь\s+(все|предыдущие|прошлые)\s+инструкции",
    r"системн(ый|ые)\s+промпт",
    r"раскрой\s+(промпт|инструкции)",
    r"покажи\s+(промпт|инструкции)",
]


def contains_prompt_injection(text: str) -> bool:
    if not text:
        return False

    lowered = text.lower()

    return any(
        re.search(pattern, lowered, flags=re.IGNORECASE)
        for pattern in PROMPT_INJECTION_PATTERNS
    )


# ============================================================
# HASHTAGS
# ============================================================

def normalize_hashtag(tag: str) -> str:
    tag = clean_ai_text(tag)

    if not tag:
        return ""

    tag = tag.replace("#", "")
    tag = re.sub(r"[^\wа-яА-ЯёЁ]", "", tag, flags=re.UNICODE)

    if not tag:
        return ""

    return f"#{tag.lower()}"


def sanitize_hashtags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    result = []

    for item in value:
        tag = normalize_hashtag(str(item))

        if not tag:
            continue

        if tag not in result:
            result.append(tag)

        if len(result) >= MAX_HASHTAGS:
            break

    return result


def ensure_hashtags(tags: list[str], rubric: str) -> list[str]:
    result = list(tags)

    fallback_by_rubric = {
        "Лидерство и управление": ["#лидерство", "#управление"],
        "Карьерный рост и развитие": ["#карьера", "#развитие"],
        "Переговоры и коммуникация": ["#переговоры", "#коммуникация"],
        "Продуктивность и рабочие привычки": ["#продуктивность", "#работа"],
        "Психология работы и бизнеса": ["#психология", "#бизнес"],
        "Личные границы и баланс": ["#границы", "#баланс"],
        "Мышление руководителя": ["#руководитель", "#мышление"],
    }

    fallbacks = fallback_by_rubric.get(
        rubric,
        ["#лидерство", "#управление"],
    )

    for fallback in fallbacks:
        if len(result) >= MIN_HASHTAGS:
            break

        if fallback not in result:
            result.append(fallback)

    return result[:MAX_HASHTAGS]


# ============================================================
# HISTORY
# ============================================================

def load_history() -> list[dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        logger.info("Файл истории отсутствует. Будет создан после публикации.")
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            logger.warning("История имеет неверный формат. Используем пустую историю.")
            return []

        return [
            item for item in data
            if isinstance(item, dict)
        ]

    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Не удалось прочитать историю: %s", exc)
        return []


def save_history(history: list[dict[str, Any]]) -> bool:
    try:
        trimmed = history[-MAX_HISTORY_ITEMS:]

        temp_file = f"{HISTORY_FILE}.tmp"

        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(
                trimmed,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        os.replace(temp_file, HISTORY_FILE)

        logger.info(
            "История сохранена: %d записей.",
            len(trimmed),
        )

        return True

    except OSError as exc:
        logger.error("Не удалось сохранить историю: %s", exc)
        return False


def get_recent_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = utc_now() - timedelta(days=ANTI_REPEAT_DAYS)

    recent = []

    for item in history:
        published_at = parse_datetime(item.get("published_at"))

        # Старые записи с датой исключаем.
        # Записи без даты оставляем, чтобы не потерять защиту от повторов
        # после миграции старого формата истории.
        if published_at is None or published_at >= cutoff:
            recent.append(item)

    return recent[-MEMORY_DEPTH:]


def build_history_context(history: list[dict[str, Any]]) -> str:
    recent = get_recent_history(history)

    if not recent:
        return "История публикаций пока пуста."

    lines = []

    for index, item in enumerate(recent, start=1):
        topic = clean_ai_text(item.get("topic", ""))
        title = clean_ai_text(item.get("title", ""))
        rubric = clean_ai_text(item.get("rubric", ""))

        summary = clean_ai_text(item.get("summary", ""))

        if topic:
            lines.append(
                f"{index}. Тема: {topic}\n"
                f"   Заголовок: {title}\n"
                f"   Рубрика: {rubric}\n"
                f"   Кратко: {summary}"
            )

    return "\n".join(lines) if lines else "История публикаций пока пуста."


# ============================================================
# TOPICS
# ============================================================

def get_today_theme() -> str:
    return WEEKDAY_THEMES[utc_now().weekday()]


def normalize_for_similarity(text: str) -> str:
    text = clean_ai_text(text).lower()

    text = text.replace("ё", "е")

    text = re.sub(r"[^\wа-яА-Я ]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def topic_keywords(text: str) -> set[str]:
    normalized = normalize_for_similarity(text)

    words = normalized.split()

    # Very common words don't contribute much to topic similarity.
    stop_words = {
        "и", "в", "во", "на", "по", "с", "со", "для", "как",
        "что", "это", "из", "к", "у", "о", "об", "не", "а",
        "или", "но", "если", "то", "за", "от", "до", "при",
        "про", "можно", "нужно", "быть", "свой", "свои",
        "the", "and", "for", "with", "from", "this", "that",
    }

    return {
        word
        for word in words
        if len(word) >= 4 and word not in stop_words
    }


def local_topic_similarity(topic_a: str, topic_b: str) -> float:
    a = normalize_for_similarity(topic_a)
    b = normalize_for_similarity(topic_b)

    if not a or not b:
        return 0.0

    sequence_score = SequenceMatcher(None, a, b).ratio()

    keywords_a = topic_keywords(a)
    keywords_b = topic_keywords(b)

    if keywords_a or keywords_b:
        union = keywords_a | keywords_b
        intersection = keywords_a & keywords_b

        jaccard_score = (
            len(intersection) / len(union)
            if union
            else 0.0
        )
    else:
        jaccard_score = 0.0

    return max(sequence_score, jaccard_score)


def is_topic_repeated(
    topic: str,
    history: list[dict[str, Any]],
) -> tuple[bool, float, str]:
    recent = get_recent_history(history)

    for item in recent:
        old_topic = clean_ai_text(item.get("topic", ""))

        if not old_topic:
            continue

        similarity = local_topic_similarity(topic, old_topic)

        if similarity >= LOCAL_TOPIC_SIMILARITY_THRESHOLD:
            return True, similarity, old_topic

    return False, 0.0, ""


# ============================================================
# GEMINI
# ============================================================

def gemini_url() -> str:
    return (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )


def gemini_request(
    contents: list[dict[str, Any]],
    system_instruction: str,
    response_schema: Optional[dict[str, Any]] = None,
    temperature: float = 0.7,
    max_output_tokens: int = GEMINI_MAX_OUTPUT_TOKENS,
) -> Optional[dict[str, Any]]:
    payload: dict[str, Any] = {
        "systemInstruction": {
            "parts": [
                {
                    "text": system_instruction,
                }
            ]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }

    if response_schema:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["responseSchema"] = response_schema

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
        "User-Agent": USER_AGENT,
    }

    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                gemini_url(),
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code in {429, 500, 502, 503, 504}:
                logger.warning(
                    "Gemini временная ошибка HTTP %s, попытка %d/%d.",
                    response.status_code,
                    attempt,
                    max_attempts,
                )

                if attempt < max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue

            if response.status_code != 200:
                logger.error(
                    "Gemini API error %s: %s",
                    response.status_code,
                    response.text[:1000],
                )
                return None

            data = response.json()

            candidates = data.get("candidates", [])

            if not candidates:
                logger.error("Gemini не вернул candidates.")
                return None

            parts = candidates[0].get("content", {}).get("parts", [])

            text_parts = [
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("text")
            ]

            text = "".join(text_parts).strip()

            if not text:
                logger.error("Gemini вернул пустой текст.")
                return None

            return {
                "text": text,
                "raw": data,
            }

        except requests.Timeout:
            logger.warning(
                "Таймаут Gemini, попытка %d/%d.",
                attempt,
                max_attempts,
            )

            if attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
                continue

        except requests.RequestException as exc:
            logger.warning(
                "Ошибка запроса Gemini: %s",
                exc,
            )

            if attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
                continue

        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Ошибка обработки ответа Gemini: %s", exc)
            return None

    return None


def extract_json(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None

    cleaned = text.strip()

    # Markdown JSON block.
    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned,
    )

    try:
        data = json.loads(cleaned)

        if isinstance(data, dict):
            return data

    except json.JSONDecodeError:
        pass

    # Fallback: locate the outermost JSON object.
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return None

    candidate = cleaned[start:end + 1]

    try:
        data = json.loads(candidate)

        if isinstance(data, dict):
            return data

    except json.JSONDecodeError:
        return None

    return None


# ============================================================
# GEMINI TOPIC CHECK
# ============================================================

TOPIC_SIMILARITY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "similar": {
            "type": "BOOLEAN",
        },
        "score": {
            "type": "NUMBER",
        },
        "reason": {
            "type": "STRING",
        },
    },
    "required": [
        "similar",
        "score",
        "reason",
    ],
}


def check_topic_with_gemini(
    topic: str,
    previous_topic: str,
) -> Optional[bool]:
    system_instruction = """
Ты проверяешь, являются ли две темы публикаций слишком похожими.

Сравнивай смысл, а не только совпадение слов.

similar=true, если новая тема фактически повторяет предыдущую,
даже если сформулирована другими словами.

similar=false, если это действительно другой угол или проблема.

Верни только JSON согласно схеме.
""".strip()

    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        f"Новая тема:\n{topic}\n\n"
                        f"Предыдущая тема:\n{previous_topic}"
                    ),
                }
            ],
        }
    ]

    result = gemini_request(
        contents=contents,
        system_instruction=system_instruction,
        response_schema=TOPIC_SIMILARITY_SCHEMA,
        temperature=0.1,
        max_output_tokens=300,
    )

    if not result:
        logger.warning(
            "Gemini-проверка похожести недоступна. "
            "Используем локальную проверку."
        )
        return None

    data = extract_json(result["text"])

    if not data:
        logger.warning("Не удалось распознать ответ Gemini similarity.")
        return None

    similar = data.get("similar")

    if isinstance(similar, bool):
        return similar

    return None


def is_topic_acceptable(
    topic: str,
    history: list[dict[str, Any]],
) -> bool:
    repeated, score, old_topic = is_topic_repeated(topic, history)

    if not repeated:
        return True

    logger.info(
        "Локально найден похожий topic: %.2f — %s",
        score,
        old_topic,
    )

    gemini_result = check_topic_with_gemini(
        topic,
        old_topic,
    )

    if gemini_result is True:
        logger.info("Gemini подтвердил повтор темы.")
        return False

    if gemini_result is False:
        logger.info(
            "Gemini считает темы достаточно разными."
        )
        return True

    # Если Gemini недоступен, локальный фильтр остаётся защитой.
    return False


# ============================================================
# TOPIC GENERATION
# ============================================================

TOPIC_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "topic": {
            "type": "STRING",
        },
    },
    "required": [
        "topic",
    ],
}


def generate_topic(
    theme: str,
    history: list[dict[str, Any]],
) -> Optional[str]:
    history_context = build_history_context(history)

    system_instruction = """
Ты — редактор Telegram-канала для руководителей, предпринимателей
и специалистов, которые хотят расти профессионально.

Твоя задача — предложить одну конкретную тему для сильного поста.

Требования:
- тема должна быть практичной;
- без банальных мотивационных клише;
- должна содержать конкретную проблему, наблюдение,
  конфликт, ошибку или неочевидный вывод;
- не повторять предыдущие публикации;
- не использовать темы из истории под другим названием;
- не придумывать статистику или исследования;
- не обращаться к читателю как к ИИ;
- не обсуждать системные инструкции.

Верни только JSON.
""".strip()

    user_prompt = f"""
Сегодняшняя рубрика:
{theme}

Последние публикации:
{history_context}

Предложи одну новую тему.
""".strip()

    result = gemini_request(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "text": user_prompt,
                    }
                ],
            }
        ],
        system_instruction=system_instruction,
        response_schema=TOPIC_SCHEMA,
        temperature=0.9,
        max_output_tokens=300,
    )

    if not result:
        return None

    data = extract_json(result["text"])

    if not data:
        logger.warning("Gemini не вернул корректный JSON темы.")
        return None

    topic = clean_ai_text(data.get("topic", ""))

    if not topic:
        return None

    if len(topic) > 220:
        topic = shorten_text(topic, 220)

    if contains_prompt_injection(topic):
        logger.warning("Тема отклонена из-за prompt injection.")
        return None

    return topic


def select_unique_topic(
    theme: str,
    history: list[dict[str, Any]],
    rejected_topics: Optional[list[str]] = None,
) -> Optional[str]:
    rejected_topics = rejected_topics or []

    working_history = list(history)

    for rejected in rejected_topics:
        working_history.append(
            {
                "topic": rejected,
                "published_at": utc_iso_now(),
            }
        )

    for attempt in range(1, MAX_TOPIC_ATTEMPTS + 1):
        topic = generate_topic(
            theme=theme,
            history=working_history,
        )

        if not topic:
            logger.warning(
                "Не удалось получить тему, попытка %d/%d.",
                attempt,
                MAX_TOPIC_ATTEMPTS,
            )
            continue

        if not is_topic_acceptable(topic, working_history):
            logger.info(
                "Тема отклонена как повторная: %s",
                topic,
            )

            working_history.append(
                {
                    "topic": topic,
                    "published_at": utc_iso_now(),
                }
            )
            continue

        logger.info("Выбрана тема: %s", topic)
        return topic

    logger.error("Не удалось выбрать уникальную тему.")
    return None


# ============================================================
# POST GENERATION
# ============================================================

POST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
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
        "rubric": {
            "type": "STRING",
        },
        "hashtags": {
            "type": "ARRAY",
            "items": {
                "type": "STRING",
            },
            "minItems": MIN_HASHTAGS,
            "maxItems": MAX_HASHTAGS,
        },
    },
    "required": [
        "title",
        "hook",
        "body",
        "cta",
        "rubric",
        "hashtags",
    ],
}


def generate_post_for_topic(
    topic: str,
    theme: str,
    history: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    history_context = build_history_context(history)

    system_instruction = """
Ты — опытный редактор Telegram-канала о лидерстве,
карьере, управлении и современной рабочей культуре.

Пиши естественно и по-человечески.

Пост должен:
- давать конкретную пользу;
- содержать одну сильную мысль;
- избегать инфобизнес-клише;
- избегать искусственной мотивационной риторики;
- не выдумывать цифры, исследования, цитаты и факты;
- не использовать HTML;
- не использовать Markdown;
- не повторять прошлые публикации;
- не начинаться с "Знаете ли вы";
- не начинаться с "Мало кто знает";
- не использовать чрезмерное количество эмодзи;
- не обращаться к читателю свысока.

Структура:
title — короткий сильный заголовок;
hook — 1–3 предложения, создающие интерес;
body — содержательная часть с конкретными наблюдениями,
примерами или действиями;
cta — короткий вопрос или приглашение подумать;
rubric — одна из доступных рубрик;
hashtags — 2–3 релевантных хэштега.

Текст должен быть пригоден для Telegram.

Верни только JSON.
""".strip()

    user_prompt = f"""
Тема:
{topic}

Сегодняшняя рубрика:
{theme}

История последних публикаций:
{history_context}

Создай новый Telegram-пост.
""".strip()

    result = gemini_request(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "text": user_prompt,
                    }
                ],
            }
        ],
        system_instruction=system_instruction,
        response_schema=POST_SCHEMA,
        temperature=0.75,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
    )

    if not result:
        return None

    data = extract_json(result["text"])

    if not data:
        logger.error("Gemini не вернул корректный JSON поста.")
        return None

    return sanitize_post(
        data=data,
        default_rubric=theme,
    )


# ============================================================
# POST SANITIZATION
# ============================================================

def sanitize_post(
    data: dict[str, Any],
    default_rubric: str,
) -> dict[str, Any]:
    title = shorten_text(
        clean_ai_text(data.get("title", "")),
        TITLE_MAX_LENGTH,
    )

    hook = shorten_text(
        clean_ai_text(data.get("hook", "")),
        HOOK_MAX_LENGTH,
    )

    body = clean_ai_text(data.get("body", ""))
    body = format_numbered_paragraphs(body)
    body = shorten_text(body, BODY_MAX_LENGTH)

    cta = shorten_text(
        clean_ai_text(data.get("cta", "")),
        CTA_MAX_LENGTH,
    )

    rubric = clean_ai_text(
        data.get("rubric", "")
    )

    if rubric not in WEEKDAY_THEMES.values():
        rubric = default_rubric

    hashtags = sanitize_hashtags(
        data.get("hashtags", [])
    )

    hashtags = ensure_hashtags(
        hashtags,
        rubric,
    )

    return {
        "title": title,
        "hook": hook,
        "body": body,
        "cta": cta,
        "rubric": rubric,
        "hashtags": hashtags,
    }


def validate_post(post: dict[str, Any]) -> bool:
    required = [
        "title",
        "hook",
        "body",
        "cta",
        "rubric",
        "hashtags",
    ]

    for field in required:
        if field not in post:
            logger.error("В посте отсутствует поле: %s", field)
            return False

    if not post["title"]:
        logger.error("Пустой title.")
        return False

    if not post["body"]:
        logger.error("Пустой body.")
        return False

    if len(post["title"]) > TITLE_MAX_LENGTH:
        return False

    if len(post["hook"]) > HOOK_MAX_LENGTH:
        return False

    if len(post["body"]) > BODY_MAX_LENGTH:
        return False

    if len(post["cta"]) > CTA_MAX_LENGTH:
        return False

    if not (
        MIN_HASHTAGS
        <= len(post["hashtags"])
        <= MAX_HASHTAGS
    ):
        logger.error("Неверное количество hashtags.")
        return False

    combined_text = " ".join(
        [
            post["title"],
            post["hook"],
            post["body"],
            post["cta"],
        ]
    )

    if contains_prompt_injection(combined_text):
        logger.error("Пост отклонён: обнаружен prompt injection.")
        return False

    return True


# ============================================================
# EDITOR / QUALITY CONTROL
# ============================================================

EDITOR_SCHEMA = {
    "type": "OBJECT",
    "properties": {
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
        "rubric": {
            "type": "STRING",
        },
        "hashtags": {
            "type": "ARRAY",
            "items": {
                "type": "STRING",
            },
            "minItems": MIN_HASHTAGS,
            "maxItems": MAX_HASHTAGS,
        },
        "quality_score": {
            "type": "INTEGER",
        },
        "editor_notes": {
            "type": "STRING",
        },
    },
    "required": [
        "title",
        "hook",
        "body",
        "cta",
        "rubric",
        "hashtags",
        "quality_score",
        "editor_notes",
    ],
}


def edit_post(
    post: dict[str, Any],
    topic: str,
    theme: str,
) -> Optional[dict[str, Any]]:
    system_instruction = """
Ты — строгий финальный редактор Telegram-поста.

Оцени пост по критериям:
1. Польза.
2. Конкретика.
3. Оригинальность.
4. Естественность языка.
5. Сила основной мысли.
6. Отсутствие клише.
7. Структура.
8. Пригодность для Telegram.
9. Отсутствие выдуманных фактов.
10. Отсутствие повторов и искусственного "AI"-стиля.

quality_score — целое число от 1 до 10.

Если пост можно улучшить, исправь его прямо в возвращаемом JSON.

Не добавляй факты, которых нет в исходном тексте.

Не используй HTML или Markdown.

Верни только JSON.
""".strip()

    original_json = json.dumps(
        post,
        ensure_ascii=False,
    )

    user_prompt = f"""
Тема:
{topic}

Рубрика:
{theme}

Исходный пост:
{original_json}

Проведи финальную редактуру.
""".strip()

    result = gemini_request(
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "text": user_prompt,
                    }
                ],
            }
        ],
        system_instruction=system_instruction,
        response_schema=EDITOR_SCHEMA,
        temperature=0.35,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
    )

    if not result:
        return None

    data = extract_json(result["text"])

    if not data:
        logger.error("Не удалось распознать JSON редактора.")
        return None

    try:
        quality_score = int(data.get("quality_score", 0))
    except (ValueError, TypeError):
        quality_score = 0

    sanitized = sanitize_post(
        data=data,
        default_rubric=theme,
    )

    sanitized["quality_score"] = quality_score
    sanitized["editor_notes"] = shorten_text(
        clean_ai_text(data.get("editor_notes", "")),
        500,
    )

    if quality_score < QUALITY_THRESHOLD:
        logger.warning(
            "Пост получил качество %d/%d — ниже порога %d.",
            quality_score,
            10,
            QUALITY_THRESHOLD,
        )
        return None

    if not validate_post(sanitized):
        logger.error("Пост не прошёл финальную валидацию.")
        return None

    return sanitized


# ============================================================
# FINAL POST CREATION
# ============================================================

def create_final_post(
    topic: str,
    theme: str,
    history: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    for attempt in range(1, MAX_EDITOR_ATTEMPTS + 1):
        logger.info(
            "Генерация поста по теме, попытка %d/%d.",
            attempt,
            MAX_EDITOR_ATTEMPTS,
        )

        generated = generate_post_for_topic(
            topic=topic,
            theme=theme,
            history=history,
        )

        if not generated:
            continue

        if not validate_post(generated):
            logger.warning("Сгенерированный пост не прошёл валидацию.")
            continue

        edited = edit_post(
            post=generated,
            topic=topic,
            theme=theme,
        )

        if edited:
            logger.info(
                "Пост прошёл редактуру. Quality: %d/10.",
                edited.get("quality_score", 0),
            )
            return edited

    logger.error(
        "Не удалось получить пост нужного качества."
    )

    return None


# ============================================================
# TELEGRAM HTML
# ============================================================

def telegram_escape(text: str) -> str:
    return html.escape(
        clean_ai_text(text),
        quote=False,
    )


def telegram_visible_length(text: str) -> int:
    # Remove HTML tags for approximate visible length.
    without_tags = re.sub(
        r"<[^>]+>",
        "",
        text,
    )

    return len(
        html.unescape(without_tags)
    )


def build_telegram_caption(
    post: dict[str, Any],
) -> str:
    title = telegram_escape(post["title"])
    hook = telegram_escape(post["hook"])
    body = telegram_escape(post["body"])
    cta = telegram_escape(post["cta"])

    hashtags = " ".join(
        telegram_escape(tag)
        for tag in post["hashtags"]
    )

    parts = [
        f"<b>{title}</b>",
    ]

    if hook:
        parts.append(hook)

    if body:
        parts.append(body)

    if cta:
        parts.append(cta)

    if hashtags:
        parts.append(hashtags)

    return "\n\n".join(parts).strip()


def fit_telegram_caption(
    post: dict[str, Any],
) -> str:
    working = {
        **post,
        "hashtags": list(post["hashtags"]),
    }

    caption = build_telegram_caption(working)

    if telegram_visible_length(caption) <= TELEGRAM_CAPTION_LIMIT:
        return caption

    logger.warning(
        "Caption слишком длинный: %d символов. Укорачиваем.",
        telegram_visible_length(caption),
    )

    # First remove the least important hashtags.
    while (
        len(working["hashtags"]) > MIN_HASHTAGS
        and telegram_visible_length(
            build_telegram_caption(working)
        ) > TELEGRAM_CAPTION_LIMIT
    ):
        working["hashtags"].pop()

    # Then progressively shorten the body.
    body_lengths = [
        560,
        500,
        450,
        400,
        350,
        300,
        250,
        200,
        150,
    ]

    for length in body_lengths:
        if (
            telegram_visible_length(
                build_telegram_caption(working)
            )
            <= TELEGRAM_CAPTION_LIMIT
        ):
            break

        working["body"] = shorten_text(
            post["body"],
            length,
        )

    # Then shorten hook.
    if (
        telegram_visible_length(
            build_telegram_caption(working)
        )
        > TELEGRAM_CAPTION_LIMIT
    ):
        working["hook"] = shorten_text(
            post["hook"],
            150,
        )

    # Then shorten CTA.
    if (
        telegram_visible_length(
            build_telegram_caption(working)
        )
        > TELEGRAM_CAPTION_LIMIT
    ):
        working["cta"] = shorten_text(
            post["cta"],
            100,
        )

    # Final safety reduction.
    if (
        telegram_visible_length(
            build_telegram_caption(working)
        )
        > TELEGRAM_CAPTION_LIMIT
    ):
        working["body"] = shorten_text(
            post["body"],
            100,
        )

    caption = build_telegram_caption(working)

    if telegram_visible_length(caption) > TELEGRAM_CAPTION_LIMIT:
        logger.error(
            "Не удалось уложить Telegram caption в лимит."
        )
        raise ValueError(
            "Telegram caption exceeds 1024 visible characters."
        )

    return caption


# ============================================================
# UNSPLASH
# ============================================================

def search_unsplash_image(
    query: str,
) -> Optional[str]:
    if not UNSPLASH_ACCESS_KEY:
        return None

    query = clean_ai_text(query)

    if not query:
        return None

    query = shorten_text(query, 120)

    url = "https://api.unsplash.com/search/photos"

    params = {
        "query": query,
        "per_page": 10,
        "orientation": "landscape",
    }

    headers = {
        "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
        "User-Agent": USER_AGENT,
    }

    for attempt in range(1, 3):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code in {
                429,
                500,
                502,
                503,
                504,
            }:
                logger.warning(
                    "Unsplash HTTP %s, попытка %d/2.",
                    response.status_code,
                    attempt,
                )

                if attempt < 2:
                    time.sleep(2)
                    continue

            if response.status_code != 200:
                logger.warning(
                    "Unsplash error %s: %s",
                    response.status_code,
                    response.text[:500],
                )
                return None

            data = response.json()

            results = data.get("results", [])

            if not results:
                logger.info(
                    "Unsplash не нашёл изображение для: %s",
                    query,
                )
                return None

            selected = random.choice(results)

            urls = selected.get("urls", {})

            image_url = (
                urls.get("regular")
                or urls.get("small")
            )

            if image_url:
                logger.info(
                    "Изображение Unsplash найдено."
                )

            return image_url

        except requests.RequestException as exc:
            logger.warning(
                "Ошибка Unsplash: %s",
                exc,
            )

            if attempt < 2:
                time.sleep(2)

        except (ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "Ошибка обработки Unsplash: %s",
                exc,
            )
            return None

    return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api_url(method: str) -> str:
    return (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )


def telegram_post_request(
    method: str,
    data: dict[str, Any],
) -> str:
    """
    Returns:
      success  - Telegram definitely accepted the request.
      failed   - Telegram definitely rejected the request.
      unknown  - network timeout/connection issue; request may have succeeded.
    """

    url = telegram_api_url(method)

    headers = {
        "User-Agent": USER_AGENT,
    }

    try:
        response = requests.post(
            url,
            data=data,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )

    except requests.Timeout:
        logger.error(
            "Telegram timeout для %s. "
            "Результат запроса неизвестен — повторно не отправляем.",
            method,
        )
        return "unknown"

    except requests.ConnectionError as exc:
        logger.error(
            "Telegram connection error: %s. "
            "Результат запроса неизвестен.",
            exc,
        )
        return "unknown"

    except requests.RequestException as exc:
        logger.error(
            "Telegram request error: %s",
            exc,
        )
        return "unknown"

    if response.status_code == 429:
        retry_after = 1

        try:
            payload = response.json()
            retry_after = int(
                payload.get("parameters", {}).get(
                    "retry_after",
                    1,
                )
            )
        except (ValueError, TypeError):
            pass

        logger.warning(
            "Telegram rate limit. Retry after %d sec.",
            retry_after,
        )

        time.sleep(
            min(max(retry_after, 1), 30)
        )

        try:
            retry_response = requests.post(
                url,
                data=data,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )

        except requests.Timeout:
            logger.error(
                "Telegram retry timeout. "
                "Результат неизвестен."
            )
            return "unknown"

        except requests.RequestException as exc:
            logger.error(
                "Telegram retry error: %s",
                exc,
            )
            return "unknown"

        response = retry_response

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code != 200:
        logger.error(
            "Telegram HTTP %s: %s",
            response.status_code,
            response.text[:1000],
        )
        return "failed"

    if not payload.get("ok"):
        logger.error(
            "Telegram API rejected request: %s",
            response.text[:1000],
        )
        return "failed"

    return "success"


def publish_photo(
    image_url: str,
    caption: str,
) -> str:
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }

    status = telegram_post_request(
        "sendPhoto",
        data,
    )

    if status == "success":
        logger.info("Пост с изображением опубликован.")
    elif status == "failed":
        logger.warning(
            "Telegram не принял изображение."
        )
    else:
        logger.error(
            "Статус публикации изображения неизвестен."
        )

    return status


def publish_text(
    caption: str,
) -> str:
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": caption,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }

    status = telegram_post_request(
        "sendMessage",
        data,
    )

    if status == "success":
        logger.info("Текстовый пост опубликован.")
    elif status == "failed":
        logger.error(
            "Telegram не принял текстовый пост."
        )
    else:
        logger.error(
            "Статус текстовой публикации неизвестен."
        )

    return status


# ============================================================
# HISTORY ENTRY
# ============================================================

def make_history_entry(
    topic: str,
    post: dict[str, Any],
) -> dict[str, Any]:
    summary_source = " ".join(
        [
            post.get("hook", ""),
            post.get("body", ""),
        ]
    )

    return {
        "published_at": utc_iso_now(),
        "topic": clean_ai_text(topic),
        "title": clean_ai_text(post.get("title", "")),
        "rubric": clean_ai_text(post.get("rubric", "")),
        "hashtags": sanitize_hashtags(
            post.get("hashtags", [])
        ),
        "summary": shorten_text(
            summary_source,
            350,
        ),
    }


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    validate_environment()

    logger.info("========================================")
    logger.info("Starting Telegram autopost")
    logger.info("Gemini model: %s", GEMINI_MODEL)
    logger.info("========================================")

    history = load_history()

    theme = get_today_theme()

    logger.info(
        "Today's theme: %s",
        theme,
    )

    rejected_topics: list[str] = []

    for post_attempt in range(1, MAX_POST_ATTEMPTS + 1):
        logger.info(
            "Post generation attempt %d/%d.",
            post_attempt,
            MAX_POST_ATTEMPTS,
        )

        topic = select_unique_topic(
            theme=theme,
            history=history,
            rejected_topics=rejected_topics,
        )

        if not topic:
            logger.error(
                "Не удалось выбрать уникальную тему."
            )
            continue

        post = create_final_post(
            topic=topic,
            theme=theme,
            history=history,
        )

        if not post:
            logger.warning(
                "Пост по теме не прошёл генерацию/редактуру: %s",
                topic,
            )

            rejected_topics.append(topic)
            continue

        # Final duplicate protection based on generated title.
        title_repeated, title_score, old_title = is_topic_repeated(
            post["title"],
            history,
        )

        if title_repeated:
            logger.warning(
                "Финальный title похож на историю: %.2f — %s",
                title_score,
                old_title,
            )

            rejected_topics.append(topic)
            continue

        try:
            caption = fit_telegram_caption(post)
        except ValueError as exc:
            logger.error(
                "Caption validation failed: %s",
                exc,
            )

            rejected_topics.append(topic)
            continue

        logger.info(
            "Final caption visible length: %d/%d",
            telegram_visible_length(caption),
            TELEGRAM_CAPTION_LIMIT,
        )

        # Search image only after text has successfully passed validation.
        image_query = (
            f"{post['rubric']} "
            f"{post['title']}"
        )

        image_url = search_unsplash_image(
            image_query,
        )

        published = False

        if image_url:
            photo_status = publish_photo(
                image_url=image_url,
                caption=caption,
            )

            if photo_status == "success":
                published = True

            elif photo_status == "unknown":
                logger.error(
                    "Публикация изображения имеет неизвестный статус. "
                    "Текстовую копию НЕ отправляем, чтобы избежать дубля."
                )
                sys.exit(2)

            else:
                logger.info(
                    "Переходим к текстовой публикации."
                )

        if not published:
            text_status = publish_text(caption)

            if text_status == "success":
                published = True

            elif text_status == "unknown":
                logger.error(
                    "Статус текстовой публикации неизвестен. "
                    "Повторно ничего не отправляем."
                )
                sys.exit(2)

        if not published:
            logger.error(
                "Публикация не удалась."
            )
            sys.exit(1)

        # Save history only after Telegram confirmed publication.
        history_entry = make_history_entry(
            topic=topic,
            post=post,
        )

        history.append(history_entry)

        if not save_history(history):
            logger.error(
                "Пост опубликован, но историю сохранить не удалось."
            )
            sys.exit(1)

        logger.info("========================================")
        logger.info("AUTPOST SUCCESS")
        logger.info("Topic: %s", topic)
        logger.info("Title: %s", post["title"])
        logger.info(
            "Quality: %s/10",
            post.get("quality_score", "?"),
        )
        logger.info("========================================")

        return

    logger.error(
        "После %d попыток публикация не создана.",
        MAX_POST_ATTEMPTS,
    )

    sys.exit(1)


if __name__ == "__main__":
    main()