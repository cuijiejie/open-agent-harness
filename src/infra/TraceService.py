"""执行轨迹（Trace）持久化

与 pa_audit_log 的分工：
  pa_audit_log  → 合规审计：只记写/执行类操作，参数存摘要，长期保留
  pa_trace_log  → 执行轨迹：每一步都记（用户输入/模型思考/工具调用/结果），
                  参数与结果基本存全文（截断保护），保留 90 天

为什么落 MySQL 而不是 jsonl：
  - jsonl 只能顺序读，无法按会话/工具/状态/时间段筛选，也无法翻页
  - 「管理员看全部、用户只看当前会话」需要在 SQL 层做权限过滤
  - 每天几百行，一年十几万行，MySQL 毫无压力

设计取舍：
  - 轨迹写入**绝不抛异常**（失败只告警），不能因为记录拖垮对话
  - 参数/结果做长度截断但仍保留尾部（错误信息通常在尾部）
  - 按 (username, created_at) 与 (session_id, created_at) 建索引，
    覆盖「我的轨迹」和「某人某会话」两种主要查询
"""
import json
from datetime import datetime, timedelta

from src.infra.utils.LogUtil import log

MAX_ARGS = 4000
MAX_RESULT = 8000
RETENTION_DAYS = 90

_TABLE = """
CREATE TABLE IF NOT EXISTS pa_trace_log (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  username    VARCHAR(64)  NOT NULL DEFAULT ''  COMMENT '用户（域账号）',
  session_id  VARCHAR(64)  NOT NULL DEFAULT ''  COMMENT '会话号',
  turn_no     INT          NOT NULL DEFAULT 0   COMMENT '第几轮',
  step_no     INT          NOT NULL DEFAULT 0   COMMENT '轮内第几步',
  kind        VARCHAR(16)  NOT NULL DEFAULT ''  COMMENT 'user/model/tool/error',
  name        VARCHAR(128) NOT NULL DEFAULT ''  COMMENT '工具名或事件名',
  args        MEDIUMTEXT                        COMMENT '参数（JSON，截断 4000）',
  result      MEDIUMTEXT                        COMMENT '结果（截断 8000）',
  status      VARCHAR(16)  NOT NULL DEFAULT 'ok' COMMENT 'ok/fail/denied',
  elapsed_ms  INT          NOT NULL DEFAULT 0   COMMENT '耗时（毫秒）',
  risk        VARCHAR(16)  NOT NULL DEFAULT ''  COMMENT '风险等级 write/exec/read',
  created_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_user_time    (username, created_at),
  KEY idx_session_time (session_id, created_at),
  KEY idx_kind_time    (kind, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='执行轨迹表：逐步骤记录'
"""

_READY = False
# 自动清理标记：每天最多触发一次（原来 purge 只能管理员手动点，没人点 = 永不清理）
_PURGE_MARK: dict = {"day": ""}


def _maybe_purge() -> None:
    try:
        day: str = datetime.now().strftime("%Y-%m-%d")
        if _PURGE_MARK.get("day") == day:
            return
        _PURGE_MARK["day"] = day
        import threading

        threading.Thread(target=purge, kwargs={"days": RETENTION_DAYS}, daemon=True).start()
    except Exception:
        pass


def _conn():
    """从连接池借一条连接（见 infra/utils/dbconn.py）

    用完 `conn.close()` 是**归还到池**，不是真的关连接 —— 所以下面的函数
    仍然保持 `try/finally: conn.close()` 的写法，出错时先 invalidate 再归还。
    """
    from src.infra.utils.dbconn import get_conn

    global _READY
    c = get_conn()
    if not _READY:
        with c.cursor() as cur:
            cur.execute(_TABLE)
        c.commit()
        _READY = True
    return c


def _clip(s, n: int) -> str:
    """截断但保留尾部（错误信息常在最后）"""
    t = s if isinstance(s, str) else str(s or "")
    if len(t) <= n:
        return t
    head = t[: int(n * 0.6)]
    tail = t[-int(n * 0.35):]
    return f"{head}\n…（省略 {len(t) - n} 字）…\n{tail}"


def record(username: str, session_id: str, kind: str, name: str = "", args="", result="",
           status: str = "ok", elapsed_ms: int = 0, risk: str = "",
           turn_no: int = 0, step_no: int = 0) -> None:
    """写一条轨迹。**绝不抛异常**。"""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pa_trace_log (username, session_id, turn_no, step_no, kind, name, "
                    "args, result, status, elapsed_ms, risk) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(username or "")[:64], str(session_id or "")[:64], int(turn_no or 0),
                     int(step_no or 0), str(kind or "")[:16], str(name or "")[:128],
                     _clip(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str), MAX_ARGS),
                     _clip(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str), MAX_RESULT),
                     str(status or "ok")[:16], int(elapsed_ms or 0), str(risk or "")[:16]),
                )
            conn.commit()
        except Exception:
            # 连接已坏：回滚 + 标记作废（别把坏连接还回池里）
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.invalidate()
            except Exception:
                pass
            raise
        finally:
            conn.close()          # 归还到池
        # 写成功顺便做一次"每天最多一次"的过期清理（90 天保留策略的兜底执行者）
        _maybe_purge()
    except Exception as e:
        log.warning(f"轨迹写入失败（忽略）: {e}")


def query(username: str = "", session_id: str = "", kind: str = "", status: str = "",
          tool: str = "", hours: int = 0, write_only: bool = False,
          page: int = 1, size: int = 100) -> dict:
    """按条件查轨迹（新→旧）。调用方负责权限：普通用户必须传 username + session_id。"""
    try:
        conn = _conn()
        try:
            where = ["1=1"]
            params: list = []
            if username:
                where.append("username = %s")
                params.append(username)
            if session_id:
                where.append("session_id = %s")
                params.append(session_id)
            if kind:
                where.append("kind = %s")
                params.append(kind)
            if status:
                where.append("status = %s")
                params.append(status)
            if tool:
                where.append("name LIKE %s")
                params.append(f"%{tool}%")
            if hours and hours > 0:
                where.append("created_at >= %s")
                params.append(datetime.now() - timedelta(hours=int(hours)))
            if write_only:
                where.append("risk IN ('write','exec')")

            cond = " AND ".join(where)
            page = max(1, int(page or 1))
            size = min(500, max(1, int(size or 100)))
            offset = (page - 1) * size

            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM pa_trace_log WHERE {cond}", params)
                total = int((cur.fetchone() or [0])[0] or 0)
                cur.execute(
                    f"SELECT id, username, session_id, turn_no, step_no, kind, name, args, result, "
                    f"status, elapsed_ms, risk, created_at FROM pa_trace_log WHERE {cond} "
                    f"ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [size, offset],
                )
                cols = ["id", "username", "session_id", "turn_no", "step_no", "kind", "name",
                        "args", "result", "status", "elapsed_ms", "risk", "created_at"]
                rows = []
                for r in cur.fetchall() or []:
                    d = dict(zip(cols, r))
                    if isinstance(d.get("created_at"), datetime):
                        d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    # 参数解析成 dict 便于前端展开；解析不了就给原文
                    try:
                        d["args_obj"] = json.loads(d.get("args") or "{}")
                    except Exception:
                        d["args_obj"] = None
                    rows.append(d)
            return {"total": total, "page": page, "size": size, "rows": rows}
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.invalidate()
            except Exception:
                pass
            raise
        finally:
            conn.close()          # 归还到池
    except Exception as e:
        log.error(f"轨迹查询失败: {e}", exc_info=True)
        return {"total": 0, "page": page, "size": size, "rows": [], "error": str(e)}


def purge(days: int = RETENTION_DAYS) -> int:
    """清理过期轨迹，返回删除条数。"""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pa_trace_log WHERE created_at < %s",
                            (datetime.now() - timedelta(days=int(days or RETENTION_DAYS)),))
                n = cur.rowcount
            conn.commit()
            return int(n or 0)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.invalidate()
            except Exception:
                pass
            raise
        finally:
            conn.close()          # 归还到池
    except Exception as e:
        log.warning(f"轨迹清理失败: {e}")
        return 0


def delete_by_session(session_id: str, username: str = "") -> int:
    """物理删除某会话的全部轨迹（删会话时调用）。

    username 非空时额外限定用户，防止越权删别人的轨迹。
    pa_audit_log **不删**——那是合规留痕，会话删除不影响审计证据。
    """
    try:
        if not session_id:
            return 0
        conn = _conn()
        try:
            with conn.cursor() as cur:
                if username:
                    cur.execute("DELETE FROM pa_trace_log WHERE session_id = %s AND username = %s",
                                (str(session_id)[:64], str(username)[:64]))
                else:
                    cur.execute("DELETE FROM pa_trace_log WHERE session_id = %s", (str(session_id)[:64],))
                n = cur.rowcount
            conn.commit()
            log.info(f"删除会话轨迹: session={session_id}, user={username or '*'}, 条数={n}")
            return int(n or 0)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.invalidate()
            except Exception:
                pass
            raise
        finally:
            conn.close()          # 归还到池
    except Exception as e:
        log.warning(f"删除会话轨迹失败: {e}")
        return 0
