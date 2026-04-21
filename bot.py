import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from config import TOKEN, WEBHOOK_URL, engine, Base
from handlers import start, join

# Регистрация роутеров
dp = Dispatcher()
dp.include_router(start.router)
dp.include_router(join.router)

bot = Bot(token=TOKEN)

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
