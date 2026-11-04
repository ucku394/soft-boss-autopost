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
# V9.0 — TELEGRAM ENGAGEMENT & CONTENT ENGINE
# 7 форматов + матрица хуков и интерактивных CTA + Telegram HTML (blockquote/spoiler)
# + скоринг вовлечённости + адаптивный self-repair + анти-повтор тем/углов/хуков
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

# Telegram: подпись к фото короче обычного текстового сообщения.
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
TELEGRAM_TEXT_LIMIT = 4096

# Дефолтные диапазоны (переопределяются профилем конкретного формата).
PHOTO_POST_RANGE = (620, 940)
TEXT_POST_RANGE = (1350, 2300)

MAX_CAPTION = TELEGRAM_TEXT_LIMIT
MAX_GENERATED_CHARS = TELEGRAM_TEXT_LIMIT
MAX_GENERATION_ATTEMPTS_PER_MODEL = 2
THINKING_LEVEL = "low"
QUALITY_THRESHOLD = 76
ACCEPTABLE_FALLBACK_THRESHOLD = 68

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("autopost-v9.0")


# ============================================================
# ТЕМАТИЧЕСКАЯ СЕТКА (РАСШИРЕННЫЙ ПУЛ С ТРИГГЕРАМИ НА 16+ НЕДЕЛЬ)
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
            "Как не превратиться в диспетчера чужих задач",
            "Почему «тихое увольнение» начинается с лучших сотрудников",
            "Как защитить команду от хаоса сверху и не стать буфером для всего",
            "Как удерживать авторитет без жёсткости и давления",
            "Почему команда ждёт одобрения даже по мелким вопросам",
            "Как перестроить управление, когда команда выросла вдвое",
            "Где проходит грань между поддержкой сотрудника и работой за него",
            "Как руководителю делегировать ответственность, а не просто куски работы",
            "Почему прозрачные правила в команде работают лучше харизмы лидера",
            "Как заметить выгорание ключевого игрока до того, как он положит заявление",
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
            "Правило 3 минут: как возвращать сотруднику ответственность за решение",
            "Асинхронный статус в чате вместо часового созвона",
            "Алгоритм разговора с сотрудником, который регулярно сдвигает сроки",
            "Чек-лист подготовки к сложной беседе один на один за 5 минут",
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
            "Ловушка спасателя: почему вам физически сложно не вмешиваться в чужую работу",
            "Синдром самозванца у руководителя, когда команда ждёт уверенности",
            "Почему руководителю страшно выглядеть «незанятым» перед командой и начальством",
            "Эмоциональное заражение: как настроение лидера считывается за первые 30 секунд созвона",
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
            "Сотрудник сорвал дедлайн и сообщил об этом в последний час",
            "Токсичный профи: даёт результат, но разрушает атмосферу в команде",
            "Сотрудник просит повышение, но его результаты за полгода не выросли",
            "Коллега из бывших равных не воспринимает вас как нового руководителя",
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
            "Почему фраза «давай я сам быстро сделаю» обходится слишком дорого",
            "Как публичный разбор полётов в общем чате убивает честность команды",
            "Ошибка найма: почему мы выбираем «удобных» вместо сильных",
            "К чему приводит привычка нагружать лучшего сотрудника за двоих",
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
            "Мини-тест: доверяет ли вам команда плохие новости",
            "5 фраз руководителя, которые звучат нормально, но считываются как недоверие",
            "Мини-тест: насколько ваша команда может работать 3 дня без вашего участия",
            "Признаки того, что ваши планёрки пора сократить вдвое",
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
            "За какую чужую задачу вы переживали на этой неделе больше, чем её исполнитель",
            "Что в вашем календаре на следующей неделе стоит отменить прямо сейчас",
            "Какое решение на этой неделе сэкономило команде больше всего нервов",
            "Где вы продолжаете тянуть проект по инерции вместо честной остановки",
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
# ФОРМАТЫ КОНТЕНТА И УГЛЫ ПОДАЧИ
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


# ============================================================
# МАТРИЦА ХУКОВ (ПЕРВЫЕ 2 СТРОКИ ПОСТА) V9.0
# Первые 2 секунды решают, развернёт ли руководитель пост.
# ============================================================

HOOK_MECHANICS = {
    "micro_scene": {
        "name": "Узнаваемая микро-сцена",
        "instruction": (
            "Открой пост конкретной рабочей микро-сценой на 1–2 предложения с узнаваемой деталью "
            "(время, уведомление в чате, фраза на созвоне, открытый вечером документ), чтобы руководитель сразу узнал себя."
        ),
    },
    "trigger_quote": {
        "name": "Фраза-триггер",
        "instruction": (
            "Начни пост с живой реалистичной реплики в кавычках («...»), которую часто слышит или произносит руководитель, "
            "и сразу покажи, какая ловушка за ней скрывается."
        ),
    },
    "paradox": {
        "name": "Управленческий парадокс",
        "instruction": (
            "Открой пост неожиданным парадоксом: покажи, почему привычное «правильное» действие руководителя "
            "в этой ситуации даёт ровно обратный результат."
        ),
    },
    "cost_of_habit": {
        "name": "Цена привычки",
        "instruction": (
            "Начни с ощутимой цены проблемы: сколько часов, фокуса или инициативы команды незаметно сжигает эта ситуация каждую неделю."
        ),
    },
    "sharp_question": {
        "name": "Проверочный вопрос в лоб",
        "instruction": (
            "Открой пост одним точным, немного неудобным вопросом к руководителю о его текущей рабочей неделе, без долгих предисловий."
        ),
    },
    "contrast_before_after": {
        "name": "Контраст восприятия",
        "instruction": (
            "Начни со столкновения двух картин: как эту ситуацию видит руководитель и как её в ту же секунду считывает сотрудник."
        ),
    },
}

FORMAT_COMPATIBLE_HOOKS = {
    "management_breakdown": ["micro_scene", "paradox", "cost_of_habit", "sharp_question", "contrast_before_after"],
    "practical_tool": ["cost_of_habit", "trigger_quote", "sharp_question", "micro_scene"],
    "psychology_story": ["micro_scene", "trigger_quote", "contrast_before_after", "paradox"],
    "dialogue_case": ["trigger_quote", "micro_scene", "contrast_before_after"],
    "management_mistake": ["paradox", "trigger_quote", "cost_of_habit", "micro_scene"],
    "mini_test": ["sharp_question", "cost_of_habit", "trigger_quote"],
    "weekly_reflection": ["sharp_question", "micro_scene", "paradox"],
}


# ============================================================
# МАТРИЦА ВОВЛЕКАЮЩИХ МЕХАНИК И CTA V9.0
# Работает на реакции, сохранения, пересылки и комментарии.
# ============================================================

ENGAGEMENT_MECHANICS = {
    "reaction_poll": {
        "name": "Голосование реакциями",
        "instruction": (
            "В финале (перед хэштегами) добавь короткое голосование реакциями в 1–2 строки, "
            "чтобы занятой руководитель мог ответить в один клик, например: "
            "«Как у вас с этим сейчас? 👍 — уже перестроил / 🔥 — знакомая боль / 🤔 — задумался» (адаптируй смыслы под тему поста)."
        ),
    },
    "ab_dilemma": {
        "name": "Управленческая дилемма (А или Б)",
        "instruction": (
            "В финале предложи читателю короткую развилку для комментариев: "
            "2 конкретных варианта поведения руководителя в этой ситуации («Как бы поступили вы: вариант 1 или 2? Или есть свой ход?»)."
        ),
    },
    "spoiler_reveal": {
        "name": "Интерактив со спойлером",
        "instruction": (
            "Используй интерактивный тег <tg-spoiler>...</tg-spoiler> для ключевого инсайта, самопроверки или расшифровки: "
            "предложи читателю сначала ответить себе на вопрос, а затем нажать на скрытую плашку со спойлером."
        ),
    },
    "save_and_share": {
        "name": "Триггер на сохранение и пересылку",
        "instruction": (
            "Оформи ключевую фразу или правило внутри блока <blockquote>...</blockquote>, "
            "а в финале предложи сохранить пост перед ближайшим 1-на-1 / планёркой или переслать коллеге-руководителю."
        ),
    },
    "deep_reflection_question": {
        "name": "Точный вопрос к себе",
        "instruction": (
            "Выдели главную мысль в <blockquote>...</blockquote> и заверши одним глубоким, но практичным вопросом к читателю "
            "про его ближайшую рабочую неделю (без банального «А что вы думаете?»)."
        ),
    },
}

FORMAT_COMPATIBLE_ENGAGEMENT = {
    "management_breakdown": ["reaction_poll", "ab_dilemma", "deep_reflection_question"],
    "practical_tool": ["save_and_share", "reaction_poll", "spoiler_reveal"],
    "psychology_story": ["deep_reflection_question", "reaction_poll", "spoiler_reveal"],
    "dialogue_case": ["ab_dilemma", "spoiler_reveal", "reaction_poll"],
    "management_mistake": ["reaction_poll", "spoiler_reveal", "save_and_share"],
    "mini_test": ["spoiler_reveal", "reaction_poll"],
    "weekly_reflection": ["deep_reflection_question", "reaction_poll"],
}


# ============================================================
# ВИЗУАЛЬНЫЕ СТИЛИ V9.0
# Формат отвечает за СОДЕРЖАНИЕ, а visual_style — за ВНЕШНИЙ ВИД.
# ============================================================

VISUAL_STYLE_PROFILES = {
    "classic": {
        "name": "Классический",
        "instruction": (
            "Начни с ёмкого жирного заголовка <b>...</b>. "
            "Используй 3–5 компактных смысловых блоков и выдели главный инсайт или фразу в <blockquote>...</blockquote>."
        ),
        "emoji_range": (3, 5),
    },
    "question_open": {
        "name": "Вопрос с открытия",
        "instruction": (
            "Открой пост сразу сильным жирным вопросом к руководителю <b>... ?</b> без рубрикатора. "
            "Ответ раскрой через 3–4 динамичных абзаца с воздухом между ними."
        ),
        "emoji_range": (2, 4),
    },
    "quote_open": {
        "name": "Фраза руководителя",
        "instruction": (
            "Открой пост короткой реалистичной репликой в <blockquote>«...»</blockquote>. "
            "Затем разбери управленческую ловушку за этой фразой и дай точное решение."
        ),
        "emoji_range": (2, 4),
    },
    "contrast": {
        "name": "Контраст",
        "instruction": (
            "Построй визуальный ритм на наглядном контрасте: ❌ привычная инерционная реакция / ✅ сильный ход руководителя. "
            "Не превращай весь текст в сухой отчёт."
        ),
        "emoji_range": (3, 6),
    },
    "checklist": {
        "name": "Чек-лист",
        "instruction": (
            "Сделай пост визуально сканируемым за 5 секунд: короткие строки, чёткие пункты и один практический вывод в <blockquote>...</blockquote>."
        ),
        "emoji_range": (3, 6),
    },
    "dialogue_open": {
        "name": "Диалог",
        "instruction": (
            "Начни с 2 острых реалистичных реплик руководителя и сотрудника. "
            "Затем сразу переходи к разбору механизма и дай точную фразу на замену."
        ),
        "emoji_range": (2, 4),
    },
    "minimal": {
        "name": "Минимализм",
        "instruction": (
            "Чистая журнальная вёрстка: короткие абзацы, максимум 1–3 эмодзи на весь пост, "
            "ключевая мысль выделена через <blockquote>...</blockquote>. Спокойный тон опытного практика."
        ),
        "emoji_range": (1, 3),
    },
}


FORMAT_PROFILES = {
    "management_breakdown": {
        "name": "Разбор управленческой ситуации",
        "photo_range": (620, 920),
        "text_range": (1400, 2200),
        "emoji_range": (3, 5),
        "instruction": """
ФОРМАТ: РАЗБОР УПРАВЛЕНЧЕСКОЙ СИТУАЦИИ.
Структура (адаптируй заголовки под выбранный визуальный стиль):
1. Живой вход: сразу покажи узнаваемую болевую точку руководителя в 1–2 предложениях.
2. 🔎 В чём реальный механизм проблемы (без воды и учебников: почему команда ведёт себя именно так).
3. 🛠 Что сделать на практике — ровно 3 конкретных шага (каждый с новой строки, с глаголом действия).
4. ⚠️ Тонкий момент: где здесь легко перегнуть палку (1–2 предложения).
5. Вовлекающий финал согласно заданной механике вовлечения.
""",
        "cta": "вовлекающий вопрос или быстрый выбор реакцией",
        "image_queries": [
            "business leader meeting team",
            "executive leadership team",
            "manager talking employee",
        ],
        "hashtags": "#лидерство #руководитель #команда",
    },
    "practical_tool": {
        "name": "Практический инструмент",
        "photo_range": (600, 920),
        "text_range": (1350, 2150),
        "emoji_range": (3, 6),
        "instruction": """
ФОРМАТ: ПРАКТИЧЕСКИЙ ИНСТРУМЕНТ.
Дай прикладной приём, который руководитель может внедрить уже сегодня за 5 минут.

Структура:
1. Короткий хук + 🛠 Название инструмента и в какой момент его включать.
2. Пошаговый алгоритм (3–4 чётких шага с цифрами 1️⃣, 2️⃣, 3️⃣ или 1., 2., 3.).
3. 💬 Готовая формулировка дословно — выдели в <blockquote>«...»</blockquote> одну точную фразу, которую можно сказать сотруднику.
4. ⚠️ Когда этот приём НЕ сработает (короткая честная оговорка).
5. Вовлекающий финал (предложи сохранить себе или оценить приём).
""",
        "cta": "триггер на сохранение инструмента или голосование реакцией",
        "image_queries": [
            "business planning desk",
            "project planning meeting",
            "manager writing notes",
        ],
        "hashtags": "#управление #инструмент #руководитель",
    },
    "psychology_story": {
        "name": "Психология и микро-история",
        "photo_range": (600, 920),
        "text_range": (1350, 2150),
        "emoji_range": (2, 4),
        "instruction": """
ФОРМАТ: ПСИХОЛОГИЧЕСКАЯ МИНИ-ИСТОРИЯ.
Начни с живой, кинематографичной микро-сцены из будней руководителя (конкретный момент, эмоция, автоматический порыв).

Не используй сухие подзаголовки «Главная мысль» и «Что сделать».
Проведи читателя по цепочке:
— что сделал или почувствовал руководитель в моменте;
— какую скрытую тревогу или потребность он пытался этим закрыть;
— почему эта защитная реакция незаметно бьёт по команде;
— какой один разворот в мышлении или действии меняет картину (ключевой инсайт можно выделить в <blockquote>...</blockquote>).

Финал — точный вопрос для самопроверки или выбор реакции.
""",
        "cta": "вопрос для самодиагностики или отклик реакцией",
        "image_queries": [
            "business executive thinking",
            "manager reflection office",
            "leadership stress office",
        ],
        "hashtags": "#психология #руководитель #лидерство",
    },
    "dialogue_case": {
        "name": "Диалог и разбор кейса",
        "photo_range": (620, 920),
        "text_range": (1400, 2250),
        "emoji_range": (2, 5),
        "instruction": """
ФОРМАТ: ДИАЛОГ И РАЗБОР КЕЙСА.
Начни с острого, жизненного мини-диалога (2–4 реплики), где чувствуется реальное рабочее напряжение:
— Сотрудник: «...»
— Руководитель: «...»

Затем разбери ситуацию по косточкам:
❌ Ловушка первой реакции: почему привычный ответ руководителя загоняет разговор в тупик или защиту.
✅ Сильный ход: дай конкретную альтернативную реплику руководителя в <blockquote>«...»</blockquote> и объясни, как она переводит разговор в русло ответственности.

В финале вовлеки читателя (предложи выбрать вариант поведения или открыть <tg-spoiler> с главным маркером ситуации).
""",
        "cta": "дилемма для комментариев: как бы ответили вы?",
        "image_queries": [
            "manager employee conversation",
            "business conflict meeting",
            "team discussion office",
        ],
        "hashtags": "#кейс #управление #команда",
    },
    "management_mistake": {
        "name": "Ошибка руководителя",
        "photo_range": (600, 920),
        "text_range": (1350, 2150),
        "emoji_range": (3, 5),
        "instruction": """
ФОРМАТ: ОШИБКА РУКОВОДИТЕЛЯ.
Покажи управленческую ловушку, в которую попадают даже опытные лидеры из лучших побуждений.

Структура:
❌ Как выглядит ошибка в реальной работе (конкретное действие или фраза).
🤔 Ложная выгода: почему в моменте это решение кажется руководителю логичным и быстрым.
⚠️ Скрытая цена: что происходит с инициативой и ответственностью команды через месяц такой практики.
✅ Чем заменить: конкретное действие или правило (можно выделить в <blockquote>...</blockquote>).

Не обвиняй руководителя — пиши с позиции союзника, который тоже через это проходил.
""",
        "cta": "вопрос на узнавание себя или голосование реакциями",
        "image_queries": [
            "business decision meeting",
            "executive decision office",
            "business problem solving",
        ],
        "hashtags": "#ошибки #руководитель #управление",
    },
    "mini_test": {
        "name": "Мини-тест",
        "photo_range": (560, 910),
        "text_range": (1100, 1750),
        "emoji_range": (4, 7),
        "instruction": """
ФОРМАТ: ИНТЕРАКТИВНЫЙ МИНИ-ТЕСТ.
Сделай пост максимально интерактивным: читатель должен захотеть посчитать свои баллы прямо во время чтения.

Структура:
1. Короткий цепляющий заголовок и подводка в 1 предложение: «Проверьте себя по 4 признакам за 30 секунд».
2. 4 конкретных жизненных вопроса-маркера (каждый с новой строки, без абстракций — только реальные действия за последнюю неделю).
3. Расшифровка результатов — спрячь ключевой вывод или расшифровку внутрь тега <tg-spoiler>...</tg-spoiler>, чтобы читатель кликнул по плашке после подсчёта ответов «да»:
   — 0–1 «да»: что это значит;
   — 2–4 «да»: где точка риска и какой один шаг сделать завтра.
4. Короткий призыв в финале: поделиться результатом реакцией или в комментариях.
""",
        "cta": "интерактивная расшифровка под спойлером и реакция по итогам теста",
        "image_queries": [
            "modern office team",
            "business coffee meeting",
            "creative team office",
        ],
        "hashtags": "#минитест #руководитель #команда",
    },
    "weekly_reflection": {
        "name": "Недельная рефлексия",
        "photo_range": (500, 860),
        "text_range": (1050, 1650),
        "emoji_range": (1, 3),
        "instruction": """
ФОРМАТ: НЕДЕЛЬНАЯ РЕФЛЕКСИЯ.
Это спокойный, глубокий воскресный пост-пауза перед новой неделей.

Не используй нумерованные списки и сухие рубрикаторы.
1. Начни с точного наблюдения о том, как проходит типичная перегруженная неделя руководителя.
2. Покажи тонкий контраст между суетой (тушением пожаров) и реальным управлением.
3. Выдели в <blockquote>...</blockquote> один сильный проверочный вопрос, который стоит задать себе сегодня вечером.
4. Предложи один маленький, реалистичный эксперимент на понедельник.

Тон: тёплый, взрослый, уважительный, без мотивационных лозунгов.
""",
        "cta": "один глубокий вопрос для личной рефлексии или тихий отклик реакцией",
        "image_queries": [
            "business planning week",
            "executive desk notebook",
            "leadership reflection",
        ],
        "hashtags": "#рефлексия #руководитель #лидерство",
    },
}


# ============================================================
# ВИЗУАЛЬНАЯ СЕТКА ЛЕНТЫ
# Чередуем фото и text-only, чтобы вся лента не выглядела одинаково.
# ============================================================

MEDIA_MODES = {
    "photo": {"name": "Фото"},
    "text": {"name": "Text-only"},
}

MEDIA_SCHEDULE_A = ["photo", "photo", "text", "photo", "text", "photo", "text"]
MEDIA_SCHEDULE_B = ["text", "photo", "photo", "text", "photo", "text", "photo"]


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
    Локальный topics_history.json между запусками сохраняется шагом git commit/push.
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
            result.append(item)

    return result


def topic_recency_penalty(topic, history, rubric):
    """
    Дополнительная защита от повторения одного управленческого угла.
    Сравниваем новую тему не только с заголовком старой публикации,
    но и с сохранённым fingerprint очищенного текста.
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

        rubric_load = rubric_counts.get(rubric, 0)
        balance_penalty = min(rubric_load * 2, 10)

        if weighted_sim >= 65:
            repeat_penalty = 120
        elif weighted_sim >= 50:
            repeat_penalty = 65
        elif weighted_sim >= 38:
            repeat_penalty = 25
        else:
            repeat_penalty = 0

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

    acceptable = [
        item for item in scored
        if item["weighted_sim"] < 38
        and item["topic_sim"] < 75
    ]

    pool = acceptable if acceptable else scored

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


def choose_visual_style(topic, content_format):
    """Выбирает совместимый с форматом визуальный стиль с защитой от повторов."""
    compatible = {
        "management_breakdown": ["classic", "question_open", "quote_open", "contrast", "checklist", "minimal"],
        "practical_tool": ["classic", "question_open", "contrast", "checklist", "minimal"],
        "psychology_story": ["classic", "question_open", "quote_open", "minimal"],
        "dialogue_case": ["classic", "question_open", "contrast", "dialogue_open"],
        "management_mistake": ["classic", "question_open", "quote_open", "contrast", "minimal"],
        "mini_test": ["classic", "question_open", "contrast", "checklist"],
        "weekly_reflection": ["classic", "question_open", "quote_open", "minimal"],
    }
    allowed = compatible.get(content_format, list(VISUAL_STYLE_PROFILES))
    history = recent_history()
    scored = []

    for style_key in allowed:
        profile = VISUAL_STYLE_PROFILES[style_key]
        penalty = 0
        recent_same_style = 0

        for idx, item in enumerate(reversed(history)):
            if item.get("visual_style", "") != style_key:
                continue

            recent_same_style += 1
            if idx < 2:
                penalty += 35

            topic_sim = similarity_percent(topic, item.get("topic", ""))
            old_format = item.get("content_format", "")

            if topic_sim >= 55 and old_format == content_format:
                penalty += 90
            elif topic_sim >= 35:
                penalty += 45
            else:
                penalty += 8

        penalty += min(recent_same_style * 4, 16)
        scored.append((penalty, style_key, profile))

    best_penalty = min(item[0] for item in scored)
    best = [item for item in scored if item[0] == best_penalty]
    selected = random.choice(best)

    log.info(
        "🎨 Визуальный стиль: %s | key=%s | penalty=%s",
        selected[2]["name"], selected[1], selected[0],
    )
    return selected[1]


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


def choose_hook_mechanic(topic, content_format):
    """Выбирает механику первых 2 строк (хук) с защитой от повторов в ленте."""
    allowed = FORMAT_COMPATIBLE_HOOKS.get(content_format, list(HOOK_MECHANICS))
    history = recent_history()
    scored = []

    for hook_key in allowed:
        penalty = 0
        for idx, item in enumerate(reversed(history)):
            if item.get("hook_mechanic", "") != hook_key:
                continue
            if idx == 0:
                penalty += 50
            elif idx <= 2:
                penalty += 25
            else:
                penalty += 8
            if similarity_percent(topic, item.get("topic", "")) >= 45:
                penalty += 40

        scored.append((penalty, hook_key))

    best_penalty = min(item[0] for item in scored)
    best = [item[1] for item in scored if item[0] == best_penalty]
    selected = random.choice(best)

    log.info(
        "🪝 Хук открытия: %s | key=%s | penalty=%s",
        HOOK_MECHANICS[selected]["name"],
        selected,
        best_penalty,
    )
    return selected


def choose_engagement_mechanic(topic, content_format):
    """Выбирает вовлекающую механику финала (CTA / интерактив) с защитой от повторов."""
    allowed = FORMAT_COMPATIBLE_ENGAGEMENT.get(content_format, list(ENGAGEMENT_MECHANICS))
    history = recent_history()
    scored = []

    for eng_key in allowed:
        penalty = 0
        for idx, item in enumerate(reversed(history)):
            if item.get("engagement_mechanic", "") != eng_key:
                continue
            if idx == 0:
                penalty += 45
            elif idx <= 2:
                penalty += 20
            else:
                penalty += 6

        scored.append((penalty, eng_key))

    best_penalty = min(item[0] for item in scored)
    best = [item[1] for item in scored if item[0] == best_penalty]
    selected = random.choice(best)

    log.info(
        "💬 Механика вовлечения: %s | key=%s | penalty=%s",
        ENGAGEMENT_MECHANICS[selected]["name"],
        selected,
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
    visual_style,
    media_mode,
    hook_mechanic="",
    engagement_mechanic="",
):
    items = load_history()

    # Очищаем HTML перед сохранением fingerprint, чтобы теги <b>/<i> не засоряли токены.
    clean_plain = strip_html(sanitize_telegram_html(text))

    items.append({
        "topic": topic,
        "rubric": rubric,
        "content_format": content_format,
        "style_variant": style_variant,
        "content_angle": content_angle,
        "visual_style": visual_style,
        "hook_mechanic": hook_mechanic,
        "engagement_mechanic": engagement_mechanic,
        "media_mode": media_mode,
        "model": model,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": normalize_topic(clean_plain)[:500],
    })

    save_history(items)


# ============================================================
# TELEGRAM HTML (ВКЛЮЧАЯ BLOCKQUOTE И TG-SPOILER)
# ============================================================

ALLOWED_TAGS = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler)(?:\s[^<>]*)?>",
    re.IGNORECASE,
)


def sanitize_telegram_html(text):
    """
    Нормализует HTML под требования Telegram Bot API (parse_mode=HTML).
    Поддерживает <b>, <i>, <blockquote>, <tg-spoiler> и др.
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

    # <br> превращаем в переносы строк.
    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    # Нормализуем возможный <spoiler> от модели в валидный <tg-spoiler>.
    text = re.sub(r"<\s*spoiler\s*>", "<tg-spoiler>", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*spoiler\s*>", "</tg-spoiler>", text, flags=re.IGNORECASE)

    # HTML entities -> обычный текст.
    text = html.unescape(text)

    # Сохраняем только разрешённые Telegram-теги (без лишних атрибутов).
    saved = []

    def stash(match):
        raw_tag = match.group(0)
        m_name = re.match(r"<\s*(/?)\s*([a-zA-Z0-9-]+)", raw_tag)
        if m_name:
            slash, tname = m_name.group(1), m_name.group(2).lower()
            clean_tag = f"<{slash}{tname}>"
        else:
            clean_tag = raw_tag
        saved.append(clean_tag)
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

    # На случай нарушения промпта моделью (Markdown -> HTML).
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

    text = re.sub(
        r"\|\|([^|\n]+)\|\|",
        r"<tg-spoiler>\1</tg-spoiler>",
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


def get_target_ranges(content_format, media_mode):
    """Возвращает целевой диапазон символов (min_chars, max_chars, max_allowed) с учётом формата и медиа."""
    profile = FORMAT_PROFILES.get(content_format, {})
    if media_mode == "text":
        min_chars, max_chars = profile.get("text_range", TEXT_POST_RANGE)
        max_allowed = TELEGRAM_TEXT_LIMIT
    else:
        min_chars, max_chars = profile.get("photo_range", PHOTO_POST_RANGE)
        max_allowed = TELEGRAM_PHOTO_CAPTION_LIMIT
    return min_chars, max_chars, max_allowed


def fit_caption(text, max_len=MAX_CAPTION):
    """
    Подготавливает caption без обрезания готовой мысли посередине предложения.
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
        "❌ Caption слишком длинный: %s символов (лимит %s) — не обрезаем мысль",
        len(compact),
        max_len,
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
        "blockquote",
        "tg-spoiler",
    }

    stack = []

    for match in re.finditer(
        r"</?([a-zA-Z0-9-]+)(?:\s[^<>]*)?>",
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
# GEMINI PROMPT & GENERATION
# ============================================================

def build_prompt(
    topic,
    rubric,
    content_format,
    style_variant=0,
    content_angle="",
    visual_style="",
    media_mode="photo",
    hook_mechanic="",
    engagement_mechanic="",
    repair=False,
    repair_reasons=None,
):
    profile = FORMAT_PROFILES[content_format]
    visual_profile = VISUAL_STYLE_PROFILES.get(visual_style, VISUAL_STYLE_PROFILES["classic"])
    hook_profile = HOOK_MECHANICS.get(hook_mechanic) or HOOK_MECHANICS["micro_scene"]
    eng_profile = ENGAGEMENT_MECHANICS.get(engagement_mechanic) or ENGAGEMENT_MECHANICS["reaction_poll"]

    min_chars, max_chars, max_allowed = get_target_ranges(content_format, media_mode)

    if media_mode == "text":
        media_instruction = f"""
МЕДИА-РЕЖИМ: TEXT-ONLY (самостоятельный пост без картинки).
Раскрой тему глубже, удерживая динамику:
— дай узнаваемую рабочую фактуру (конкретную сцену, диалог или микро-кейс);
— объясни психологический или управленческий механизм («почему так происходит»);
— дай конкретные формулировки или шаги («бери и делай»);
— используй 4–7 смысловых блоков с воздухом между ними.
Целевой объём: {min_chars}–{max_chars} символов (без воды и затянутых вступлений).
"""
    else:
        media_instruction = f"""
МЕДИА-РЕЖИМ: ФОТО (пост-подпись под изображением).
Текст должен считываться с одного экрана телефона:
— плотная подача, ноль лишних вводных предложений;
— каждый абзац — не длиннее 2–3 коротких предложений.
Целевой объём: {min_chars}–{max_chars} символов (строго до {max_allowed} символов вместе с HTML-тегами).
"""

    emoji_min, emoji_max = visual_profile["emoji_range"]

    style_variants = [
        "Первая строка должна сразу бить в знакомую управленческую боль без разогрева.",
        "Начни с точного наблюдения из реальной жизни руководителя, чтобы читатель кивнул уже на второй секунде.",
        "Построй вход на живом контрасте между тем, что руководитель хотел сделать, и тем, что получилось.",
    ]
    opening_style = style_variants[style_variant % len(style_variants)]

    repair_text = ""
    if repair:
        specific_fixes = ""
        if repair_reasons:
            bullet_fixes = "\n".join(f"— ИСПРАВИТЬ: {r};" for r in repair_reasons)
            specific_fixes = f"\nПричины отклонения предыдущей попытки:\n{bullet_fixes}\n"
        repair_text = f"""
ВНИМАНИЕ — ПОВТОРНАЯ ГЕНЕРАЦИЯ (SELF-REPAIR):{specific_fixes}
Строго проконтролируй:
— уложись точно в целевой диапазон {min_chars}–{max_chars} символов (и не превышай {max_allowed});
— сделай первые 2 строки острыми и конкретными (прямая речь, микро-сцена или сильный вопрос);
— обязательно включи живые формулировки в кавычках «...» и обращение к читателю на «вы»;
— используй корректно закрытые Telegram HTML теги (<b>, <i>, <blockquote>, <tg-spoiler>);
— заверши пост вовлекающим финалом и блоком хэштегов на отдельной строке.
"""

    return f"""
Ты — главный редактор и автор популярного Telegram-канала для руководителей и тимлидов «Лидерство без выгорания».
Твои посты пересылают коллегам и сохраняют в Избранное, потому что в них нет сухой теории — только живая практика, точные формулировки и узнаваемые рабочие ситуации.

ПАРАМЕТРЫ ПОСТА:
- Рубрика: {rubric}
- Тема: {topic}
- Редакционный формат: {profile["name"]}
- Угол раскрытия темы: {content_angle}
- Визуальный стиль: {visual_profile["name"]} ({visual_profile["instruction"]})
- Механика первых 2 строк (ХУК): {hook_profile["name"]} — {hook_profile["instruction"]}
- Механика вовлечения в финале (CTA): {eng_profile["name"]} — {eng_profile["instruction"]}

ИНСТРУКЦИЯ ПО ФОРМАТУ:
{profile["instruction"]}

ПРАВИЛА ВЫСОКОЙ ВОВЛЕЧЁННОСТИ (ОБЯЗАТЕЛЬНО):
1. Сильный старт (первые 2 строки): {opening_style} Никаких общих фраз вроде «Каждый руководитель знает...» или «В работе с командой важно...».
2. Конкретика и живая речь: включи в пост хотя бы одну точную жизненную фразу в кавычках «...» (реплику сотрудника, внутреннюю мысль или готовую формулировку руководителя).
3. Прямой диалог с читателем: пиши для живого человека («вы», «ваша команда», «ваш календарь»), как опытный руководитель делится опытом с равным за чашкой кофе.
4. Анти-вода и анти-канцелярит: запрещены штампы «в современном мире», «ключевым фактором является», «данный подход», «важно понимать», «не секрет, что», «давайте разберёмся», «подводя итог», «таким образом», «синергия», «экосистема», «драйвер», «трансформация», «декомпозиция», «стейкхолдеры».
5. Достоверность: не выдумывай фейковую статистику («83% компаний»), несуществующие исследования или ссылки.

{media_instruction}

ВИЗУАЛЬНАЯ ВЁРСТКА TELEGRAM HTML:
- Разрешены ТОЛЬКО теги Telegram HTML: <b>...</b>, <i>...</i>, <blockquote>...</blockquote> и <tg-spoiler>...</tg-spoiler>.
- НЕ используй Markdown (**жирный**, *курсив*, ||спойлер||, # заголовки).
- Используй <blockquote>...</blockquote> ровно 1 раз на пост — чтобы визуально выделить главную фразу для сотрудника, острую цитату или ключевой управленческий принцип.
- Если формат или механика вовлечения предполагает самопроверку/интерактив — используй <tg-spoiler>...</tg-spoiler> для скрытого ответа или расшифровки.
- Используй {emoji_min}–{emoji_max} уместных эмодзи на весь пост (не ставь эмодзи в начале каждой строки).
- Отделяй смысловые блоки пустой строкой.

ОБЪЁМ И ФИНАЛ:
- Целевой объём: {min_chars}–{max_chars} символов (абсолютный потолок: {max_allowed} символов).
- Перед хэштегами заверши пост вовлекающим финалом ({eng_profile["name"]}).
- В самом конце на отдельной строке добавь ровно этот блок хэштегов:
{profile["hashtags"]}

{repair_text}

Верни только готовый текст поста без пояснений от себя.
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
            "maxOutputTokens": 2400,
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


def looks_like_complete_post(text, finish_reason="", content_format="management_breakdown", media_mode="photo"):
    """Проверяет завершённость поста с учётом длины конкретного формата и лимитов Telegram."""
    if not text:
        return False

    clean = sanitize_telegram_html(text)
    plain = strip_html(clean).strip()
    min_chars, max_chars, max_allowed = get_target_ranges(content_format, media_mode)

    if finish_reason.upper() in {"MAX_TOKENS", "LENGTH"}:
        return False

    # Проверяем как длину plain-текста, так и длину с HTML-разметкой для Telegram API.
    if len(clean) > max_allowed:
        return False

    if len(plain) < max(340, min_chars - 90) or len(plain) > max_allowed:
        return False

    body, hashtags = split_hashtags(clean)

    if not hashtags:
        return False

    content_last = last_content_line(clean)
    if not content_last:
        return False

    if content_last.endswith(("-", "—", ",", ":", ";", "…")):
        return False

    # Разрешаем завершение знаком препинания, кавычкой, скобкой или эмодзи (например, в голосовании реакциями).
    if not (
        re.search(r'[.!?»")]$', content_last)
        or emoji_count(content_last[-4:]) >= 1
    ):
        return False

    if len(plain) > max_chars + 180:
        return False

    return True


def get_finish_reason(data):
    try:
        return str(data["candidates"][0].get("finishReason", ""))
    except (KeyError, IndexError, TypeError):
        return ""


def emoji_count(text):
    return len(re.findall(
        r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]",
        strip_html(text),
    ))


def formatting_score(text, content_format="management_breakdown", visual_style="classic"):
    """Оценивает визуальную вёрстку с учётом выбранного стиля (не штрафуя minimal за малое число эмодзи)."""
    clean = sanitize_telegram_html(text)
    body, _ = split_hashtags(clean)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    emojis = emoji_count(body)
    visual_profile = VISUAL_STYLE_PROFILES.get(visual_style, VISUAL_STYLE_PROFILES["classic"])
    emin, emax = visual_profile.get("emoji_range", (2, 5))

    score = 0

    if emin <= emojis <= emax + 1:
        score += 7
    elif 1 <= emojis <= 8:
        score += 4

    if 3 <= len(paragraphs) <= 7:
        score += 5
    elif len(paragraphs) >= 2:
        score += 3

    # Наличие выделений жирным/курсивом/цитатой/спойлером улучшает сканируемость.
    if "<b>" in clean:
        score += 3
    if "<blockquote>" in clean or "<tg-spoiler>" in clean:
        score += 3

    if re.search(r"(?:^|\n)(?:🎯|💡|🛠|⚠️|👉|🔎|❌|✅|💬|❓|1️⃣|1\.)\s*", body) or visual_style == "minimal":
        score += 2

    return min(score, 20)


def simplicity_score(text):
    """Оценивает читаемость: короткие предложения, отсутствие канцелярита и водянистых клише."""
    plain = strip_html(text)
    sentences = [x.strip() for x in re.split(r"[.!?]+", plain) if x.strip()]
    if not sentences:
        return 0

    lengths = [len(re.findall(r"\b[А-Яа-яЁёA-Za-z0-9-]+\b", s)) for s in sentences]
    avg = sum(lengths) / len(lengths)
    long_count = sum(1 for n in lengths if n > 20)

    score = 18
    if avg <= 14:
        score += 7
    elif avg <= 17:
        score += 4
    elif avg <= 21:
        score += 1

    score -= min(8, long_count * 2)

    jargon_and_cliches = (
        "синерг", "экосистем", "драйвер", "компетенц",
        "эффективизац", "имплементац", "парадигм",
        "стейкхолдер", "декомпозиц", "трансформац",
        "канцелярит", "проактив", "кросс-функцион",
        "в современном мире", "не секрет что", "не секрет, что",
        "важно понимать", "давайте разберемся", "давайте разберёмся",
        "подводя итог", "таким образом", "ключевым фактором",
        "данный подход", "безусловно",
    )
    low = plain.lower()
    hits = sum(low.count(word) for word in jargon_and_cliches)
    score -= min(12, hits * 4)
    return max(0, min(25, score))


def engagement_score(text, content_format="management_breakdown"):
    """
    Оценивает вовлекающий потенциал поста (до 25 баллов):
    1) Сила первых строк (Hook: вопрос, кавычки, конкретика, компактность).
    2) Живая речь и прямой контакт с читателем («вы / ваш», прямая речь «...»).
    3) Интерактивность и вовлекающий финал (вопрос, выбор, реакции, спойлер, цитата).
    """
    clean = sanitize_telegram_html(text)
    body, _ = split_hashtags(clean)
    plain_body = strip_html(body).strip()
    low = plain_body.lower()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", plain_body) if p.strip()]

    score = 0
    reasons = []

    # 1. Оценка хука (первые 1–2 абзаца).
    first_chunk = "\n".join(paragraphs[:2]) if paragraphs else ""
    hook_points = 0
    if "?" in first_chunk or "«" in first_chunk or '"' in first_chunk or "—" in first_chunk:
        hook_points += 5
    if re.search(r"\b(?:\d+|час|минут|понедельник|пятниц|вечер|утром|созвон|чат|отчет|отчёт|задач|срок|ошиб)", first_chunk.lower()):
        hook_points += 3
    if paragraphs and len(paragraphs[0]) <= 220:
        hook_points += 2
    score += min(8, hook_points)
    if hook_points < 4:
        reasons.append("слабый хук в начале поста (нужна микро-сцена, цитата или острый вопрос)")

    # 2. Контакт с читателем и живая фактура.
    contact_points = 0
    if re.search(r"\b(?:вы|вам|вас|ваш|ваша|ваше|ваши|вашей|вашем)\b", low):
        contact_points += 4
    else:
        reasons.append("нет прямого обращения к читателю («вы»)")

    if "«" in plain_body or '"' in plain_body or re.search(r"(?m)^\s*[—-]\s*", plain_body):
        contact_points += 4
    else:
        reasons.append("не хватает живой прямой речи или готовой фразы в кавычках")

    score += min(8, contact_points)

    # 3. Сила финала (CTA) и нативные элементы вовлечения Telegram.
    tail_chunk = "\n".join(paragraphs[-2:]) if paragraphs else ""
    cta_points = 0
    if "?" in tail_chunk:
        cta_points += 5
    if any(marker in tail_chunk.lower() for marker in ["реакц", "сохран", "перешл", "напиш", "вариант", "провер", "👍", "🔥", "🤔", "1 или 2"]):
        cta_points += 4
    if "<tg-spoiler>" in clean or "<blockquote>" in clean:
        cta_points += 3

    score += min(9, cta_points)
    if cta_points < 4:
        reasons.append("пассивный финал (добавь вопрос к читателю, выбор или голосование реакциями)")

    return min(25, score), reasons


def format_quality_score(text, content_format):
    clean = sanitize_telegram_html(text)
    body, _ = split_hashtags(clean)
    low = body.lower()
    score = 0
    reasons = []

    if content_format == "management_breakdown":
        if re.search(r"(?:🛠|🔎|⚠️|👉|<blockquote>)", clean):
            score += 5
        if len(re.findall(r"(?m)^\s*(?:\d+(?:\ufe0f?\u20e3|[.)])?|[—•-])\s+", body)) >= 3:
            score += 8
        else:
            reasons.append("разбор должен содержать 3 конкретных шага")

    elif content_format == "practical_tool":
        numbered = len(re.findall(r"(?m)^\s*\d+(?:\ufe0f?\u20e3|[.)])?\s+", body))
        if numbered >= 3:
            score += 8
        else:
            reasons.append("инструмент должен содержать пошаговое применение (1, 2, 3)")
        if "«" in body or '"' in body or "<blockquote>" in clean:
            score += 5
        else:
            reasons.append("в инструменте нужна готовая фраза для руководителя")

    elif content_format == "psychology_story":
        if any(x in low for x in ["он ", "она ", "руководитель", "вы ", "вечер", "чат", "ошиб"]):
            score += 5
        if any(x in low for x in ["почему", "на самом деле", "страх", "тревог", "контрол", "кажется"]):
            score += 5
        if "<blockquote>" in clean or "?" in body:
            score += 3

    elif content_format == "dialogue_case":
        dialogue_lines = len(re.findall(r"(?m)^\s*[—-]\s*(?:сотрудник|руководитель|коллега|тимлид)?", body, re.I))
        if dialogue_lines >= 2 or body.count("«") >= 2:
            score += 8
        else:
            reasons.append("нет реалистичного диалога в начале кейса")
        if "❌" in body and "✅" in body:
            score += 5
        else:
            reasons.append("в кейсе нужны маркеры ❌ (ловушка) и ✅ (сильный ход)")

    elif content_format == "management_mistake":
        if "❌" in body:
            score += 4
        if "🤔" in body or "⚠️" in body:
            score += 4
        if "✅" in body:
            score += 4
        if not any(x in low for x in ["ошиб", "вместо", "приводит", "ловушк"]):
            reasons.append("слабо раскрыт механизм ошибки")

    elif content_format == "mini_test":
        questions = len(re.findall(r"(?m)^\s*(?:\d+(?:\ufe0f?\u20e3|[.)])?|❓|[—•-])\s+.+\?", body))
        if questions < 3:
            questions = body.count("?")
        if questions >= 3:
            score += 8
        else:
            reasons.append("мини-тест должен содержать минимум 3 проверочных вопроса")
        if ("если" in low or "да" in low or "<tg-spoiler>" in clean):
            score += 5

    elif content_format == "weekly_reflection":
        numbered = len(re.findall(r"(?m)^\s*\d+(?:\ufe0f?\u20e3|\.)\s+", body))
        if numbered == 0:
            score += 6
        else:
            reasons.append("воскресная рефлексия не должна превращаться в нумерованный список")
        if body.count("?") >= 1:
            score += 7

    return min(score, 15), reasons


def score_post(text, topic, content_format, media_mode="photo", visual_style="classic"):
    """Комплексная оценка качества и вовлечённости поста (0..100)."""
    clean = sanitize_telegram_html(text)
    plain = strip_html(clean).strip()
    body, hashtags = split_hashtags(clean)

    min_chars, max_chars, _ = get_target_ranges(content_format, media_mode)

    score = 0
    reasons = []

    # 1. Визуальная структура и HTML (до 15 баллов)
    visual = formatting_score(clean, content_format=content_format, visual_style=visual_style)
    score += round(visual * 0.75)

    # 2. Чистота языка и отсутствие клише (до 15 баллов)
    simplicity = simplicity_score(body)
    score += round(simplicity * 0.60)
    if simplicity < 14:
        reasons.append("слишком длинные предложения или канцелярские штампы")

    # 3. Точность попадания в целевой объём (до 15 баллов)
    length = len(plain)
    if min_chars <= length <= max_chars:
        score += 15
    elif min_chars - 80 <= length <= max_chars + 120:
        score += 10
    else:
        reasons.append(f"неоптимальная длина ({length} симв. при цели {min_chars}–{max_chars})")

    # 4. Базовые структурные требования (до 15 баллов)
    if hashtags:
        score += 5
    else:
        reasons.append("нет блока хэштегов")

    content_last = last_content_line(clean)
    if re.search(r'[.!?»")]$', content_last) or emoji_count(content_last[-4:]) >= 1:
        score += 5
    else:
        reasons.append("незавершённая финальная строка")

    topic_tokens = tokens(topic)
    text_tokens = tokens(body)
    if (topic_tokens and topic_tokens & text_tokens) or any(
        x in body.lower() for x in ["руководитель", "сотрудник", "команда", "задач", "делегир", "контрол"]
    ):
        score += 5
    else:
        reasons.append("слабая связь с заявленной темой")

    # 5. Соблюдение ДНК конкретного формата (до 15 баллов)
    format_score, format_reasons = format_quality_score(clean, content_format)
    score += format_score
    reasons.extend(format_reasons)

    # 6. Потенциал вовлечения читателя (до 25 баллов)
    eng_score, eng_reasons = engagement_score(clean, content_format)
    score += eng_score
    reasons.extend(eng_reasons)

    return min(score, 100), reasons


def generate_with_failover(
    topic,
    rubric,
    content_format,
    style_variant=0,
    content_angle="",
    visual_style="",
    media_mode="photo",
    hook_mechanic="",
    engagement_mechanic="",
):
    profile = FORMAT_PROFILES[content_format]
    best_candidate = None  # (quality, text, model)

    for model in GEMINI_MODELS:
        log.info("🤖 Gemini генерация: %s | формат=%s", model, profile["name"])
        last_failure_reasons = []

        for attempt in range(1, MAX_GENERATION_ATTEMPTS_PER_MODEL + 1):
            repair = attempt > 1 or bool(last_failure_reasons)
            prompt = build_prompt(
                topic,
                rubric,
                content_format,
                style_variant=style_variant,
                content_angle=content_angle,
                visual_style=visual_style,
                media_mode=media_mode,
                hook_mechanic=hook_mechanic,
                engagement_mechanic=engagement_mechanic,
                repair=repair,
                repair_reasons=last_failure_reasons,
            )

            try:
                response = gemini_request(model, prompt)

                if response.status_code == 200:
                    data = response.json()
                    text = extract_gemini_text(data)
                    finish_reason = get_finish_reason(data)
                    plain_len = len(strip_html(sanitize_telegram_html(text))) if text else 0

                    log.info(
                        "🔍 Gemini %s | finishReason=%s | chars=%s",
                        model,
                        finish_reason or "NONE",
                        plain_len,
                    )

                    if text and looks_like_complete_post(
                        text,
                        finish_reason,
                        content_format,
                        media_mode=media_mode,
                    ):
                        quality, reasons = score_post(
                            text,
                            topic,
                            content_format,
                            media_mode=media_mode,
                            visual_style=visual_style,
                        )

                        log.info(
                            "🎨 Формат: %s | эмодзи=%s",
                            profile["name"],
                            emoji_count(text),
                        )
                        log.info(
                            "📊 Качество и вовлечённость: %s/100%s",
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

                        last_failure_reasons = reasons
                        if quality >= ACCEPTABLE_FALLBACK_THRESHOLD:
                            if not best_candidate or quality > best_candidate[0]:
                                best_candidate = (quality, text, model)

                        log.warning(
                            "⚠️ Пост отклонён по качеству: %s/100 | порог %s",
                            quality,
                            QUALITY_THRESHOLD,
                        )
                    else:
                        min_c, max_c, max_all = get_target_ranges(content_format, media_mode)
                        last_failure_reasons = [
                            f"текст не прошёл проверку целостности или объёма (получено {plain_len} симв., нужно {min_c}–{max_c}, лимит {max_all}, и обязательны хэштеги в конце)"
                        ]
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

    if best_candidate:
        q, best_text, best_model = best_candidate
        log.info(
            "✅ Используем лучший валидный кандидат Gemini (%s, качество %s/100)",
            best_model,
            q,
        )
        return best_text, best_model

    return None, None


# ============================================================
# ЛОКАЛЬНЫЙ FALLBACK (КАЛИБРОВАН ПОД PHOTO И TEXT РЕЖИМЫ)
# ============================================================

def local_fallback(topic, content_format):
    if content_format == "practical_tool":
        return (
            f"🛠 <b>{topic}</b>\n\n"
            "Знакомо: вы объясняли задачу 10 минут, сотрудник кивнул, а через два дня принёс совсем не то? "
            "Чаще всего сбой происходит в момент передачи задачи.\n\n"
            "Проверьте 4 точки перед тем, как закрыть диалог:\n"
            "1️⃣ Назовите конкретный образ результата.\n"
            "2️⃣ Зафиксируйте точный срок и приоритет.\n"
            "3️⃣ Обозначьте границы: что сотрудник решает сам, а где нужен ваш сигнал.\n"
            "4️⃣ Сверьте понимание одним вопросом.\n\n"
            "<blockquote>💬 Фраза, которая экономит часы переделок: «Как ты понял главный результат и с какого шага начнёшь сегодня?»</blockquote>\n\n"
            "⚠️ Не превращайте сверку в допрос — спрашивайте спокойно, как партнёр.\n\n"
            "👉 Сохраните этот чек-лист перед планёркой. Какой пункт у вас чаще выпадает в спешке — 1, 3 или 4?\n\n"
            "#управление #инструмент #руководитель"
        )

    if content_format == "mini_test":
        return (
            f"🧪 <b>{topic}: проверьте себя за 30 секунд</b>\n\n"
            "Иногда кажется, что вы просто «держите руку на пульсе», хотя на деле уже работаете за команду. "
            "Ответьте себе по итогам этой недели:\n\n"
            "1. Сотрудники приходят с вопросом «как сделать?», не предложив свой вариант?\n"
            "2. Вы правите чужие черновики вечером, потому что «проще переписать самому»?\n"
            "3. В ваше отсутствие мелкие решения в отделе встают на паузу?\n"
            "4. Вы просите ставить вас в копию писем, где не требуется ваше решение?\n\n"
            "<tg-spoiler>💡 Ключ к тесту: если вы ответили «да» хотя бы 2 раза — вы незаметно забрали у команды часть её ответственности. "
            "Завтра на первый же вопрос ответьте: «А какой вариант решения видишь ты?»</tg-spoiler>\n\n"
            "Сколько «да» насчитали у себя? Ставьте реакцию: 👍 — 0–1 / 🔥 — 2 и больше.\n\n"
            "#минитест #руководитель #команда"
        )

    if content_format == "weekly_reflection":
        return (
            f"☕ <b>{topic}</b>\n\n"
            "Воскресный вечер. Рабочий чат наконец молчит, но в голове всё ещё звучит привычное: «Надо не забыть в понедельник проверить всех с самого утра».\n\n"
            "Мы часто меряем неделю числом потушенных пожаров. Но зрелость руководителя видна в другом — какие решения команда приняла без вашего участия.\n\n"
            "<blockquote>Спросите себя перед новой неделей: за какую задачу вы переживали сильнее, чем человек, которому её поручили?</blockquote>\n\n"
            "Выберите завтра одну такую задачу. Вместо проверки статуса обсудите с сотрудником образ результата и границы его самостоятельности.\n\n"
            "А с какой задачи вы снимете лишний контроль уже в этот понедельник?\n\n"
            "#рефлексия #руководитель #лидерство"
        )

    if content_format == "dialogue_case":
        return (
            f"🎭 <b>{topic}</b>\n\n"
            "Пятница, 17:30. До сдачи проекта клиенту остаётся два часа, и в чате прилетает сообщение:\n"
            "— Сотрудник: «Я не успеваю доделать расчёт сегодня, там вылезли сложности».\n"
            "— Руководитель: «Почему ты молчал до вечера пятницы?! Ты должен был сказать вчера!»\n\n"
            "❌ <b>Ловушка первой реакции:</b> упрёк включает у сотрудника глухую защиту. "
            "Вы потеряете время на поиск виноватых, а в следующий раз проблему от вас будут прятать ещё дольше.\n\n"
            "✅ <b>Сильный ход руководителя:</b>\n"
            "<blockquote>«Принял. Какую часть мы точно можем отдать к 19:00, и что нужно от меня, чтобы закрыть остальное завтра к утру? А в понедельник разберём, где сработал стоп-кран».</blockquote>\n\n"
            "Как бы вы ответили в 17:30 пятницы: вариант 1 (разбор полётов) или вариант 2 (развилка решений)?\n\n"
            "#кейс #управление #команда"
        )

    if content_format == "management_mistake":
        return (
            f"⚠️ <b>{topic}</b>\n\n"
            "«Скинь мне перед отправкой, я быстро гляну одним глазом» — фраза, которую руководитель произносит из лучших побуждений, чтобы подстраховать команду.\n\n"
            "❌ <b>В чём ошибка:</b> превращать разовую подстраховку в обязательный фильтр для каждой рабочей мелочи.\n\n"
            "🤔 <b>Почему это кажется логичным:</b> вам нужно всего 3 минуты, чтобы заметить неточность и сберечь качество.\n\n"
            "⚠️ <b>Скрытая цена:</b> через месяц сотрудники перестают вычитывать свою работу перед сдачей — ведь руководитель всё равно проверит и поправит.\n\n"
            "✅ <b>Чем заменить:</b>\n"
            "<blockquote>Согласуйте 3 критерия готового результата на старте, а готовую работу проверяйте выборочно, возвращая на доработку без своих правок.</blockquote>\n\n"
            "Узнали у себя привычку «быстро глянуть»? 👍 — уже убрал / 🔥 — ещё борюсь.\n\n"
            "#ошибки #руководитель #управление"
        )

    if content_format == "psychology_story":
        return (
            f"🧠 <b>{topic}</b>\n\n"
            "Вторник, 21:15. В рабочем чате всплывает вопрос от специалиста. Рука руководителя тянется к телефону: «Сейчас отвечу за полминуты, чтобы человек с утра не стоял».\n\n"
            "Снаружи это выглядит как забота. Но внутри за мгновенными вечерними ответами часто прячется собственная тревога лидера — страх упустить контроль и ощущение, что без вас всё встанет.\n\n"
            "Парадокс в том, что круглосуточная доступность шефа тренирует у команды беспомощность: зачем думать самому, если ответ прилетит за минуту в любое время суток?\n\n"
            "<blockquote>Спокойствие команды строится на предсказуемых границах руководителя, а не на его ночном онлайне.</blockquote>\n\n"
            "Попробуйте отложить несрочный вечерний ответ до 9:30 утра. А вы часто отвечаете в рабочих чатах после 20:00?\n\n"
            "#психология #руководитель #лидерство"
        )

    return (
        f"🎯 <b>{topic}</b>\n\n"
        "Когда задача пробуксовывает второй раз подряд, первый порыв руководителя — закрутить гайки. "
        "Но жёсткий контроль в чате лечит только симптом, оставляя причину внутри процесса.\n\n"
        "🔎 <b>Что происходит:</b> чаще всего сбой возникает на стыке — сотрудник не видит критериев результата, "
        "боится сказать о риске вовремя или не имеет полномочий влиять на смежников.\n\n"
        "🛠 <b>Три шага для возврата управляемости:</b>\n"
        "1. Разделите на встрече сбой процесса и отношение человека.\n"
        "2. Спросите: «Какой главный риск по задаче ты видишь прямо сейчас?»\n"
        "3. Зафиксируйте одну точку сверки вместо ежедневного дёрганья.\n\n"
        "<blockquote>Сильный руководитель ищет не виноватого в сбое, а место, где застряло решение.</blockquote>\n\n"
        "⚠️ Если же нарушаются прямые договорённости — это уже вопрос дисциплины. "
        "А что чаще тормозит задачи у вас: размытый старт (👍) или замалчивание рисков (🔥)?\n\n"
        "#лидерство #руководитель #команда"
    )


# ============================================================
# UNSPLASH
# ============================================================

def get_unsplash_photo(rubric, content_format, visual_style="", media_mode="photo"):
    if media_mode != "photo":
        log.info("📰 Медиа-режим: text-only | стиль=%s", visual_style or "default")
        return None

    if not UNSPLASH_ACCESS_KEY:
        log.warning("⚠️ UNSPLASH_ACCESS_KEY не задан")
        return None

    profile = FORMAT_PROFILES.get(content_format, {})
    queries = profile.get("image_queries") or IMAGE_QUERIES.get(
        rubric,
        ["leadership business", "business team"],
    )

    visual_queries = {
        "quote_open": ["manager employee conversation", "executive speaking team", "business conversation"],
        "dialogue_open": ["manager employee conversation", "business discussion people", "team conversation office"],
        "minimal": ["executive desk notebook", "minimal office desk", "business workspace"],
        "checklist": ["business planning notebook", "project checklist office", "planning desk"],
        "contrast": ["business team meeting", "executive decision meeting", "office discussion"],
        "question_open": ["executive thinking office", "manager reflection", "business leadership portrait"],
    }
    query = random.choice(visual_queries.get(visual_style) or queries)

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

    max_len = (
        TELEGRAM_PHOTO_CAPTION_LIMIT
        if image
        else TELEGRAM_TEXT_LIMIT
    )

    caption = fit_caption(
        caption,
        max_len=max_len,
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
    """
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        log.info(
            "💾 topics_history.json обновлён — GitHub Actions сохранит его после публикации"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    print("🚀 TELEGRAM CONTENT ENGINE V9.0 (ENGAGEMENT EDITION)")
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

    visual_style = choose_visual_style(
        topic,
        content_format,
    )

    hook_mechanic = choose_hook_mechanic(
        topic,
        content_format,
    )

    engagement_mechanic = choose_engagement_mechanic(
        topic,
        content_format,
    )

    media_schedule = MEDIA_SCHEDULE_A if week_number % 2 else MEDIA_SCHEDULE_B
    media_mode = media_schedule[weekday]

    log.info(
        "📰 Медиа-режим: %s | неделя=%s | хук=%s | CTA=%s",
        MEDIA_MODES[media_mode]["name"],
        week_number,
        HOOK_MECHANICS[hook_mechanic]["name"],
        ENGAGEMENT_MECHANICS[engagement_mechanic]["name"],
    )

    generated, model = generate_with_failover(
        topic,
        rubric,
        content_format,
        style_variant=style_variant,
        content_angle=content_angle,
        visual_style=visual_style,
        media_mode=media_mode,
        hook_mechanic=hook_mechanic,
        engagement_mechanic=engagement_mechanic,
    )

    if generated:
        caption = sanitize_telegram_html(generated)
        fitted = fit_caption(
            caption,
            max_len=(
                TELEGRAM_TEXT_LIMIT
                if media_mode == "text"
                else TELEGRAM_PHOTO_CAPTION_LIMIT
            ),
        )

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
        visual_style=visual_style,
        media_mode=media_mode,
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
            visual_style,
            media_mode,
            hook_mechanic=hook_mechanic,
            engagement_mechanic=engagement_mechanic,
        )
        persist_history_to_git()

        log.info(
            "🎉 V9.0 завершил работу успешно | %s | %s | стиль=%s | хук=%s | CTA=%s | медиа=%s",
            profile["name"],
            topic,
            VISUAL_STYLE_PROFILES.get(visual_style, {}).get("name", visual_style),
            HOOK_MECHANICS.get(hook_mechanic, {}).get("name", hook_mechanic),
            ENGAGEMENT_MECHANICS.get(engagement_mechanic, {}).get("name", engagement_mechanic),
            MEDIA_MODES[media_mode]["name"],
        )

        return 0

    log.error("❌ Пост не опубликован")
    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
