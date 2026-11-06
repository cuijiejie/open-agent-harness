"""每轮对话的可观测指标（不引入外部依赖）

记录什么（每轮一条，JSONL 追加）：
    ts            时间
    user/session  谁、哪个会话
    model         模型名
    duration_s    本轮耗时
    content_chars / reasoning_chars / tool_calls   输出体量
    tool_errors   工具报错次数
    tokens_in / tokens_out   模型返回的 usage（DeepSeek 会带；没有则为 null）

为什么单独一个服务而不是塞日志：
日志是给人翻的，指标是给接口/看板聚合的。JSONL 一行一条，`/api/metrics/summary`
直接按天聚合，不需要引入 Prometheus/LangSmith 这类外部依赖。
注意：**不记录对话内容**（只记长度），避免在指标里泄露业务数据。

LangSmith（可选，配了才启用）：
在 `[observability]` 里配 `langsmith_tracing=true` + `langsmith_api_key`，
应用启动时会把 LANGCHAIN_* 环境变量设好，LangChain 自动上报 trace。
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from src.infra.utils.LogUtil import log

# 指标与 trace 统一落在 <仓库根>/logs（原来指标按文件位置算、trace 按 cwd 算，
# 换个工作目录就写到两个地方去了）
_LOG_DIR: str = str(Path(__file__).resolve().parents[2] / "logs")
_METRICS_FILE: str = os.path.join(_LOG_DIR, "turn_metrics.jsonl")
_LOCK = threading.Lock()
_RECENT: deque = deque(maxlen=500)   # 进程内最近 500 轮（多 worker 各存各的，summary 会合并文件）


def record_turn(**fields) -> None:
    """记录一轮对话的指标（失败绝不影响对话）"""
    try:
        row: dict = {"ts": datetime.now().isoformat(timespec="seconds")}
        row.update({k: v for k, v in fields.items() if v is not None})
        with _LOCK:
            _RECENT.append(row)
            os.makedirs(_LOG_DIR, exist_ok=True)
            with open(_METRICS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"写指标失败（忽略）: {e}")


def summary(days: int = 1) -> dict:
    """按天聚合（读 JSONL；文件不大，直接全读过滤）"""
    since: datetime = datetime.now() - timedelta(days=max(1, days))
    rows: list[dict] = []
    try:
        if os.path.isfile(_METRICS_FILE):
            with open(_METRICS_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if datetime.fromisoformat(r.get("ts", "")) >= since:
                            rows.append(r)
                    except Exception:
                        continue
    except Exception as e:
        log.warning(f"读指标失败: {e}")

    turns: int = len(rows)
    if not turns:
        return {"days": days, "turns": 0, "note": "该区间暂无数据"}

    def _sum(key: str) -> int:
        return sum(int(r.get(key) or 0) for r in rows)

    tok_in: int = _sum("tokens_in")
    tok_out: int = _sum("tokens_out")
    dur: float = sum(float(r.get("duration_s") or 0) for r in rows)
    tool_calls: int = _sum("tool_calls")
    tool_errors: int = _sum("tool_errors")
    by_day: dict[str, int] = {}
    for r in rows:
        d: str = str(r.get("ts", ""))[:10]
        by_day[d] = by_day.get(d, 0) + 1
    users: dict[str, int] = {}
    for r in rows:
        u: str = str(r.get("user") or "-")
        users[u] = users.get(u, 0) + 1

    return {
        "days": days,
        "turns": turns,
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "avg_duration_s": round(dur / turns, 2),
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "tool_error_rate": round(tool_errors / tool_calls, 3) if tool_calls else 0.0,
        "avg_tool_calls_per_turn": round(tool_calls / turns, 2),
        "turns_by_day": dict(sorted(by_day.items())),
        "turns_by_user": dict(sorted(users.items(), key=lambda kv: -kv[1])[:10]),
        # 注意：recent 必须从**文件**取（gunicorn 多 worker 各存各的内存队列，取内存会漏）
        "recent": rows[-10:],
    }


def enable_langsmith(cfg) -> bool:
    """按配置开启 LangSmith（必须在 langchain 首次 import 之前调用）"""
    try:
        if not bool(getattr(cfg, "langsmithTracing", False)):
            return False
        key: str = str(getattr(cfg, "langsmithApiKey", "") or "").strip()
        if not key:
            log.warning("langsmith_tracing=true 但未配置 langsmith_api_key，跳过")
            return False
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = key
        os.environ["LANGCHAIN_PROJECT"] = str(getattr(cfg, "langsmithProject", "open-agent-harness") or "open-agent-harness")
        os.environ["LANGCHAIN_ENDPOINT"] = str(
            getattr(cfg, "langsmithEndpoint", "https://api.smith.langchain.com") or "https://api.smith.langchain.com"
        )
        log.info(f"LangSmith 已启用: project={os.environ['LANGCHAIN_PROJECT']}")
        return True
    except Exception as e:
        log.warning(f"启用 LangSmith 失败（忽略）: {e}")
        return False


__all__ = ["record_turn", "summary", "enable_langsmith"]


# ======================= Trace（每轮一条，含每个工具 span） =======================
# 为什么自己写而不直接上 OpenTelemetry：
#   生产机在内网、装不了 otel-sdk（无 PyPI），所以这里产出 **OTel 兼容字段**
#   （trace_id / span 名 / 起止毫秒 / attributes），落 JSONL，随时可以用脚本或
#   collector 二次导入；等环境允许装 otel 时，只需把 record_trace 换成 span.end()。

import json as _json
import os as _os
import threading as _threading
import time as _time
import uuid as _uuid

_TRACE_LOCK = _threading.Lock()
_TRACE_RING: list = []          # 最近 N 条，供接口查询（进程内）
_TRACE_RING_MAX: int = 500
_TRACE_DIR: str = _LOG_DIR


def record_trace(user: str, session: str, model: str, duration_s: float,
                 tools: list | None = None, tokens_in: int | None = None,
                 tokens_out: int | None = None, budget_hit: bool = False,
                 error: str = "", note: dict | None = None) -> dict:
    """记录一轮对话的 trace（不含对话内容，只记行为与耗时）"""
    now_ms = int(_time.time() * 1000)
    rec: dict = {
        "trace_id": _uuid.uuid4().hex,
        "kind": "turn",
        "user": user,
        "session": session,
        "model": model,
        "start_ms": now_ms - int((duration_s or 0) * 1000),
        "end_ms": now_ms,
        "duration_ms": int((duration_s or 0) * 1000),
        "tool_count": len(tools or []),
        "tool_error_count": sum(1 for t in (tools or []) if t.get("status") == "error"),
        "tools": tools or [],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "budget_hit": bool(budget_hit),
        "error": (error or "")[:300],
        "note": note or {},
    }
    with _TRACE_LOCK:
        _TRACE_RING.append(rec)
        if len(_TRACE_RING) > _TRACE_RING_MAX:
            del _TRACE_RING[: len(_TRACE_RING) - _TRACE_RING_MAX]
    try:
        _os.makedirs(_TRACE_DIR, exist_ok=True)
        path = _os.path.join(_TRACE_DIR, f"trace-{_time.strftime('%Y%m%d')}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


_TAIL_CACHE: dict = {}          # (date, limit, session) -> (ts, rows)
_TAIL_CACHE_TTL: float = 2.0    # 秒；吸收"反复切标签"造成的重复读


def _read_tail(path: str, want: int, chunk: int = 64 * 1024) -> list:
    """从文件末尾反向读，只解析需要的最后 want 行（不把整文件读进内存）

    复杂度 O(want)，与文件大小无关：100MB 的文件和 2KB 的文件一样快。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, _os.SEEK_END)
            size = f.tell()
            data = b""
            pos = size
            need = want + 1
            while pos > 0 and data.count(b"\n") < need:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except Exception:
        return []
    lines = [ln for ln in data.split(b"\n") if ln.strip()]
    out = []
    for ln in lines[-want:]:
        try:
            out.append(_json.loads(ln.decode("utf-8", "ignore")))
        except Exception:
            continue
    return out


def recent_traces(limit: int = 50, session: str = "") -> list:
    """最近 N 条 trace（新→旧）。session 非空时只返回该会话的。"""
    day = _time.strftime("%Y%m%d")
    key = (day, limit, session or "")
    now = _time.time()
    hit = _TAIL_CACHE.get(key)
    if hit and now - hit[0] < _TAIL_CACHE_TTL:
        return hit[1]

    path = _os.path.join(_TRACE_DIR, f"trace-{day}.jsonl")
    # 需要多取一些再按会话过滤（一个会话在一批里可能是稀疏的）
    want = limit if not session else min(2000, max(limit * 20, 200))
    rows = _read_tail(path, want) if _os.path.exists(path) else []
    rows.reverse()                                  # 新→旧
    if session:
        rows = [r for r in rows if str(r.get("session") or "") == session]
    rows = rows[:limit]

    if not rows:                                    # 兜底：内存环形缓冲
        with _TRACE_LOCK:
            rows = list(reversed(_TRACE_RING[-limit:]))
            if session:
                rows = [r for r in rows if str(r.get("session") or "") == session]

    _TAIL_CACHE[key] = (now, rows)
    if len(_TAIL_CACHE) > 64:
        for k in list(_TAIL_CACHE.keys())[:32]:
            _TAIL_CACHE.pop(k, None)
    return rows


def trace_stats(limit: int = 200, rows: list | None = None) -> dict:
    """近 N 条的聚合：轮数/平均耗时/工具失败率/平均 token（读文件，跨 worker 一致）

    rows 非空时**只聚合这批行**（供普通用户"只看自己"的场景用，避免泄露全站数据）。
    """
    if rows is None:
        rows = list(reversed(recent_traces(limit)))     # 已带 2s 缓存，不会重复读文件
    if not rows:
        return {"turns": 0}
    n = len(rows)
    dur = sorted(r.get("duration_ms") or 0 for r in rows)
    tools = sum(r.get("tool_count") or 0 for r in rows)
    errs = sum(r.get("tool_error_count") or 0 for r in rows)
    tin = [r["tokens_in"] for r in rows if r.get("tokens_in")]
    tout = [r["tokens_out"] for r in rows if r.get("tokens_out")]
    return {
        "turns": n,
        "avg_duration_ms": int(sum(dur) / n),
        "p95_duration_ms": dur[max(0, int(n * 0.95) - 1)],
        "tool_calls": tools,
        "tool_errors": errs,
        "tool_error_rate": round(errs / tools, 4) if tools else 0,
        "avg_tokens_in": int(sum(tin) / len(tin)) if tin else 0,
        "avg_tokens_out": int(sum(tout) / len(tout)) if tout else 0,
        "budget_hits": sum(1 for r in rows if r.get("budget_hit")),
        "errors": sum(1 for r in rows if r.get("error")),
    }
