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
# V8.7 — TELEGRAM CONTENT ENGINE
# 7 форматов контента + анти-повтор тем и углов подачи + failover + Telegram HTML
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

HISTORY_FILE = BASE_DIR / "topics_history.json"

PREFERRED_MODEL = os.getenv("GEMINI_MODEL", "").strip()

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

HISTORY_WEEKS = 12
MAX_CAPTION = 900
TELEGRAM_CAPTION_LIMIT = 1024
MAX_GENERATED_CHARS = 900
MAX_GENERATION_ATTEMPTS_PER_MODEL = 2
THINKING_LEVEL = "low"
QUALITY_THRESHOLD = 75

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("autopost-v8.7")


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
            "Как проверить понимание задачи без микроменеджмента",
            "Что делать, если сотрудник постоянно ждёт указаний",
            "Как задавать срок, чтобы задача не стала вечной",
            "Как отличить плохую постановку задачи от плохого исполнения",
            "Как давать сотруднику свободу и сохранять контроль результата",
            "Как завершать задачу: короткий разбор без поиска виноватых",
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
            "Почему усталый руководитель становится жёстче, чем нужно",
            "Как заметить, что раздражение влияет на решения",
            "Что делать, если после ошибки хочется сразу всё контролировать",
            "Как руководителю выдерживать неопределённость",
            "Почему постоянная доступность руководителя истощает команду",
            "Как восстановить рабочий ритм после тяжёлой недели",
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
            "Сотрудник согласился на задачу, но ничего не сделал",
            "Два отдела перекладывают ответственность друг на друга",
            "Сильный сотрудник требует особых условий",
            "Команда молчит на совещаниях: где искать причину",
            "Руководитель дал обратную связь, но сотрудник закрылся",
            "Сотрудник приносит проблему вместо вариантов решения",
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
            "Почему руководитель становится узким местом для команды",
            "Как сократить лишние согласования",
            "Когда инструкция помогает, а когда мешает",
            "Почему руководитель отвечает на вопросы, которые команда могла решить сама",
            "Как перестать превращать исключения в постоянные правила",
            "Что делать с задачами, которые никто не считает своими",
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
            "Фраза руководителя, после которой команда перестаёт предлагать идеи",
            "Маленькая привычка руководителя, которая повышает доверие",
            "Почему хорошее совещание иногда длится 15 минут",
            "Один признак команды, которой можно доверять",
            "Что руководителю стоит перестать проверять каждый день",
            "Как понять, что вы уже слишком много решаете за команду",
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
            "Какая договорённость с командой требует пересмотра",
            "Что на следующей неделе можно делегировать",
            "Какой разговор руководитель откладывает слишком долго",
            "Где команда стала сильнее за последний месяц",
            "Какую задачу стоит убрать, а не ускорять",
            "Что руководителю стоит проверить не в отчёте, а в разговоре",
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
# ФОРМАТЫ КОНТЕНТА V8.5
# Каждый день имеет свой редакционный формат, структуру и визуальный ритм.
# ============================================================

CONTENT_FORMATS = {
    0: "management_breakdown",
    1: "practical_tool",
    2: "psychology_story",
    3: "dialogue_case",
    4: "management_mistake",
    5: "mini_test",
    6: "weekly_reflection",
}

CONTENT_ANGLES = {
    "management_breakdown": [
        "через раннее распознавание проблемы",
        "через пошаговый алгоритм действий руководителя",
        "через типичную ошибку в этой ситуации",
        "через границы ответственности руководителя и сотрудника",
        "через один вопрос, который быстро проясняет ситуацию",
    ],
    "practical_tool": [
        "через готовый алгоритм, который можно применить сегодня",
        "через готовую фразу руководителя",
        "через короткий чек-лист перед действием",
        "через точку контроля без микроменеджмента",
        "через сравнение плохого и хорошего способа",
    ],
    "psychology_story": [
        "через внутреннюю реакцию руководителя",
        "через скрытую причину привычной реакции",
        "через последствия реакции для команды",
        "через момент, когда руководитель может остановить автоматическую реакцию",
        "через спокойный самоанализ после сложной ситуации",
    ],
    "dialogue_case": [
        "через конфликт ожиданий",
        "через неудачную первую реплику руководителя",
        "через перевод спора в решение",
        "через границы ответственности в разговоре",
        "через вопрос, который меняет ход разговора",
    ],
    "management_mistake": [
        "через ошибочную, но логичную реакцию руководителя",
        "через скрытую причину управленческой ошибки",
        "через последствия ошибки для самостоятельности команды",
        "через то, чем заменить привычную реакцию",
        "через один ранний сигнал, который помогает заметить ошибку",
    ],
    "mini_test": [
        "через самодиагностику руководителя",
        "через признаки микроменеджмента",
        "через качество делегирования",
        "через самостоятельность команды",
        "через управленческие привычки, которые незаметно забирают время",
    ],
    "weekly_reflection": [
        "через одну задачу, которую пора перестать держать на себе",
        "через разговор, который нельзя бесконечно откладывать",
        "через решение, которое оказалось важнее срочных задач",
        "через изменение самостоятельности команды",
        "через один управленческий вывод недели",
    ],
}


FORMAT_PROFILES = {
    "management_breakdown": {
        "name": "Разбор управленческой ситуации",
        "range": (500, 760),
        "emoji_range": (3, 5),
        "instruction": """
ФОРМАТ: РАЗБОР УПРАВЛЕНЧЕСКОЙ СИТУАЦИИ.
Структура:
🎯 Короткий заголовок.
Сразу опиши знакомую проблему руководителя в 1–2 предложениях.

🔎 Что происходит.
Объясни причину без теории и общих слов.

🛠 Что делать.
Дай ровно 3 конкретных действия. Каждый пункт — отдельная строка.

⚠️ Где легко ошибиться.
Одна короткая оговорка.

👉 Финальный вывод и один естественный вопрос читателю.
""",
        "cta": "естественный вопрос по теме",
        "image_queries": [
            "business leader meeting team",
            "executive leadership team",
            "manager talking employee",
        ],
        "hashtags": "#лидерство #руководитель #команда",
    },
    "practical_tool": {
        "name": "Практический инструмент",
        "range": (480, 780),
        "emoji_range": (3, 6),
        "instruction": """
ФОРМАТ: ПРАКТИЧЕСКИЙ ИНСТРУМЕНТ.
Дай инструмент, который руководитель может применить сегодня.

Структура:
🛠 Название инструмента.
Когда его применять — 1 короткое предложение.

Пошаговое применение:
1️⃣ ...
2️⃣ ...
3️⃣ ...
4️⃣ ... (если действительно нужно)

💬 Дай одну готовую фразу, которую руководитель может сказать сотруднику.

⚠️ Короткая оговорка: когда этот инструмент лучше не применять.
Финальная строка — практический вывод без обязательного вопроса.
""",
        "cta": "короткий практический вывод",
        "image_queries": [
            "business planning desk",
            "project planning meeting",
            "manager writing notes",
        ],
        "hashtags": "#управление #инструмент #руководитель",
    },
    "psychology_story": {
        "name": "Психологическая мини-история",
        "range": (480, 780),
        "emoji_range": (2, 4),
        "instruction": """
ФОРМАТ: ПСИХОЛОГИЧЕСКАЯ МИНИ-ИСТОРИЯ.
Начни с короткой узнаваемой сцены из жизни руководителя.

Не используй заголовки «Главная мысль» и «Что сделать».
Покажи:
— что сделал руководитель;
— что он на самом деле пытался решить;
— почему его реакция могла усилить проблему;
— что можно сделать иначе.

Финал — одна спокойная практическая мысль.
Можно закончить вопросом для самоанализа.
""",
        "cta": "вопрос для самоанализа или спокойный вывод",
        "image_queries": [
            "business executive thinking",
            "manager reflection office",
            "leadership stress office",
        ],
        "hashtags": "#психология #руководитель #лидерство",
    },
    "dialogue_case": {
        "name": "Диалог и разбор кейса",
        "range": (500, 800),
        "emoji_range": (2, 4),
        "instruction": """
ФОРМАТ: ДИАЛОГ И РАЗБОР КЕЙСА.
Построй начало как короткий реалистичный диалог руководителя и сотрудника.

Используй 2–4 реплики:
— Сотрудник: «...»
— Руководитель: «...»

Затем покажи:
❌ что в такой реакции руководителя не сработало;
✅ как можно ответить конструктивнее.

В конце объясни принцип в 2–3 коротких предложениях.
Не превращай пост в театральную сценку.
""",
        "cta": "короткий вопрос: как бы вы ответили?",
        "image_queries": [
            "manager employee conversation",
            "business conflict meeting",
            "team discussion office",
        ],
        "hashtags": "#кейс #управление #команда",
    },
    "management_mistake": {
        "name": "Ошибка руководителя",
        "range": (480, 760),
        "emoji_range": (3, 5),
        "instruction": """
ФОРМАТ: ОШИБКА РУКОВОДИТЕЛЯ.
Начни с фразы «Одна из частых ошибок руководителя...», но не обязательно дословно.

Покажи:
❌ какая ошибка;
🤔 почему она кажется логичной;
⚠️ к чему она приводит;
✅ что делать вместо неё.

Не обвиняй руководителя. Объясняй механизм проблемы.
Финал — одна короткая фраза, которую стоит запомнить.
""",
        "cta": "короткая фраза-вывод",
        "image_queries": [
            "business decision meeting",
            "executive decision office",
            "business problem solving",
        ],
        "hashtags": "#ошибки #руководитель #управление",
    },
    "mini_test": {
        "name": "Мини-тест",
        "range": (480, 760),
        "emoji_range": (4, 8),
        "instruction": """
ФОРМАТ: МИНИ-ТЕСТ.
Сделай пост интерактивным.

Начни с короткого заголовка и предложи читателю ответить на 3–5 вопросов.
Каждый вопрос должен быть отдельной строкой и иметь вариант «да/нет» или понятный выбор.

После вопросов дай простую расшифровку:
— если большинство ответов «да» — что это может означать;
— если большинство «нет» — на что обратить внимание.

Не ставь диагнозов и не используй псевдонаучные утверждения.
Финал — предложение написать результат или подумать над одним вопросом.
""",
        "cta": "предложение проверить себя или написать результат",
        "image_queries": [
            "modern office team",
            "business coffee meeting",
            "creative team office",
        ],
        "hashtags": "#мини-тест #руководитель #команда",
    },
    "weekly_reflection": {
        "name": "Недельная рефлексия",
        "range": (400, 650),
        "emoji_range": (1, 3),
        "instruction": """
ФОРМАТ: НЕДЕЛЬНАЯ РЕФЛЕКСИЯ.
Это спокойный воскресный пост.

Не используй нумерованный список и много подзаголовков.
Напиши короткую мысль о прошедшей неделе руководителя.
Задай 1 сильный вопрос для размышления.
Предложи одно небольшое действие на следующую неделю.

Тон: спокойный, взрослый, без мотивационных лозунгов.
Пост должен ощущаться как пауза, а не как инструкция.
""",
        "cta": "один вопрос для личной рефлексии",
        "image_queries": [
            "business planning week",
            "executive desk notebook",
            "leadership reflection",
        ],
        "hashtags": "#рефлексия #руководитель #лидерство",
    },
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


def history_persistence_diagnostic():
    """
    GitHub Actions каждый запуск получает чистое рабочее дерево.
    Локальный topics_history.json между запусками сам по себе не сохраняется.
    """
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        if HISTORY_FILE.exists():
            log.info("🧠 История найдена: %s", HISTORY_FILE)
        else:
            log.warning(
                "⚠️ GitHub Actions: %s отсутствует в начале run. "
                "История анти-повтора между запусками пока не сохраняется.",
                HISTORY_FILE.name,
            )


def normalize_topic(text):
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    # Убираем не только служебные слова, но и слова-контексты,
    # которые встречаются почти в каждом посте. Иначе темы про
    # «руководителя / сотрудника / команду» получают искусственно
    # высокое сходство.
    stop = {
        "как", "что", "это", "для", "при", "если", "или", "из",
        "не", "и", "в", "на", "с", "по", "к", "у", "о", "а", "то",
        "же", "бы", "ли", "до", "за", "от", "под", "над",
        "руководитель", "руководителя", "руководителю", "руководителем",
        "сотрудник", "сотрудника", "сотруднику", "сотрудником",
        "команда", "команды", "команде", "командой",
        "работа", "работы", "работу", "рабочий", "рабочая",
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


def topic_recency_penalty(topic, history, rubric):
    """
    Дополнительная защита от повторения одного управленческого угла.

    Сравниваем новую тему не только с заголовком старой публикации,
    но и с сохранённым fingerprint текста. Это помогает ловить случаи,
    когда заголовки разные, а содержание фактически вращается вокруг
    одной и той же мысли.
    """
    best_topic_sim = 0
    best_body_sim = 0
    recent_rubric_items = 0

    for item in history:
        if item.get("rubric", "") != rubric:
            continue

        recent_rubric_items += 1

        old_topic = item.get("topic", "")
        old_body = item.get("fingerprint", "")

        best_topic_sim = max(
            best_topic_sim,
            similarity_percent(topic, old_topic),
        )

        if old_body:
            best_body_sim = max(
                best_body_sim,
                similarity_percent(topic, old_body),
            )

    # Заголовок важнее fingerprint: fingerprint может содержать
    # много общих слов. Но body-сходство помогает ловить близкий угол.
    weighted_similarity = round(
        best_topic_sim * 0.70 + best_body_sim * 0.30
    )

    return (
        best_topic_sim,
        best_body_sim,
        weighted_similarity,
        recent_rubric_items,
    )


def choose_topic(candidates, rubric):
    history = recent_history()

    log.info(
        "🧠 История за последние %s недель: %s публикаций",
        HISTORY_WEEKS,
        len(history),
    )

    rubric_counts = {}
    for item in history:
        r = item.get("rubric", "")
        rubric_counts[r] = rubric_counts.get(r, 0) + 1

    scored = []

    for topic in candidates:
        (
            topic_sim,
            body_sim,
            weighted_sim,
            same_rubric_count,
        ) = topic_recency_penalty(
            topic,
            history,
            rubric,
        )

        # Баланс рубрик остаётся мягким: нельзя отказываться от
        # понедельничной рубрики только потому, что она выходит каждую неделю.
        rubric_load = rubric_counts.get(rubric, 0)
        balance_penalty = min(rubric_load * 2, 10)

        # Повтор одного смыслового угла должен стоить дороже,
        # чем простое совпадение слова в заголовке.
        if weighted_sim >= 65:
            repeat_penalty = 120
        elif weighted_sim >= 50:
            repeat_penalty = 65
        elif weighted_sim >= 38:
            repeat_penalty = 25
        else:
            repeat_penalty = 0

        # Если тема почти совпадает с недавней — практически блокируем её.
        exact_penalty = 80 if topic_sim >= 75 else 0

        score = (
            weighted_sim
            + repeat_penalty
            + exact_penalty
            + balance_penalty
        )

        scored.append({
            "score": score,
            "topic_sim": topic_sim,
            "body_sim": body_sim,
            "weighted_sim": weighted_sim,
            "topic": topic,
        })

        log.info(
            "🔎 Тема: %s | заголовок %s%% | текст %s%% | "
            "взвешенное %s%% | рубрика %s | score %s",
            topic,
            topic_sim,
            body_sim,
            weighted_sim,
            same_rubric_count,
            score,
        )

    # Сначала стараемся брать темы без заметного смыслового пересечения.
    acceptable = [
        item for item in scored
        if item["weighted_sim"] < 38
        and item["topic_sim"] < 75
    ]

    pool = acceptable if acceptable else scored

    # Не всегда берём математически первый минимум.
    # Небольшая случайность среди лучших тем предотвращает ситуацию,
    # когда один и тот же кандидат регулярно выигрывает при равных score.
    pool = sorted(
        pool,
        key=lambda item: (
            item["score"],
            item["weighted_sim"],
            item["topic_sim"],
        ),
    )

    shortlist = pool[:min(3, len(pool))]
    selected = random.choice(shortlist)

    log.info(
        "✅ Выбрана тема: %s | заголовок %s%% | текст %s%% | "
        "взвешенное %s%% | score %s",
        selected["topic"],
        selected["topic_sim"],
        selected["body_sim"],
        selected["weighted_sim"],
        selected["score"],
    )

    return selected["topic"]


def choose_content_angle(topic, content_format):
    """
    Выбирает угол подачи отдельно от темы.
    Одна тема может возвращаться через месяцы, но ближайшие публикации
    не должны повторять один и тот же способ её раскрытия.
    """
    angles = CONTENT_ANGLES.get(content_format, [])
    if not angles:
        return ""

    history = recent_history()
    scored = []

    for angle in angles:
        penalty = 0

        for item in history:
            old_angle = item.get("content_angle", "")
            if not old_angle or old_angle != angle:
                continue

            topic_sim = similarity_percent(
                topic,
                item.get("topic", ""),
            )

            if topic_sim >= 55:
                penalty += 100
            elif topic_sim >= 35:
                penalty += 45
            else:
                penalty += 12

        scored.append((penalty, angle))

    best_penalty = min(item[0] for item in scored)
    best = [item for item in scored if item[0] == best_penalty]
    selected = random.choice(best)[1]

    log.info(
        "🎯 Угол подачи: %s | формат=%s | penalty=%s",
        selected,
        content_format,
        best_penalty,
    )

    return selected


def record_success(
    topic,
    rubric,
    model,
    text,
    content_format,
    style_variant,
    content_angle,
):
    items = load_history()

    items.append({
        "topic": topic,
        "rubric": rubric,
        "content_format": content_format,
        "style_variant": style_variant,
        "content_angle": content_angle,
        "model": model,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": normalize_topic(text)[:500],
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
    """
    Подготавливает caption без обрезания готовой мысли.
    V8.1 не режет пост посередине: слишком длинный результат должен
    быть отклонён генератором или заменён fallback.
    """
    text = sanitize_telegram_html(text).strip()

    if len(text) <= max_len:
        return text

    compact = re.sub(r"[ \t]+", " ", text)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()

    if len(compact) <= max_len:
        log.warning(
            "⚠️ Caption уплотнён: %s → %s символов",
            len(text), len(compact),
        )
        return compact

    log.error(
        "❌ Caption слишком длинный: %s символов — не обрезаем мысль",
        len(compact),
    )
    return ""

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

def build_prompt(topic, rubric, content_format, style_variant=0, content_angle="", repair=False):
    profile = FORMAT_PROFILES[content_format]
    min_chars, max_chars = profile["range"]
    emoji_min, emoji_max = profile["emoji_range"]

    style_variants = [
        "Начни максимально прямо: первая строка должна сразу зацепить знакомой руководителю ситуацией.",
        "Начни с короткого наблюдения, которое руководитель легко узнает по своей работе.",
        "Начни с неожиданного, но практичного вопроса или противопоставления.",
    ]
    opening_style = style_variants[style_variant % len(style_variants)]

    repair_text = """
Это повторная попытка. Исправь типичные проблемы:
— не обрывай текст;
— строго соблюдай выбранный формат;
— уложись в допустимый объём;
— не повторяй одну мысль;
— убери лишние вводные фразы;
— сохрани законченный финал и хэштеги.
""" if repair else ""

    return f"""
Ты — главный редактор Telegram-канала «Лидерство без выгорания».
Рубрика: {rubric}
Тема: {topic}
Редакционный формат: {profile["name"]}
Угол подачи: {content_angle}

{profile["instruction"]}

ОБЩИЙ СТИЛЬ:
- Пиши как опытный руководитель, который спокойно объясняет тему коллеге.
- Простые слова, короткие предложения, минимум теории.
- Один абзац — одна мысль.
- Текст должен легко читаться с телефона.
- Не используй канцелярит, мотивационные клише и искусственный «бизнес-язык».
- Не пиши: «В современном мире», «ключевым фактором является»,
  «данный подход позволяет», «синергия», «экосистема», «драйвер»,
  «трансформация», «декомпозиция», «стейкхолдеры».
- Не выдумывай факты, цифры, исследования, цитаты или ссылки.
- Не морализируй и не обвиняй сотрудников или руководителей.
- {opening_style}

ВИЗУАЛЬНЫЙ РИТМ:
- Используй {emoji_min}–{emoji_max} уместных эмодзи.
- Не ставь эмодзи в каждой строке.
- Используй пустые строки между смысловыми блоками.
- Не делай длинных абзацев.
- Не используй Markdown.
- Разрешён только Telegram HTML: <b>...</b> и <i>...</i>.
- Не используй HTML-теги для оформления списков.
- В конце обязательно добавь именно этот блок хэштегов:
{profile["hashtags"]}

ОБЪЁМ:
- Целевой диапазон: {min_chars}–{max_chars} символов.
- Абсолютный максимум: {MAX_CAPTION} символов.

ФИНАЛ:
- Заверши мысль до блока хэштегов.
- Тип финала: {profile["cta"]}.
- Не добавляй подпись автора, пояснение редактора или служебный текст.

{repair_text}

Верни только готовый пост.
"""

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
            # Gemini 3.x тратит maxOutputTokens также на thinking tokens.
            # Для короткого Telegram-поста low существенно снижает риск MAX_TOKENS.
            "maxOutputTokens": 1600,
            "thinkingConfig": {
                "thinkingLevel": THINKING_LEVEL,
            },
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


def split_hashtags(text):
    """Отделяет финальный блок хэштегов от содержательного текста."""
    clean = sanitize_telegram_html(text)
    plain = strip_html(clean).strip()
    lines = plain.splitlines()

    while lines and not lines[-1].strip():
        lines.pop()

    hashtag_lines = []
    while lines:
        line = lines[-1].strip()
        if re.fullmatch(r"(?:#[\wА-Яа-яЁё0-9_-]+(?:\s+|$))+", line):
            hashtag_lines.insert(0, line)
            lines.pop()
        else:
            break

    return "\n".join(lines).strip(), "\n".join(hashtag_lines).strip()


def last_content_line(text):
    body, _ = split_hashtags(text)
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    return lines[-1] if lines else ""


def looks_like_complete_post(text, finish_reason="", content_format="management_breakdown"):
    """Проверяет завершённость поста с учётом длины конкретного формата."""
    if not text:
        return False

    clean = sanitize_telegram_html(text)
    plain = strip_html(clean).strip()
    min_chars, max_chars = FORMAT_PROFILES[content_format]["range"]

    if finish_reason.upper() in {"MAX_TOKENS", "LENGTH"}:
        return False

    if len(plain) < max(350, min_chars - 50) or len(plain) > MAX_CAPTION:
        return False

    body, hashtags = split_hashtags(clean)

    if not hashtags:
        return False

    content_last = last_content_line(clean)
    if not content_last:
        return False

    if content_last.endswith(("-", "—", ",", ":", ";", "…")):
        return False

    if not re.search(r"[.!?)]$", content_last):
        return False

    # Не принимаем пост, если он явно вышел за целевой максимум формата.
    if len(plain) > max_chars + 100:
        return False

    return True

def get_finish_reason(data):
    try:
        return str(data["candidates"][0].get("finishReason", ""))
    except (KeyError, IndexError, TypeError):
        return ""


def emoji_count(text):
    # Достаточно широкий диапазон Unicode-эмодзи для контроля оформления.
    return len(re.findall(
        r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF]",
        strip_html(text),
    ))


def formatting_score(text):
    clean = sanitize_telegram_html(text)
    body, hashtags = split_hashtags(clean)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    emojis = emoji_count(body)
    score = 0

    if 4 <= emojis <= 6:
        score += 8
    elif 2 <= emojis <= 8:
        score += 5

    if len(paragraphs) >= 4:
        score += 6
    elif len(paragraphs) >= 3:
        score += 4

    # Визуальные маркеры разделов.
    if re.search(r"(?:^|\n)(?:🎯|💡|🛠|⚠️|👉)\s*", body):
        score += 6

    # Нумерованный список — хороший признак практического формата.
    if re.search(r"(?:^|\n)1️⃣?\s+", body):
        score += 3

    return min(score, 20)


def simplicity_score(text):
    """Оценивает читаемость: короткие предложения и отсутствие сложного жаргона."""
    plain = strip_html(text)
    sentences = [x.strip() for x in re.split(r'[.!?]+', plain) if x.strip()]
    if not sentences:
        return 0

    lengths = [len(re.findall(r"\b[А-Яа-яЁёA-Za-z0-9-]+\b", s)) for s in sentences]
    avg = sum(lengths) / len(lengths)
    long_count = sum(1 for n in lengths if n > 18)

    score = 20
    if avg <= 14:
        score += 10
    elif avg <= 18:
        score += 6
    elif avg <= 22:
        score += 2

    score -= min(10, long_count * 3)

    jargon = (
        "синерг", "экосистем", "драйвер", "компетенц",
        "эффективизац", "имплементац", "парадигм",
        "стейкхолдер", "декомпозиц", "трансформац",
        "канцелярит", "проактив", "кросс-функцион"
    )
    hits = sum(plain.lower().count(word) for word in jargon)
    score -= min(10, hits * 3)
    return max(0, min(30, score))


def format_quality_score(text, content_format):
    clean = sanitize_telegram_html(text)
    body, _ = split_hashtags(clean)
    low = body.lower()
    score = 0
    reasons = []

    if content_format == "management_breakdown":
        if re.search(r"\n(?:🛠|🔎|⚠️|👉)", body):
            score += 5
        if len(re.findall(r"(?m)^\s*\d+[️⃣.]?\s+", body)) >= 3:
            score += 8
        else:
            reasons.append("разбор без трёх конкретных действий")

    elif content_format == "practical_tool":
        numbered = len(re.findall(r"(?m)^\s*\d+[️⃣.]?\s+", body))
        if numbered >= 3:
            score += 10
        else:
            reasons.append("инструмент должен содержать пошаговое применение")
        if "«" in body or '"' in body:
            score += 5

    elif content_format == "psychology_story":
        if any(x in low for x in ["он ", "она ", "руководитель", "ситуац"]):
            score += 5
        if "почему" in low or "на самом деле" in low:
            score += 5
        if not re.search(r"(?m)^\s*(?:🛠|💡)\s*<b>", body):
            score += 3

    elif content_format == "dialogue_case":
        dialogue_lines = len(re.findall(r"(?m)^\s*[—-]\s*(?:сотрудник|руководитель)\s*:", body, re.I))
        if dialogue_lines >= 2:
            score += 10
        else:
            reasons.append("нет реалистичного диалога")
        if "❌" in body and "✅" in body:
            score += 5

    elif content_format == "management_mistake":
        if "❌" in body:
            score += 4
        if "🤔" in body:
            score += 4
        if "✅" in body:
            score += 4
        if not any(x in low for x in ["ошиб", "вместо", "приводит"]):
            reasons.append("слабо раскрыта сама ошибка")

    elif content_format == "mini_test":
        questions = len(re.findall(r"(?m)^\s*(?:\d+[.)️⃣]?|❓)\s+", body))
        if questions >= 3:
            score += 10
        else:
            reasons.append("мини-тест должен содержать минимум 3 вопроса")
        if "если" in low and ("да" in low or "нет" in low):
            score += 5

    elif content_format == "weekly_reflection":
        numbered = len(re.findall(r"(?m)^\s*\d+[️⃣.]?\s+", body))
        if numbered == 0:
            score += 6
        else:
            reasons.append("рефлексия не должна превращаться в список")
        if body.count("?") >= 1:
            score += 6

    return min(score, 18), reasons


def score_post(text, topic, content_format):
    """Локальная оценка качества с учётом конкретного формата."""
    clean = sanitize_telegram_html(text)
    plain = strip_html(clean).strip()
    body, hashtags = split_hashtags(clean)
    profile = FORMAT_PROFILES[content_format]
    min_chars, max_chars = profile["range"]

    score = 0
    reasons = []

    visual = formatting_score(clean)
    score += min(20, visual)

    simplicity = simplicity_score(body)
    score += round(simplicity * 0.35)

    length = len(plain)
    if min_chars <= length <= max_chars:
        score += 18
    elif min_chars - 50 <= length <= max_chars + 60:
        score += 11
    else:
        reasons.append("неоптимальная длина")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if 3 <= len(paragraphs) <= 7:
        score += 10
    elif len(paragraphs) >= 2:
        score += 6
    else:
        reasons.append("мало смысловых блоков")

    if hashtags:
        score += 8
    else:
        reasons.append("нет хэштегов")

    content_last = last_content_line(clean)
    if re.search(r"[.!?)]$", content_last):
        score += 8
    else:
        reasons.append("нет нормального завершения")

    if any(x in body.lower() for x in ["руководитель", "сотрудник", "команда", "задач"]):
        score += 7
    else:
        reasons.append("слабая связь с управленческой темой")

    topic_tokens = tokens(topic)
    text_tokens = tokens(body)
    if topic_tokens and topic_tokens & text_tokens:
        score += 5
    else:
        reasons.append("тема слабо отражена в тексте")

    format_score, format_reasons = format_quality_score(clean, content_format)
    score += format_score
    reasons.extend(format_reasons)

    return min(score, 100), reasons


def generate_with_failover(topic, rubric, content_format, style_variant=0, content_angle=""):
    profile = FORMAT_PROFILES[content_format]

    for model in GEMINI_MODELS:
        log.info("🤖 Gemini генерация: %s | формат=%s", model, profile["name"])

        for attempt in range(1, MAX_GENERATION_ATTEMPTS_PER_MODEL + 1):
            repair = attempt > 1
            prompt = build_prompt(
                topic,
                rubric,
                content_format,
                style_variant=style_variant,
                content_angle=content_angle,
                repair=repair,
            )

            try:
                response = gemini_request(model, prompt)

                if response.status_code == 200:
                    data = response.json()
                    text = extract_gemini_text(data)
                    finish_reason = get_finish_reason(data)

                    log.info(
                        "🔍 Gemini %s | finishReason=%s | chars=%s",
                        model,
                        finish_reason or "NONE",
                        len(strip_html(sanitize_telegram_html(text))) if text else 0,
                    )

                    if text and looks_like_complete_post(
                        text,
                        finish_reason,
                        content_format,
                    ):
                        quality, reasons = score_post(
                            text,
                            topic,
                            content_format,
                        )

                        log.info(
                            "🎨 Формат: %s | эмодзи=%s",
                            profile["name"],
                            emoji_count(text),
                        )
                        log.info(
                            "📊 Качество поста: %s/100%s",
                            quality,
                            f" | {', '.join(reasons)}" if reasons else "",
                        )

                        if quality >= QUALITY_THRESHOLD:
                            log.info(
                                "✅ Gemini принят: %s | попытка %s | качество %s/100",
                                model,
                                attempt,
                                quality,
                            )
                            return text, model

                        log.warning(
                            "⚠️ Пост отклонён по качеству: %s/100 | порог %s",
                            quality,
                            QUALITY_THRESHOLD,
                        )
                    else:
                        log.warning(
                            "⚠️ Ответ отклонён | %s | finishReason=%s | попытка %s/%s",
                            model,
                            finish_reason or "NONE",
                            attempt,
                            MAX_GENERATION_ATTEMPTS_PER_MODEL,
                        )

                    if attempt < MAX_GENERATION_ATTEMPTS_PER_MODEL:
                        time.sleep(1.5 + random.uniform(0.3, 1.0))
                    continue

                if response.status_code in RETRYABLE_HTTP:
                    log.warning(
                        "⚠️ Gemini HTTP %s | %s | попытка %s/%s",
                        response.status_code,
                        model,
                        attempt,
                        MAX_GENERATION_ATTEMPTS_PER_MODEL,
                    )
                    if attempt < MAX_GENERATION_ATTEMPTS_PER_MODEL:
                        delay = 2 + (2 ** (attempt - 1)) + random.uniform(0.5, 1.5)
                        time.sleep(delay)
                    continue

                log.error(
                    "❌ Gemini HTTP %s | %s: %s",
                    response.status_code,
                    model,
                    response.text[:500],
                )
                break

            except requests.RequestException as exc:
                log.warning(
                    "⚠️ Сетевая ошибка Gemini | %s | попытка %s/%s: %s",
                    model,
                    attempt,
                    MAX_GENERATION_ATTEMPTS_PER_MODEL,
                    exc,
                )
                if attempt < MAX_GENERATION_ATTEMPTS_PER_MODEL:
                    time.sleep(2 + random.uniform(0.5, 1.5))

        log.warning("➡️ Переключаемся с %s на следующую модель", model)

    return None, None

# ============================================================
# ЛОКАЛЬНЫЙ FALLBACK
# ============================================================

def local_fallback(topic, content_format):
    if content_format == "practical_tool":
        return (
            f"🛠 <b>{topic}</b>\n\n"
            "Если задача регулярно возвращается к руководителю, проверьте не сотрудника, а саму постановку.\n\n"
            "1️⃣ Назовите конкретный результат.\n"
            "2️⃣ Зафиксируйте срок.\n"
            "3️⃣ Определите границы самостоятельности.\n"
            "4️⃣ Договоритесь о точке контроля.\n\n"
            "💬 Полезная фраза: «Как ты понял результат и что сделаешь первым?»\n\n"
            "👉 Хорошая постановка задачи часто убирает лишний контроль.\n\n"
            "#управление #инструмент #руководитель"
        )

    if content_format == "mini_test":
        return (
            "🧪 <b>Мини-тест для руководителя</b>\n\n"
            "❓ Команда часто ждёт вашего решения?\n"
            "❓ Вы проверяете то, что уже поручили?\n"
            "❓ Сотрудники редко предлагают свой вариант?\n\n"
            "Если на два вопроса ответ «да», стоит посмотреть, где вы забираете у команды самостоятельность.\n\n"
            "👉 Выберите один пункт и попробуйте изменить его на следующей неделе.\n\n"
            "#мини-тест #руководитель #команда"
        )

    if content_format == "weekly_reflection":
        return (
            f"☕ <b>{topic}</b>\n\n"
            "Неделя редко показывает качество управления только в отчётах. Иногда важнее посмотреть на разговоры, решения и то, что вы продолжаете делать сами.\n\n"
            "Остановитесь на минуту и спросите себя: что из этого действительно нужно было решать лично вам?\n\n"
            "👉 Одну такую задачу можно попробовать передать команде уже на следующей неделе.\n\n"
            "#рефлексия #руководитель #лидерство"
        )

    if content_format == "dialogue_case":
        return (
            f"🎭 <b>{topic}</b>\n\n"
            "— Сотрудник: «Я не успеваю».\n"
            "— Руководитель: «Ты должен был предупредить раньше».\n\n"
            "❌ Проблема здесь не только в сроке. Разговор сразу ушёл в поиск виноватого.\n\n"
            "✅ Лучше спросить: «Что сейчас мешает закончить задачу и какой вариант ты предлагаешь?»\n\n"
            "👉 Важен не только контроль результата, но и качество следующего действия.\n\n"
            "#кейс #управление #команда"
        )

    if content_format == "management_mistake":
        return (
            f"⚠️ <b>{topic}</b>\n\n"
            "❌ Руководитель пытается решить проблему усилением контроля.\n\n"
            "🤔 Это кажется логичным: если что-то пошло не так, нужно чаще проверять.\n\n"
            "Но постоянные проверки быстро забирают самостоятельность.\n\n"
            "✅ Сначала уточните результат, ответственность и точку контроля.\n\n"
            "👉 Контроль должен закрывать риск, а не заменять доверие.\n\n"
            "#ошибки #руководитель #управление"
        )

    if content_format == "psychology_story":
        return (
            f"🧠 <b>{topic}</b>\n\n"
            "После ошибки сотрудника руководителю захотелось проверить всё самому. Это знакомая реакция: хочется быстро вернуть ощущение контроля.\n\n"
            "Но причина раздражения не всегда в работе сотрудника. Иногда руководитель просто пытается снизить собственную неопределённость.\n\n"
            "👉 Перед новой проверкой спросите себя: какой риск я действительно сейчас контролирую?\n\n"
            "#психология #руководитель #лидерство"
        )

    return (
        f"🎯 <b>{topic}</b>\n\n"
        "Управленческая проблема редко решается одним жёстким распоряжением. Сначала важно понять, где именно возник разрыв.\n\n"
        "🔎 Что не совпало: результат, ответственность, срок или ожидания?\n\n"
        "🛠 Назовите проблему прямо, договоритесь о следующем действии и зафиксируйте точку контроля.\n\n"
        "👉 Чем точнее договорённость, тем меньше лишнего контроля.\n\n"
        "#лидерство #руководитель #команда"
    )

# ============================================================
# UNSPLASH
# ============================================================

def get_unsplash_photo(rubric, content_format):
    if not UNSPLASH_ACCESS_KEY:
        log.warning("⚠️ UNSPLASH_ACCESS_KEY не задан")
        return None

    profile = FORMAT_PROFILES.get(content_format, {})
    queries = profile.get("image_queries") or IMAGE_QUERIES.get(
        rubric,
        ["leadership business", "business team"],
    )

    query = random.choice(queries)

    url = "https://api.unsplash.com/photos/random"
    params = {
        "query": query,
        "orientation": "landscape",
        "content_filter": "high",
    }
    headers = {
        "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
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
        image_url = data.get("urls", {}).get("regular")

        if not image_url:
            return None

        image = requests.get(
            image_url,
            timeout=UNSPLASH_TIMEOUT,
        )

        if image.status_code != 200:
            return None

        photo_id = data.get("id", "")

        log.info(
            "🖼️ Unsplash: %s | photo_id=%s | формат=%s",
            query,
            photo_id or "unknown",
            FORMAT_PROFILES.get(content_format, {}).get("name", content_format),
        )

        return {
            "bytes": image.content,
            "photo_url": data.get("links", {}).get("html", ""),
            "author": data.get("user", {}).get("name", "Unsplash"),
            "query": query,
            "photo_id": photo_id,
        }

    except requests.RequestException as exc:
        log.warning("⚠️ Ошибка Unsplash: %s", exc)
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


def persist_history_to_git():
    """
    История записывается в topics_history.json.
    Commit/push выполняет GitHub Actions после успешной публикации.
    Это исключает двойной commit/push из одного запуска.
    """
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        log.info(
            "💾 topics_history.json обновлён — GitHub Actions сохранит его после публикации"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    print("🚀 TELEGRAM CONTENT ENGINE V8.7")
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

    history_persistence_diagnostic()

    now = datetime.now()
    weekday = now.weekday()

    day_data = THEMES[weekday]
    rubric = day_data["rubric"]

    content_format = CONTENT_FORMATS[weekday]
    profile = FORMAT_PROFILES[content_format]

    # Меняем не смысл дня, а подачу внутри него.
    # Номер недели слегка меняет открытие, поэтому одинаковый понедельник
    # не выглядит визуально идентичным через неделю.
    week_number = now.isocalendar().week
    style_variant = week_number % 3

    log.info(
        "📅 День недели: %s | рубрика: %s | формат: %s | вариант: %s",
        weekday,
        rubric,
        profile["name"],
        style_variant + 1,
    )

    base_topic = random.choice(day_data["topics"])

    log.info("🎯 Базовая тема дня: %s", base_topic)
    log.info("🗂️ Тем дня доступно: %s", len(day_data["topics"]))

    topic = choose_topic(
        [base_topic] + [
            x for x in day_data["topics"] if x != base_topic
        ],
        rubric,
    )

    content_angle = choose_content_angle(
        topic,
        content_format,
    )

    generated, model = generate_with_failover(
        topic,
        rubric,
        content_format,
        style_variant=style_variant,
        content_angle=content_angle,
    )

    if generated:
        caption = sanitize_telegram_html(generated)
        fitted = fit_caption(caption)

        if not fitted:
            log.warning(
                "🛟 Сгенерированный пост слишком длинный — используем локальный fallback"
            )
            caption = local_fallback(topic, content_format)
            model = "local-fallback"
        else:
            caption = fitted

        log.info("🧠 Использована модель: %s", model)

    else:
        log.error("❌ Все Gemini-модели недоступны")
        log.warning("🛟 Используем локальный fallback")

        caption = local_fallback(topic, content_format)
        model = "local-fallback"

    image = get_unsplash_photo(
        rubric,
        content_format,
    )

    if publish(caption, image):
        record_success(
            topic,
            rubric,
            model,
            caption,
            content_format,
            style_variant,
            content_angle,
        )
        persist_history_to_git()

        log.info(
            "🎉 V8.7 завершил работу успешно | %s | %s",
            profile["name"],
            topic,
        )

        return 0

    log.error("❌ Пост не опубликован")
    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )