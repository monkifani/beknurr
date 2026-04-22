import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, update
from config import async_session
from models import User, Simulation
from services.ai import get_client_reply, judge_simulation
import json
from datetime import datetime

logger = logging.getLogger(__name__)
router = Router()

MAX_STEPS = 6

class SimState(StatesGroup):
    waiting_niche = State()
    in_progress = State()

# Временное хранилище (FSM state_data)
# history, niche, step хранятся в state.data

@router.message(Command("sim"))
async def cmd_sim(m: types.Message, state: FSMContext):
    """Начать симуляцию"""
    await state.clear()
    
    # Проверка что пользователь в компании
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == m.from_user.id))
        user = result.scalar_one_or_none()
        
        if not user or not user.company_id:
            await m.answer("⚠️ Сначала присоединитесь к компании через /start")
            return
    
    await m.answer(
        "💼 <b>Что вы продаете?</b>\n\n"
        "Напишите конкретно, например:\n"
        "• CRM-система\n"
        "• Квартиры в новостройке\n"
        "• Страховые полисы",
        parse_mode="HTML"
    )
    await state.set_state(SimState.waiting_niche)

@router.message(SimState.waiting_niche)
async def start_simulation(m: types.Message, state: FSMContext):
    """Начало симуляции - первое сообщение клиента"""
    niche = m.text.strip()
    
    if len(niche) < 3:
        await m.answer("❌ Слишком коротко. Напишите полноценное название продукта.")
        return
    
    # Генерируем первое сообщение от клиента
    await m.answer("⏳ Клиент думает...")
    
    initial_history = [{"role": "system", "content": f"Начало диалога. Продукт: {niche}"}]
    first_message = await get_client_reply(initial_history, niche)
    
    history = [{"role": "client", "content": first_message}]
    
    # Сохраняем в state
    await state.update_data(
        niche=niche,
        history=history,
        step=0
    )
    
    await m.answer(
        f"🟢 <b>Симуляция началась!</b>\n\n"
        f"Вы продаете: <b>{niche}</b>\n"
        f"Клиент: Баке (инвестор)\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{first_message}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Напишите ваш ответ 👇",
        parse_mode="HTML"
    )
    
    await state.set_state(SimState.in_progress)

@router.message(SimState.in_progress, F.text)
async def process_message(m: types.Message, state: FSMContext):
    """Обработка сообщений менеджера"""
    text = m.text.strip()
    
    if len(text) < 5:
        await m.answer("❌ Слишком короткое сообщение. Пишите полноценные предложения.")
        return
    
    # Получаем данные из state
    data = await state.get_data()
    history = data.get("history", [])
    niche = data.get("niche", "продукт")
    step = data.get("step", 0) + 1
    
    # Добавляем сообщение менеджера
    history.append({"role": "manager", "content": text})
    
    # Проверяем, не финал ли
    if step >= MAX_STEPS:
        await finish_simulation(m, state, history, niche)
        return
    
    # Генерируем ответ клиента
    await m.answer(f"⏳ Реплика {step}/{MAX_STEPS}")
    client_reply = await get_client_reply(history, niche)
    
    history.append({"role": "client", "content": client_reply})
    
    # Обновляем state
    await state.update_data(
        history=history,
        step=step
    )
    
    # Прогресс-бар
    filled = "█" * step
    empty = "░" * (MAX_STEPS - step)
    progress_bar = f"[{filled}{empty}] {step}/{MAX_STEPS}"
    
    await m.answer(
        f"{progress_bar}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{client_reply}\n"
        f"━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML"
    )

async def finish_simulation(m: types.Message, state: FSMContext, history: list, niche: str):
    """Завершение симуляции и оценка"""
    await m.answer("🏁 <b>Симуляция завершена!</b>\n\n⏳ Аудитор анализирует диалог (10-15 сек)...", parse_mode="HTML")
    
    # Получаем оценку от AI
    result = await judge_simulation(history, niche)
    
    # Сохраняем в БД
    async with async_session() as session:
        sim = Simulation(
            user_id=m.from_user.id,
            company_id=(await session.execute(select(User.company_id).where(User.id == m.from_user.id))).scalar(),
            niche=niche,
            score=result["total_score"],
            verdict=json.dumps(result, ensure_ascii=False)
        )
        session.add(sim)
        await session.commit()
    
    # Формируем красивый отчет
    score = result["total_score"]
    verdict_emoji = {
        "ЭЛИТА": "🏆",
        "ХОРОШО": "✅",
        "СРЕДНЕ": "⚠️",
        "СЛАБО": "❌"
    }.get(result["verdict"], "📊")
    
    # Прогресс-бар для общего балла
    filled = int(score / 5)
    empty = 20 - filled
    score_bar = "█" * filled + "░" * empty
    
    report = (
        f"{verdict_emoji} <b>РЕЗУЛЬТАТ АУДИТА</b>\n\n"
        f"Общий балл: <b>{score}/100</b>\n"
        f"[{score_bar}]\n"
        f"Вердикт: <b>{result['verdict']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>📊 По критериям:</b>\n"
        f"• Квалификация: {result['criteria']['qualifying']}/25\n"
        f"• Ценность: {result['criteria']['value']}/25\n"
        f"• Возражения: {result['criteria']['objections']}/25\n"
        f"• Закрытие: {result['criteria']['closing']}/25\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>✅ Сильные стороны:</b>\n{result['strengths']}\n\n"
        f"<b>⚠️ Слабые стороны:</b>\n{result['weaknesses']}\n\n"
    )
    
    if result.get("red_flags"):
        flags = "\n".join([f"• {flag}" for flag in result["red_flags"]])
        report += f"<b>🚩 Красные флаги:</b>\n{flags}\n\n"
    
    report += (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>❓ Вопрос на собеседовании:</b>\n{result['question']}\n\n"
        f"Результат сохранен и отправлен руководству."
    )
    
    await m.answer(report, parse_mode="HTML")
    await state.clear()
    
    # Предложить новую симуляцию
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новая симуляция", callback_data="new_sim")]
    ])
    await m.answer("Хотите попробовать еще раз?", reply_markup=kb)

@router.callback_query(F.data == "new_sim")
async def new_sim(c: types.CallbackQuery, state: FSMContext):
    await c.message.delete()
    await cmd_sim(c.message, state)
