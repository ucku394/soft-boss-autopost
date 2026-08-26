import os
import sys
import json
import random
import logging
from datetime import datetime
from dotenv import load_dotenv
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('autopost.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID', '').strip()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GEMINI_API_KEY]):
    logger.error("❌ Не все переменные заданы!")
    sys.exit(1)

# Файл, где хранится история уже опубликованных подтем — чтобы не повторяться
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'topics_history.json')

# Сколько последних публикаций по каждому дню недели помнить,
# прежде чем подтема может быть использована повторно
MEMORY_DEPTH = 4  # т.е. подтема не повторится ближайшие ~4 недели

THEMES = {
    0: {
        "rubric": "Мотивация недели / фокус",
        "format_hint": "короткий заряжающий пост, задаёт тон неделе, 150-250 слов",
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
        "format_hint": "практический инструмент с конкретными шагами, который можно сразу применить",
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
        "format_hint": "развлекательно-полезный контент под выходной день, без давления",
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

    # Кандидаты — те, что не встречались среди последних MEMORY_DEPTH публикаций
    candidates = [t for t in pool if t not in recent[-MEMORY_DEPTH:]]
    if not candidates:
        # Пул исчерпан — сбрасываем память по этому дню и берём заново
        candidates = pool

    topic = random.choice(candidates)

    recent.append(topic)
    history[key] = recent[-MEMORY_DEPTH * 2:]  # не даём файлу расти бесконечно
    save_history(history)

    return topic


def get_prompt_for_today():
    weekday = datetime.now().weekday()
    day_data = THEMES[weekday]
    topic = pick_topic(weekday)

    return f"""Напиши пост для Telegram-канала @soft_boss про лидерство и управление командой для руководителей среднего звена.

Рубрика дня: {day_data['rubric']}
Формат: {day_data['format_hint']}
Конкретная тема поста: {topic}

Требования:
- 200-350 слов (для коротких форматов допустимо 100-150 слов)
- 3-5 эмодзи
- 2-3 хэштега в конце
- Призыв к действию или вопрос аудитории в конце
- HTML-теги: <b>жирный</b>, <i>курсив</i>
- Только готовый текст, без пояснений
"""


def call_gemini(model_name):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": [{"parts": [{"text": get_prompt_for_today()}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 4000
        }
    }

    try:
        logger.info(f"⏳ Пробуем {model_name}...")
        response = requests.post(url, json=payload, timeout=(10, 60))
        data = response.json()

        if response.status_code != 200:
            logger.warning(f"⚠️ {model_name} HTTP {response.status_code}")
            return None

        candidates = data.get("candidates", [])
        if not candidates:
            return None

        # ⬇️ БЕРЁМ ТОЛЬКО parts[0] — основной текст, игнорируем thoughtSignature в parts[1]
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return None

        text = parts[0].get("text", "")
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")

        logger.info(f"📊 {model_name}: finishReason={finish_reason}, длина={len(text)}")

        if text and len(text) > 30:
            return text
        return None

    except Exception as e:
        logger.warning(f"⚠️ Ошибка {model_name}: {e}")
        return None


def generate_post():
    models = ["gemini-3.6-flash", "gemini-3.5-flash"]

    for model in models:
        text = call_gemini(model)
        if text:
            logger.info(f"✅ Используем {model}: {len(text)} символов")
            return text

    logger.error("❌ Все модели вернули пустой/короткий текст")
    return None


def publish_to_telegram(text):
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
            logger.info(f"✅ Пост опубликован! Message ID: {data['result']['message_id']}")
            return True
        else:
            logger.error(f"❌ Ошибка Telegram: {data}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return False


def main():
    logger.info("🚀 Запуск...")

    post_text = generate_post()
    if not post_text:
        sys.exit(1)

    success = publish_to_telegram(post_text)
    if not success:
        sys.exit(1)

    logger.info("🎉 Готово!")


if __name__ == "__main__":
    main()
