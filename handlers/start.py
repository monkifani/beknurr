from aiogram import Router, types, F
from aiogram.filters import Command

router = Router()

@router.message(Command("start"))
async def cmd_start(m: types.Message):
    await m.answer("🏢 SalesAI B2B\n\nБот в разработке. Сегодня настраиваем БД.")
