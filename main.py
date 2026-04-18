import os
import asyncio
import logging
import re
import time
import json
import aiosqlite
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from google import genai
from google.genai import types as genai_types

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}" if RENDER_URL else None

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY не задан!")


DATA_DIR = Path("/var/data")
DB_PATH = DATA_DIR / "bot.db"


gemini_client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

MAX_STEPS = 6

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

async def init_db():
     try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        logger.error(f"Нет прав на {DATA_DIR}. Проверь mountPath в render.yaml")
        raise
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                niche    TEXT,
                history  TEXT,
                step     INTEGER DEFAULT 0
            )
        """)
        await db.commit()
    logger.info("БД инициализирована: %s", DB_PATH)

async def db_get(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT niche, history, step FROM sessions WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "niche":   row[0],
                    "history": json.loads(row[1]),
                    "step":    row[2],
                }
    return None

async def db_save(user_id: int, niche: str, history: list, step: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO sessions (user_id, niche, history, step)
            VALUES (?, ?, ?, ?)
        """, (user_id, niche, json.dumps(history, ensure_ascii=False), step))
        await db.commit()

async def db_clear(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()

# ============================================================
# AI
# ============================================================

CLIENT_SYSTEM = """Ты клиент в мессенджере. Роль: Баке, инвестор 50 лет.
Пиши как живой человек в WhatsApp:
- 1-2 предложения
- без эмодзи
- без списков
- без звездочек
- маленькая буква можно
- скептичный, но не грубый
- если менеджер говорит по делу — интерес растет
- если льет воду — реагируй холодно
Выдавай только текст сообщения."""

JUDGE_SYSTEM = """Ты эксперт по продажам. Оцени навыки менеджера.

Критерии (макс 15 баллов):
1. Выявление потребности (0-3)
2. Аргументация ценности (0-3)  
3. Работа с возражениями (0-3)
4. Закрытие на следующий шаг (0-3)
5. Стиль и грамотность (0-3)

Формат ответа СТРОГО такой:
БАЛЛ: [число от 0 до 15]
ВЕРДИКТ: [ЭЛИТА / ХОРОШО / СРЕДНЕ / СЛАБО]
РАЗБОР: [3-5 предложений что хорошо и что плохо]
ВОПРОС: [один вопрос кандидату на собеседовании]"""


async def ai_reply(history: list, niche: str) -> str:
    """Ответ клиента на реплику менеджера."""
    try:
        # Строим историю в формате Gemini
        contents = []
        for msg in history:
            role = "user" if msg["role"] == "manager" else "model"
            contents.append(
                genai_types.Content(
                    role=role,
                    parts=[genai_types.Part.from_text(text=msg["content"])]
                )
            )

        config = genai_types.GenerateContentConfig(
            temperature=0.8,
            system_instruction=CLIENT_SYSTEM + f"\n\nМенеджер продает: {niche}."
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.0-flash",
            contents=contents,
            config=config,
        )
        text = clean(response.text)
        return text if text else "ну не знаю"
    except Exception as e:
        logger.error("ai_reply error: %s", e)
        return f"❌ Ошибка AI: {e}"


async def ai_judge(history: list, niche: str) -> str:
    """Финальная оценка менеджера."""
    try:
        dialogue = "\n".join([
            f"{'Менеджер' if m['role'] == 'manager' else 'Клиент'}: {m['content']}"
            for m in history
        ])
        prompt = f"Продукт: {niche}\n\nДиалог:\n{dialogue}"

        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=JUDGE_SYSTEM
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.0-flash",
            contents=prompt,
            config=config,
        )
        return response.text.strip()
    except Exception as e:
        logger.error("ai_judge error: %s", e)
        return f"❌ Ошибка оценки: {e}"


def clean(text: str) -> str:
    """Убирает артефакты из ответа AI."""
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'`+', '', text)
    text = text.replace('—', '-').replace('«', '"').replace('»', '"')
    text = re.sub(r' +', ' ', text)
    return text.strip()

# ============================================================
# FSM
# ============================================================

class S(StatesGroup):
    menu     = State()
    niche    = State()
    dialogue = State()

# ============================================================
# ХЕНДЛЕРЫ
# ============================================================

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать симуляцию", callback_data="sim")],
    ])


@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await db_clear(m.from_user.id)
    await m.answer(
        "<b>SalesAI Demo</b>\n\n"
        "Потренируйся продавать AI-клиенту.\n"
        "Клиент — скептичный инвестор Баке.\n"
        "После 6 реплик получишь оценку аудитора.",
        reply_markup=main_kb(),
        parse_mode="HTML"
    )
    await state.set_state(S.menu)


@dp.message(Command("cancel"))
async def cmd_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await db_clear(m.from_user.id)
    await m.answer("Симуляция отменена.", reply_markup=main_kb())
    await state.set_state(S.menu)


@dp.callback_query(F.data == "sim")
async def cb_sim(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text(
        "💼 <b>Что вы продаете?</b>\n\n"
        "Напишите нишу, например:\n"
        "• CRM-система\n"
        "• Квартиры в новостройке\n"
        "• Онлайн-курс по продажам",
        parse_mode="HTML"
    )
    await state.set_state(S.niche)


@dp.message(S.niche)
async def msg_niche(m: types.Message, state: FSMContext):
    niche = m.text.strip()
    if len(niche) < 3:
        await m.answer("Слишком коротко. Напишите нормально, например: страховки, квартиры, CRM.")
        return

    await m.answer(
        f"🟢 <b>Симуляция началась!</b>\n\n"
        f"Вы продаете: <b>{niche}</b>\n"
        f"Клиент: Баке (инвестор)\n\n"
        f"Напишите первое сообщение клиенту 👇",
        parse_mode="HTML"
    )

    # Начальный контекст — клиент "открывает" чат
    history = [{"role": "manager", "content": f"[Менеджер начал диалог. Продукт: {niche}]"}]
    opening = await ai_reply(history, niche)
    history = [{"role": "client", "content": opening}]

    await db_save(m.from_user.id, niche, history, 0)
    await m.answer(opening)
    await state.set_state(S.dialogue)


@dp.message(S.dialogue)
async def msg_dialogue(m: types.Message, state: FSMContext):
    text = m.text.strip()
    if len(text) < 3:
        await m.answer("Слишком коротко. Напишите полноценное сообщение.")
        return

    session = await db_get(m.from_user.id)
    if not session:
        await m.answer("Сессия не найдена. Нажмите /start")
        await state.clear()
        return

    history = session["history"]
    niche   = session["niche"]
    step    = session["step"] + 1

    # Добавляем реплику менеджера
    history.append({"role": "manager", "content": text})

    # Показываем прогресс
    await m.answer(f"Реплика {step} из {MAX_STEPS}")
    await bot.send_chat_action(m.chat.id, "typing")

    if step >= MAX_STEPS:
        # Финал — запускаем оценку параллельно
        await db_save(m.from_user.id, niche, history, step)
        await finish(m, state, history, niche)
        return

    # Ответ клиента
    reply = await ai_reply(history, niche)
    history.append({"role": "client", "content": reply})
    await db_save(m.from_user.id, niche, history, step)
    await m.answer(reply)


async def finish(m: types.Message, state: FSMContext, history: list, niche: str):
    await m.answer("🏁 <b>Симуляция завершена!</b>\n\n⏳ Аудитор анализирует диалог...", parse_mode="HTML")

    verdict = await ai_judge(history, niche)

    # Парсим балл
    score_match = re.search(r'БАЛЛ:\s*(\d+)', verdict)
    score = int(score_match.group(1)) if score_match else 0
    score = max(0, min(15, score))
    bar = "█" * score + "░" * (15 - score)

    result_text = (
        f"📊 <b>РЕЗУЛЬТАТ АУДИТА</b>\n\n"
        f"Балл: <b>{score}/15</b>\n"
        f"[{bar}]\n\n"
        f"{verdict}"
    )

    await m.answer(result_text, parse_mode="HTML")
    await db_clear(m.from_user.id)
    await state.clear()
    await m.answer("Нажмите кнопку для новой симуляции:", reply_markup=main_kb())
    await state.set_state(S.menu)

# ============================================================
# FASTAPI
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info("Webhook: %s", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_URL не задан — бот не будет отвечать!")
    yield
    await bot.delete_webhook()
    await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return JSONResponse({"status": "ok", "service": "SalesAI"})


@app.head("/")
async def root_head():
    # Render шлет HEAD / для health check
    return JSONResponse({"status": "ok"})


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = types.Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error("Webhook error: %s", e)
    return JSONResponse({"ok": True})


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "db": str(DB_PATH), "webhook": WEBHOOK_URL})
