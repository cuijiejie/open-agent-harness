"""会话模型：chat_sessions 表 ORM 映射"""
import uuid
from typing import Any

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.infra.db.database import Base


class ChatSession(Base):
    """聊天会话模型"""
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint('session_id', 'username', name='uq_session_user'),
    )

    id: Column = Column(String(32), primary_key=True, index=True, default=lambda: uuid.uuid4().hex)
    session_id: Column = Column(String(32), nullable=False, index=True, comment="前端传入的会话ID")
    username: Column = Column(String(100), nullable=True)
    # 上下文治理：更早对话的滚动摘要（超预算时由 ContextService 生成并累加）
    summary: Column = Column(Text, nullable=True, comment="更早对话的摘要（上下文治理用）")
    summary_updated_at: Column = Column(DateTime(timezone=True), nullable=True)
    created_at: Column = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: Column = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # 归档：不删除数据，只从默认列表隐藏
    archived: Column = Column(Boolean, nullable=False, default=False, server_default="0", index=True)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")

    @staticmethod
    def generate_id() -> str:
        return uuid.uuid4().hex

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'session_id': self.session_id,
            'username': self.username,
            'summary': self.summary,
            'summary_updated_at': self.summary_updated_at.isoformat() if self.summary_updated_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
