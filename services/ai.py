import asyncio
import logging
import json
import re
from google import genai
from google.genai import types as genai_types
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)
client = genai.Client(api_key=GEMINI_API_KEY)

# === ПРОМПТ ДЛЯ КЛИЕНТА ===
CLIENT_SYSTEM = """Ты Баке, инвестор 50 лет. Общаешься в Telegram.

Твой характер:
- Занятой, скептичный, но вежливый
- Не сдаешься сразу, требуешь конкретики
- Если менеджер задает вопросы — отвечаешь честно
- Если давит или льет воду — сопротивляешься

Как пишешь:
- 1-2 коротких предложения
- Без эмодзи, без списков, без звездочек
- Маленькие буквы можно
- Как живой человек в WhatsApp

Выдавай ТОЛЬКО текст сообщения, без пояснений."""

async def get_client_reply(history: list, niche: str) -> str:
    """Генерирует ответ AI-клиента"""
    try:
        # Берем последние 8 сообщений для контекста (не перегружаем)
        recent = history[-8:]
        
        contents = []
        for msg in recent:
            role = "user" if msg["role"] == "manager" else "model"
            contents.append(genai_types.Content(
                role=role,
                parts=[genai_types.Part.from_text(text=msg["content"])]
            ))
        
        config = genai_types.GenerateContentConfig(
            temperature=0.8,
            system_instruction=f"{CLIENT_SYSTEM}\n\nМенеджер продает: {niche}"
        )
        
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-1.5-flash",
            contents=contents,
            config=config
        )
        
        text = response.text.strip() if response.text else "..."
        # Очистка от артефактов
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'`+', '', text)
        return text[:300]  # Ограничение длины ответа
        
    except Exception as e:
        logger.error(f"AI client error: {e}")
        return "не понял, можете конкретнее?"

# === ПРОМПТ ДЛЯ СУДЬИ ===
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

Будь строгим но справедливым. Если менеджер не задавал вопросов — низкий балл."""

async def judge_simulation(history: list, niche: str) -> dict:
    """Оценивает диалог и возвращает структурированный результат"""
    try:
        dialogue = "\n".join([
            f"{'[Менеджер]' if m['role'] == 'manager' else '[Клиент]'}: {m['content']}"
            for m in history
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
        
        # Валидация результата
        if not isinstance(result.get("total_score"), int):
            raise ValueError("Invalid score")
        
        return result
        
    except Exception as e:
        logger.error(f"Judge error: {e}")
        # Fallback результат при ошибке
        return {
            "total_score": 50,
            "criteria": {
                "qualifying": 12,
                "value": 12,
                "objections": 13,
                "closing": 13
            },
            "verdict": "СРЕДНЕ",
            "strengths": "Вежлив, грамотно пишет",
            "weaknesses": "Технические проблемы с оценкой",
            "red_flags": [],
            "question": "Расскажите о вашем опыте продаж"
        }
