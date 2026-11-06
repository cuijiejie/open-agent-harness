#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RBAC 角色与权限接口

可见性规则（与「技能与工具」面板一致）：
· 查看：所有登录用户都能看自己有什么权限、系统有哪些角色、谁挂了什么角色
· 分配：仅管理员（`is_admin`）可写 → 前端按 `can_manage` 隐藏按钮
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

# 注意：`evict_all_agents` / `evict_agent_cache_by_user` 是**模块级函数**，不是 AgentManager 的方法
from src.agent.main_agent import evict_agent_cache_by_user, evict_all_agents
from src.infra.RbacService import rbac
from src.infra.utils.LogUtil import log
from src.web.security import require_login

router: APIRouter = APIRouter()


@router.get("/api/rbac/me")
def me(username: str = Depends(require_login)):
    """当前登录人的角色与权限（前端据此显隐功能）"""
    perm: dict = rbac.permissions(username)
    # 顺手在后台预热「新会话第一句」要用的东西（技能扫描 / MCP 工具清单）
    try:
        from src.infra.Prewarm import warm_user

        warm_user(username)
    except Exception:
        pass
    tools = perm["tools"]
    return JSONResponse({
        "username": username,
        "roles": perm["roles"],
        "is_admin": perm["is_admin"],
        "tools": tools if tools == "*" else sorted(tools),
        "enabled": rbac.enabled,
    })


@router.get("/api/users")
def list_users_admin(request: Request, username: str = Depends(require_login)):
    """用户管理列表（仅管理员）：账号/姓名、角色、状态、最近登录、会话数"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以查看用户列表"}, status_code=403)
    qp = request.query_params
    keyword: str = str(qp.get("q") or "")
    role: str = str(qp.get("role") or "")
    try:
        page: int = max(1, int(qp.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size: int = max(1, min(100, int(qp.get("page_size") or 10)))
    except (TypeError, ValueError):
        page_size = 10
    paged: dict = rbac.page_users(keyword, role, page, page_size)
    return JSONResponse({
        "ok": True,
        "users": paged["users"],
        "total": paged["total"],
        "page": paged["page"],
        "page_size": paged["page_size"],
        "roles": rbac.list_roles(),
        "default_role": rbac.default_role_code(),
        "can_manage": True,
    })


@router.post("/api/users")
def create_user(payload: dict | None = Body(None), username: str = Depends(require_login)):
    """新增用户：{"username": "zhangsan", "display_name": "张三", "roles": ["operator"]}"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以新增用户"}, status_code=403)
    body: dict = payload or {}
    res: dict = rbac.create_user(
        str(body.get("username") or ""),
        str(body.get("display_name") or ""),
        list(body.get("roles") or []),
        str(body.get("password") or ""),      # 不传则用默认密码 123456
    )
    if res.get("ok"):
        try:
            evict_agent_cache_by_user(res["username"])
        except Exception:
            pass
        log.warning(f"新增用户: 操作人={username}, 目标={res['username']}, 角色={res.get('roles')}")
        return JSONResponse(res)
    return JSONResponse(res, status_code=400)


@router.post("/api/users/{target}/password")
def reset_password(target: str, payload: dict | None = Body(None),
                   username: str = Depends(require_login)):
    """管理员重置密码：{"password": "xxx"}；不传则重置为默认 123456

    用户忘记密码时唯一的自助恢复入口（系统没有邮箱/短信找回）。
    """
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以重置密码"}, status_code=403)
    body: dict = payload or {}
    pwd: str = str(body.get("password") or "").strip()
    if pwd and len(pwd) < 6:
        return JSONResponse({"ok": False, "error": "密码至少 6 位"}, status_code=400)
    res: dict = rbac.set_password(target, pwd)
    if res.get("ok"):
        log.warning(f"重置密码: 操作人={username}, 目标={target}, "
                    f"{'使用默认密码' if not pwd else '自定义密码'}")
        return JSONResponse(res)
    return JSONResponse(res, status_code=400)


@router.post("/api/users/{target}/status")
def set_user_status(target: str, payload: dict | None = Body(None),
                    username: str = Depends(require_login)):
    """启用/停用账号（仅管理员；不能停用自己）"""
    if not rbac.is_admin(username):
        log.warning(f"越权尝试修改账号状态: user={username}, target={target}")
        return JSONResponse({"ok": False, "error": "只有管理员可以启用/停用账号"}, status_code=403)
    if target == username:
        return JSONResponse({"ok": False, "error": "不能停用自己的账号"}, status_code=400)
    status: str = str((payload or {}).get("status") or "")
    if status not in ("active", "disabled"):
        return JSONResponse({"ok": False, "error": "status 只能是 active 或 disabled"}, status_code=400)
    if status == "disabled" and rbac.is_last_active_admin(target):
        return JSONResponse({"ok": False,
                             "error": "这是最后一个启用中的管理员，停用会没人能管理系统设置"},
                            status_code=400)
    if not rbac.set_status(target, status):
        return JSONResponse({"ok": False, "error": "写入失败（详见服务端日志）"}, status_code=500)
    try:
        evict_agent_cache_by_user(target)
    except Exception:
        pass
    log.warning(f"账号状态变更: 操作人={username}, 目标={target}, status={status}")
    return JSONResponse({"ok": True, "username": target, "status": status})


@router.patch("/api/users/{target}")
def update_user(target: str, payload: dict | None = Body(None),
                username: str = Depends(require_login)):
    """改姓名：{"display_name": "张三"}"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以修改用户"}, status_code=403)
    body: dict = payload or {}
    if "display_name" not in body:
        return JSONResponse({"ok": False, "error": "只支持修改 display_name（角色走保存角色）"},
                            status_code=400)
    if not rbac.update_user(target, str(body.get("display_name") or "")):
        return JSONResponse({"ok": False, "error": "保存失败"}, status_code=500)
    log.info(f"修改用户姓名: 操作人={username}, 目标={target}")
    return JSONResponse({"ok": True, "username": target})


@router.delete("/api/users/{target}")
def delete_user(target: str, username: str = Depends(require_login)):
    """软删除用户（隐藏 + 禁止登录；聊天与日志保留）"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以删除用户"}, status_code=403)
    res: dict = rbac.delete_user(target, username)
    if res.get("ok"):
        try:
            evict_agent_cache_by_user(target)
        except Exception:
            pass
        return JSONResponse(res)
    return JSONResponse(res, status_code=400)


@router.post("/api/rbac/roles")
def create_role(payload: dict | None = Body(None), username: str = Depends(require_login)):
    """新增自定义角色：{"code": "finance_review", "name": "财务复核", "note": "…", "tools": [...]}"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以新增角色"}, status_code=403)
    body: dict = payload or {}
    res: dict = rbac.create_role(
        str(body.get("code") or ""), str(body.get("name") or ""),
        str(body.get("note") or ""), list(body.get("tools") or []),
    )
    if res.get("ok"):
        log.warning(f"新增角色: 操作人={username}, code={res['role_code']}")
        return JSONResponse(res)
    return JSONResponse(res, status_code=400)


@router.patch("/api/rbac/roles/{code}")
def update_role(code: str, payload: dict | None = Body(None),
                username: str = Depends(require_login)):
    """改角色名称/说明：{"name": "财务复核", "note": "…"}"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以修改角色"}, status_code=403)
    body: dict = payload or {}
    if not rbac.update_role(code, str(body.get("name") or ""), str(body.get("note") or "")):
        return JSONResponse({"ok": False, "error": "保存失败"}, status_code=500)
    log.info(f"修改角色: 操作人={username}, code={code}")
    return JSONResponse({"ok": True, "role_code": code})


@router.delete("/api/rbac/roles/{code}")
def delete_role(code: str, username: str = Depends(require_login)):
    """删除自定义角色（预置角色、仍被使用的角色会被拒绝）"""
    if not rbac.is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以删除角色"}, status_code=403)
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
        return JSONResponse(res)
    return JSONResponse(res, status_code=400)


@router.post("/api/rbac/roles/{code}/tools")
def set_role_tools(code: str, payload: dict | None = Body(None),
                   username: str = Depends(require_login)):
    """给角色分配工具：{"tools": ["sandbox_shell", "mcp_*"]}（`*` = 全部）"""
    if not rbac.is_admin(username):
        log.warning(f"越权尝试修改角色工具: user={username}, role={code}")
        return JSONResponse({"ok": False, "error": "只有管理员可以修改角色工具"}, status_code=403)
    body: dict = payload or {}
    if "tools" not in body:
        return JSONResponse({"ok": False, "error": "缺少 tools 字段"}, status_code=400)
    # 允许空列表：角色可以一个业务工具都不给（仍有人人可用的工具）
    tools: list = list(body.get("tools") or [])
    if not rbac.set_role_tools(code, tools):
        return JSONResponse({"ok": False, "error": "保存失败（角色不存在或写入异常）"}, status_code=500)
    try:
        try:
            from src.infra.utils import CacheUtil as _CU2

            _CU2.delete_prefix("rbac:perm:")
            _CU2.delete("cache:rbac:catalog")
        except Exception:
            pass
        try:
            rbac.invalidate()
        except Exception:
            pass
        evict_all_agents()
    except Exception as e:
        log.warning(f"角色工具变更后清理 agent 缓存失败: {e}")
    log.warning(f"角色工具变更: 操作人={username}, role={code}, tools={tools}")
    return JSONResponse({"ok": True, "role": code, "tools": tools})


@router.get("/api/rbac/roles")
def list_roles(username: str = Depends(require_login)):
    return JSONResponse({
        "roles": rbac.list_roles(),
        "can_manage": rbac.is_admin(username),
        "enabled": rbac.enabled,
    })


@router.get("/api/rbac/users")
def list_users(username: str = Depends(require_login)):
    return JSONResponse({
        "users": rbac.list_users(),
        "can_manage": rbac.is_admin(username),
    })


@router.post("/api/rbac/users")
def assign_roles(payload: dict | None = Body(None), username: str = Depends(require_login)):
    """覆盖式设置某用户的角色：{"username": "yaoyue", "roles": ["operator"]}"""
    if not rbac.is_admin(username):
        target_req: str = str((payload or {}).get("username") or "")
        log.warning(f"RBAC 越权尝试: user={username} 试图修改 {target_req} 的角色")
        return JSONResponse({"ok": False, "error": "只有管理员可以分配角色"}, status_code=403)

    body: dict = payload or {}
    target: str = str(body.get("username") or "").strip()
    roles: list = list(body.get("roles") or [])
    if not target or not roles:
        return JSONResponse({"ok": False, "error": "username 与 roles 均不能为空"}, status_code=400)
    # 可分配的角色以数据库为准（自定义角色同样可选），不再用硬编码模板
    valid_codes: set = {str(r["role_code"]) for r in rbac.list_roles()}
    bad: list = [r for r in roles if r not in valid_codes]
    if bad:
        return JSONResponse({"ok": False, "error": f"未知角色: {bad}，可选 {sorted(valid_codes)}"},
                            status_code=400)

    if not rbac.assign(target, roles):
        return JSONResponse({"ok": False, "error": "写入角色失败（详见服务端日志）"}, status_code=500)

    # 角色变了 → 三层缓存都要清，否则要等 TTL 才生效
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

    log.info(f"RBAC 角色变更: 操作人={username}, 目标={target}, 角色={roles}")
    return JSONResponse({"ok": True, "username": target, "roles": roles})


@router.get("/api/rbac/catalog")
def catalog(request: Request, username: str = Depends(require_login)):
    """角色 → 工具 分配面板需要的全部数据：内置工具、MCP 工具、角色定义

    说明：技能包不在这里 —— 技能是**用户自己的**（各自沙箱），不需要按角色分配。
    """
    from src.infra.utils import CacheUtil as _CU

    # 分页/搜索参数必须进缓存键：之前固定 key，命中后 60 秒内翻页/搜索都返回第 1 页
    qp = request.query_params
    try:
        page: int = max(1, int(qp.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size: int = max(1, min(100, int(qp.get("page_size") or 10)))
    except (TypeError, ValueError):
        page_size = 10
    keyword: str = str(qp.get("q") or "")

    import hashlib as _hl

    _ck: str = (
        "cache:rbac:catalog:"
        f"{page}:{page_size}:{_hl.md5(keyword.encode('utf-8')).hexdigest()[:8]}"
        f":v{_CU.get('rbac:roles:ver') or '0'}"   # 角色定义版本：保存工具后换 key
    )
    _cached = _CU.get(_ck)
    if _cached is not None:
        # can_manage 与用户相关，不进缓存
        return JSONResponse({**_cached, "can_manage": rbac.is_admin(username)})

    try:
        from src.agent.tools.registry import ALWAYS_ON_MCP_NAMES
        from src.agent.tools.registry import describe as describe_tools

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
            # 已停用的不进角色页；人人可用的（always_on）要展示，前端渲染成勾选且禁用
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
                    "always_on": bool((_tool_meta or {}).get(str(tname), {}).get("always_on"))
                    or (str(tname) not in (_tool_meta or {}) and str(tname) in ALWAYS_ON_MCP_NAMES),
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
        "all_roles": rbac.list_roles(),
        "builtin": builtin,
        "mcp": mcp,
        "enabled": rbac.enabled,
    }
    # 缓存里不放 can_manage（与当前用户有关），取缓存时再补
    _CU.set(_ck, _payload, 60)
    return JSONResponse({**_payload, "can_manage": rbac.is_admin(username)})
