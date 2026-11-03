"""操作审计

记什么：**写操作与执行操作**（risk = write / exec），以及所有被拒绝的调用。
不记什么：查询类（read）——量大且无追责价值，轨迹里已有。

用途：
  1. 出事可查：谁在什么时候改了什么产品、结果如何
  2. 异常发现：某人一天改了几十个产品、大量被拒的调用
  3. 合规举证

设计取舍：
  - 参数只存**摘要**（≤500 字），不存全文，避免把敏感数据抄两遍
  - 写库失败绝不能影响业务流程（全部 try/except）
  - 查询只给管理员
"""
import json
import re
from datetime import datetime

from src.infra.utils.LogUtil import log

MAX_PARAM_LEN = 500
_TABLE = """
CREATE TABLE IF NOT EXISTS pa_audit_log (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  ts             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '发生时间',
  username       VARCHAR(64) NOT NULL DEFAULT ''   COMMENT '操作人（域账号）',
  session_id     VARCHAR(64) NOT NULL DEFAULT ''   COMMENT '会话号',
  tool_name      VARCHAR(64) NOT NULL DEFAULT ''   COMMENT '工具名',
  risk           VARCHAR(16) NOT NULL DEFAULT ''   COMMENT '风险等级：write/exec',
  params_summary VARCHAR(500) NOT NULL DEFAULT ''  COMMENT '参数摘要（截断）',
  result         VARCHAR(16) NOT NULL DEFAULT ''   COMMENT 'ok / error / rejected',
  detail         VARCHAR(500) NOT NULL DEFAULT ''  COMMENT '结果摘要或错误信息',
  elapsed_ms     INT NOT NULL DEFAULT 0            COMMENT '耗时（毫秒）',
  trace_id       VARCHAR(64) NOT NULL DEFAULT ''   COMMENT '关联的轨迹 ID',
  KEY idx_ts (ts),
  KEY idx_user_ts (username, ts),
  KEY idx_tool_ts (tool_name, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作审计表：写操作与执行操作的留痕'
"""

_READY = False
# 敏感参数名，值要打码
_SECRET_KEYS = ("password", "passwd", "token", "secret", "apikey", "api_key", "身份证", "银行卡")


def _conn():
    """取本线程复用的连接（见 infra/utils/dbconn.py；这里不 close）"""
    from src.infra.utils.dbconn import get_conn

    global _READY
    c = get_conn()
    if not _READY:
        with c.cursor() as cur:
            cur.execute(_TABLE)
        c.commit()
        _READY = True
    return c


def _mask(value, depth: int = 0):
    """递归打码：敏感键的值置为 ***，字符串里的 `password=xxx` 也替换掉

    以前只匹配**顶层** key，嵌套结构（env/headers/嵌套参数）里的 token/password
    会原样进 pa_audit_log，而审计表是长期保留、可被导出的。
    """
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(s in str(k).lower() for s in _SECRET_KEYS):
                out[k] = "***"
            else:
                out[k] = _mask(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_mask(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def _mask_text(text: str) -> str:
    """自由文本里的 `password=xxx` / `token: xxx` 打码"""
    try:
        return re.sub(
            r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)\b(\s*[=:]\s*)(\S+)",
            r"\1\2***",
            str(text or ""),
        )
    except Exception:
        return str(text or "")


def _summarize(params) -> str:
    """参数摘要：递归打码 + 截断"""
    try:
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                return _mask_text(params)[:MAX_PARAM_LEN]
        safe = _mask(params)
        out = json.dumps(safe, ensure_ascii=False, default=str)
        return out if len(out) <= MAX_PARAM_LEN else out[:MAX_PARAM_LEN] + "…"
    except Exception:
        return _mask_text(params)[:MAX_PARAM_LEN]


def record(username: str, tool_name: str, risk: str, params, result: str = "ok",
           detail: str = "", elapsed_ms: int = 0, session_id: str = "", trace_id: str = "") -> None:
    """写一条审计。**绝不抛异常**——审计失败不影响业务。"""
    try:
        if str(risk).lower() not in ("write", "exec") and result != "rejected":
            return   # 只记写/执行，以及被拒的调用
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pa_audit_log (username, session_id, tool_name, risk, "
                    "params_summary, result, detail, elapsed_ms, trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (username or "", session_id or "", tool_name or "", str(risk or "")[:16],
                     _summarize(params), (result or "")[:16], _mask_text(detail)[:500],
                     int(elapsed_ms or 0), trace_id or ""),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.invalidate()   # 坏连接别还回池里
            except Exception:
                pass
            raise
        finally:
            conn.close()            # 归还到池
    except Exception as e:
        log.warning(f"审计写入失败（忽略）: {e}")


def query(username: str = "", tool: str = "", result: str = "",
          start: str = "", end: str = "", limit: int = 100, offset: int = 0) -> dict:
    """查审计（管理员用）。不带 username 表示查全部。"""
    where, args = ["1=1"], []
    if username:
        where.append("username=%s"); args.append(username)
    if tool:
        where.append("tool_name LIKE %s"); args.append(f"%{tool}%")
    if result:
        where.append("result=%s"); args.append(result)
    if start:
        where.append("ts >= %s"); args.append(start)
    if end:
        where.append("ts <= %s"); args.append(end)
    w = " AND ".join(where)

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM pa_audit_log WHERE {w}", args)
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT id, ts, username, session_id, tool_name, risk, params_summary, "
                f"result, detail, elapsed_ms FROM pa_audit_log WHERE {w} "
                f"ORDER BY id DESC LIMIT %s OFFSET %s",
                args + [min(int(limit), 500), int(offset)],
            )
            rows = cur.fetchall()
        cols = ["id", "ts", "username", "session_id", "tool_name", "risk",
                "params_summary", "result", "detail", "elapsed_ms"]
        items = []
        for r in rows:
            d = dict(zip(cols, r))
            d["ts"] = d["ts"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if isinstance(d["ts"], datetime) else str(d["ts"])
            items.append(d)
        return {"items": items, "total": total}
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
        conn.close()            # 归还到池


__all__ = ["record", "query"]
