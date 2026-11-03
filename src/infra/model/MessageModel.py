"""消息模型：chat_messages 表 ORM 映射"""
import uuid
from typing import Any

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.infra.db.database import Base


class ChatMessage(Base):
    """聊天消息模型"""
    __tablename__ = "chat_messages"

    id: Column = Column(String(32), primary_key=True, index=True)
    session_id: Column = Column(String(32), ForeignKey("chat_sessions.id"), nullable=False)
    role: Column = Column(String(20), nullable=False)
    content: Column = Column(Text, nullable=False)
    options: Column = Column(Text, nullable=True, default=None)  # ask_user 选项 JSON（{"question":..., "options":[...]}），刷新后恢复复选框
    created_at: Column = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    @staticmethod
    def generate_id() -> str:
        return uuid.uuid4().hex

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'options': self.options,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
