#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 服务管理接口（查看对所有登录用户开放；增删改/重连/公共开关仅管理员）

只支持 streamable_http：应用主动出网去调别人的 MCP，不在本机执行任何程序。
管理员在页面上填 名称 + 地址（+ 鉴权头）即可接入，每次写操作都记审计日志。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from config.Configs import cfg
from src.infra.utils.LogUtil import log
from src.web.security import require_login

router: APIRouter = APIRouter()

# MCP 工具中文名缓存（60 秒），原来是 globals() 里的 _META_CACHE
_META_CACHE: tuple[float, dict] = (0.0, {})


def _is_admin(username: str) -> bool:
    """管理员判定：RBAC 的 admin 角色；RBAC 关闭时回落到 tool_admin_users"""
    try:
        from src.infra.RbacService import rbac

        if rbac.enabled:
            return rbac.is_admin(username)
    except Exception:
        pass
    admins: set[str] = {x.strip() for x in str(getattr(cfg, "toolAdminUsers", "") or "").split(",") if x.strip()}
    if not admins:
        return True
    return username in admins


@router.get("/api/mcp/servers")
def list_servers(request: Request, username: str = Depends(require_login)):
    """已配置的 MCP 服务（所有登录用户可看；管理动作仍限管理员）

    查看放开是因为：这就是"平台接了哪些外部能力"的说明，藏起来反而让用户
    不知道哪些工具能用；而真正有副作用的操作（重连、重载）仍在写接口里校验管理员。
    """
    can_manage: bool = _is_admin(username)
    if not bool(getattr(cfg, "mcpEnabled", False)):
        return JSONResponse({"enabled": False, "servers": [], "can_manage": can_manage})
    try:
        import time as _t2

        from src.agent.mcp.client import mcp_manager

        # describe_servers 要逐个探测服务连接状态（起子进程），实测 1.3s。
        # 缓存 30 秒：切页签/翻页都走缓存；「重新连接全部」会清掉。
        # 默认「快速模式」：只读配置 + 已知连接状态，秒回；?probe=1 才真的去连
        _probe: bool = str(request.query_params.get("probe") or "") in ("1", "true", "yes")

        from src.agent.tools.registry import ALWAYS_ON_MCP_NAMES as _AON_MCP
        from src.infra.RbacService import rbac as _rbac
        from src.infra.utils import CacheUtil as _CU

        _RK: str = "cache:mcp:servers"

        # ① 服务清单：**只读配置，不起子进程**（秒回）
        all_servers: list = list(mcp_manager.describe_servers(connect=False) or [])

        # 「公共」集合：读库（工具管理 / MCP 服务页的开关改的就是这一列）
        try:
            from src.infra.ToolMetaService import tool_meta as _tm_pub

            _public_mcp: set = {t for t in _tm_pub.public_names() if str(t).startswith("mcp_")}
        except Exception:
            _public_mcp = set()
        if not _public_mcp:
            _public_mcp = set(_AON_MCP)

        # ② 用户能用的工具：**Redis 权限快照**里已经有了
        _perm: dict = _rbac.permissions(username) or {}
        _my_tools = _perm.get("tools")
        _see_all: bool = can_manage or _my_tools == "*"

        if not _see_all:
            _mine: set = {str(t) for t in (_my_tools or [])} | _public_mcp
            scoped: list = []
            for srv in all_servers:
                _prefix: str = f"mcp_{srv.get('name')}_"
                _hit_tools: list = sorted(t for t in _mine if t.startswith(_prefix))
                if _hit_tools:
                    scoped.append({**srv, "tools": _hit_tools, "tool_count": len(_hit_tools)})
            all_servers = scoped
        else:
            # 管理员 / 全权限：需要完整工具清单 → Redis → 内存 → 真连探测一次
            _cached = None if _probe else _CU.get(_RK)
            if isinstance(_cached, list) and _cached and any((s or {}).get("tools") for s in _cached):
                all_servers = list(_cached)
            elif _probe or not any((s or {}).get("tools") for s in all_servers):
                try:
                    all_servers = list(mcp_manager.describe_servers(connect=True) or [])
                    if any((s or {}).get("tools") for s in all_servers):
                        _CU.set(_RK, all_servers, 600)
                        log.info(f"MCP 清单已写 Redis: {_RK}, 服务 {len(all_servers)} 个")
                except Exception as e:
                    log.warning(f"MCP 探测失败（沿用现有结果）: {e}")
        qp = request.query_params
        try:
            page: int = max(1, int(qp.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size: int = max(1, min(100, int(qp.get("page_size") or 10)))
        except (TypeError, ValueError):
            page_size = 10
        # 把 MCP 工具登记到 pa_tools（只补不覆盖）——「公共」开关就存在这张表里
        try:
            from src.infra.ToolMetaService import tool_meta as _tm_reg

            _rows: list = []
            for _srv in all_servers:
                for _tn in (_srv.get("tools") or []):
                    _rows.append({
                        "name": str(_tn),
                        "grp": f"MCP·{_srv.get('name')}",
                        "risk": "exec",
                        "note": f"来自 MCP 服务 {_srv.get('name')}",
                        "always_on": str(_tn) in set(_AON_MCP),
                    })
            if _rows:
                _tm_reg.ensure_tools(_rows)
                try:
                    _public_mcp = {t for t in _tm_reg.public_names() if str(t).startswith("mcp_")} or _public_mcp
                except Exception:
                    pass
        except Exception as _e:
            log.warning(f"MCP 工具登记到 pa_tools 失败（忽略）: {_e}")

        # 给每个工具补中文名：从 pa_tools 里取 display_name / note
        try:
            import time as _t

            global _META_CACHE
            if _t.time() - _META_CACHE[0] > 60:
                from src.infra.ToolMetaService import tool_meta as _tm

                _META_CACHE = (_t.time(), _tm.all() or {})
            _meta: dict = _META_CACHE[1]
            for _srv in all_servers:
                _disp: dict = {}
                _names: dict = {}
                for _t in (_srv.get("tools") or []):
                    _m = _meta.get(str(_t)) or {}
                    _disp[str(_t)] = _m.get("display_name") or ""
                    _names[str(_t)] = _m.get("note") or ""
                _srv["tool_display"] = _disp
                _srv["tool_notes"] = _names
                # 服务级「公共」状态：全部工具都公共才算开了
                try:
                    _tl: list = [str(x) for x in (_srv.get("tools") or [])]
                    _srv["public"] = bool(_tl) and all(x in _public_mcp for x in _tl)
                except Exception:
                    _srv["public"] = False
        except Exception as _e:
            log.warning(f"补 MCP 工具中文名失败（忽略）: {_e}")

        total: int = len(all_servers)
        start: int = (page - 1) * page_size
        return JSONResponse({
            "enabled": True,
            "servers": all_servers[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "can_manage": can_manage,
        })
    except Exception as e:
        log.warning(f"读取 MCP 服务列表失败: {e}")
        return JSONResponse({"enabled": True, "servers": [], "can_manage": can_manage,
                             "error": str(e)[:200]})


@router.patch("/api/mcp/servers/{name}")
def update_server_meta(name: str, payload: dict | None = Body(None),
                       username: str = Depends(require_login)):
    """只改展示元数据（服务中文名 / 工具中文名 / 说明）——仅管理员

    command / args / env **不允许**通过接口修改：那等于把远程执行开给界面。
    """
    if not _is_admin(username):
        return JSONResponse({"ok": False, "error": "仅管理员可修改 MCP 服务配置"}, status_code=403)
    body: dict = payload or {}
    try:
        from src.agent.mcp.server_bean import mcp_servers

        res = mcp_servers.update_meta(
            name,
            display_name=body.get("display_name") if "display_name" in body else None,
            tool_names=body.get("tool_names") if "tool_names" in body else None,
            note=body.get("note") if "note" in body else None,
        )
        # 工具中文名同时写进 pa_tools（服务列表/角色页都从那里取展示名）
        try:
            _tn: dict = body.get("tool_names") or {}
            if _tn and res.get("ok"):
                from src.infra.TraceService import _conn as _mc

                _c = _mc()
                try:
                    with _c.cursor() as _cur:
                        for _t, _disp in _tn.items():
                            _d: str = str(_disp or "").strip()
                            if not _d:
                                continue
                            _cur.execute(
                                "INSERT INTO pa_tools (tool_name, display_name, grp, risk, note, always_on, enabled, ghost, sort_no) "
                                "VALUES (%s,%s,%s,%s,%s,0,1,0,200) "
                                "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name)",
                                (str(_t)[:128], _d[:128], "MCP", "read", ""),
                            )
                    _c.commit()
                finally:
                    _c.close()
        except Exception as _e:
            log.warning(f"同步工具中文名到 pa_tools 失败（忽略）: {_e}")
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    except Exception as e:
        log.error(f"修改 MCP 元数据失败: {name}, err={e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "修改失败（详见服务端日志）"}, status_code=500)


def _after_change() -> None:
    """配置变更后统一失效：工具缓存 / Redis 清单 / agent 实例"""
    try:
        from src.agent.mcp.client import mcp_manager

        mcp_manager.reload()
    except Exception:
        pass
    for key in ("cache:mcp:servers", "cache:tools:meta"):
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.delete(key)
        except Exception:
            pass
    try:
        from src.infra.RbacService import rbac as _rb
        from src.infra.utils import CacheUtil

        CacheUtil.delete_prefix("cache:rbac:catalog:")
        _rb._bump_roles_ver()
    except Exception:
        pass
    try:
        from src.agent.main_agent import evict_all_agents

        evict_all_agents()
    except Exception:
        pass


@router.post("/api/mcp/servers")
def create_server(payload: dict | None = Body(None), username: str = Depends(require_login)):
    """新增 MCP 服务（streamable_http）——仅管理员

    只接受 URL：没有 command/args/env，避免把"在服务器上起进程"开给界面。
    """
    if not _is_admin(username):
        return JSONResponse({"ok": False, "error": "仅管理员可新增 MCP 服务"}, status_code=403)
    if not bool(getattr(cfg, "mcpEnabled", False)):
        return JSONResponse({"ok": False, "error": "MCP 未启用（configs.cfg 的 [mcp] enabled）"},
                            status_code=400)
    body: dict = payload or {}
    from src.agent.mcp.client import normalize_headers, validate_remote

    name: str = str(body.get("name") or "").strip().lower()
    transport: str = str(body.get("transport") or "streamable_http").strip().lower()
    url: str = str(body.get("url") or "").strip()
    err: str = validate_remote(name, transport, url)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    try:
        from src.agent.mcp.server_bean import mcp_servers

        res = mcp_servers.create_server(
            name, transport, url,
            headers=normalize_headers(body.get("headers")),
            display_name=str(body.get("display_name") or "")[:64],
            note=str(body.get("note") or "")[:255],
            actor=username,
        )
        if res.get("ok"):
            _after_change()
        log.warning(f"MCP 服务新增: user={username}, name={name}, ok={res.get('ok')}")
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    except Exception as e:
        log.error(f"新增 MCP 服务异常: {name}, err={e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "新增失败（详见服务端日志）"}, status_code=500)


@router.put("/api/mcp/servers/{name}")
def update_server(name: str, payload: dict | None = Body(None),
                  username: str = Depends(require_login)):
    """修改 MCP 服务（URL / 鉴权头 / 显示名 / 说明 / 启用）——仅管理员"""
    if not _is_admin(username):
        return JSONResponse({"ok": False, "error": "仅管理员可修改 MCP 服务"}, status_code=403)
    body: dict = payload or {}
    from src.agent.mcp.client import TRANSPORTS, normalize_headers

    transport = body.get("transport")
    url = body.get("url")
    if transport is not None and str(transport).strip().lower() not in TRANSPORTS:
        return JSONResponse({"ok": False, "error": "只支持 streamable_http 传输"}, status_code=400)
    if url is not None and not str(url).strip().lower().startswith(("http://", "https://")):
        return JSONResponse({"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}, status_code=400)
    try:
        from src.agent.mcp.server_bean import mcp_servers

        res = mcp_servers.update_server(
            name,
            transport=str(transport).strip().lower() if transport is not None else None,
            url=str(url).strip() if url is not None else None,
            headers=normalize_headers(body.get("headers")) if "headers" in body else None,
            display_name=body.get("display_name") if "display_name" in body else None,
            note=body.get("note") if "note" in body else None,
            enabled=bool(body.get("enabled")) if "enabled" in body else None,
            actor=username,
        )
        if res.get("ok"):
            _after_change()
        log.warning(f"MCP 服务修改: user={username}, name={name}, ok={res.get('ok')}")
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    except Exception as e:
        log.error(f"修改 MCP 服务异常: {name}, err={e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "修改失败（详见服务端日志）"}, status_code=500)


@router.delete("/api/mcp/servers/{name}")
def delete_server(name: str, username: str = Depends(require_login)):
    """删除 MCP 服务——仅管理员"""
    if not _is_admin(username):
        return JSONResponse({"ok": False, "error": "仅管理员可删除 MCP 服务"}, status_code=403)
    try:
        from src.agent.mcp.server_bean import mcp_servers

        res = mcp_servers.delete_server(name, actor=username)
        if res.get("ok"):
            _after_change()
        log.warning(f"MCP 服务删除: user={username}, name={name}, ok={res.get('ok')}")
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    except Exception as e:
        log.error(f"删除 MCP 服务异常: {name}, err={e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "删除失败（详见服务端日志）"}, status_code=500)


@router.post("/api/mcp/servers/{name}/test")
def test_server(name: str, username: str = Depends(require_login)):
    """重连某个服务并返回它能提供的工具（排障用）"""
    if not _is_admin(username):
        return JSONResponse({"error": "仅管理员可操作"}, status_code=403)
    try:
        from src.agent.mcp.client import mcp_manager

        result = mcp_manager.test_server(name)
        log.info(f"MCP 测试连接: user={username}, server={name}, ok={result.get('ok')}")
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    except Exception as e:
        log.warning(f"MCP 测试连接异常: server={name}, err={e}")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@router.post("/api/mcp/reload")
def reload_servers(username: str = Depends(require_login)):
    """断开所有 MCP 连接并清缓存（改完配置后用；下一次对话会重新接入）"""
    if not _is_admin(username):
        return JSONResponse({"error": "仅管理员可操作"}, status_code=403)
    try:
        from src.agent.main_agent import evict_all_agents
        from src.agent.mcp.client import mcp_manager

        result = mcp_manager.reload()
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.delete("cache:mcp:servers")   # Redis 里的服务/工具清单也清掉
        except Exception:
            pass
        cleared: int = evict_all_agents()
        log.info(f"MCP 重载: user={username}, servers={result.get('servers')}, 清理 agent 缓存={cleared}")
        return JSONResponse({**result, "agents_cleared": cleared})
    except Exception as e:
        log.warning(f"MCP 重载失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@router.post("/api/mcp/servers/{name}/public")
def set_server_public(name: str, payload: dict | None = Body(None),
                      username: str = Depends(require_login)):
    """把整个 MCP 服务设为「公共」/取消公共（名下所有工具一起改）"""
    if not _is_admin(username):
        return JSONResponse({"ok": False, "error": "仅管理员可操作"}, status_code=403)
    public: bool = bool((payload or {}).get("public"))
    try:
        from src.agent.mcp.client import mcp_manager
        from src.infra.ToolMetaService import tool_meta

        tools: list = []
        for srv in mcp_manager.describe_servers(connect=False):
            if str(srv.get("name")) == name:
                tools = [str(t) for t in (srv.get("tools") or [])]
                break
        if not tools:      # 冷启动时还没探测出清单 → 用 pa_tools 里已登记的工具
            tools = sorted(k for k in (tool_meta.all() or {}) if k.startswith(f"mcp_{name}_"))
        if not tools:
            # 还是空 → 用「测试连接」时落库的清单（别的 worker 探测过也算数）
            try:
                from src.agent.mcp import server_bean as _sb

                _srv = next((x for x in _sb.mcp_servers.list_servers() if str(x.get("name")) == name), {})
                _tn = _srv.get("tool_names") or {}
                if isinstance(_tn, dict):
                    tools = [f"mcp_{name}_{k}" if not str(k).startswith("mcp_") else str(k) for k in _tn.keys()]
                elif isinstance(_tn, list):
                    tools = [str(k) for k in _tn]
            except Exception:
                tools = []
        if not tools:
            return JSONResponse({"ok": False, "error": "该服务还没有工具清单，先点一次「刷新」再试"},
                                status_code=400)

        n: int = tool_meta.set_public(tools, public)
        # 权限快照 + 角色目录 + agent 实例一起失效
        try:
            from src.infra.RbacService import rbac as _rb
            from src.infra.utils import CacheUtil

            CacheUtil.delete_prefix("rbac:perm:")
            CacheUtil.delete_prefix("cache:rbac:catalog:")
            CacheUtil.delete("cache:tools:meta")
            _rb._bump_roles_ver()
        except Exception:
            pass
        try:
            from src.agent.main_agent import evict_all_agents

            evict_all_agents()
        except Exception:
            pass
        log.warning(f"MCP 服务公共标记: server={name}, public={public}, tools={len(tools)}, actor={username}")
        return JSONResponse({"ok": True, "server": name, "public": public,
                             "tools": tools, "updated": n})
    except Exception as e:
        log.warning(f"设置 MCP 公共服务失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
