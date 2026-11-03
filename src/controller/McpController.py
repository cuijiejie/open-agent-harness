"""MCP 服务管理接口（**仅管理员**）

安全边界（务必保持）：
MCP server 是**在应用机上启动的程序**，所以这里**只提供查看与连通性测试**，
不提供"在页面上新增/修改 server"的能力 —— 那等于把任意命令执行开给界面用户。
要加服务，仍然改 `configs.cfg` 的 `[mcp] servers=`（由管理员/运维操作，可审计可回滚）。
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from config.Configs import cfg
from src.controller.ChatController import _get_username
from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

McpController = Blueprint('McpController', __name__)


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


@McpController.route('/api/mcp/servers', methods=['GET'])
@login_required
def list_servers() -> tuple[Response, int]:
    """已配置的 MCP 服务（**所有登录用户可看**；连接/重载等管理动作仍限管理员）

    查看放开是因为：这就是"平台接了哪些外部能力"的说明，藏起来反而让用户
    不知道哪些工具能用；而真正有副作用的操作（重连、重载）仍在写接口里校验管理员。
    """
    username = _get_username()
    can_manage: bool = _is_admin(username)
    if not bool(getattr(cfg, "mcpEnabled", False)):
        return jsonify({'enabled': False, 'servers': [], 'can_manage': can_manage}), 200
    try:
        import time as _t2

        from src.agent.mcp.client import mcp_manager

        # describe_servers 要逐个探测服务连接状态（起子进程），实测 1.3s。
        # 缓存 30 秒：切页签/翻页都走缓存；「重新连接全部」会清掉。
        # 默认「快速模式」：只读配置 + 已知连接状态，秒回；
        # ?probe=1 才真的去连（「测试」「重新连接」用）
        _probe: bool = str(request.args.get('probe') or '') in ('1', 'true', 'yes')

        from src.infra.utils import CacheUtil as _CU
        from src.infra.RbacService import rbac as _rbac
        from src.agent.tools.registry import ALWAYS_ON_MCP_NAMES as _AON_MCP

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

        # ② 用户能用的工具：**Redis 权限快照**（rbac:perm:<user>）里已经有了，
        #    直接按 mcp_<服务名>_ 前缀取，不需要问服务端「你有哪些工具」。
        _perm: dict = _rbac.permissions(username) or {}
        _my_tools = _perm.get("tools")
        _see_all: bool = can_manage or _my_tools == "*"

        if not _see_all:
            # 普通用户：权限快照 ∩ 配置里的服务 → 服务端声明的工具清单根本不需要知道。
            # 公共工具装配时人人都有，这里必须同样算进去。
            _mine: set = {str(t) for t in (_my_tools or [])} | _public_mcp
            scoped: list = []
            for srv in all_servers:
                _prefix: str = f"mcp_{srv.get('name')}_"
                _hit_tools: list = sorted(t for t in _mine if t.startswith(_prefix))
                if _hit_tools:
                    scoped.append({**srv, 'tools': _hit_tools, 'tool_count': len(_hit_tools)})
            all_servers = scoped
        else:
            # 管理员 / 全权限：需要完整工具清单 → Redis → 内存 → 真连探测一次（1.3s，结果回写 Redis）
            _cached = None if _probe else _CU.get(_RK)
            if isinstance(_cached, list) and _cached and any((s or {}).get('tools') for s in _cached):
                all_servers = list(_cached)
            elif _probe or not any((s or {}).get('tools') for s in all_servers):
                try:
                    all_servers = list(mcp_manager.describe_servers(connect=True) or [])
                    if any((s or {}).get('tools') for s in all_servers):
                        _CU.set(_RK, all_servers, 600)
                        log.info(f"MCP 清单已写 Redis: {_RK}, 服务 {len(all_servers)} 个")
                except Exception as e:
                    log.warning(f"MCP 探测失败（沿用现有结果）: {e}")
        try:
            page: int = max(1, int(request.args.get('page') or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size: int = max(1, min(100, int(request.args.get('page_size') or 10)))
        except (TypeError, ValueError):
            page_size = 10
        # 把 MCP 工具登记到 pa_tools（只补不覆盖）——「公共」开关就存在这张表里。
        # 必须放在拿到真实工具清单之后（管理员分支会真连一次），否则冷启动时清单是空的。
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
                        # 仅对「新登记」的行生效；已存在的不覆盖管理员的设置
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

        # 给每个工具补中文名：从 pa_tools 里取 display_name / note（管理员可在工具页编辑）
        try:
            import time as _t

            _cached = globals().get("_META_CACHE") or (0.0, {})
            if _t.time() - _cached[0] > 60:
                from src.infra.ToolMetaService import tool_meta as _tm

                _cached = (_t.time(), _tm.all() or {})
                globals()["_META_CACHE"] = _cached
            _meta: dict = _cached[1]
            for _srv in all_servers:
                _disp: dict = {}
                _names: dict = {}
                for _t in (_srv.get('tools') or []):
                    _m = _meta.get(str(_t)) or {}
                    _disp[str(_t)] = _m.get('display_name') or ''
                    _names[str(_t)] = _m.get('note') or ''
                _srv['tool_display'] = _disp
                _srv['tool_notes'] = _names
                # 服务级「公共」状态：全部工具都公共才算开了
                try:
                    _tl: list = [str(x) for x in (_srv.get('tools') or [])]
                    _srv['public'] = bool(_tl) and all(x in _public_mcp for x in _tl)
                except Exception:
                    _srv['public'] = False
        except Exception as _e:
            log.warning(f"补 MCP 工具中文名失败（忽略）: {_e}")

        total: int = len(all_servers)
        start: int = (page - 1) * page_size
        return jsonify({
            'enabled': True,
            'servers': all_servers[start:start + page_size],
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': max(1, (total + page_size - 1) // page_size),
            'can_manage': can_manage,
        }), 200
    except Exception as e:
        log.warning(f"读取 MCP 服务列表失败: {e}")
        return jsonify({'enabled': True, 'servers': [], 'can_manage': can_manage, 'error': str(e)[:200]}), 200


@McpController.route('/api/mcp/servers/<name>', methods=['PATCH'])
@login_required
def update_server_meta(name: str) -> tuple[Response, int]:
    """只改展示元数据（服务中文名 / 工具中文名 / 说明）——仅管理员

    command / args / env **不允许**通过接口修改：那等于把远程执行开给界面。
    """
    username = _get_username()
    if not _is_admin(username):
        return jsonify({'ok': False, 'error': '仅管理员可修改 MCP 服务配置'}), 403
    body: dict = request.get_json(silent=True) or {}
    try:
        from src.agent.mcp.server_bean import mcp_servers

        res = mcp_servers.update_meta(
            name,
            display_name=body.get('display_name') if 'display_name' in body else None,
            tool_names=body.get('tool_names') if 'tool_names' in body else None,
            note=body.get('note') if 'note' in body else None,
        )
        # 工具中文名同时写进 pa_tools（服务列表/角色页都从那里取展示名）
        try:
            _tn: dict = body.get('tool_names') or {}
            if _tn and res.get('ok'):
                from src.infra.TraceService import _conn as _mc

                _c = _mc()
                try:
                    with _c.cursor() as _cur:
                        for _t, _disp in _tn.items():
                            _d: str = str(_disp or '').strip()
                            if not _d:
                                continue
                            _cur.execute(
                                "INSERT INTO pa_tools (tool_name, display_name, grp, risk, note, always_on, enabled, ghost, sort_no) "
                                "VALUES (%s,%s,%s,%s,%s,0,1,0,200) "
                                "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name)",
                                (str(_t)[:128], _d[:128], 'MCP', 'read', ''),
                            )
                    _c.commit()
                finally:
                    _c.close()
        except Exception as _e:
            log.warning(f"同步工具中文名到 pa_tools 失败（忽略）: {_e}")
        return jsonify(res), (200 if res.get('ok') else 400)
    except Exception as e:
        log.error(f"修改 MCP 元数据失败: {name}, err={e}", exc_info=True)
        return jsonify({'ok': False, 'error': '修改失败（详见服务端日志）'}), 500


@McpController.route('/api/mcp/servers/<name>/test', methods=['POST'])
@login_required
def test_server(name: str) -> tuple[Response, int]:
    """重连某个服务并返回它能提供的工具（排障用）"""
    username = _get_username()
    if not _is_admin(username):
        return jsonify({'error': '仅管理员可操作'}), 403
    try:
        from src.agent.mcp.client import mcp_manager

        result = mcp_manager.test_server(name)
        log.info(f"MCP 测试连接: user={username}, server={name}, ok={result.get('ok')}")
        return jsonify(result), 200 if result.get("ok") else 400
    except Exception as e:
        log.warning(f"MCP 测试连接异常: server={name}, err={e}")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@McpController.route('/api/mcp/reload', methods=['POST'])
@login_required
def reload_servers() -> tuple[Response, int]:
    """断开所有 MCP 连接并清缓存（改完配置后用；下一次对话会重新接入）"""
    username = _get_username()
    if not _is_admin(username):
        return jsonify({'error': '仅管理员可操作'}), 403
    try:
        from src.agent.main_agent import evict_all_agents
        from src.agent.mcp.client import mcp_manager

        result = mcp_manager.reload()
        globals().pop("_SRV_CACHE", None)   # 重连后清缓存
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.delete("cache:mcp:servers")   # Redis 里的服务/工具清单也清掉
        except Exception:
            pass
        cleared: int = evict_all_agents()
        log.info(f"MCP 重载: user={username}, servers={result.get('servers')}, 清理 agent 缓存={cleared}")
        return jsonify({**result, 'agents_cleared': cleared}), 200
    except Exception as e:
        log.warning(f"MCP 重载失败: {e}")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@McpController.route('/api/mcp/servers/<name>/public', methods=['POST'])
@login_required
def set_server_public(name: str) -> tuple[Response, int]:
    """把整个 MCP 服务设为「公共」/取消公共（名下所有工具一起改）

    「公共」= pa_tools.always_on，与工具管理页同源；
    公共工具人人可用、不参与角色分配，新建角色也自动拥有。
    改完清权限快照 + 角色目录 + agent 缓存，下一轮对话立刻生效。
    """
    username = _get_username()
    if not _is_admin(username):
        return jsonify({'ok': False, 'error': '仅管理员可操作'}), 403
    body: dict = request.get_json(silent=True) or {}
    public: bool = bool(body.get('public'))
    try:
        from src.agent.mcp.client import mcp_manager
        from src.infra.ToolMetaService import tool_meta

        tools: list = []
        for srv in mcp_manager.describe_servers(connect=False):
            if str(srv.get('name')) == name:
                tools = [str(t) for t in (srv.get('tools') or [])]
                break
        if not tools:      # 冷启动时还没探测出清单 → 用 pa_tools 里已登记的工具
            tools = sorted(k for k in (tool_meta.all() or {}) if k.startswith(f"mcp_{name}_"))
        if not tools:
            return jsonify({'ok': False, 'error': '该服务还没有工具清单，先点一次「刷新」再试'}), 400

        n: int = tool_meta.set_public(tools, public)
        # 权限快照 + 角色目录 + agent 实例一起失效
        try:
            from src.infra.RbacService import rbac as _rb
            from src.infra.utils import CacheUtil

            CacheUtil.delete_prefix('rbac:perm:')
            CacheUtil.delete_prefix('cache:rbac:catalog:')
            CacheUtil.delete('cache:tools:meta')
            _rb._bump_roles_ver()
        except Exception:
            pass
        try:
            from src.agent.main_agent import evict_all_agents

            evict_all_agents()
        except Exception:
            pass
        log.warning(f"MCP 服务公共标记: server={name}, public={public}, tools={len(tools)}, actor={username}")
        return jsonify({'ok': True, 'server': name, 'public': public, 'tools': tools, 'updated': n}), 200
    except Exception as e:
        log.warning(f"设置 MCP 公共服务失败: {e}")
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500
