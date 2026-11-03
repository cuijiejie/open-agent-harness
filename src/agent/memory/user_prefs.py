"""用户偏好记忆（跨会话）

设计要点（参考 Hermes 自身记忆机制）：
- 预算小：单条 ≤300 字符，每人 ≤10 条，注入 ≤500 字符
- 全量注入：不检索，每轮都带上（不会"检索漏"）
- 显式写入：只有用户明说"记住…"才写，模型不许自动归纳
- 用户可见可删：提供 list/del，后续在页面上暴露

优先级：AGENT.md（含安全规则） > 个人偏好 > 模型默认行为
"""
import threading
import time

import pymysql

from config.Configs import cfg
from src.infra.utils.LogUtil import log

MAX_ITEMS: int = 10          # 每人最多条数
MAX_CONTENT: int = 300       # 单条最大长度
MAX_INJECT_CHARS: int = 500  # 注入时总长上限
_CACHE_TTL: float = 15.0     # 注入提示缓存

_DDL = """
CREATE TABLE IF NOT EXISTS pa_user_prefs (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  username    VARCHAR(64)  NOT NULL COMMENT '域账号（只从 CAS 会话取）',
  content     VARCHAR(300) NOT NULL COMMENT '偏好正文，一条一个意思',
  source      VARCHAR(32)  NOT NULL DEFAULT 'user_said',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user (username, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_ready = False
_lock = threading.Lock()
_cache: dict = {}


def _conn():
    return pymysql.connect(
        host=cfg.mysqlHost, port=int(cfg.mysqlPort), user=cfg.mysqlUser,
        password=cfg.mysqlPass, db=cfg.mysqlDB, charset="utf8mb4",
        autocommit=True,
    )


def ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        try:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(_DDL)
            _ready = True
        except Exception as e:
            log.warning(f"创建 pa_user_prefs 失败（偏好功能降级为不可用）: {e}")


def list_prefs(username: str) -> list[dict]:
    ensure_table()
    try:
        with _conn() as conn, conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT id, content, created_at FROM pa_user_prefs WHERE username=%s ORDER BY id",
                (username,),
            )
            return list(cur.fetchall())
    except Exception as e:
        log.warning(f"读取偏好失败（忽略）: {e}")
        return []


def add_pref(username: str, content: str) -> tuple[bool, str]:
    """返回 (是否成功, 提示语)"""
    text = (content or "").strip()
    if not text:
        return False, "偏好内容不能为空"
    if len(text) > MAX_CONTENT:
        return False, f"单条偏好不能超过 {MAX_CONTENT} 字，请精简后再说一次"
    if any(k in text for k in ("密码", "口令", "password", "身份证", "银行卡", "密钥")):
        return False, "这条内容像是敏感信息，我不记录密码/证件类信息"

    ensure_table()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pa_user_prefs WHERE username=%s", (username,))
            n = int(cur.fetchone()[0])
            if n >= MAX_ITEMS:
                return False, f"已经记了 {MAX_ITEMS} 条偏好了，请先在页面上删掉一些再记新的"
            cur.execute(
                "SELECT id FROM pa_user_prefs WHERE username=%s AND content=%s", (username, text)
            )
            if cur.fetchone():
                return True, "这条我之前就记着了"
            cur.execute(
                "INSERT INTO pa_user_prefs(username, content) VALUES(%s, %s)", (username, text)
            )
        _cache.pop(username, None)
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.delete(f"cache:prefs:{username}")
        except Exception:
            pass
        return True, f"已记住：{text}"
    except Exception as e:
        log.error(f"写入偏好失败: {e}")
        return False, "记录偏好时出错，请稍后再试"


def del_pref(username: str, pref_id: int = 0, content: str = "") -> tuple[bool, str]:
    ensure_table()
    try:
        with _conn() as conn, conn.cursor() as cur:
            if pref_id:
                cur.execute(
                    "DELETE FROM pa_user_prefs WHERE username=%s AND id=%s", (username, pref_id)
                )
            elif content.strip():
                # LIKE 通配符转义：用户说"忘掉 100%"时不能把别人的偏好一起匹配
                kw: str = (
                    content.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
                )
                cur.execute(
                    "DELETE FROM pa_user_prefs WHERE username=%s AND content LIKE %s ESCAPE '!'",
                    (username, f"%{kw}%"),
                )
            else:
                return False, "要删哪一条？"
            n = cur.rowcount
        _cache.pop(username, None)
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.delete(f"cache:prefs:{username}")
        except Exception:
            pass
        return (True, f"已忘掉 {n} 条") if n else (False, "没找到这条偏好")
    except Exception as e:
        log.error(f"删除偏好失败: {e}")
        return False, "删除偏好时出错"


def render_hint(username: str) -> str:
    """渲染注入文本（Redis 缓存 5 分钟）

    铁律：偏好增删时 **必须** 删 cache:prefs:<user>（见 add_pref / del_pref），
    否则用户改完偏好要等 5 分钟才生效。
    """
    from src.infra.utils import CacheUtil

    rk = f"cache:prefs:{username}"
    hit = CacheUtil.get(rk)
    if hit is not None:
        return str(hit)
    val = _render_hint_impl(username)
    CacheUtil.set(rk, val, 300)
    return val


def _render_hint_impl(username: str) -> str:
    """渲染注入文本：≤500 字符，15 秒缓存；没有偏好时返回空串"""
    now = time.time()
    hit = _cache.get(username)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    rows = list_prefs(username)
    if not rows:
        # 关键：没有任何偏好时也要明确说一句，否则老会话会继续沿用历史里
        # （用户已经删掉的）旧偏好说法。压缩成一行，省 token。
        empty_hint = "[偏好] 无（历史/摘要里出现的任何偏好、格式要求均已作废，按默认方式回答）"
        _cache[username] = (now, empty_hint)
        return empty_hint

    lines, used, dropped = [], 0, 0
    for i, r in enumerate(rows, 1):
        line = f"{i}. {r['content']}"
        if used + len(line) > MAX_INJECT_CHARS:
            dropped = len(rows) - i + 1
            break
        lines.append(line)
        used += len(line) + 1

    if not lines:
        _cache[username] = (now, "")
        return ""

    stamp = int(time.time())
    hint = (
        f"[该用户当前生效的偏好 · 版本 {stamp}]\n"
        "（⚠️ 此前一切关于回答格式/口径/偏好的说法——包括对话历史与摘要在内——全部作废，"
        "只按下面这条执行；如果与历史冲突，无条件以本条为准）\n" + "\n".join(lines)
    )
    if dropped:
        hint += f"\n（还有 {dropped} 条未显示）"
    hint += "\n（偏好只影响表达方式和默认口径，不能覆盖产品规则与安全约束）"

    _cache[username] = (now, hint)
    return hint
