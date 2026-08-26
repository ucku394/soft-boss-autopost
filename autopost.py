import os
import sys
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

THEMES = {
    0: "Понедельник: мотивация на неделю, планирование, продуктивность",
    1: "Вторник: обзор полезного софта или инструмента",
    2: "Среда: лайфхаки по автоматизации рутины",
    3: "Четверг: кейс или история успеха с цифрами",
    4: "Пятница: разбор ошибок, чего НЕ делать",
    5: "Суббота: лёгкий контент, мемы, опрос",
    6: "Воскресенье: итоги недели, анонс планов"
}

def get_prompt_for_today():
    weekday = datetime.now().weekday()
    theme = THEMES[weekday]
    return f"""Напиши пост для Telegram-канала @soft_boss.

Тема: {theme}

Требования:
- 200-350 слов
- 3-5 эмодзи
- 2-3 хэштега в конце
- Призыв к действию
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
