import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from config import TOKEN, WEBHOOK_URL, engine
from models import Base
import handlers.start  # импортируем для регистрации хендлеров

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Регистрируем роутеры
dp.include_router(handlers.start.router)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
    yield
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = types.Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
