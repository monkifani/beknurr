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
            # manager -> user, client -> model
            role = "user" if msg["role"] == "manager" else "model"
            text = msg["content"]
            
            # Пропускаем системные сообщения
            if msg["role"] == "system":
                continue
                
            contents.append(genai_types.Content(
                role=role,
                parts=[genai_types.Part.from_text(text=text)]
            ))
        
        # Gemini требует чтобы первым было user сообщение
        # Если история пустая или начинается с model — добавляем затравку
        if not contents or contents[0].role == "model":
            contents.insert(0, genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(
                    text=f"Менеджер хочет предложить вам: {niche}"
                )]
            ))
        
        # Убираем дублирующиеся роли подряд
        filtered = []
        for c in contents:
            if filtered and filtered[-1].role == c.role:
                # Если подряд одинаковые роли — пропускаем
                continue
            filtered.append(c)
        
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
        
        if not response.text:
            return "и что вы хотите предложить конкретно?"
            
        text = response.text.strip()
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'`+', '', text)
        return text[:300]
        
    except Exception as e:
        logger.error(f"AI client error: {e}", exc_info=True)
        return "и что вы хотите предложить конкретно?"


JUDGE_SYSTEM = """Ты Head of Sales. Оцени навыки менеджера по продажам.

Верни СТРОГО JSON (без markdown, без пояснений):

{
  "total_score": 0-100,
  "criteria": {
    "qualifying": 0-25,
    "value": 0-25,
    "objections": 0-25,
    "closing": 0-25
  },
  "verdict": "ЭЛИТА или ХОРОШО или СРЕДНЕ или СЛАБО",
  "strengths": "2-3 сильные стороны через запятую",
  "weaknesses": "2-3 слабые стороны через запятую",
  "red_flags": ["список критичных ошибок"],
  "question": "Вопрос для собеседования"
}

Критерии:
- qualifying (0-25): Выявил потребность, бюджет, сроки, decision maker?
- value (0-25): Объяснил ценность для клиента, а не просто фичи?
- objections (0-25): Как обработал возражения (если были)?
- closing (0-25): Назвал следующий конкретный шаг?

Будь строгим но справедливым."""

async def judge_simulation(history: list, niche: str) -> dict:
    try:
        dialogue = "\n".join([
            f"{'[Менеджер]' if m['role'] == 'manager' else '[Клиент]'}: {m['content']}"
            for m in history
            if m["role"] != "system"
        ])
        
        prompt = f"Продукт: {niche}\n\nДИАЛОГ:\n{dialogue}\n\nВерни JSON с оценкой."
        
        config = genai_types.GenerateContentConfig(
            temperature=0.2,
            system_instruction=JUDGE_SYSTEM,
            response_mime_type="application/json"
        )
        
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-1.5-flash",
            contents=prompt,
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
            "criteria": {"qualifying": 12, "value": 12, "objections": 13, "closing": 13},
            "verdict": "СРЕДНЕ",
            "strengths": "Вежлив, грамотно пишет",
            "weaknesses": "Не удалось проанализировать",
            "red_flags": [],
            "question": "Расскажите о вашем опыте продаж"
        }
