from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Text
from sqlalchemy.sql import func
from config import Base

class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    code = Column(String(10), unique=True)  # Код для приглашения
    created_at = Column(DateTime, server_default=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)  # Telegram ID
    full_name = Column(String(100))
    company_id = Column(Integer, ForeignKey("companies.id"))
    role = Column(String(20), default="manager")  # admin/manager
    created_at = Column(DateTime, server_default=func.now())

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    niche = Column(String(200))
    score = Column(Integer)  # 0-100
    criteria = Column(Text)  # JSON с баллами по критериям
    verdict = Column(Text)   # Текст вердикта
    created_at = Column(DateTime, server_default=func.now())
