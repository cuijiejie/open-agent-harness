"""通用缓存（Redis）

统一入口，四个用途共用：
  cache:tools:meta         内置工具元数据（pa_tools 全表）
  cache:skills:<user>      某用户的技能清单
  cache:rbac:catalog       角色/工具目录
  cache:prefs:<user>       用户偏好 render_hint 结果

铁律（四个用途都遵守）：
  1. 读：Redis 有就用；没有 → 查库 → 写 Redis
  2. 写：**先落库，成功后 DEL 对应 key**（不是覆盖写，避免并发互相盖）
  3. TTL 兜底：万一某次 DEL 失败，最迟 TTL 秒后自动纠正
  4. Redis 不可用：一律返回 None，调用方走数据库（绝不因缓存挂掉业务）
"""
import json
from typing import Any

from src.infra.utils.LogUtil import log

DEFAULT_TTL: int = 300

try:
    from src.infra.utils.RedisUtil import redis_util
except Exception:                                    # pragma: no cover
    redis_util = None


def _client():
    try:
        if redis_util is not None and redis_util.available:
            return redis_util._client
    except Exception:
        pass
    return None


async def aget(key: str) -> Any | None:
    """异步版 `get`：异步上下文（请求依赖/流式链路）里用这个，别用同步版

    语义完全一致：Redis 不可用/取不到/解析失败 → None（调用方走数据库）。
    """
    try:
        if redis_util is None or not redis_util.available:
            return None
        c = redis_util.aclient
        if c is None:
            return None
        raw = await c.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw)
    except Exception:
        return None


def get(key: str) -> Any | None:
    """取缓存；不存在或不可用返回 None"""
    c = _client()
    if c is None:
        return None
    try:
        raw = c.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
    """写缓存（失败只告警，不影响业务）"""
    c = _client()
    if c is None:
        return
    try:
        c.setex(key, int(ttl or DEFAULT_TTL), json.dumps(value, ensure_ascii=False, default=str))
    except Exception as e:
        log.warning(f"缓存写入失败（忽略）: {key}, {e}")


def delete(*keys: str) -> None:
    """删缓存——**数据变更后必须调用**"""
    c = _client()
    if c is None or not keys:
        return
    try:
        c.delete(*[k for k in keys if k])
    except Exception as e:
        log.warning(f"缓存删除失败（忽略）: {e}")


def delete_prefix(prefix: str) -> int:
    """按前缀批量删（角色变更影响所有用户时用）"""
    c = _client()
    if c is None or not prefix:
        return 0
    n = 0
    try:
        for k in (c.scan_iter(match=prefix + "*", count=200) or []):
            c.delete(k)
            n += 1
    except Exception as e:
        log.warning(f"缓存前缀删除失败（忽略）: {e}")
    return n
