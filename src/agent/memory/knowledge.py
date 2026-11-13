"""业务知识 / 长期记忆（L2）

设计原则（参照 Hermes 的"小预算全量注入"思路，不做向量检索）：
  1. 每轮只召回 **≤3 条**，注入 ≤800 字符 —— 多了干扰模型
  2. 召回靠**关键词**（tags + content 匹配），可解释、无外部依赖
  3. 只认用户**明确说的**（"记住…"），模型不许自己归纳
  4. 两种范围：user（本人）/ global（管理员维护，全公司共享）
  5. 带 expires_at：企业知识会过期，业务系统才是权威

表：pa_knowledge
"""
import json
import re
from datetime import datetime

from src.infra.utils.LogUtil import log

MAX_CONTENT = 500
MAX_ITEMS_PER_USER = 50
RECALL_LIMIT = 3
HINT_BUDGET = 800

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pa_knowledge (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  scope       VARCHAR(16)  NOT NULL DEFAULT 'user'   COMMENT '范围：user=个人，global=全局共享',
  username    VARCHAR(64)  NULL                      COMMENT 'scope=user 时的域账号',
  content     VARCHAR(500) NOT NULL                  COMMENT '知识内容',
  tags        VARCHAR(200) NOT NULL DEFAULT ''       COMMENT '召回关键词，逗号分隔',
  project     VARCHAR(32)  NOT NULL DEFAULT ''       COMMENT '所属项目/插件',
  created_by  VARCHAR(64)  NOT NULL DEFAULT ''       COMMENT '创建人',
  expires_at  DATETIME     NULL                      COMMENT '过期时间，空=永不过期',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_scope_user (scope, username),
  KEY idx_project (project),
  FULLTEXT KEY ft_content (content, tags)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='业务知识与长期记忆表'
"""

_READY = False


def _conn():
    from config.Configs import cfg
    import pymysql

    global _READY
    c = pymysql.connect(host=cfg.mysqlHost, port=int(cfg.mysqlPort), user=cfg.mysqlUser,
                        password=cfg.mysqlPass, db=cfg.mysqlDB, charset="utf8mb4")
    if not _READY:
        with c.cursor() as cur:
            cur.execute(_TABLE_SQL)
        c.commit()
        _READY = True
    return c


def _keywords(text: str) -> list[str]:
    """从用户消息里抽关键词（中文按 2-4 字滑窗 + 英文单词）"""
    s = (text or "").strip()
    if not s:
        return []
    words: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", s):
        words.append(w.lower())
    cn = re.sub(r"[^\u4e00-\u9fa5]", " ", s)
    for seg in cn.split():
        if len(seg) < 2:
            continue
        # 先出短词（2 字最可能是实体/术语），再出长词，避免截断时丢掉关键短词
        for n in (2, 3, 4):
            for i in range(0, max(1, len(seg) - n + 1)):
                words.append(seg[i:i + n])
    # 去重、去掉太通用的词
    stop = {"这个", "那个", "一下", "怎么", "什么", "我们", "你们", "可以", "帮我", "现在", "已经"}
    out, seen = [], set()
    for w in words:
        if w in stop or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:24]


def _norm_tags(tags) -> str:
    """tags 允许 str 或 list（前端/接口可能传数组），统一成逗号分隔字符串。

    历史上 add 用 str() 强转、update 直接 .strip()，传数组时一个存成 "['QA']"、
    另一个直接 500（'list' object has no attribute 'strip'）。
    """
    if tags is None:
        return ""
    if isinstance(tags, (list, tuple, set)):
        parts: list[str] = []
        for x in tags:
            v: str = str(x).strip()
            if v and v not in parts:
                parts.append(v)
        return ",".join(parts)[:200]
    return str(tags).strip()[:200]


def add(content: str, tags: str = "", scope: str = "user", username: str = "",
        project: str = "", expires_at: str | None = None) -> dict:
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "内容不能为空"}
    if len(content) > MAX_CONTENT:
        return {"ok": False, "error": f"内容太长（{len(content)} > {MAX_CONTENT} 字），请精简"}
    if scope not in ("user", "global"):
        scope = "user"
    if scope == "user" and not username:
        return {"ok": False, "error": "个人知识必须指定用户"}

    conn = _conn()
    try:
        with conn.cursor() as cur:
            if scope == "user":
                cur.execute("SELECT COUNT(*) FROM pa_knowledge WHERE scope='user' AND username=%s", (username,))
                if cur.fetchone()[0] >= MAX_ITEMS_PER_USER:
                    return {"ok": False, "error": f"知识条数已达上限（{MAX_ITEMS_PER_USER} 条），请先删几条"}
            cur.execute(
                "INSERT INTO pa_knowledge (scope, username, content, tags, project, created_by, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (scope, username or None, content, _norm_tags(tags), project,
                 username or "admin", expires_at or None),
            )
            new_id = cur.lastrowid
        conn.commit()
        return {"ok": True, "id": new_id, "content": content, "tags": tags, "scope": scope}
    finally:
        conn.close()


def _like(s: str) -> str:
    """LIKE 通配符转义：用户说"忘掉 100%"时不能把整表都匹配上

    用 '!' 做转义字符（SQL 里写 ESCAPE '!'），避免反斜杠在 Python/MySQL 两层转义的坑。
    """
    return str(s or "").replace("!", "!!").replace("%", "!%").replace("_", "!_")


def get_by_id(kid: int) -> dict | None:
    """按主键取单条知识（用于删除前的归属校验）

    以前用 `list_items(limit=500)` 建索引再查：知识超过 500 条时，更早的个人知识
    根本不在结果里 → 前端删不掉（返回 404）。这里直接按主键查，且**不过滤过期的**，
    过期知识也应该允许用户手动删掉。
    """
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, scope, username, content, tags, project, expires_at, created_at "
                    "FROM pa_knowledge WHERE id=%s",
                    (int(kid),),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        cols = ["id", "scope", "username", "content", "tags", "project", "expires_at", "created_at"]
        return dict(zip(cols, row))
    except Exception as e:
        log.warning(f"按 id 读取知识失败: id={kid}, err={e}")
        return None


def update(kid: int, content: str | None = None, tags: str | None = None) -> dict:
    """改一条知识的**内容 / 关键词**（只改传进来的字段）

    归属与权限由调用方（HTTP 接口 / 工具）先校验——这里只管数据合法性与落库。
    """
    sets: list[str] = []
    args: list = []
    if content is not None:
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "内容不能为空"}
        if len(content) > MAX_CONTENT:
            return {"ok": False, "error": f"内容太长（{len(content)} > {MAX_CONTENT} 字），请精简"}
        sets.append("content=%s")
        args.append(content)
    if tags is not None:
        sets.append("tags=%s")
        args.append(_norm_tags(tags))
    if not sets:
        return {"ok": False, "error": "没有要修改的字段"}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE pa_knowledge SET {', '.join(sets)} WHERE id=%s", (*args, int(kid)))
        conn.commit()
        return {"ok": True, "id": int(kid)}
    except Exception as e:
        log.warning(f"更新知识失败: id={kid}, err={e}")
        return {"ok": False, "error": "更新失败"}
    finally:
        conn.close()


def delete(content: str or int, username: str = "", allow_global: bool = False) -> dict:
    """删除知识

    allow_global=False（默认，给 agent 工具用）时**只能删自己的个人知识**：
    按编号删也必须带 scope/username 条件 —— 以前只按 id 删，模型拿着一个编号
    就能删掉别人甚至管理员维护的共享知识（HTTP 接口那侧本来有归属校验）。
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if isinstance(content, int) or str(content).isdigit():
                kid: int = int(content)
                if allow_global:
                    cur.execute("DELETE FROM pa_knowledge WHERE id=%s", (kid,))
                else:
                    cur.execute(
                        "DELETE FROM pa_knowledge WHERE id=%s AND scope='user' AND username=%s",
                        (kid, username),
                    )
            else:
                cur.execute(
                    "DELETE FROM pa_knowledge WHERE scope='user' AND username=%s "
                    "AND content LIKE %s ESCAPE '!'",
                    (username, f"%{_like(str(content).strip())}%"),
                )
            n = cur.rowcount
        conn.commit()
        return {"ok": n > 0, "deleted": n}
    finally:
        conn.close()


def list_items(username: str = "", scope: str = "", limit: int = 100) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            sql = ("SELECT id, scope, username, content, tags, project, expires_at, created_at "
                   "FROM pa_knowledge WHERE 1=1")
            args: list = []
            if scope:
                sql += " AND scope=%s"
                args.append(scope)
            if username and not scope:
                sql += " AND (scope='global' OR username=%s)"
                args.append(username)
            sql += " AND (expires_at IS NULL OR expires_at > NOW()) ORDER BY id DESC LIMIT %s"
            args.append(min(int(limit), 500))
            cur.execute(sql, args)
            rows = cur.fetchall()
        cols = ["id", "scope", "username", "content", "tags", "project", "expires_at", "created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def recall(username: str, text: str, limit: int = RECALL_LIMIT) -> list[dict]:
    """按关键词召回相关知识（global 优先，其次本人）"""
    kws = _keywords(text)
    if not kws:
        return []
    conn = _conn()
    try:
        with conn.cursor() as cur:
            ors, args = [], []
            for k in kws:
                ors.append("(content LIKE %s OR tags LIKE %s)")
                args += [f"%{k}%", f"%{k}%"]
            where = "(" + " OR ".join(ors) + ")"
            sql = ("SELECT id, scope, content, tags, project FROM pa_knowledge "
                   f"WHERE {where} AND (scope='global' OR (scope='user' AND username=%s)) "
                   "AND (expires_at IS NULL OR expires_at > NOW()) "
                   "ORDER BY (scope='global') DESC, id DESC LIMIT %s")
            args += [username, int(limit)]
            cur.execute(sql, args)
            rows = cur.fetchall()
        return [{"id": r[0], "scope": r[1], "content": r[2], "tags": r[3], "project": r[4]} for r in rows]
    except Exception as e:
        log.warning(f"知识召回失败（忽略）: {e}")
        return []
    finally:
        conn.close()


def render_hint(username: str, user_message: str) -> str:
    """给本轮对话生成"已知业务知识"提示（≤800 字符，最多 3 条）"""
    if not username:
        return ""
    rows = recall(username, user_message)
    if not rows:
        return ""
    lines: list[str] = []
    used = 0
    for r in rows:
        tag = "共享" if r["scope"] == "global" else "个人"
        line = f"- [{tag}] {r['content']}"
        if used + len(line) > HINT_BUDGET:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return ("【已知业务知识】（与本次提问相关，供参考；"
            "与实时查询冲突时以工具查询结果为准）\n" + "\n".join(lines))


__all__ = ["add", "update", "delete", "list_items", "recall", "render_hint", "MAX_ITEMS_PER_USER"]
