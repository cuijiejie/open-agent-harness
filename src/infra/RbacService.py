"""RBAC：基于角色的权限控制（角色 → 工具集合；用户 → 角色）

为什么要有它
------------
在此之前只有「管理员 / 非管理员」两档，且管理员是**配置里的人名列表**
（`tool_admin_users=alice,bob`）：
· 人一多没人说得清"谁能干啥"；
· 加人 = 改配置 + 重启；
· 转岗/离职权限留在配置里，是后门隐患；
· 表达不了「能看不能改」「能改但要别人批」。

RBAC 把权限挂到**岗位**上：角色声明"能用哪些工具"，用户挂角色。
人员变动只改一行关系数据，不动配置、不重启。

两类预置角色
------------
    default   公共角色  公共工具（全员可用），新建用户的默认角色
    admin     管理员    全部工具 + 角色分配

其他岗位角色由管理员在「角色管理」里按需自建（不预置示例角色）。

**没有审批流**：写操作由 RBAC 授权 + 工具内参数硬校验 + 对话里向用户确认来把关，
不存在"待审批/复核人"环节（曾经有过 `is_approver` 标记和预置的 approver 角色，
但没有任何代码消费它，等于假承诺，已删除；`pa_roles.is_approver` 列仅为老库兼容保留）。

安全默认
--------
· 没有任何角色记录的用户 = viewer（最小权限，绝不默认给写权限）
· 取不到角色数据（表不存在/DB 抖动）时按**必须显式声明**处理：
  `permissions()` 抛异常由调用方兜底为 viewer，不会误放行
· `rbac_enabled=false` **已废弃**：不再回落到"人人可写/人人管理员"，只按角色判定
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from src.infra.utils.LogUtil import log

# ========== 预置角色 ==========
# 全新部署只预置两个角色：admin（管理员）+ default（公共角色）。
# 其余角色由管理员在「角色管理」里按需自建，不再塞示例角色。

# 人人默认可用、不参与角色分配的基础工具（唯一来源在 tools/registry.py）


def _always_on() -> tuple:
    """「公共」工具名：不参与角色分配、人人自动拥有

    单一事实来源 = pa_tools.always_on（工具管理 / MCP 服务里勾的「公共」）。
    读不到数据库时才回落到代码常量，避免 DB 抖动导致工具凭空消失。
    """
    try:
        from src.infra.ToolMetaService import tool_meta

        names: tuple = tuple(sorted(tool_meta.public_names()))
        if names:
            return names
    except Exception:
        pass
    try:
        from src.agent.tools.registry import ALWAYS_ON_NAMES
        return tuple(ALWAYS_ON_NAMES)
    except Exception:
        return ("ask_user", "sandbox_shell")

DEFAULT_ROLES: dict[str, dict] = {
    "default": {
        "name": "公共角色",
        "tools": [],                 # 播种时用「公共工具」动态填充（见 _always_on）
        "is_admin": 0,
        "builtin": 1,
        "desc": "默认角色：新建用户自动挂它，只带公共工具",
    },
    "admin": {
        "name": "管理员",
        "tools": ["*"],
        "is_admin": 1,
        "builtin": 1,
        "desc": "全部权限 + 角色分配 + 技能/MCP 管理",
    },
}

_CACHE_TTL: float = 60.0


# ---------- 权限缓存：Redis（多 worker 共享） ----------
_PERM_TTL: int = 300          # 5 分钟兜底过期
_PERM_PREFIX: str = "rbac:perm:"


def _redis_get(username: str) -> dict | None:
    """从 Redis 取权限快照（不可用时返回 None，走数据库）"""
    try:
        from src.infra.utils.RedisUtil import redis_util

        if not redis_util.available:
            return None
        raw = redis_util._client.get(_PERM_PREFIX + str(username).strip())
        if not raw:
            return None
        d = json.loads(raw)
        # set 在 JSON 里是 list，还原成 set
        if isinstance(d.get("tools"), list):
            d["tools"] = set(d["tools"])
        return d
    except Exception:
        return None


def _redis_set(username: str, perm: dict) -> None:
    """写权限快照到 Redis（失败只告警，不影响业务）"""
    try:
        from src.infra.utils.RedisUtil import redis_util

        if not redis_util.available:
            return
        payload = dict(perm)
        if isinstance(payload.get("tools"), set):
            payload["tools"] = sorted(payload["tools"])
        redis_util._client.setex(
            _PERM_PREFIX + str(username).strip(), _PERM_TTL,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        pass


def _redis_del(username: str) -> None:
    try:
        from src.infra.utils.RedisUtil import redis_util

        if redis_util.available:
            redis_util._client.delete(_PERM_PREFIX + str(username).strip())
    except Exception:
        pass


def _redis_clear_all() -> None:
    """全量清（角色变更影响所有持有该角色的人）"""
    try:
        from src.infra.utils.RedisUtil import redis_util

        if not redis_util.available:
            return
        for k in (redis_util._client.scan_iter(match=_PERM_PREFIX + "*", count=200) or []):
            redis_util._client.delete(k)
    except Exception:
        pass


class RbacService:
    """角色与权限（DB 存储 + 60 秒进程内缓存）"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self._status_cache: dict[str, tuple[float, str]] = {}
        # 账号状态的跨进程版本号（见 is_disabled）：状态一改就 bump，
        # 其它 worker 发现版本变了就丢掉本地缓存，否则"停用/删除"最长 30s 仍可用
        self._status_ver: str = ""
        self._schema_ready: bool = False

    # ---------- 配置 ----------
    @staticmethod
    def _cfg_bool(key: str, default: bool) -> bool:
        from config.Configs import cfg
        return bool(getattr(cfg, key, default))

    @property
    def enabled(self) -> bool:
        """RBAC **永远是开着的**——`rbac_enabled=false` 这个开关已废弃（2026-11-03）

        为什么废弃：它原来的语义是"关掉就回落到旧的 tool_admin_users/tool_write_scope"，
        而那条老路在 `tool_write_scope=all` 时等于**人人可写**、`is_admin()` 更是直接
        返回 True（人人管理员）。这等于给系统留了一个"配置一行就全员提权"的后门。

        现在的约定：**权限只来自数据库里的角色分配**；
          · 谁是管理员 = 角色里 is_admin=1（或显式配置在 tool_admin_users 里）
          · 角色没给的工具 = 调不到（见 resolve_tools / allows_tool）
        配置里即使写了 `rbac_enabled=false` 也不会放行，只会在日志里提示"该开关已废弃"。
        """
        try:
            raw: bool = self._cfg_bool("rbacEnabled", True)
        except Exception:
            return True
        if not raw:
            self._warn_deprecated_switch()
        return True

    _warned_deprecated: bool = False

    def _warn_deprecated_switch(self) -> None:
        if RbacService._warned_deprecated:
            return
        RbacService._warned_deprecated = True
        log.warning(
            "配置里 rbac_enabled=false 已废弃：不会再放行任何权限。"
            "权限只来自角色分配；若要收回某人的权限，请改他的角色（或停用账号）。"
        )

    @property
    def default_role(self) -> str:
        """未分配角色时的默认角色：现在一律为空（权限只来自数据库分配），保留属性兼容旧调用"""
        return ""

    # ---------- DB ----------
    @staticmethod
    def _conn():
        import pymysql
        from config.Configs import cfg

        return pymysql.connect(
            host=cfg.mysqlHost, port=int(cfg.mysqlPort), user=cfg.mysqlUser,
            password=cfg.mysqlPass, database=cfg.mysqlDB, charset="utf8mb4",
            autocommit=True, connect_timeout=5,
        )

    def ensure_schema(self) -> bool:
        """建表 + 灌预置角色（幂等，启动时调用一次；失败只记日志不影响主流程）"""
        if self._schema_ready:
            return True
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pa_roles (
                      role_code   VARCHAR(32)  NOT NULL PRIMARY KEY,
                      role_name   VARCHAR(64)  NOT NULL,
                      tools       TEXT         NOT NULL,
                      is_approver TINYINT      NOT NULL DEFAULT 0,
                      is_admin    TINYINT      NOT NULL DEFAULT 0,
                      is_builtin  TINYINT      NOT NULL DEFAULT 0,
                      note        VARCHAR(255) DEFAULT '',
                      created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # 老库补列（幂等）
                cur.execute("SELECT COUNT(*) FROM information_schema.columns "
                            "WHERE table_schema=DATABASE() AND table_name='pa_roles' AND column_name='is_builtin'")
                if int(cur.fetchone()[0] or 0) == 0:
                    cur.execute("ALTER TABLE pa_roles ADD COLUMN is_builtin TINYINT NOT NULL DEFAULT 0")
                    log.info("pa_roles 已补列 is_builtin")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pa_user_roles (
                      username   VARCHAR(64) NOT NULL,
                      role_code  VARCHAR(32) NOT NULL,
                      created_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
                      PRIMARY KEY (username, role_code)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pa_users (
                      username     VARCHAR(64)  NOT NULL PRIMARY KEY,
                      display_name VARCHAR(128) DEFAULT '',
                      status       VARCHAR(16)  NOT NULL DEFAULT 'active',
                      first_login  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                      last_login   TIMESTAMP    NULL,
                      KEY idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # 密码列：老库自动补列（MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS）
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='pa_users' "
                    "AND COLUMN_NAME='password_hash'"
                )
                if int(cur.fetchone()[0] or 0) == 0:
                    cur.execute("ALTER TABLE pa_users ADD COLUMN password_hash VARCHAR(255) NULL")
                    log.warning("pa_users 新增 password_hash 列；存量账号密码为空，"
                                "需管理员在「用户管理」里重置后才能登录")

                # 预置角色只在「角色表为空」时初始化：管理员删掉的预置角色不会被重启复活
                cur.execute("SELECT COUNT(*) FROM pa_roles")
                _seed_items = list(DEFAULT_ROLES.items()) if int(cur.fetchone()[0] or 0) == 0 else []
                for code, r in _seed_items:
                    # tools 只在**新建**时写默认值：管理员在「角色管理」里改过之后，
                    # 重启不能被默认值覆盖掉（所以这里不更新 tools 列）
                    cur.execute(
                        "INSERT INTO pa_roles (role_code, role_name, tools, is_approver, is_admin, is_builtin, note) "
                        "VALUES (%s,%s,%s,0,%s,%s,%s) ON DUPLICATE KEY UPDATE role_name=VALUES(role_name), "
                        "is_admin=VALUES(is_admin), is_builtin=VALUES(is_builtin), note=VALUES(note)",
                        (code, r["name"], json.dumps(r["tools"], ensure_ascii=False),
                         r["is_admin"], int(r.get("builtin", 0)), r.get("desc", "")),
                    )
                # 内置标记归一：只有「管理员」和「默认角色（公共角色）」是系统角色，
                # 其余预置角色（操作员/查看者）只是示例，允许管理员删除。
                _dc: str = self.default_role_code()
                # 老版本预置过 viewer/operator，新库不再预置；这里把它们的内置标记清掉，允许删除
                cur.execute("UPDATE pa_roles SET is_builtin=0 WHERE role_code IN ('viewer','operator')")
                cur.execute("UPDATE pa_roles SET is_builtin=1 WHERE role_code='admin'")
                if _dc:
                    cur.execute("UPDATE pa_roles SET is_builtin=1 WHERE role_code=%s", (_dc,))
                # 默认角色（公共角色）必须存在：新建用户默认挂它（部署保证，幂等）
                if _dc:
                    _pub_tools: list[str] = list(_always_on())
                    cur.execute(
                        "INSERT IGNORE INTO pa_roles "
                        "(role_code, role_name, tools, is_approver, is_admin, is_builtin, note) "
                        "VALUES (%s,%s,%s,0,0,1,%s)",
                        (_dc, DEFAULT_ROLES.get(_dc, {}).get("name", _dc),
                         json.dumps(_pub_tools, ensure_ascii=False),
                         DEFAULT_ROLES.get(_dc, {}).get("desc", "默认角色")),
                    )
                    # 老库里的 display name 可能还是编码本身（default），统一改成「公共角色」
                    cur.execute(
                        "UPDATE pa_roles SET role_name=%s WHERE role_code=%s "
                        "AND (role_name IS NULL OR role_name='' OR role_name=role_code)",
                        (DEFAULT_ROLES[_dc]["name"], _dc),
                    )
                    # 内置不可删；工具若为空（老库第一次补这条角色）则用公共工具填一次，不覆盖已有配置
                    cur.execute(
                        "UPDATE pa_roles SET is_builtin=1, "
                        "tools=IF(tools IS NULL OR JSON_LENGTH(tools)=0, %s, tools) "
                        "WHERE role_code=%s",
                        (json.dumps(_pub_tools, ensure_ascii=False), _dc),
                    )
            self._schema_ready = True
            log.info("RBAC 表就绪（权限只来自数据库：pa_roles / pa_user_roles / pa_users）")
            return True
        except Exception as e:
            log.warning(f"RBAC 建表失败（本轮按最小权限处理）: {e}")
            return False

    # ---------- 权限查询 ----------
    def permissions(self, username: str) -> dict:
        """解析用户权限：{roles, tools(set|'*'), is_admin, fresh}

        缓存策略：
        - Redis 可用 → **只用 Redis**（多 worker 共享，角色变更时 DEL 立即全局生效）。
          进程内缓存只作 Redis 不可用时的兜底，否则别的 worker 改完权限，
          本进程还会拿旧快照最多 60 秒（"改了权限要等一会儿"的老问题）。
        """
        from src.infra.utils.RedisUtil import redis_util

        redis_ok: bool = bool(redis_util.available)
        if redis_ok:
            _hit = _redis_get(username)
            if _hit is not None:
                return _hit

        user: str = (username or "").strip()
        now: float = time.time()
        if not redis_ok:
            hit = self._cache.get(user)
            if hit and now - hit[0] < _CACHE_TTL:
                return hit[1]

        tools: set[str] = set()
        roles: list[str] = []
        is_admin: bool = False
        try:
            if not self.ensure_schema():
                raise RuntimeError("schema not ready")
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT r.role_code, r.tools, r.is_admin "
                    "FROM pa_user_roles u JOIN pa_roles r ON r.role_code = u.role_code "
                    "WHERE u.username = %s", (user,),
                )
                rows = cur.fetchall()
            for code, tools_json, ad in rows:
                roles.append(code)
                is_admin = is_admin or bool(ad)
                try:
                    items = json.loads(tools_json or "[]")
                except Exception:
                    items = []
                if items == ["*"] or "*" in items:
                    tools = {"*"}
                else:
                    tools |= {str(x) for x in items}
        except Exception as e:
            log.warning(f"RBAC 读取失败，按 viewer 处理: user={user}, err={e}")
            roles, tools, is_admin = [self.default_role], set(), False

        if not roles:
            # 未分配角色：若 default_role 在库里确实存在就给它，否则视为「无权限」
            dr: str = self.default_role
            _row = None
            try:
                with self._conn() as c, c.cursor() as cur:
                    cur.execute("SELECT tools, is_admin FROM pa_roles WHERE role_code=%s", (dr,))
                    _row = cur.fetchone()
            except Exception:
                _row = None
            if _row:
                roles = [dr]
                try:
                    tools = set(json.loads(_row[0] or "[]"))
                except Exception:
                    tools = set()
                is_admin = bool(_row[1])
            else:
                roles, tools, is_admin = [], set(), False

        # ask_user / sandbox_shell 是基础能力：所有用户默认拥有
        # 注意：* 在集合里是通配符，必须用 in 判断（别拿集合和字符串比）
        if "*" not in tools:
            tools = set(tools) | set(_always_on())

        perm: dict = {
            "roles": roles, "tools": "*" if "*" in tools else tools,
            "is_admin": is_admin, "fresh": now,
        }
        self._cache[user] = (now, perm)
        _redis_set(user, perm)
        return perm

    def allows_tool(self, username: str, tool_name: str) -> bool:
        """该用户能否使用某个工具（内置工具名，或 MCP 工具的 mcp_<server>_<tool>）

        角色 tools 里可写：`*`（全部）/ 内置工具名 / `mcp_*`（全部 MCP）/ 具体 mcp_xxx 名

        注意：**没有"开关关掉就放行"的分支**（2026-11-03 移除）。
        旧代码是 `if not self.enabled: return True` —— 配置一行就能全员提权，是后门。
        """
        perm: dict = self.permissions(username)
        tools = perm["tools"]
        if tools == "*":
            return True
        if tool_name in tools:
            return True
        if tool_name.startswith("mcp_") and "mcp_*" in tools:
            return True
        return False

    def is_admin(self, username: str) -> bool:
        """是不是管理员 —— **只认数据库里的标记**（角色 is_admin=1）

        不再有 `if not self.enabled: return True` 那种兜底：
        没被分配管理员角色的人，任何配置都变不成管理员。
        """
        user: str = (username or "").strip()
        if not user:
            return False
        return bool(self.permissions(user)["is_admin"])

    def invalidate(self, username: str = "") -> None:
        """角色变更后立即生效（同时清进程内缓存和 Redis）"""
        if username:
            self._cache.pop(username.strip(), None)
            _redis_del(username)
        else:
            self._cache.clear()
            _redis_clear_all()

    # ---------- 用户主数据（登录建档 / 启停 / 列表） ----------
    # ---------- 密码 ----------
    DEFAULT_NEW_USER_PASSWORD: str = "123456"

    @staticmethod
    def _bcrypt(plain: str) -> bytes:
        """bcrypt 只取前 72 字节（超长会被新版本库直接拒绝）"""
        return str(plain or "").encode("utf-8")[:72]

    @classmethod
    def hash_password(cls, plain: str) -> str:
        """bcrypt 哈希（每次随机盐；cost=12）"""
        import bcrypt

        return bcrypt.hashpw(cls._bcrypt(plain), bcrypt.gensalt(rounds=12)).decode("ascii")

    @classmethod
    def check_password_hash(cls, stored: str, plain: str) -> bool:
        """常数时间比对；哈希为空/格式坏一律 False"""
        import bcrypt

        if not stored or not str(stored).startswith("$2"):
            return False
        try:
            return bool(bcrypt.checkpw(cls._bcrypt(plain), str(stored).encode("utf-8")))
        except Exception:
            return False

    def user_row(self, username: str) -> dict | None:
        """取用户记录（不存在返回 None）"""
        user: str = (username or "").strip()
        if not user:
            return None
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT username, display_name, status, password_hash FROM pa_users WHERE username=%s",
                    (user,),
                )
                r = cur.fetchone()
        except Exception as e:
            log.warning(f"查询用户失败: user={user}, err={e}")
            return None
        if not r:
            return None
        return {"username": r[0], "display_name": r[1] or "", "status": r[2] or "",
                "password_hash": r[3] or ""}

    def verify_password(self, username: str, plain: str) -> bool:
        """校验账号密码：账号不存在 / 未设密码 / 密码错 → False"""
        row: dict | None = self.user_row(username)
        if not row or not row["password_hash"]:
            return False
        return self.check_password_hash(row["password_hash"], plain)

    def set_password(self, username: str, plain: str = "") -> dict:
        """设置/重置密码（空密码按默认 123456 处理）；账号不存在则拒绝"""
        row: dict | None = self.user_row(username)
        if not row:
            return {"ok": False, "error": f"用户 {username} 不存在"}
        pwd: str = str(plain or "").strip() or self.DEFAULT_NEW_USER_PASSWORD
        try:
            h: str = self.hash_password(pwd)
            with self._conn() as c, c.cursor() as cur:
                cur.execute("UPDATE pa_users SET password_hash=%s WHERE username=%s", (h, row["username"]))
        except Exception as e:
            log.error(f"设置密码失败: user={username}, err={e}")
            return {"ok": False, "error": "密码保存失败"}
        log.warning(f"密码已变更: user={row['username']}")
        return {"ok": True, "username": row["username"]}

    def touch_user(self, username: str, display_name: str = "") -> None:
        """登录成功后只更新最后登录时间（**不再建档**）

        账号只能由管理员通过「用户管理」（POST /api/users）创建。历史上这里会
        "不存在就建号并分配默认角色"，等于任何字符串都能注册进系统——已删除。
        """
        user: str = (username or "").strip()
        if not user:
            return
        try:
            self.ensure_schema()
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "UPDATE pa_users SET last_login=NOW(), "
                    "display_name=IF(%s<>'', %s, display_name) WHERE username=%s",
                    ((display_name or "")[:128], (display_name or "")[:128], user),
                )
        except Exception as e:
            log.warning(f"更新登录时间失败（不影响登录）: user={user}, err={e}")

    def default_role_code(self) -> str:
        """配置里的默认角色编码（[app] default_role，缺省 default）"""
        try:
            from config.Configs import cfg as _cfg

            return str(getattr(_cfg, "defaultRole", "") or "").strip()
        except Exception:
            return ""

    def _assign_default_role(self, username: str) -> None:
        """给刚建档的新账号分配默认角色（任何异常都不影响登录）"""
        try:
            from config.Configs import cfg as _cfg

            code: str = str(getattr(_cfg, "defaultRole", "") or "").strip()
            if not code:
                return
            with self._conn() as c, c.cursor() as cur:
                # 已有角色就不动（管理员建号时手工指定过角色）
                cur.execute("SELECT 1 FROM pa_user_roles WHERE username=%s LIMIT 1", (username,))
                if cur.fetchone():
                    return
                # 默认角色必须真实存在，否则跳过（避免写入悬空绑定）
                cur.execute("SELECT 1 FROM pa_roles WHERE role_code=%s", (code,))
                if not cur.fetchone():
                    log.warning(f"默认角色不存在，跳过分配: user={username}, role={code}")
                    return
                cur.execute(
                    "INSERT IGNORE INTO pa_user_roles (username, role_code) VALUES (%s,%s)",
                    (username, code),
                )
            self.invalidate(username)
            log.info(f"新账号已分配默认角色: user={username}, role={code}")
        except Exception as e:
            log.warning(f"分配默认角色失败（不影响登录）: user={username}, err={e}")

    def is_disabled(self, username: str) -> bool:
        """账号是否已停用/已删除（30 秒缓存；查不到一律视为未停用，避免 DB 抖动把人锁在门外）"""
        user: str = (username or "").strip()
        if not user:
            return False
        now: float = time.time()
        # 跨进程一致性：设置状态的那个 worker 会 bump 版本号，
        # 其它 worker 发现版本变了就立刻清掉本地缓存 —— 否则 gunicorn 多 worker 下，
        # "停用/删除"最长有 30 秒窗口仍然能正常调接口（离职交接时是安全漏洞）
        try:
            from src.infra.utils import CacheUtil

            ver = CacheUtil.get("rbac:status:ver")
            if ver is not None and str(ver) != self._status_ver:
                self._status_ver = str(ver)
                self._status_cache.clear()
        except Exception:
            pass
        hit = self._status_cache.get(user)
        if hit and now - hit[0] < 30:
            return hit[1] in ("disabled", "deleted")
        status: str = "active"
        try:
            self.ensure_schema()
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT status FROM pa_users WHERE username=%s", (user,))
                row = cur.fetchone()
                if row and row[0]:
                    status = str(row[0])
        except Exception as e:
            log.warning(f"读取账号状态失败（按未停用处理）: user={user}, err={e}")
        self._status_cache[user] = (now, status)
        return status in ("disabled", "deleted")

    async def ais_disabled(self, username: str) -> bool:
        """`is_disabled` 的异步版（请求依赖 require_login 走这条）

        语义与同步版一致：版本号检查 → 30 秒本地缓存 → 查库 → 缓存。
        DB 用**连接池化的 AsyncSession**（不是每次 pymysql.connect）——这是每请求
        路径上唯一还剩下的同步阻塞点。
        """
        user: str = (username or "").strip()
        if not user:
            return False
        now: float = time.time()

        try:
            from src.infra.utils import CacheUtil

            ver = await CacheUtil.aget("rbac:status:ver")
            if ver is not None and str(ver) != self._status_ver:
                self._status_ver = str(ver)
                self._status_cache.clear()
        except Exception:
            pass

        hit = self._status_cache.get(user)
        if hit and now - hit[0] < 30:
            return hit[1] in ("disabled", "deleted")

        status: str = "active"
        try:
            if not self._schema_ready:
                import asyncio

                await asyncio.to_thread(self.ensure_schema)
            from sqlalchemy import text as sa_text

            from src.infra.db.database import acquire_db

            # acquire_db：请求内借用中间件的 session，请求外（脚本/后台）自建并关闭，
            # 避免 get_db() 在无请求上下文时留下的连接要等 GC 才回池
            async with acquire_db() as adb:
                row = (await adb.execute(
                    sa_text("SELECT status FROM pa_users WHERE username=:u"), {"u": user}
                )).first()
            if row and row[0]:
                status = str(row[0])
        except Exception as e:
            log.warning(f"读取账号状态失败（按未停用处理）: user={user}, err={e}")
        self._status_cache[user] = (now, status)
        return status in ("disabled", "deleted")

    def is_last_active_admin(self, username: str) -> bool:
        """该用户是否是「最后一个启用中的管理员」

        停用账号的接口必须在写库前调用它：否则管理员可以把自己（或唯一另一个
        管理员）停用，之后系统再没有人能进用户/角色管理，只能改库救场。
        查询失败按 False 处理（不阻断正常运维），删除用户那条路径已有同样的兜底。
        """
        user: str = (username or "").strip()
        if not user:
            return False
        try:
            self.ensure_schema()
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM pa_user_roles u JOIN pa_roles r "
                    "ON r.role_code=u.role_code WHERE u.username=%s AND r.is_admin=1",
                    (user,),
                )
                if int(cur.fetchone()[0] or 0) == 0:
                    return False
                cur.execute(
                    "SELECT COUNT(DISTINCT pu.username) FROM pa_users pu "
                    "JOIN pa_user_roles u ON u.username=pu.username "
                    "JOIN pa_roles r ON r.role_code=u.role_code "
                    "WHERE r.is_admin=1 AND pu.status='active' AND pu.username<>%s",
                    (user,),
                )
                return int(cur.fetchone()[0] or 0) == 0
        except Exception as e:
            log.warning(f"判断最后管理员失败（按否处理）: user={user}, err={e}")
            return False

    def set_status(self, username: str, status: str) -> bool:
        """启用/停用。停用后该账号无法登录，已登录的接口请求也会被 403 拦下。"""
        user: str = (username or "").strip()
        val: str = "disabled" if status == "disabled" else "active"
        if not user:
            return False
        try:
            self.ensure_schema()
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "INSERT INTO pa_users (username, status) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE status=VALUES(status)",
                    (user, val),
                )
            self._status_cache[user] = (time.time(), val)
            self._bump_status_ver()
            log.warning(f"账号状态变更: user={user}, status={val}")
            return True
        except Exception as e:
            log.error(f"账号状态变更失败: user={user}, err={e}")
            return False

    def _bump_status_ver(self) -> None:
        """通知所有 worker：账号状态变了，丢掉 is_disabled 的本地缓存"""
        try:
            from src.infra.utils import CacheUtil

            ver: float = time.time()
            CacheUtil.set("rbac:status:ver", ver, ttl=86400)
            self._status_ver = str(ver)
        except Exception as e:
            log.warning(f"状态版本号写入失败（多 worker 可能延迟生效）: {e}")

    def list_all_users(self, keyword: str = "", role: str = "") -> list[dict]:
        """用户列表（含角色、会话数、待办数）；keyword 匹配账号/姓名，role 过滤角色"""
        self.ensure_schema()
        kw: str = f"%{(keyword or '').strip()}%"
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT u.username, u.display_name, u.status, u.first_login, u.last_login, "
                    "       GROUP_CONCAT(r.role_code) AS roles, "
                    "       (SELECT COUNT(*) FROM chat_sessions s WHERE s.username = u.username) AS sess "
                    "FROM pa_users u LEFT JOIN pa_user_roles r ON r.username = u.username "
                    "WHERE u.status <> 'deleted' AND (u.username LIKE %s OR u.display_name LIKE %s) "
                    "GROUP BY u.username, u.display_name, u.status, u.first_login, u.last_login "
                    "ORDER BY u.last_login IS NULL, u.last_login DESC, u.username",
                    (kw, kw),
                )
                rows = cur.fetchall()
        except Exception as e:
            log.warning(f"读取用户列表失败: {e}")
            return []

        out: list[dict] = []
        for username, disp, st, first, last, roles, sess in rows:
            rs: list[str] = [x for x in (roles or "").split(",") if x]
            if role and role not in rs:
                continue
            out.append({
                "username": username,
                "display_name": disp or "",
                "status": st or "active",
                "roles": rs or [],
                "sessions": int(sess or 0),
                "first_login": first.strftime("%Y-%m-%d %H:%M") if first else "",
                "last_login": last.strftime("%Y-%m-%d %H:%M") if last else "从未登录",
            })
        return out

    # ---------- 真分页（SQL LIMIT/OFFSET） ----------
    @staticmethod
    def _users_where(keyword: str, role: str) -> tuple[str, list]:
        where: list[str] = ["u.status <> 'deleted'"]
        args: list = []
        kw: str = (keyword or "").strip()
        if kw:
            where.append("(u.username LIKE %s OR u.display_name LIKE %s)")
            args += [f"%{kw}%", f"%{kw}%"]
        if role:
            # 支持输入角色编码或中文名片段（下拉改成输入框后，按 LIKE 匹配更自然）
            where.append(
                "EXISTS (SELECT 1 FROM pa_user_roles rr JOIN pa_roles rp ON rp.role_code = rr.role_code "
                "WHERE rr.username = u.username AND (rr.role_code LIKE %s OR rp.role_name LIKE %s))"
            )
            args += [f"%{role.strip()}%", f"%{role.strip()}%"]
        return " AND ".join(where), args

    def page_users(self, keyword: str = "", role: str = "", page: int = 1, page_size: int = 10) -> dict:
        """用户分页（真分页：数据库 LIMIT/OFFSET，只取当前页）"""
        where, args = self._users_where(keyword, role)
        pg: int = max(1, int(page or 1))
        size: int = max(1, min(100, int(page_size or 10)))
        offset: int = (pg - 1) * size
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM pa_users u WHERE {where}", tuple(args))
                total: int = int(cur.fetchone()[0] or 0)
                cur.execute(
                    "SELECT u.username, u.display_name, u.status, u.first_login, u.last_login, "
                    "       GROUP_CONCAT(r.role_code) AS roles, "
                    "       (SELECT COUNT(*) FROM chat_sessions s WHERE s.username = u.username) AS sess "
                    "FROM pa_users u LEFT JOIN pa_user_roles r ON r.username = u.username "
                    f"WHERE {where} "
                    "GROUP BY u.username, u.display_name, u.status, u.first_login, u.last_login "
                    "ORDER BY u.last_login IS NULL, u.last_login DESC, u.username "
                    "LIMIT %s OFFSET %s",
                    tuple(args) + (size, offset),
                )
                rows = cur.fetchall()
        except Exception as e:
            log.warning(f"用户分页查询失败: {e}")
            return {"users": [], "total": 0, "page": pg, "page_size": size}

        out: list[dict] = []
        for username, disp, st, first, last, roles, sess in rows:
            out.append({
                "username": username,
                "display_name": disp or "",
                "status": st or "active",
                "roles": [x for x in (roles or "").split(",") if x],
                "sessions": int(sess or 0),
                "first_login": first.strftime("%Y-%m-%d %H:%M") if first else "",
                "last_login": last.strftime("%Y-%m-%d %H:%M") if last else "从未登录",
            })
        return {"users": out, "total": total, "page": pg, "page_size": size}

    def page_roles(self, page: int = 1, page_size: int = 10, keyword: str = "") -> dict:
        """角色分页（真分页）；keyword 匹配角色编码或名称"""
        pg: int = max(1, int(page or 1))
        size: int = max(1, min(100, int(page_size or 10)))
        offset: int = (pg - 1) * size
        kw: str = (keyword or "").strip()
        where: str = ""
        args: list = []
        if kw:
            where = "WHERE role_code LIKE %s OR role_name LIKE %s"
            args = [f"%{kw}%", f"%{kw}%"]
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM pa_roles {where}", tuple(args))
                total: int = int(cur.fetchone()[0] or 0)
                cur.execute(
                    "SELECT role_code, role_name, tools, is_admin, note, is_builtin "
                    f"FROM pa_roles {where} ORDER BY is_builtin DESC, role_code LIMIT %s OFFSET %s",
                    tuple(args) + (size, offset),
                )
                rows = cur.fetchall()
        except Exception as e:
            log.warning(f"角色分页查询失败: {e}")
            return {"roles": [], "total": 0, "page": pg, "page_size": size}

        roles = [{
            "role_code": r[0], "role_name": r[1], "tools": json.loads(r[2] or "[]"),
            "is_admin": bool(r[3]), "note": r[4], "is_builtin": bool(r[5]),
        } for r in rows]
        return {"roles": roles, "total": total, "page": pg, "page_size": size}

    # ---------- 用户增删改 ----------
    def create_user(self, username: str, display_name: str = "", roles: list[str] | None = None,
                    password: str = "") -> dict:
        """新增用户；已存在则拒绝（避免悄悄覆盖别人的角色）

        password 为空时用默认密码 123456（管理员可在建号时指定）。
        """
        user: str = (username or "").strip()
        if not user:
            return {"ok": False, "error": "域账号不能为空"}
        if len(user) > 64 or "/" in user or "\\" in user or ".." in user \
                or any(ord(ch) < 32 for ch in user):
            return {"ok": False, "error": "账号不合法（不能含路径分隔符/..，长度 ≤64）"}
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT status FROM pa_users WHERE username=%s", (user,))
                row = cur.fetchone()
                if row and str(row[0]) != "deleted":
                    return {"ok": False, "error": f"用户 {user} 已存在"}
                cur.execute(
                    "INSERT INTO pa_users (username, display_name, status, password_hash) "
                    "VALUES (%s,%s,'active',%s) "
                    "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), status='active', "
                    "password_hash=VALUES(password_hash)",
                    (user, (display_name or "")[:128],
                     self.hash_password(str(password or "").strip() or self.DEFAULT_NEW_USER_PASSWORD)),
                )
            codes: list[str] = [r for r in (roles or []) if str(r).strip()]
            if not codes:                     # 不传角色 → 默认挂公共角色
                _d: str = self.default_role_code()
                if _d:
                    codes = [_d]
            if codes:
                self.assign(user, codes)
            else:
                # 管理员建号时没勾角色 → 用配置里的默认角色（通常是「公用角色」）
                self._assign_default_role(user)
            # 回读真实角色：默认角色若不存在，这里就拿到空列表，前端不会显示一个假角色
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT role_code FROM pa_user_roles WHERE username=%s ORDER BY role_code",
                    (user,),
                )
                codes = [str(r[0]) for r in cur.fetchall()]
            log.warning(f"新增用户: user={user}, roles={codes}")
            return {"ok": True, "username": user, "roles": codes}
        except Exception as e:
            log.error(f"新增用户失败: {user}, err={e}")
            return {"ok": False, "error": "写入失败（详见服务端日志）"}

    def update_user(self, username: str, display_name: str) -> bool:
        """只改姓名（角色走 assign，状态走 set_status）"""
        user: str = (username or "").strip()
        if not user:
            return False
        try:
            self.ensure_schema()
            with self._conn() as c, c.cursor() as cur:
                cur.execute("UPDATE pa_users SET display_name=%s WHERE username=%s",
                            ((display_name or "")[:128], user))
            return True
        except Exception as e:
            log.error(f"更新用户姓名失败: {user}, err={e}")
            return False

    def delete_user(self, username: str, operator: str) -> dict:
        """软删除：隐藏 + 禁止登录，聊天/日志保留。不能删自己，也不能删掉最后一个管理员。"""
        user: str = (username or "").strip()
        if not user:
            return {"ok": False, "error": "用户名不能为空"}
        if user == operator:
            return {"ok": False, "error": "不能删除自己的账号"}
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM pa_users WHERE username=%s AND status<>'deleted'", (user,))
                if int(cur.fetchone()[0] or 0) == 0:
                    return {"ok": False, "error": f"用户 {user} 不存在或已删除"}
                # 是否管理员？是的话必须还剩至少一个未删除的管理员
                cur.execute("SELECT COUNT(*) FROM pa_user_roles u JOIN pa_roles r ON r.role_code=u.role_code "
                            "WHERE u.username=%s AND r.is_admin=1", (user,))
                is_admin: bool = int(cur.fetchone()[0] or 0) > 0
                if is_admin:
                    cur.execute("SELECT COUNT(DISTINCT pu.username) FROM pa_users pu "
                                "JOIN pa_user_roles u ON u.username=pu.username "
                                "JOIN pa_roles r ON r.role_code=u.role_code "
                                "WHERE r.is_admin=1 AND pu.status='active' AND pu.username<>%s", (user,))
                    if int(cur.fetchone()[0] or 0) == 0:
                        return {"ok": False, "error": "这是最后一个管理员，不能删除（会没人能管理系统设置）"}
                cur.execute("UPDATE pa_users SET status='deleted' WHERE username=%s", (user,))
                # 软删的用户不该继续占着角色：否则这角色永远删不掉
                # （delete_role 会回"还有 N 个用户在用"，而那个用户早已被删）
                cur.execute("DELETE FROM pa_user_roles WHERE username=%s", (user,))
            self.invalidate(user)
            self._status_cache[user] = (time.time(), "deleted")
            self._bump_status_ver()
            log.warning(f"删除用户（软删）: user={user}, 操作人={operator}")
            return {"ok": True, "username": user}
        except Exception as e:
            log.error(f"删除用户失败: {user}, err={e}")
            return {"ok": False, "error": "删除失败（详见服务端日志）"}

    # ---------- 角色增删改 ----------
    def create_role(self, code: str, name: str, note: str = "", tools: list[str] | None = None) -> dict:
        role_code: str = (code or "").strip()
        # 只要求「英文」+「不重复」；大小写、下划线、短横、点都行
        if not role_code or not re.match(r"^[A-Za-z0-9_.\-]{1,32}$", role_code):
            return {"ok": False, "error": "角色编码只能用英文（字母、数字、下划线、短横），且不能与已有角色重复"}
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT 1 FROM pa_roles WHERE role_code=%s", (role_code,))
                if cur.fetchone():
                    return {"ok": False, "error": f"角色编码 {role_code} 已存在"}
                cur.execute(
                    "INSERT INTO pa_roles (role_code, role_name, tools, is_approver, is_admin, is_builtin, note) "
                    "VALUES (%s,%s,%s,0,0,0,%s)",
                    (role_code, (name or role_code)[:64],
                     json.dumps(tools or [], ensure_ascii=False), (note or "")[:255]),
                )
            self.invalidate()
            self._bump_roles_ver()
            log.warning(f"新增角色: code={role_code}, name={name}")
            return {"ok": True, "role_code": role_code}
        except Exception as e:
            log.error(f"新增角色失败: {role_code}, err={e}")
            return {"ok": False, "error": "写入失败（详见服务端日志）"}

    def update_role(self, code: str, name: str = "", note: str = "") -> bool:
        """改名称/说明（工具走 set_role_tools）"""
        role_code: str = (code or "").strip()
        if not role_code:
            return False
        try:
            self.ensure_schema()
            sets: list[str] = []
            args: list = []
            if name:
                sets.append("role_name=%s")
                args.append(name[:64])
            if note is not None and note != "":
                sets.append("note=%s")
                args.append(note[:255])
            if not sets:
                return True
            args.append(role_code)
            with self._conn() as c, c.cursor() as cur:
                cur.execute(f"UPDATE pa_roles SET {', '.join(sets)} WHERE role_code=%s", tuple(args))
            return True
        except Exception as e:
            log.error(f"更新角色失败: {role_code}, err={e}")
            return False

    def delete_role(self, code: str) -> dict:
        """删除角色：没有用户在使用即可删除；最后一个管理员角色不可删"""
        role_code: str = (code or "").strip()
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT is_builtin, is_admin FROM pa_roles WHERE role_code=%s", (role_code,))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": "角色不存在"}
                # 系统角色一律不可删：管理员 + 配置里的默认角色（公用角色）
                # 为什么：删了管理员就没人能进角色管理；删了默认角色，新建用户会落到「无角色」
                _protected: set[str] = {"admin"}
                _dc: str = self.default_role_code()
                if _dc:
                    _protected.add(_dc)
                if role_code in _protected:
                    return {"ok": False, "error": "系统角色不可删除（管理员 / 公用角色）"}
                # 只要没有**在用**的用户就可以删（预置角色同样允许）
                # 注意排除已删除用户：软删的用户如果还留着角色绑定，这角色就永远删不掉
                cur.execute(
                    "SELECT COUNT(*) FROM pa_user_roles ur "
                    "JOIN pa_users u ON u.username = ur.username "
                    "WHERE ur.role_code=%s AND u.status <> 'deleted'",
                    (role_code,),
                )
                n: int = int(cur.fetchone()[0] or 0)
                if n:
                    return {"ok": False, "error": f"还有 {n} 个用户在用这个角色，请先改掉他们的角色"}
                # 绑定关系里有脏数据（指向已删用户）→ 顺手清掉
                cur.execute(
                    "DELETE ur FROM pa_user_roles ur JOIN pa_users u ON u.username = ur.username "
                    "WHERE ur.role_code=%s AND u.status = 'deleted'",
                    (role_code,),
                )
                # 但最后一个管理员角色不能删，否则谁都进不了用户/角色管理
                if int(row[1] or 0) == 1:
                    cur.execute("SELECT COUNT(*) FROM pa_roles WHERE is_admin=1")
                    if int(cur.fetchone()[0] or 0) <= 1:
                        return {"ok": False, "error": "这是最后一个管理员角色，删掉就没人能管理权限了"}
                cur.execute("DELETE FROM pa_roles WHERE role_code=%s", (role_code,))
            self.invalidate()
            self._bump_roles_ver()
            log.warning(f"删除角色: code={role_code}")
            return {"ok": True, "role_code": role_code}
        except Exception as e:
            log.error(f"删除角色失败: {role_code}, err={e}")
            return {"ok": False, "error": "删除失败（详见服务端日志）"}

    # ---------- 角色分配（管理员） ----------
    def _bump_roles_ver(self) -> None:
        """角色定义变了 → 让目录缓存换个 key（避免保存前发出的请求把旧数据写回缓存）

        目录缓存原本是固定 key + 60s TTL。存在竞态：请求 A 在保存**之前**读到旧数据，
        保存后 DEL 掉 key，随后 A 把旧 payload 写回去 → 下一个读者（包括刚保存的页面）
        又看到旧勾选。用版本号做 key 后缀，旧版本的写入只会写进已废弃的 key。
        """
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.set("rbac:roles:ver", str(time.time()), 30 * 24 * 3600)
        except Exception:
            pass

    def set_role_tools(self, role_code: str, tools: list[str]) -> bool:
        """给角色分配工具（内置工具名 / mcp_* / mcp_xxx / '*'）。技能不参与分配（归用户自己）。"""
        code: str = (role_code or "").strip()
        if not code:
            return False
        cleaned: list[str] = []
        for t in tools or []:
            v: str = str(t).strip()
            if not v or v in _always_on():
                continue
            if v == "*":
                cleaned = ["*"]
                break
            if v not in cleaned:
                cleaned.append(v)
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT 1 FROM pa_roles WHERE role_code=%s", (code,))
                if not cur.fetchone():
                    log.warning(f"角色不存在，拒绝改工具: {code}")
                    return False
                cur.execute("UPDATE pa_roles SET tools=%s WHERE role_code=%s",
                            (json.dumps(cleaned, ensure_ascii=False), code))
            self.invalidate()   # 权限变了，所有用户的缓存都要清（下一轮重建 agent）
            self._bump_roles_ver()   # 目录缓存换 key，旧响应写不回新 key
            log.warning(f"角色工具变更: role={code}, tools={cleaned}")
            return True
        except Exception as e:
            log.error(f"角色工具变更失败: role={code}, err={e}")
            return False

    def list_roles(self) -> list[dict]:
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT role_code, role_name, tools, is_admin, note, is_builtin FROM pa_roles ORDER BY is_builtin DESC, role_code")
                rows = cur.fetchall()
            return [{
                "role_code": r[0], "role_name": r[1],
                "tools": json.loads(r[2] or "[]"),
                "is_admin": bool(r[3]), "note": r[4], "is_builtin": bool(r[5]),
            } for r in rows]
        except Exception as e:
            log.warning(f"读取角色失败: {e}")
            return []

    def list_users(self) -> list[dict]:
        self.ensure_schema()
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT username, role_code FROM pa_user_roles ORDER BY username")
                rows = cur.fetchall()
        except Exception as e:
            log.warning(f"读取用户角色失败: {e}")
            return []
        users: dict[str, list[str]] = {}
        for u, r in rows:
            users.setdefault(u, []).append(r)
        return [{"username": u, "roles": rs} for u, rs in sorted(users.items())]

    def assign(self, username: str, role_codes: list[str]) -> bool:
        """覆盖式设置某用户的角色（先清空再写入）"""
        user: str = (username or "").strip()
        wanted: list[str] = [str(c).strip() for c in (role_codes or []) if str(c).strip()]
        if not user or not wanted:
            return False
        self.ensure_schema()
        codes: list[str] = []
        try:
            with self._conn() as c, c.cursor() as cur:
                q = ",".join(["%s"] * len(wanted))
                cur.execute(f"SELECT role_code FROM pa_roles WHERE role_code IN ({q})", tuple(wanted))
                codes = [r[0] for r in cur.fetchall()]
        except Exception as e:
            log.error(f"校验角色失败: {e}")
            return False
        if not codes:
            return False
        try:
            with self._conn() as c, c.cursor() as cur:
                # 用户主数据也补一行：否则"手动新增 + 保存角色"后，用户列表（读 pa_users）里看不到
                cur.execute("INSERT IGNORE INTO pa_users (username) VALUES (%s)", (user,))
                cur.execute("DELETE FROM pa_user_roles WHERE username = %s", (user,))
                cur.executemany(
                    "INSERT INTO pa_user_roles (username, role_code) VALUES (%s, %s)",
                    [(user, code) for code in codes],
                )
            self.invalidate(user)
            log.info(f"RBAC 角色分配: user={user}, roles={codes}")
            return True
        except Exception as e:
            log.error(f"RBAC 角色分配失败: user={user}, err={e}")
            return False


rbac = RbacService()

__all__ = ["rbac", "RbacService", "DEFAULT_ROLES"]
