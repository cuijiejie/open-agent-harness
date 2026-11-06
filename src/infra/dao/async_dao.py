"""会话 / 消息数据访问层（**异步**，FastAPI 侧专用）

为什么是独立模块：Flask 侧的 `SessionDao` / `MessageDao` 绑在 `flask.g` 的同步
Session 上，两边不能共用同一个类。P3 阶段两套并存；P2 删掉 Flask 后这份是唯一实现。

用法（必须在 async 上下文）：`await SessionDao.list_sessions(...)`
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError

from src.infra.db.database import get_db
from src.infra.model.MessageModel import ChatMessage
from src.infra.model.SessionModel import ChatSession
from src.infra.utils.LogUtil import log


class SessionDao:
    @staticmethod
    async def belongs_to(session_id: str, username: str) -> bool:
        """该会话是否属于这个用户（产出文件下发前的归属校验，纵深防御）"""
        db = get_db()
        try:
            row = (await db.execute(
                select(ChatSession).where(
                    ChatSession.session_id == session_id,
                    ChatSession.username == username,
                )
            )).scalars().first()
            return row is not None
        except Exception as e:
            log.warning(f"会话归属校验失败: session_id={session_id}, err={e}")
            return False

    @staticmethod
    async def get_or_create_session(session_id: str, username: str) -> ChatSession:
        db = get_db()
        try:
            session = (await db.execute(
                select(ChatSession).where(
                    ChatSession.session_id == session_id,
                    ChatSession.username == username,
                )
            )).scalars().first()

            if not session:
                session = ChatSession(
                    session_id=session_id,
                    username=username,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(session)
                await db.commit()
                log.info(f"创建会话成功: session_id={session_id}, username={username}")
            else:
                log.info(f"会话已存在: session_id={session_id}, username={username}")

            # 兜底：主键必须拿到。并发/回滚之后对象可能处于过期状态，`id` 读出来是 None，
            # 上层拿它写 chat_messages 就会撞外键
            # （生产日志里见过：Cannot add or update a child row ... chat_messages_ibfk_1）
            if session is not None and getattr(session, "id", None) is None:
                await db.refresh(session)
                if getattr(session, "id", None) is None:
                    session = (await db.execute(
                        select(ChatSession).where(
                            ChatSession.session_id == session_id,
                            ChatSession.username == username,
                        )
                    )).scalars().first()
                    log.warning(f"会话主键为空，已重新查询: session_id={session_id}, "
                                f"id={getattr(session, 'id', None)}")
            return session
        except IntegrityError:
            await db.rollback()
            session = (await db.execute(
                select(ChatSession).where(
                    ChatSession.session_id == session_id,
                    ChatSession.username == username,
                )
            )).scalars().first()
            if session:
                try:
                    await db.refresh(session)   # 回滚后对象可能已过期，刷新拿真实主键
                except Exception:
                    pass
                log.info(f"并发冲突, 回退查询成功: session_id={session_id}, username={username}")
                return session
            raise
        except Exception as e:
            await db.rollback()
            log.error(f"获取或创建会话失败: {str(e)}")
            raise

    @staticmethod
    async def get_session(session_id: str) -> ChatSession | None:
        db = get_db()
        try:
            return (await db.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            )).scalars().first()
        except Exception as e:
            log.error(f"获取会话失败: {str(e)}")
            raise

    @staticmethod
    async def get_messages_by_frontend_id(session_id: str, username: str) -> list[dict[str, Any]]:
        """根据前端 session_id 获取消息列表"""
        db = get_db()
        try:
            chat_session = (await db.execute(
                select(ChatSession).where(
                    ChatSession.session_id == session_id,
                    ChatSession.username == username,
                )
            )).scalars().first()
            if not chat_session:
                return []
            messages = (await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session.id)
                .order_by(ChatMessage.created_at.asc())
            )).scalars().all()
            return [m.to_dict() for m in messages]
        except Exception as e:
            log.error(f"获取消息列表失败: {str(e)}")
            raise

    @staticmethod
    async def list_sessions(username: str, page: int = 1, page_size: int = 20,
                            archived: bool = False) -> dict[str, Any]:
        """分页列出用户的会话列表，含首条用户消息作为标题（子查询消除 N+1）"""
        db = get_db()
        try:
            where = (ChatSession.username == username, ChatSession.archived == bool(archived))

            total: int = int((await db.execute(
                select(func.count()).select_from(ChatSession).where(*where)
            )).scalar_one() or 0)

            sessions = (await db.execute(
                select(ChatSession)
                .where(*where)
                .order_by(desc(ChatSession.updated_at))
                .offset((page - 1) * page_size)
                .limit(page_size)
            )).scalars().all()

            # 批量获取首条消息：子查询取每 session 最早 user 消息
            session_ids = [s.id for s in sessions]
            first_msgs: dict[str, str] = {}
            if session_ids:
                subq = (
                    select(
                        ChatMessage.session_id,
                        func.min(ChatMessage.created_at).label("min_time"),
                    )
                    .where(
                        ChatMessage.session_id.in_(session_ids),
                        ChatMessage.role == "user",
                    )
                    .group_by(ChatMessage.session_id)
                    .subquery()
                )
                rows = (await db.execute(
                    select(ChatMessage.session_id, ChatMessage.content)
                    .join(subq, (ChatMessage.session_id == subq.c.session_id)
                          & (ChatMessage.created_at == subq.c.min_time))
                )).all()
                first_msgs = {str(row[0]): (row[1] or "") for row in rows}

            result = []
            for s in sessions:
                content = first_msgs.get(str(s.id), "")
                title = content[:20] if content else "新对话"
                result.append({
                    "id": s.id,
                    "session_id": s.session_id,
                    "title": title,
                    "username": s.username,
                    "created_at": s.created_at.replace(tzinfo=timezone.utc).isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.replace(tzinfo=timezone.utc).isoformat() if s.updated_at else None,
                })

            return {"data": result, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            log.error(f"获取会话列表失败: {str(e)}")
            raise

    @staticmethod
    async def get_summary(session_id: str, username: str = "") -> str:
        """取会话的滚动摘要（上下文治理用；没有则返回空串）

        username 非空时必须限定归属：唯一键是 (session_id, username)，同一个前端
        session_id 可能被多个用户各建一行；不带 username 会读到别人的摘要。
        """
        db = get_db()
        try:
            stmt = select(ChatSession).where(ChatSession.session_id == session_id)
            if username:
                stmt = stmt.where(ChatSession.username == username)
            stmt = stmt.order_by(desc(ChatSession.created_at)).limit(1)
            row = (await db.execute(stmt)).scalars().first()
            return (row.summary or "") if row else ""
        except Exception as e:
            log.warning(f"读取会话摘要失败: session_id={session_id}, err={e}")
            return ""

    @staticmethod
    async def save_summary(session_id: str, summary: str, username: str = "") -> None:
        """保存/更新会话的滚动摘要

        **必须带 username**：否则一次 UPDATE 会命中同一 session_id 的所有行，
        把别人会话的摘要一起覆盖掉（跨用户内容串台）。
        """
        db = get_db()
        try:
            stmt = update(ChatSession).where(ChatSession.session_id == session_id)
            if username:
                stmt = stmt.where(ChatSession.username == username)
            await db.execute(stmt.values(
                summary=summary,
                summary_updated_at=datetime.now(timezone.utc),
            ))
            await db.commit()
        except Exception as e:
            await db.rollback()
            log.warning(f"保存会话摘要失败: session_id={session_id}, user={username}, err={e}")

    @staticmethod
    async def get_recent_messages(session_id: str, username: str = "", limit: int = 12) -> list:
        """按**前端会话号**取最近 N 条消息（新→旧再反转）

        注意：chat_messages.session_id 存的是 chat_sessions.id（主键），不是前端
        会话号 —— 直接拿前端 session_id 去查永远查不到，必须先解析出主键。
        """
        db = get_db()
        try:
            stmt = select(ChatSession).where(ChatSession.session_id == session_id)
            if username:
                stmt = stmt.where(ChatSession.username == username)
            stmt = stmt.order_by(desc(ChatSession.created_at)).limit(1)
            sess = (await db.execute(stmt)).scalars().first()
            if not sess:
                return []
            rows = (await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == sess.id)
                .order_by(desc(ChatMessage.created_at))
                .limit(limit)
            )).scalars().all()
            return list(rows)[::-1]
        except Exception as e:
            log.warning(f"获取最近消息失败: session_id={session_id}, err={e}")
            return []

    @staticmethod
    async def update_session_time(chat_session: ChatSession) -> None:
        """更新会话的 updated_at 时间（用于列表排序）"""
        db = get_db()
        try:
            await db.execute(
                update(ChatSession)
                .where(ChatSession.id == chat_session.id)
                .values(updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            log.error(f"更新会话时间失败: {str(e)}")
            raise

    @staticmethod
    async def delete_session(session_id: str, username: str) -> bool:
        """删除会话及其所有消息（级联删除）"""
        db = get_db()
        try:
            session = (await db.execute(
                select(ChatSession).where(
                    ChatSession.session_id == session_id,
                    ChatSession.username == username,
                )
            )).scalars().first()
            if not session:
                return False
            await db.delete(session)
            await db.commit()
            log.info(f"删除会话成功: session_id={session_id}, username={username}")
            return True
        except Exception as e:
            await db.rollback()
            log.error(f"删除会话失败: {str(e)}")
            raise


class MessageDao:
    @staticmethod
    async def create_message(session_id: str, role: str, content: str,
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
            await db.commit()
            log.info(f"创建消息成功: session_id={session_id}, role={role}")
            return message
        except Exception as e:
            await db.rollback()
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
                    await db.commit()
                    log.info(f"创建消息重试成功: session_id={session_id}, role={role}")
                    return message
            except Exception as e2:
                await db.rollback()
                log.error(f"创建消息重试仍失败: {str(e2)}")
            log.error(f"创建消息失败: {str(e)}")
            raise

    @staticmethod
    async def get_messages_by_session(session_id: str, limit: int = 0) -> list[ChatMessage]:
        """按**主键**查消息（session_id 是 chat_sessions.id，不是前端会话号）

        ⚠️ 不校验归属：只在明确知道自己要读哪个主键的地方用；
        按前端会话号取历史请用 SessionDao.get_recent_messages（它会先解析主键）。
        """
        from sqlalchemy import select

        db = get_db()
        try:
            stmt = (select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc()))
            if limit and limit > 0:
                stmt = stmt.limit(limit)
            return list((await db.execute(stmt)).scalars().all())
        except Exception as e:
            log.error(f"获取消息失败: {str(e)}")
            raise
