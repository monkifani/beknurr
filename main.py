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

# === КОНФИГ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}" if RENDER_URL else None

if not TOKEN or not GEMINI_API_KEY:
    raise ValueError("Задай TELEGRAM_TOKEN и GEMINI_API_KEY")

# База в локальной папке (не требует диска)
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

MAX_STEPS = 6

# === БАЗА ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                niche TEXT,
                history TEXT,
                step INTEGER DEFAULT 0
            )
        """)
        await db.commit()
    logger.info("БД готова")

async def db_get(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT niche, history, step FROM sessions WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return {"niche": row[0], "history": json.loads(row[1]), "step": row[2]}
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

# === AI ===
CLIENT_SYS = """Ты клиент Баке (инвестор, 50 лет). Пиши как живой человек в WhatsApp: коротко, 1-2 предложения, без эмодзи, скептичный но вежливый. Выдавай только текст."""

async def ai_reply(history: list, niche: str) -> str:
    try:
        contents = []
        for msg in history:
            role = "user" if msg["role"] == "manager" else "model"
            contents.append(genai_types.Content(role=role, parts=[genai_types.Part.from_text(text=msg["content"])]))
        
        config = genai_types.GenerateContentConfig(temperature=0.8, system_instruction=CLIENT_SYS + f" Менеджер продает: {niche}.")
        resp = await asyncio.to_thread(gemini_client.models.generate_content, model="gemini-1.5-flash", contents=contents, config=config)
        return resp.text.strip() if resp.text else "..."
    except Exception as e:
        logger.error(f"AI error: {e}")
        return "Ошибка AI, попробуй еще раз."

# === FSM ===
class S(StatesGroup):
    menu = State()
    niche = State()
    dialogue = State()

# === ХЕНДЛЕРЫ ===
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await db_clear(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Начать", callback_data="sim")]])
    await m.answer("<b>SalesAI Demo</b>\n\nДиалог с AI-клиентом (Баке). После 6 реплик — оценка.", reply_markup=kb, parse_mode="HTML")
    await state.set_state(S.menu)

@dp.callback_query(F.data == "sim")
async def cb_sim(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text("Что продаете? (например: CRM, квартиры, курсы):")
    await state.set_state(S.niche)

@dp.message(S.niche)
async def msg_niche(m: types.Message, state: FSMContext):
    niche = m.text.strip()
    if len(niche) < 3:
        return await m.answer("Слишком коротко")
    
    await m.answer(f"🟢 Симуляция началась!\n\nВы: {niche}\n\nКлиент на связи. Пишите первое сообщение:")
    
    # Первый ответ клиента
    history = [{"role": "manager", "content": f"Начало диалога. Продукт: {niche}"}]
    first = await ai_reply(history, niche)
    history = [{"role": "client", "content": first}]
    
    await db_save(m.from_user.id, niche, history, 0)
    await m.answer(first)
    await state.set_state(S.dialogue)

@dp.message(S.dialogue)
async def msg_dialogue(m: types.Message, state: FSMContext):
    text = m.text.strip()
    if len(text) < 3:
        return await m.answer("Слишком короткое сообщение")
    
    session = await db_get(m.from_user.id)
    if not session:
        await m.answer("Сессия потеряна. /start")
        return await state.clear()
    
    history = session["history"]
    step = session["step"] + 1
    niche = session["niche"]
    
    history.append({"role": "manager", "content": text})
    await bot.send_chat_action(m.chat.id, "typing")
    
    if step >= MAX_STEPS:
        await finish(m, history, niche)
        await state.clear()
        return
    
    reply = await ai_reply(history, niche)
    history.append({"role": "client", "content": reply})
    await db_save(m.from_user.id, niche, history, step)
    await m.answer(reply)

async def finish(m: types.Message, history: list, niche: str):
    await m.answer("🏁 Симуляция завершена!")
    # Здесь можно добавить оценку, пока просто завершаем
    await db_clear(m.from_user.id)
    await m.answer("Нажмите /start для новой попытки")

# === WEB SERVER ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info(f"Webhook: {WEBHOOK_URL}")
    yield
    await bot.delete_webhook()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return JSONResponse({"ok": True})

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = types.Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return JSONResponse({"ok": True})
