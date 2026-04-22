import asyncio
import logging
import json
import re
from google import genai
from google.genai import types as genai_types
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)
client = genai.Client(api_key=GEMINI_API_KEY)

CLIENT_SYSTEM = """Ты Баке, инвестор 50 лет. Общаешься в Telegram.
Характер: занятой, скептичный, но вежливый. Требуешь конкретики.
Если менеджер задает вопросы — отвечаешь честно.
Если давит или льет воду — сопротивляешься.
Пиши: 1-2 коротких предложения, без эмодзи, без списков, без звездочек.
Выдавай ТОЛЬКО текст сообщения."""


async def get_client_reply(history: list, niche: str) -> str:
    try:
        contents = []

        for msg in history[-8:]:
            if msg["role"] == "system":
                continue
            # manager = user, client = model
            role = "user" if msg["role"] == "manager" else "model"
            contents.append(genai_types.Content(
                role=role,
                parts=[genai_types.Part.from_text(text=msg["content"])]
            ))

        # Gemini требует: первое сообщение = user, чередование user/model
        # Убираем подряд идущие одинаковые роли
        filtered = []
        for c in contents:
            if filtered and filtered[-1].role == c.role:
                continue
            filtered.append(c)

        # Первым всегда должен быть user
        if not filtered or filtered[0].role != "user":
            filtered.insert(0, genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(
                    text=f"Здравствуйте, хочу рассказать о {niche}"
                )]
            ))

        config = genai_types.GenerateContentConfig(
            temperature=0.8,
            system_instruction=f"{CLIENT_SYSTEM}\n\nМенеджер продает: {niche}"
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-1.5-flash",
            contents=filtered,
            config=config
        )

        if not response or not response.text:
            return "и что конкретно вы предлагаете?"

        text = response.text.strip()
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'`+', '', text)
        return text[:300]

    except Exception as e:
        logger.error(f"AI client error: {e}", exc_info=True)
        return "и что конкретно вы предлагаете?"


JUDGE_SYSTEM = """Ты Head of Sales. Оцени навыки менеджера по продажам.

Верни СТРОГО JSON (без markdown, без пояснений):

{
  "total_score": 75,
  "criteria": {
    "qualifying": 20,
    "value": 18,
    "objections": 20,
    "closing": 17
  },
  "verdict": "ХОРОШО",
  "strengths": "Задавал вопросы, слушал клиента",
  "weaknesses": "Не назвал следующий шаг, не уточнил бюджет",
  "red_flags": ["Не закрыл на встречу", "Не выяснил бюджет"],
  "question": "Как вы обычно закрываете сделку на следующий шаг?"
}

Критерии:
- qualifying (0-25): Выявил потребность, бюджет, сроки?
- value (0-25): Объяснил ценность для клиента?
- objections (0-25): Обработал возражения?
- closing (0-25): Назвал следующий конкретный шаг?"""


async def judge_simulation(history: list, niche: str) -> dict:
    try:
        dialogue = "\n".join([
            f"{'[Менеджер]' if m['role'] == 'manager' else '[Клиент]'}: {m['content']}"
            for m in history
            if m["role"] != "system"
        ])

        config = genai_types.GenerateContentConfig(
            temperature=0.2,
            system_instruction=JUDGE_SYSTEM,
            response_mime_type="application/json"
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-1.5-flash",
            contents=f"Продукт: {niche}\n\nДИАЛОГ:\n{dialogue}",
            config=config
        )

        result = json.loads(response.text)

        if not isinstance(result.get("total_score"), int):
            raise ValueError("Invalid score")

        return result

    except Exception as e:
        logger.error(f"Judge error: {e}", exc_info=True)
        return {
            "total_score": 50,
            "criteria": {
                "qualifying": 12,
                "value": 12,
                "objections": 13,
                "closing": 13
            },
            "verdict": "СРЕДНЕ",
            "strengths": "Вежлив, пытался объяснить продукт",
            "weaknesses": "Не выявил потребности, не закрыл на шаг",
            "red_flags": [],
            "question": "Расскажите о вашем опыте продаж"
        }
