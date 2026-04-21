from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, BigInteger
from sqlalchemy.sql import func
from config import Base

class Company(Base):
    __tablename__ = "companies"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    code = Column(String(10), unique=True, nullable=False)  # Код для менеджеров
    created_at = Column(DateTime, server_default=func.now())
    
    def __repr__(self):
        return f"<Company {self.name}>"

class User(Base):
    __tablename__ = "users"
    
    id = Column(BigInteger, primary_key=True)  # Telegram ID
    full_name = Column(String(100))
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    role = Column(String(20), default="manager")  # admin/manager
    created_at = Column(DateTime, server_default=func.now())

class Simulation(Base):
    __tablename__ = "simulations"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    niche = Column(String(200))
    score = Column(Integer)  # 0-100
    verdict = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
