from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from config import async_session
from models import Company, User
import random
import string

router = Router()

class RegState(StatesGroup):
    waiting_name = State()

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@router.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    
    async with async_session() as session:
        # Проверяем, есть ли пользователь в БД
        result = await session.execute(select(User).where(User.id == m.from_user.id))
        user = result.scalar_one_or_none()
        
        if user and user.company_id:
            # Уже в компании
            await m.answer(f"Вы в компании. Нажмите /sim для симуляции")
        else:
            # Новый пользователь
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏢 Создать компанию", callback_data="create_company")],
                [InlineKeyboardButton(text="🔗 Присоединиться", callback_data="join_company")]
            ])
            await m.answer("Добро пожаловать! Выберите действие:", reply_markup=kb)

@router.callback_query(F.data == "create_company")
async def create_company(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text("Введите название вашей компании:")
    await state.set_state(RegState.waiting_name)

@router.message(RegState.waiting_name)
async def save_company(m: types.Message, state: FSMContext):
    company_name = m.text.strip()
    if len(company_name) < 2:
        await m.answer("Слишком короткое название")
        return
    
    code = generate_code()
    
    async with async_session() as session:
        # Создаем компанию
        company = Company(name=company_name, code=code)
        session.add(company)
        await session.flush()  # Получаем company.id
        
        # Создаем пользователя-админа
        user = User(
            id=m.from_user.id,
            full_name=m.from_user.full_name,
            company_id=company.id,
            role="admin"
        )
        session.add(user)
        await session.commit()
    
    await m.answer(
        f"✅ Компания <b>{company_name}</b> создана!\n\n"
        f"Код для менеджеров: <code>{code}</code>\n\n"
        f"Отправьте этот код менеджерам, чтобы они присоединились.",
        parse_mode="HTML"
    )
    await state.clear()
