import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from config import async_session
from models import User, Simulation
from services.ai import get_client_reply, judge_simulation
import json

logger = logging.getLogger(__name__)
router = Router()
MAX_STEPS = 6


class SimState(StatesGroup):
    waiting_niche = State()
    in_progress = State()


@router.message(Command("sim"))
async def cmd_sim(m: types.Message, state: FSMContext):
    await state.clear()

    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == m.from_user.id))
        user = result.scalar_one_or_none()
        if not user or not user.company_id:
            await m.answer("⚠️ Сначала зарегистрируйтесь через /start")
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
    niche = m.text.strip()
    if len(niche) < 3:
        await m.answer("❌ Слишком коротко.")
        return

    await m.answer("⏳ Клиент думает...")

    # Формируем начальный контекст ПРАВИЛЬНО
    # Первым должен быть user (менеджер), чтобы Gemini мог ответить как model (клиент)
    seed_history = [
        {"role": "manager", "content": f"Добрый день, я хочу рассказать вам о {niche}"}
    ]
    first_message = await get_client_reply(seed_history, niche)

    # История для дальнейшего диалога
    history = [
        {"role": "manager", "content": f"Добрый день, я хочу рассказать вам о {niche}"},
        {"role": "client", "content": first_message}
    ]

    await state.update_data(niche=niche, history=history, step=0)

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
    text = m.text.strip()
    if len(text) < 5:
        await m.answer("❌ Слишком короткое сообщение.")
        return

    data = await state.get_data()
    history = data.get("history", [])
    niche = data.get("niche", "продукт")
    step = data.get("step", 0) + 1

    # Добавляем реплику менеджера
    history.append({"role": "manager", "content": text})

    # Финал
    if step >= MAX_STEPS:
        # Сохраняем перед финалом
        await state.update_data(history=history, step=step)
        await finish_simulation(m, state, history, niche)
        return

    # Ответ клиента
    await bot_typing(m)
    client_reply = await get_client_reply(history, niche)
    history.append({"role": "client", "content": client_reply})

    await state.update_data(history=history, step=step)

    filled = "█" * step
    empty = "░" * (MAX_STEPS - step)

    await m.answer(
        f"[{filled}{empty}] {step}/{MAX_STEPS}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{client_reply}\n"
        f"━━━━━━━━━━━━━━━━━━",
    )


async def bot_typing(m: types.Message):
    """Показывает 'печатает...' """
    try:
        await m.bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass


async def finish_simulation(m: types.Message, state: FSMContext, history: list, niche: str):
    await m.answer(
        "🏁 <b>Симуляция завершена!</b>\n\n"
        "⏳ Аудитор анализирует диалог...",
        parse_mode="HTML"
    )

    result = await judge_simulation(history, niche)

    # Сохраняем в БД
    try:
        async with async_session() as session:
            comp_result = await session.execute(
                select(User.company_id).where(User.id == m.from_user.id)
            )
            company_id = comp_result.scalar()

            sim = Simulation(
                user_id=m.from_user.id,
                company_id=company_id,
                niche=niche,
                score=result["total_score"],
                verdict=json.dumps(result, ensure_ascii=False)
            )
            session.add(sim)
            await session.commit()
    except Exception as e:
        logger.error(f"DB save error: {e}")

    # Формируем отчет
    score = result["total_score"]
    verdict_emoji = {
        "ЭЛИТА": "🏆",
        "ХОРОШО": "✅",
        "СРЕДНЕ": "⚠️",
        "СЛАБО": "❌"
    }.get(result["verdict"], "📊")

    filled = int(score / 5)
    empty = 20 - filled
    score_bar = "█" * filled + "░" * empty

    report = (
        f"{verdict_emoji} <b>РЕЗУЛЬТАТ АУДИТА</b>\n\n"
        f"Балл: <b>{score}/100</b>\n"
        f"[{score_bar}]\n"
        f"Вердикт: <b>{result['verdict']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>📊 По критериям:</b>\n"
        f"• Квалификация: {result['criteria']['qualifying']}/25\n"
        f"• Ценность: {result['criteria']['value']}/25\n"
        f"• Возражения: {result['criteria']['objections']}/25\n"
        f"• Закрытие: {result['criteria']['closing']}/25\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>✅ Сильные стороны:</b>\n"
        f"{result['strengths']}\n\n"
        f"<b>⚠️ Слабые стороны:</b>\n"
        f"{result['weaknesses']}\n\n"
    )

    if result.get("red_flags"):
        flags = "\n".join([f"• {flag}" for flag in result["red_flags"]])
        report += f"<b>🚩 Красные флаги:</b>\n{flags}\n\n"

    report += (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>❓ Вопрос на собеседовании:</b>\n"
        f"{result['question']}\n\n"
        f"✅ Результат сохранен."
    )

    await m.answer(report, parse_mode="HTML")
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новая симуляция", callback_data="new_sim")]
    ])
    await m.answer("Хотите попробовать еще раз?", reply_markup=kb)


@router.callback_query(F.data == "new_sim")
async def new_sim(c: types.CallbackQuery, state: FSMContext):
    await c.message.delete()
    await cmd_sim(c.message, state)
