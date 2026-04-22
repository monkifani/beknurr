import asyncio
from google import genai
from google.genai import types as genai_types
from config import GEMINI_API_KEY
import json
import re

client = genai.Client(api_key=GEMINI_API_KEY)

# Промпт для "клиента" (время отклика ~1-2 сек)
CLIENT_PROMPT = """Ты Баке, инвестор 50 лет. Ты в Telegram.
Правила:
- Пиши 1-2 коротких предложения
- Без эмодзи, без списков, без "*"
- Ты занятой, скептичный, но вежливый
- Если менеджер задает вопросы — отвечай. Если давит — сопротивляйся
- Не соглашайся сразу, торгуйся или откладывай решение"""

async def get_client_reply(history: list, niche: str) -> str:
    """Получает ответ AI-клиента"""
    try:
        contents = []
        for msg in history[-6:]:  # Последние 6 сообщений для контекста
            role = "user" if msg["role"] == "manager" else "model"
            contents.append(genai_types.Content(
                role=role, 
                parts=[genai_types.Part.from_text(text=msg["content"])]
            ))
        
        config = genai_types.GenerateContentConfig(
            temperature=0.8,
            system_instruction=CLIENT_PROMPT + f"\n\nМенеджер продает: {niche}"
        )
        
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-1.5-flash",
            contents=contents,
            config=config
        )
        return resp.text.strip() if resp.text else "Напишите конкретнее"
    except Exception as e:
        return f"Ошибка: {str(e)[:50]}"

# Промпт для "судьи" (структурированная оценка)
JUDGE_PROMPT = """Ты эксперт по продажам (Head of Sales). Оцени диалог менеджера.
Проанализируй по критериям и верни ТОЛЬКО JSON:

{
  "total_score": 0-100,
  "criteria": {
    "qualifying": 0-25,
    "value_proposition": 0-25, 
    "objection_handling": 0-25,
    "closing": 0-25
  },
  "verdict": "Один из: ЭЛИТА / ХОРОШО / СРЕДНЕ / СЛАБО",
  "strengths": "2-3 сильные стороны",
  "weaknesses": "2-3 слабые стороны",
  "red_flags": ["список ошибок, например: Не выяснил бюджет"],
  "interview_question": "Вопрос для реального собеседования"
}

Критерии:
- qualifying: Выявил ли потребности, бюджет, сроки?
- value_proposition: Объяснил ли ценность, а не просто фичи?
- objection_handling: Как работал с возражениями?
- closing: Назвал ли следующий шаг?"""

async def judge_dialogue(history: list, niche: str) -> dict:
    """Возвращает структурированную оценку"""
    try:
        dialogue_text = "\n".join([
            f"{'Менеджер' if m['role'] == 'manager' else 'Клиент'}: {m['content']}" 
            for m in history
        ])
        
        config = genai_types.GenerateContentConfig(
            temperature=0.2,  # Низкая температура для стабильности оценки
            system_instruction=JUDGE_PROMPT,
            response_mime_type="application/json"  # Заставляем выдать JSON
        )
        
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-1.5-flash",
            contents=f"Продукт: {niche}\n\nДиалог:\n{dialogue_text}",
            config=config
        )
        
        # Парсим JSON
        result = json.loads(resp.text)
        return result
        
    except Exception as e:
        # Fallback если AI не вернул JSON
        return {
            "total_score": 50,
            "criteria": {"qualifying": 10, "value_proposition": 10, "objection_handling": 10, "closing": 20},
            "verdict": "ОШИБКА ОЦЕНКИ",
            "strengths": "Не удалось проанализировать",
            "weaknesses": str(e),
            "red_flags": [],
            "interview_question": "Расскажите о вашем опыте продаж"
        }
