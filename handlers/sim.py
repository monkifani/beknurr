# handlers/sim.py - Черновик для следующего шага
from aiogram import Router, F
from aiogram.fsm.state import State, StatesGroup

router = Router()

class SimState(StatesGroup):
    in_progress = State()  # Ждем сообщения менеджера
    # step считаем из БД, не из FSM (надежнее при перезапуске)

@router.message(F.text, SimState.in_progress)
async def process_message(m: Message):
    # 1. Достать историю из БД (async_session)
    # 2. Добавить сообщение менеджера
    # 3. Если step >= 6: вызвать judge_dialogue() и сохранить результат
    # 4. Иначе: вызвать get_client_reply() и отправить ответ
    pass
