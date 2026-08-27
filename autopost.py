import os
import sys
import json
import random
import base64
import logging
import urllib.parse
from datetime import datetime
from dotenv import load_dotenv
import requests

# =============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('autopost.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# =============================================================================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID', '').strip()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GEMINI_API_KEY]):
    logger.error("❌ Не все переменные заданы! Проверь .env файл:")
    logger.error("   TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GEMINI_API_KEY")
    sys.exit(1)

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'topics_history.json')
MEMORY_DEPTH = 4  # не повторять подтемы ближайшие ~4 недели

# АКТУАЛЬНЫЕ МОДЕЛИ ДЛЯ БЕСПЛАТНОГО ТАРИФА GEMINI (текст)
TEXT_MODELS = [
    "gemini-3.5-flash",       # Проверено — работает стабильно на free tier
    "gemini-1.5-flash",       # Старая, но надёжная fallback
    "gemini-3.6-flash",       # Новая, но может давать 503
    "gemini-3.1-pro-preview", # Для сложного текста
]

# =============================================================================
# ТЕМЫ ПО ДНЯМ НЕДЕЛИ
# =============================================================================
THEMES = {
    0: {
        "rubric": "Мотивация недели / фокус",
        "format_hint": "короткий заряжающий пост, задаёт тон неделе",
        "topics": [
            "Одна метрика, на которую стоит смотреть эту неделю",
            "Вредная привычка руководителя, от которой стоит отказаться на 7 дней",
            "Мини-вызов: одно управленческое действие, которое нужно сделать до пятницы",
            "Что я перестал делать как руководитель — и стало легче",
            "Как настроить команду на продуктивную неделю без давления",
        ]
    },
    1: {
        "rubric": "Инструмент / шаблон",
        "format_hint": "практический инструмент с конкретными шагами",
        "topics": [
            "Матрица приоритизации задач руководителя",
            "Шаблон one-to-one встречи с сотрудником",
            "Чек-лист делегирования без потери контроля",
            "Скрипт сложного разговора (критика, понижение, увольнение)",
            "Формула обратной связи (SBI/DESC)",
            "Таблица распределения ролей в команде",
        ]
    },
    2: {
        "rubric": "Короткое наблюдение / инсайт",
        "format_hint": "короткий пост-мысль, 3-5 предложений, без лонгрида",
        "topics": [
            "Фраза, которая выдаёт слабого руководителя",
            "Признак того, что команда вам не доверяет",
            "Разница между 'занят' и 'эффективен' у менеджера",
            "Один вопрос, который стоит задавать себе перед каждым решением",
            "Незаметная ошибка в делегировании",
        ]
    },
    3: {
        "rubric": "Опрос / вовлечение",
        "format_hint": "пост построен вокруг вопроса аудитории, провоцирует ответы в комментариях",
        "topics": [
            "Как вы принимаете решение, если команда не согласна?",
            "Что чаще выгорает — вы или ваша команда?",
            "Сколько встреч one-to-one вы проводите в месяц?",
            "Что сложнее — нанять или уволить?",
            "Какой стиль обратной связи вам ближе?",
        ]
    },
    4: {
        "rubric": "Разбор ситуации / вопрос подписчика",
        "format_hint": "разбор реальной или собирательной управленческой ситуации с выводом",
        "topics": [
            "Сотрудник постоянно опаздывает — что делать",
            "Как забрать проект у человека, который не справляется",
            "Коллега саботирует решения на встречах",
            "Новый руководитель не может добиться уважения команды",
            "Как сказать 'нет' вышестоящему руководству",
        ]
    },
    5: {
        "rubric": "Лёгкий формат",
        "format_hint": "развлекательно-полезный контент под выходной день",
        "topics": [
            "5 книг, изменивших подход к управлению",
            "Подборка привычек эффективных руководителей",
            "Забавный или провальный кейс из практики без назидания",
            "Тест: какой вы тип руководителя",
        ]
    },
    6: {
        "rubric": "Рефлексия недели",
        "format_hint": "мягкое подведение итогов + вопрос на подумать перед новой неделей",
        "topics": [
            "Что вы узнали о себе как о руководителе на этой неделе?",
            "Один урок из провала за неделю",
            "Вопрос-размышление без единственно верного ответа",
            "Благодарность команде + личный вывод",
        ]
    },
}

# =============================================================================
# РАБОТА С ИСТОРИЕЙ
# =============================================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Не удалось прочитать историю тем: {e}")
    return {}


def save_history(history):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ Не удалось сохранить историю тем: {e}")


def pick_topic(weekday: int) -> str:
    """Выбирает подтему для дня недели, избегая последних MEMORY_DEPTH повторов."""
    day_data = THEMES[weekday]
    pool = day_data["topics"]

    history = load_history()
    key = str(weekday)
    recent = history.get(key, [])

    candidates = [t for t in pool if t not in recent[-MEMORY_DEPTH:]]
    if not candidates:
        candidates = pool

    topic = random.choice(candidates)

    recent.append(topic)
    history[key] = recent[-MEMORY_DEPTH * 2:]
    save_history(history)

    return topic


def get_prompt_for_today():
    weekday = datetime.now().weekday()
    day_data = THEMES[weekday]
    topic = pick_topic(weekday)

    prompt = f"""Напиши короткий пост для Telegram-канала @soft_boss про лидерство и управление командой для руководителей среднего звена.

Рубрика дня: {day_data['rubric']}
Формат: {day_data['format_hint']}
Конкретная тема поста: {topic}

СТРОГИЕ ТРЕБОВАНИЯ К ДЛИНЕ:
- Общая длина текста ДОЛЖНА БЫТЬ СТРОГО МЕНЬШЕ 950 символов (включая пробелы и эмодзи), чтобы текст гарантированно поместился в подпись к картинке Telegram.
- 120-170 слов максимум.
- 2-3 эмодзи.
- 1-2 хэштега в конце.
- Короткий призыв к действию или вопрос аудитории в конце.
- HTML-теги: <b>жирный</b>, <i>курсив</i>.
- Только готовый текст, без пояснений.
"""
    return prompt, topic, day_data


def get_image_prompt(topic: str, rubric: str) -> str:
    """Короткий промпт для Pollinations.ai — чем короче, тем лучше."""
    short_topic = topic.split('—')[0].split('(')[0].strip()[:60]
    return f"Minimalist flat business illustration, {short_topic}, corporate blue and warm accent colors, clean composition, no text, no letters, no numbers, no words, flat 3D style, horizontal banner"


# =============================================================================
# GEMINI API — ГЕНЕРАЦИЯ ТЕКСТА
# =============================================================================
def call_gemini_text(model_name: str, prompt_text: str):
    """Запрашивает текст у Gemini. Возвращает текст или None."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 1500
        }
    }

    try:
        logger.info(f"⏳ Пробуем {model_name} для текста...")
        response = requests.post(url, json=payload, timeout=(10, 60))
        data = response.json()

        if response.status_code != 200:
            logger.warning(f"⚠️ {model_name} HTTP {response.status_code}: {json.dumps(data, ensure_ascii=False)[:400]}")
            return None

        candidates = data.get("candidates", [])
        if not candidates:
            logger.warning(f"⚠️ {model_name}: пустой candidates")
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            logger.warning(f"⚠️ {model_name}: пустой parts")
            return None

        text = parts[0].get("text", "").strip()
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")

        logger.info(f"📊 {model_name}: finishReason={finish_reason}, длина={len(text)}")

        if text and len(text) > 20:
            return text
        return None

    except Exception as e:
        logger.warning(f"⚠️ Ошибка {model_name}: {e}")
        return None


def generate_post():
    """Возвращает (текст_поста, тема, рубрика) или (None, None, None) при неудаче."""
    prompt_text, topic, day_data = get_prompt_for_today()

    for model in TEXT_MODELS:
        text = call_gemini_text(model, prompt_text)
        if text:
            # Страховка: если текст всё же длиннее лимита подписи Telegram (1024), обрезаем его аккуратно
            if len(text) > 1024:
                logger.warning(f"⚠️ Сгенерированный текст ({len(text)} симв.) превысил лимит подписи. Обрезаем до 1020 символов.")
                text = text[:1020].rsplit(' ', 1)[0] + "…"

            logger.info(f"✅ Текст сгенерирован через {model}: {len(text)} символов")
            return text, topic, day_data["rubric"]

    logger.error("❌ Все текстовые модели вернули пустой/короткий текст")
    return None, None, None


# =============================================================================
# POLLINATIONS.AI — ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ
# =============================================================================
def generate_image(topic: str, rubric: str):
    """Генерирует картинку через Pollinations.ai. Возвращает байты или None."""
    image_prompt = get_image_prompt(topic, rubric)
    
    encoded_prompt = urllib.parse.quote(image_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
    
    params = {
        "width": 1200,
        "height": 630,
        "seed": random.randint(1, 999999),
        "nologo": "true",
        "model": "flux",
    }
    
    try:
        logger.info(f"⏳ Генерируем картинку через Pollinations.ai...")
        response = requests.get(url, params=params, timeout=(15, 120))
        
        if response.status_code == 200 and len(response.content) > 1000:
            content_type = response.headers.get('Content-Type', '')
            if 'image' in content_type or response.content[:4] in [b'\xff\xd8\xff\xe0', b'\x89PNG', b'RIFF']:
                logger.info(f"✅ Картинка сгенерирована: {len(response.content)} байт, тип: {content_type}")
                return response.content
            else:
                logger.warning(f"⚠️ Ответ не является изображением. Content-Type: {content_type}")
                return None
        else:
            logger.warning(f"⚠️ Pollinations.ai HTTP {response.status_code}")
            return None
            
    except Exception as e:
        logger.warning(f"⚠️ Ошибка генерации картинки: {e}")
        return None


# =============================================================================
# TELEGRAM — ПУБЛИКАЦИЯ
# =============================================================================
def publish_to_telegram(text):
    """Публикует обычное текстовое сообщение (fallback, если картинка не сгенерировалась)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(url, json=payload, timeout=(5, 30))
        data = response.json()

        if response.status_code == 200 and data.get("ok"):
            logger.info(f"✅ Пост опубликован (текстом)! Message ID: {data['result']['message_id']}")
            return True
        else:
            logger.error(f"❌ Ошибка Telegram: {data}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return False


def publish_photo_to_telegram(image_bytes: bytes, text: str):
    """Публикует фото вместе с текстом в качестве единой подписи (caption)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    
    files = {"photo": ("cover.png", image_bytes, "image/png")}
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "caption": text,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, data=data, files=files, timeout=(10, 60))
        resp_json = response.json()

        if response.status_code == 200 and resp_json.get("ok"):
            logger.info(f"✅ Пост с картинкой и текстом опубликован единым сообщением! Message ID: {resp_json['result']['message_id']}")
            return True
        else:
            logger.error(f"❌ Ошибка отправки фото в Telegram: {resp_json}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки фото: {e}")
        return False


# =============================================================================
# ГЛАВНЫЙ ЦИКЛ
# =============================================================================
def main():
    logger.info("🚀 Запуск автопостинга...")

    post_text, topic, rubric = generate_post()
    if not post_text:
        logger.error("❌ Не удалось сгенерировать текст поста. Завершение.")
        sys.exit(1)

    image_bytes = generate_image(topic, rubric)

    if image_bytes:
        success = publish_photo_to_telegram(image_bytes, post_text)
    else:
        logger.warning("⚠️ Картинка не сгенерирована — публикуем только текст")
        success = publish_to_telegram(post_text)

    if not success:
        logger.error("❌ Не удалось опубликовать пост. Завершение.")
        sys.exit(1)

    logger.info("🎉 Готово! Пост успешно опубликован.")


if __name__ == "__main__":
    main()