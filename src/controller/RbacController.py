"""RBAC 角色与权限接口

可见性规则（与「技能与工具」面板一致）：
· 查看：所有登录用户都能看自己有什么权限、系统有哪些角色、谁挂了什么角色
  （排障时不用翻日志）
· 分配：仅管理员（`is_admin`）可写 → 前端按 `can_manage` 隐藏按钮
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from src.controller.ChatController import _get_username
# 注意：`evict_all_agents` / `evict_agent_cache_by_user` 是**模块级函数**，
# 不是 AgentManager 的方法。以前写成 `_agent_manager.evict_all_agents()`，
# 每次都抛 AttributeError 被 except 吞掉 → 改角色/停用用户后 agent 缓存从来没被清过。
from src.agent.main_agent import evict_agent_cache_by_user, evict_all_agents
from src.infra.RbacService import DEFAULT_ROLES, rbac
from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

RbacController = Blueprint('RbacController', __name__)


@RbacController.route('/api/rbac/me', methods=['GET'])
@login_required
def me() -> tuple[Response, int]:
    """当前登录人的角色与权限（前端据此显隐功能）"""
    username: str = _get_username()
    perm: dict = rbac.permissions(username)
    # 顺手在后台预热「新会话第一句」要用的东西（技能扫描 / MCP 工具清单）。
    # 只读、失败吞掉、同用户 120 秒最多一次 → 对功能没有影响。
    try:
        from src.infra.Prewarm import warm_user

        warm_user(username)
    except Exception:
        pass
    tools = perm["tools"]
    return jsonify({
        "username": username,
        "roles": perm["roles"],
        "is_admin": perm["is_admin"],
        "tools": tools if tools == "*" else sorted(tools),
        "enabled": rbac.enabled,
    }), 200


@RbacController.route('/api/users', methods=['GET'])
@login_required
def list_users_admin() -> tuple[Response, int]:
    """用户管理列表（仅管理员）：账号/姓名、角色、状态、最近登录、会话数"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以查看用户列表"}), 403
    keyword: str = str(request.args.get("q") or "")
    role: str = str(request.args.get("role") or "")
    page: int = int(request.args.get("page") or 1)
    page_size: int = int(request.args.get("page_size") or 10)
    paged: dict = rbac.page_users(keyword, role, page, page_size)
    return jsonify({
        "ok": True,
        "users": paged["users"],
        "total": paged["total"],
        "page": paged["page"],
        "page_size": paged["page_size"],
        "roles": rbac.list_roles(),        # 全量角色：筛选下拉与角色标签要用
        "default_role": rbac.default_role_code(),   # 新增用户时默认勾选的角色
        "can_manage": True,
    }), 200


@RbacController.route('/api/users/<target>/status', methods=['POST'])
@login_required
def set_user_status(target: str) -> tuple[Response, int]:
    """启用/停用账号（仅管理员；不能停用自己）"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        log.warning(f"越权尝试修改账号状态: user={operator}, target={target}")
        return jsonify({"ok": False, "error": "只有管理员可以启用/停用账号"}), 403
    if target == operator:
        return jsonify({"ok": False, "error": "不能停用自己的账号"}), 400
    status: str = str((request.get_json(silent=True) or {}).get("status") or "")
    if status not in ("active", "disabled"):
        return jsonify({"ok": False, "error": "status 只能是 active 或 disabled"}), 400
    if not rbac.set_status(target, status):
        return jsonify({"ok": False, "error": "写入失败（详见服务端日志）"}), 500
    try:
        evict_agent_cache_by_user(target)
    except Exception:
        pass
    log.warning(f"账号状态变更: 操作人={operator}, 目标={target}, status={status}")
    return jsonify({"ok": True, "username": target, "status": status}), 200

@RbacController.route('/api/users', methods=['POST'])
@login_required
def create_user() -> tuple[Response, int]:
    """新增用户：{"username": "zhangsan", "display_name": "张三", "roles": ["operator"]}"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以新增用户"}), 403
    body: dict = request.get_json(silent=True) or {}
    res: dict = rbac.create_user(
        str(body.get("username") or ""),
        str(body.get("display_name") or ""),
        list(body.get("roles") or []),
    )
    if res.get("ok"):
        try:
            evict_agent_cache_by_user(res["username"])
        except Exception:
            pass
        log.warning(f"新增用户: 操作人={operator}, 目标={res['username']}, 角色={res.get('roles')}")
        return jsonify(res), 200
    return jsonify(res), 400


@RbacController.route('/api/users/<target>', methods=['PATCH'])
@login_required
def update_user(target: str) -> tuple[Response, int]:
    """改姓名：{"display_name": "张三"}"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以修改用户"}), 403
    body: dict = request.get_json(silent=True) or {}
    if "display_name" not in body:
        return jsonify({"ok": False, "error": "只支持修改 display_name（角色走保存角色）"}), 400
    if not rbac.update_user(target, str(body.get("display_name") or "")):
        return jsonify({"ok": False, "error": "保存失败"}), 500
    log.info(f"修改用户姓名: 操作人={operator}, 目标={target}")
    return jsonify({"ok": True, "username": target}), 200


@RbacController.route('/api/users/<target>', methods=['DELETE'])
@login_required
def delete_user(target: str) -> tuple[Response, int]:
    """软删除用户（隐藏 + 禁止登录；聊天与日志保留）"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以删除用户"}), 403
    res: dict = rbac.delete_user(target, operator)
    if res.get("ok"):
        try:
            evict_agent_cache_by_user(target)
        except Exception:
            pass
        return jsonify(res), 200
    return jsonify(res), 400


@RbacController.route('/api/rbac/roles', methods=['POST'])
@login_required
def create_role() -> tuple[Response, int]:
    """新增自定义角色：{"code": "finance_review", "name": "财务复核", "note": "…", "tools": [...]}"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以新增角色"}), 403
    body: dict = request.get_json(silent=True) or {}
    res: dict = rbac.create_role(
        str(body.get("code") or ""), str(body.get("name") or ""),
        str(body.get("note") or ""), list(body.get("tools") or []),
    )
    if res.get("ok"):
        log.warning(f"新增角色: 操作人={operator}, code={res['role_code']}")
        return jsonify(res), 200
    return jsonify(res), 400


@RbacController.route('/api/rbac/roles/<code>', methods=['PATCH'])
@login_required
def update_role(code: str) -> tuple[Response, int]:
    """改角色名称/说明：{"name": "财务复核", "note": "…"}"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以修改角色"}), 403
    body: dict = request.get_json(silent=True) or {}
    if not rbac.update_role(code, str(body.get("name") or ""), str(body.get("note") or "")):
        return jsonify({"ok": False, "error": "保存失败"}), 500
    log.info(f"修改角色: 操作人={operator}, code={code}")
    return jsonify({"ok": True, "role_code": code}), 200


@RbacController.route('/api/rbac/roles/<code>', methods=['DELETE'])
@login_required
def delete_role(code: str) -> tuple[Response, int]:
    """删除自定义角色（预置角色、仍被使用的角色会被拒绝）"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        return jsonify({"ok": False, "error": "只有管理员可以删除角色"}), 403
    res: dict = rbac.delete_role(code)
    if res.get("ok"):
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.delete_prefix("rbac:perm:")     # 角色没了，所有人的权限快照都失效
            CacheUtil.delete("cache:rbac:catalog")
        except Exception:
            pass
        try:
            rbac.invalidate()
            evict_all_agents()
        except Exception:
            pass
        return jsonify(res), 200
    return jsonify(res), 400


@RbacController.route('/api/rbac/catalog', methods=['GET'])
@login_required
def catalog() -> tuple[Response, int]:
    """角色 → 工具 分配面板需要的全部数据：内置工具、MCP 工具、角色定义

    说明：技能包不在这里 —— 技能是**用户自己的**（各自沙箱），不需要按角色分配。
    """
    username: str = _get_username()
    import time as _t4

    from src.infra.utils import CacheUtil as _CU

    # 分页/搜索参数必须进缓存键：之前固定 key，命中后 60 秒内翻页/搜索都返回第 1 页
    try:
        page: int = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size: int = max(1, min(100, int(request.args.get("page_size") or 10)))
    except (TypeError, ValueError):
        page_size = 10
    keyword: str = str(request.args.get("q") or "")

    import hashlib as _hl

    _ck: str = (
        "cache:rbac:catalog:"
        f"{page}:{page_size}:{_hl.md5(keyword.encode('utf-8')).hexdigest()[:8]}"
        f":v{_CU.get('rbac:roles:ver') or '0'}"   # 角色定义版本：保存工具后换 key，
                                                  # 保存前发出的旧请求写不进新缓存
    )
    _cached = _CU.get(_ck)
    if _cached is not None:
        # can_manage 与用户相关，不进缓存（否则会把管理员的权限渲染给普通用户）
        return jsonify({**_cached, "can_manage": rbac.is_admin(username)}), 200

    try:
        from src.agent.tools.registry import describe as describe_tools
        from src.agent.tools.registry import ALWAYS_ON_MCP_NAMES
        from src.agent.tools.registry import ALWAYS_ON_NAMES as ALWAYS_ON_TOOLS

        builtin: list[dict] = [
            {
                "name": t["name"],
                "display_name": t.get("display_name") or t["name"],
                "grp": t.get("grp") or "其他",
                "risk": t["risk"],
                "note": t.get("note", ""),
                "always_on": bool(t.get("always_on")),
                "enabled": bool(t.get("enabled", True)),
            }
            for t in describe_tools()
            # 已停用的不进角色页（不能再授权给别人）；
            # 人人可用的（always_on）**要展示**，前端渲染成勾选且禁用，让管理员看得见
            if t.get("enabled", True)
        ]
    except Exception as e:
        log.warning(f"读取内置工具清单失败: {e}")
        builtin = []

    mcp: list[dict] = []
    _tool_meta: dict = {}
    try:
        from src.infra.ToolMetaService import tool_meta as _tm2

        _tool_meta = _tm2.all() or {}
    except Exception:
        pass
    try:
        from src.agent.mcp.client import mcp_manager

        for srv in mcp_manager.describe_servers():
            for tname in (srv.get("tools") or []):
                mcp.append({
                    "name": str(tname),
                    "server": srv.get("name", ""),
                    "connected": bool(srv.get("connected")),
                    # 人人可用（公共）：读库 pa_tools.always_on，读不到回落到常量
                    "always_on": bool((_tool_meta or {}).get(str(tname), {}).get("always_on"))
                    or (str(tname) not in (_tool_meta or {}) and str(tname) in ALWAYS_ON_MCP_NAMES),
                    # 中文名（来自 pa_tools，管理员可在「MCP 服务」编辑）
                    "display_name": str((_tool_meta or {}).get(str(tname), {}).get("display_name") or ""),
                })
    except Exception as e:
        log.warning(f"读取 MCP 工具清单失败（不影响内置工具）: {e}")

    paged_roles: dict = rbac.page_roles(page, page_size, keyword)
    _payload = {
        "ok": True,
        "roles": paged_roles["roles"],
        "roles_total": paged_roles["total"],
        "page": paged_roles["page"],
        "page_size": paged_roles["page_size"],
        "all_roles": rbac.list_roles(),     # 全量：角色名映射用
        "builtin": builtin,
        "mcp": mcp,
        "enabled": rbac.enabled,
    }
    # 缓存里不放 can_manage（与当前用户有关），取缓存时再补
    _CU.set(_ck, _payload, 60)
    return jsonify({**_payload, "can_manage": rbac.is_admin(username)}), 200


@RbacController.route('/api/rbac/roles/<code>/tools', methods=['POST'])
@login_required
def set_role_tools(code: str) -> tuple[Response, int]:
    """给角色分配工具：{"tools": ["demo_say_hello", "mcp_*"]}（`*` = 全部）"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        log.warning(f"越权尝试修改角色工具: user={operator}, role={code}")
        return jsonify({"ok": False, "error": "只有管理员可以修改角色工具"}), 403
    body: dict = request.get_json(silent=True) or {}
    if "tools" not in body:
        return jsonify({"ok": False, "error": "缺少 tools 字段"}), 400
    # 允许空列表：角色可以一个业务工具都不给（仍有人人可用的工具）
    tools: list = list(body.get("tools") or [])
    if not rbac.set_role_tools(code, tools):
        return jsonify({"ok": False, "error": "保存失败（角色不存在或写入异常）"}), 500
    try:
        try:
            from src.infra.utils import CacheUtil as _CU2

            _CU2.delete_prefix("rbac:perm:")          # 所有用户权限快照
            _CU2.delete("cache:rbac:catalog")         # 角色工具目录
        except Exception:
            pass
        try:
            rbac.invalidate()                          # 进程内缓存
        except Exception:
            pass
        evict_all_agents()   # 角色权限变了 → 所有用户缓存重建（并广播给其它 worker）
    except Exception as e:
        log.warning(f"角色工具变更后清理 agent 缓存失败: {e}")
    log.warning(f"角色工具变更: 操作人={operator}, role={code}, tools={tools}")
    return jsonify({"ok": True, "role": code, "tools": tools}), 200


@RbacController.route('/api/rbac/roles', methods=['GET'])
@login_required
def list_roles() -> tuple[Response, int]:
    username: str = _get_username()
    return jsonify({
        "roles": rbac.list_roles(),
        "can_manage": rbac.is_admin(username),
        "enabled": rbac.enabled,
    }), 200


@RbacController.route('/api/rbac/users', methods=['GET'])
@login_required
def list_users() -> tuple[Response, int]:
    username: str = _get_username()
    return jsonify({
        "users": rbac.list_users(),
        "can_manage": rbac.is_admin(username),
    }), 200


@RbacController.route('/api/rbac/users', methods=['POST'])
@login_required
def assign_roles() -> tuple[Response, int]:
    """覆盖式设置某用户的角色：{"username": "yaoyue", "roles": ["operator"]}"""
    operator: str = _get_username()
    if not rbac.is_admin(operator):
        target_req: str = str((request.get_json(silent=True) or {}).get("username") or "")
        log.warning(f"RBAC 越权尝试: user={operator} 试图修改 {target_req} 的角色")
        return jsonify({"ok": False, "error": "只有管理员可以分配角色"}), 403

    body: dict = request.get_json(silent=True) or {}
    target: str = str(body.get("username") or "").strip()
    roles: list = list(body.get("roles") or [])
    if not target or not roles:
        return jsonify({"ok": False, "error": "username 与 roles 均不能为空"}), 400
    # 可分配的角色以数据库为准（自定义角色同样可选），不再用硬编码模板
    valid_codes: set = {str(r["role_code"]) for r in rbac.list_roles()}
    bad: list = [r for r in roles if r not in valid_codes]
    if bad:
        return jsonify({"ok": False, "error": f"未知角色: {bad}，可选 {sorted(valid_codes)}"}), 400

    if not rbac.assign(target, roles):
        return jsonify({"ok": False, "error": "写入角色失败（详见服务端日志）"}), 500

    # 角色变了 → 三层缓存都要清，否则要等 TTL 才生效
    #   ① Redis 权限快照 rbac:perm:<user>
    #   ② 进程内 rbac._cache
    #   ③ agent 实例（工具清单在装配时已固化）
    try:
        from src.infra.utils import CacheUtil

        CacheUtil.delete(f"rbac:perm:{target}")
    except Exception:
        pass
    try:
        rbac.invalidate(target)
    except Exception:
        pass
    try:
        evict_agent_cache_by_user(target)
    except Exception as e:
        log.warning(f"角色变更后清理 agent 缓存失败: user={target}, err={e}")

    log.info(f"RBAC 角色变更: 操作人={operator}, 目标={target}, 角色={roles}")
    return jsonify({"ok": True, "username": target, "roles": roles}), 200
