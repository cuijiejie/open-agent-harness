"""消息数据访问层：chat_messages 表的 CRUD 操作"""
from datetime import datetime, timezone

from src.infra.model.MessageModel import ChatMessage
from src.infra.db.database import get_db
from src.infra.utils.LogUtil import log


class MessageDao:
    @staticmethod
    def create_message(session_id: str, role: str, content: str,
                       options: str | None = None) -> ChatMessage:
        db = get_db()
        try:
            message = ChatMessage(
                id=ChatMessage.generate_id(),
                session_id=session_id,
                role=role,
                content=content,
                options=options,
                created_at=datetime.now(timezone.utc),
            )
            db.add(message)
            db.commit()
            log.info(f"创建消息成功: session_id={session_id}, role={role}")
            return message
        except Exception as e:
            db.rollback()
            # MySQL 偶发 2013 (Lost connection during query)：重连后重试一次，
            # 避免用户消息/回复因为一次网络抖动就丢掉
            try:
                from sqlalchemy.exc import OperationalError

                if isinstance(e, OperationalError):
                    log.warning(f"创建消息失败，重试一次: session_id={session_id}, err={e}")
                    message = ChatMessage(
                        id=ChatMessage.generate_id(),
                        session_id=session_id,
                        role=role,
                        content=content,
                        options=options,
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(message)
                    db.commit()
                    log.info(f"创建消息重试成功: session_id={session_id}, role={role}")
                    return message
            except Exception as e2:
                db.rollback()
                log.error(f"创建消息重试仍失败: {str(e2)}")
            log.error(f"创建消息失败: {str(e)}")
            raise

    @staticmethod
    def get_messages_by_session(session_id: str, limit: int = 0) -> list[ChatMessage]:
        """按**主键**查消息（session_id 是 chat_sessions.id，不是前端会话号）

        ⚠️ 不校验归属：只在明确知道自己要读哪个主键的地方用；
        按前端会话号取历史请用 SessionDao.get_recent_messages（它会先解析主键）。
        """
        db = get_db()
        try:
            q = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
            q = q.order_by(ChatMessage.created_at.asc())
            if limit and limit > 0:
                q = q.limit(limit)
            return q.all()
        except Exception as e:
            log.error(f"获取消息失败: {str(e)}")
            raise
