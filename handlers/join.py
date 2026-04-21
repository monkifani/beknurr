from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from config import async_session
from models import Company, User

router = Router()

class JoinState(StatesGroup):
    waiting_code = State()

@router.callback_query(F.data == "join_company")
async def ask_code(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text("Введите код компании (6 символов):")
    await state.set_state(JoinState.waiting_code)

@router.message(JoinState.waiting_code)
async def process_code(m: types.Message, state: FSMContext):
    code = m.text.strip().upper()
    
    async with async_session() as session:
        result = await session.execute(select(Company).where(Company.code == code))
        company = result.scalar_one_or_none()
        
        if not company:
            await m.answer("❌ Неверный код. Попробуйте еще раз.")
            return
        
        # Создаем пользователя
        user = User(
            id=m.from_user.id,
            full_name=m.from_user.full_name,
            company_id=company.id,
            role="manager"
        )
        session.add(user)
        await session.commit()
        
        await m.answer(f"✅ Вы присоединились к компании <b>{company.name}</b>!", parse_mode="HTML")
    
    await state.clear()
