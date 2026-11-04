"""聊天控制器：HTTP API 路由定义"""
from flask import Blueprint, request, jsonify, current_app, Response, send_file, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge
import hashlib
import os
import re
import uuid

from src.chat.stream import (
    invalidate_sandbox_files_hint,
    process_chat_stream,
    process_upload_stream,
)
from src.agent.main_agent import evict_agent_from_cache, delete_thread
from sqlalchemy import or_
from src.infra.dao.SessionDao import SessionDao
from src.infra.db.database import get_db
from src.infra.model.SessionModel import ChatSession
from src.infra.dto.ChatDto import ChatRequest, ChatResponse
from src.infra.utils.LogUtil import log
from src.infra.utils.RedisUtil import redis_util
from src.infra.utils.AuthUtil import login_required, get_current_user


ChatController: Blueprint = Blueprint('chat', __name__)
_DEDUP_TTL: int = 5  # 去重锁 TTL（秒），同一消息在此时间内不可重复提交
_PROD_ERROR_MSG: str = '服务器繁忙，请稍后重试'  # 生产环境通用错误提示
_RATE_LIMIT_WINDOW: int = 60  # 速率限制窗口（秒）
_RATE_LIMIT_MAX: int = 30     # 每窗口最大请求数


def _get_username() -> str:
    """从登录 session 获取当前登录用户名。

    安全约束：用户名只能来自后台登录 session（get_current_user），
    绝不接受前端传入的 username，防止身份伪造越权。
    未登录时返回空字符串（路由上的 @login_required 已负责拦截）。
    """
    return get_current_user()


def _cleanup_saved(saved: list[dict], extra: str = "") -> None:
    """删掉已落盘的临时文件（校验失败时别把垃圾留在磁盘上）"""
    for item in saved:
        try:
            os.remove(str(item.get("path") or ""))
        except OSError:
            pass
    if extra:
        try:
            os.remove(extra)
        except OSError:
            pass


def _push_extras_to_sandbox(username: str, items: list[dict]) -> list[str]:
    """把「第一个之外」的附件推进该用户沙箱，返回成功的文件名列表

    个数不限（只限总大小 100MB）。逐个调 openshell CLI 每个约 0.5~1s，
    文件一多就慢得没法看，所以超过 5 个时打成一个 tar.gz **一次**上传、在沙箱里解开。
    """
    if not items:
        return []
    try:
        from src.agent.backends.sandbox import sandbox_manager
    except Exception as e:
        log.warning(f"沙箱模块不可用，额外附件未入库: {e}")
        return []
    if not sandbox_manager.enabled:
        return []

    names: list[str] = [str(i.get("name") or "") for i in items]
    try:
        sandbox_manager.exec(username, "mkdir -p /sandbox/upload /sandbox/.tmp", timeout=30)
    except Exception as e:
        log.warning(f"准备沙箱目录失败: {e}")

    def _put_one(item: dict) -> bool:
        try:
            return bool(sandbox_manager.upload(
                username, str(item["path"]), f"/sandbox/upload/{item['name']}"
            ))
        except Exception as e:
            log.warning(f"附件入库失败 {item.get('name')}: {e}")
            return False

    if len(items) <= 5:
        saved = [names[i] for i, it in enumerate(items) if _put_one(it)]
        if saved:
            invalidate_sandbox_files_hint(username)
        return saved

    # 多文件：打包 → 一次上传 → 沙箱内解开（失败则回退逐个传）
    import tarfile
    import tempfile

    tmp_dir: str = tempfile.mkdtemp(prefix="pa_batch_")
    tgz: str = os.path.join(tmp_dir, "batch.tar.gz")
    try:
        with tarfile.open(tgz, "w:gz") as tf:
            for it in items:
                # arcname 只用清理过的文件名（basename），解包不会逃出 upload/
                tf.add(str(it["path"]), arcname=str(it["name"]))
        remote: str = f"/sandbox/.tmp/batch_{uuid.uuid4().hex[:10]}.tar.gz"
        if sandbox_manager.upload(username, tgz, remote):
            r = sandbox_manager.exec(
                username,
                f"cd /sandbox/upload && tar xzf {remote} && rm -f {remote} && echo PA_OK",
                timeout=180,
            )
            if "PA_OK" in (r.stdout or ""):
                log.info(f"批量附件已入库: user={username}, 文件数={len(items)}")
                return names
            log.warning(f"沙箱内解包失败: {(r.stderr or r.error or '')[:200]}")
    except Exception as e:
        log.warning(f"批量附件打包上传失败，回退逐个上传: {e}")
    finally:
        try:
            os.remove(tgz)
            os.rmdir(tmp_dir)
        except OSError:
            pass

    return [names[i] for i, it in enumerate(items) if _put_one(it)]


@ChatController.route('/api/chat', methods=['POST'])
@login_required
def chat() -> tuple[Response, int]:
    try:
        data: dict = request.get_json()
        chat_request: ChatRequest = ChatRequest(**data)

        username = _get_username()

        if not chat_request.session_id or not username:
            return jsonify({'error': '请先登录系统'}), 400

        # resume 请求时 message 可为空（仅携带 resume_value）
        if not chat_request.message and not chat_request.resume_value:
            return jsonify({'error': '消息不能为空'}), 400

        # 速率限制
        if not _check_rate_limit(username):
            return jsonify({'error': '请求过于频繁，请稍后再试'}), 429

        # 防重复提交：对 session+user+message 做 hash，加 Redis 锁
        # resume_value 可能是列表（多选），统一序列化为字符串用于去重
        _dedup_payload = chat_request.message or ''
        if chat_request.resume_value:
            _rv = chat_request.resume_value
            _dedup_payload = _rv if isinstance(_rv, str) else ','.join(str(x) for x in _rv)
        dedup_key = _build_dedup_key(
            chat_request.session_id, username,
            _dedup_payload
        )
        if _acquire_dedup_lock(dedup_key):
            log.warning(f"重复提交已被拦截: session=<{chat_request.session_id}>, "
                        f"user=<{username}>, msg=<{_dedup_payload}>")
            return jsonify({'error': '请勿重复提交'}), 429

        def generate():
            for chunk in process_chat_stream(
                chat_request.session_id,
                username,
                chat_request.message,
                resume_value=chat_request.resume_value,
            ):
                yield chunk

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            }
        ), 200
    except Exception as e:
        log.error(f"错误: {str(e)}", exc_info=True)
        return jsonify({'error': _safe_error(e)}), 500


@ChatController.route('/logout', methods=['GET', 'POST'])
def do_logout() -> tuple[str, int]:
    """退出登录：清掉本地会话后回登录页"""
    from flask import redirect, session as flask_session
    flask_session.clear()
    return redirect('/open-agent-harness/login')


@ChatController.route('/api/tools', methods=['GET'])
@login_required
def list_tools() -> tuple[Response, int]:
    """内置工具清单（只读）：风险等级 / 授权范围 / 当前账号是否可见 / 是否需审批

    为什么要有这个接口：内置工具是按权限下发的（见 tools/registry.py），
    界面上看不到"我有哪些工具、为什么少了一个"，排障和给业务方解释都很费劲。
    """
    username = _get_username() or ''
    try:
        from src.agent.tools import describe, resolve_tools

        visible: set[str] = {getattr(t, 'name', '') for t in resolve_tools(username)}
        all_rows: list[dict] = []
        for spec in describe():
            name: str = str(spec.get('name') or '')
            all_rows.append({
                **spec,
                'visible': name in visible,
            })

        # 查询（工具名 / 展示名 / 说明 / 分组）
        kw: str = str(request.args.get('q') or '').strip().lower()
        if kw:
            all_rows = [
                r for r in all_rows
                if any(kw in str(r.get(k) or '').lower()
                       for k in ('name', 'display_name', 'note', 'grp'))
            ]

        # 真分页（默认 10 条一页）
        try:
            page: int = max(1, int(request.args.get('page') or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size: int = max(1, min(100, int(request.args.get('page_size') or 10)))
        except (TypeError, ValueError):
            page_size = 10
        total: int = len(all_rows)
        start: int = (page - 1) * page_size
        rows = all_rows[start:start + page_size]

        try:
            from src.infra.RbacService import rbac
            can_manage: bool = rbac.is_admin(username)
        except Exception:
            can_manage = False
        return jsonify({'tools': rows, 'total': total, 'page': page, 'page_size': page_size,
                        'pages': max(1, (total + page_size - 1) // page_size),
                        'visible_count': len(visible), 'can_manage': can_manage}), 200
    except Exception as e:
        log.warning(f"读取内置工具清单失败: {e}")
        return jsonify({'tools': [], 'error': str(e)[:200]}), 200


@ChatController.route('/api/tools/<name>', methods=['PATCH'])
@login_required
def update_tool_meta(name: str) -> tuple[Response, int]:
    """修改工具元数据（说明/分组/风险/停用/人人可用）——仅管理员

    只影响"是否出现在清单里 / 显示什么"，不改变工具行为；工具的真实能力仍由代码决定。
    """
    username = _get_username() or ''
    try:
        from src.infra.RbacService import rbac
        if not rbac.is_admin(username):
            return jsonify({'ok': False, 'error': '仅管理员可修改工具配置'}), 403
        from src.infra.ToolMetaService import tool_meta
        body: dict = request.get_json(silent=True) or {}
        if str(name).startswith('mcp_'):
            return jsonify({'ok': False, 'error': 'MCP 工具的元数据由 MCP 服务自己声明，不能在此修改（可在角色管理里授权）'}), 400
        res: dict = tool_meta.update(name, body, actor=username)
        if res.get('ok'):
            # 停用/启用工具是**全局**开关：必须让所有 worker 丢弃已装配的 agent，
            # 否则 enabled=0 只对当前进程生效，"紧急下线"变成"下次重启才下线"
            try:
                from src.agent.main_agent import evict_all_agents

                evict_all_agents()
            except Exception as _ee:
                log.warning(f"工具元数据变更后清理 agent 缓存失败: {_ee}")
            # 改了「公共」或「停用」→ 权限快照与角色目录都要失效（目录缓存是带版本号的）
            if 'always_on' in body or 'enabled' in body:
                try:
                    from src.infra.RbacService import rbac as _rbac3
                    from src.infra.utils import CacheUtil

                    CacheUtil.delete_prefix('rbac:perm:')
                    CacheUtil.delete_prefix('cache:rbac:catalog:')
                    _rbac3._bump_roles_ver()
                except Exception as _ce:
                    log.warning(f"工具可见性变更后清权限缓存失败: {_ce}")
        return jsonify(res), (200 if res.get('ok') else 400)
    except Exception as e:
        log.error(f"修改工具元数据失败: {name}, err={e}", exc_info=True)
        return jsonify({'ok': False, 'error': '修改失败（详见服务端日志）'}), 500


@ChatController.route('/api/traces', methods=['GET'])
@login_required
def list_traces() -> tuple[Response, int]:
    """最近的对话 trace（仅管理员）：每轮一条，含每个工具的名字/耗时/状态"""
    username = _get_username() or ''
    try:
        from src.infra.RbacService import rbac
        is_admin: bool = rbac.is_admin(username)
        from src.infra.MetricsService import recent_traces, trace_stats
        try:
            limit: int = max(1, min(200, int(request.args.get('limit') or 50)))
        except (TypeError, ValueError):
            limit = 50
        sess: str = str(request.args.get('session') or '')
        rows = recent_traces(limit, session=sess)
        stats: dict
        if not is_admin:
            # 普通用户只能看自己的轨迹（**统计也只按自己的行算**，
            # 原来这里把全站 trace_stats(200) 一起返回了，等于泄露别人的用量）
            rows = [r for r in rows if str(r.get('user') or '') == username]
            stats = trace_stats(200, rows=rows)
        else:
            stats = trace_stats(200)
        return jsonify({'traces': rows[:limit], 'stats': stats, 'is_admin': is_admin}), 200
    except Exception as e:
        log.error(f"读取 trace 失败: {e}")
        return jsonify({'traces': [], 'error': '读取失败'}), 500


@ChatController.route('/api/metrics/summary', methods=['GET'])
@login_required
def metrics_summary() -> tuple[Response, int]:
    """每轮指标聚合（默认近 1 天）：轮数 / token / 平均耗时 / 工具调用与错误率

    仅管理员：返回的是**全站**聚合，还带「谁用得最多」的账号榜；
    普通用户能看就等于把域账号名单和用量都暴露了（口径与 /api/audit 保持一致）。
    """
    username: str = _get_username() or ''
    try:
        from src.infra.RbacService import rbac

        if not rbac.is_admin(username):
            return jsonify({'error': '只有管理员可以查看全站指标'}), 403
    except Exception:
        return jsonify({'error': '权限校验失败'}), 403
    try:
        days: int = max(1, min(30, int(request.args.get('days', 1))))
    except (TypeError, ValueError):
        days = 1
    from src.infra.MetricsService import summary

    return jsonify(summary(days)), 200


@ChatController.route('/api/chat/upload', methods=['POST'])
@login_required
def chat_upload() -> tuple[Response, int]:
    try:
        session_id: str = request.form.get('session_id')
        username = _get_username()
        message: str = request.form.get('message', '')
        
        if not session_id or not username:
            return jsonify({'error': '请先登录系统'}), 400

        if not _check_rate_limit(username):
            return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
        
        
        # —— 支持一次上传多个文件：**个数不限，总大小 ≤ 100MB** ——
        # 兼容：老前端用 file 字段传单个；新前端用 files 字段传多个
        _incoming = list(request.files.getlist('files')) + list(request.files.getlist('file'))
        _files = [f for f in _incoming if f and (f.filename or '').strip()]
        if not _files:
            return jsonify({'error': '请选择要上传的文件'}), 400
        try:
            from src.app import TOTAL_UPLOAD_BYTES as _MAX_TOTAL
        except Exception:
            _MAX_TOTAL = 100 * 1024 * 1024

        # 允许：表格 / 图片 / 常见办公文档 / 压缩包 / 纯文本
        _ALLOWED_EXT = {
            'xlsx', 'xls', 'csv',
            'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp',
            'pdf', 'doc', 'docx', 'ppt', 'pptx', 'rtf', 'odt', 'ods', 'odp',
            'zip', 'rar', '7z', 'tar', 'gz',
            'txt', 'md', 'json', 'xml', 'html', 'htm', 'yaml', 'yml', 'log',
        }

        safe_user: str = re.sub(r"[^\w.\-]+", "_", username)[:64] or "unknown"
        user_upload_dir: str = os.path.join(current_app.config['UPLOAD_FOLDER'], safe_user)
        os.makedirs(user_upload_dir, exist_ok=True)

        saved: list[dict] = []
        _total: int = 0
        for _f in _files:
            _parts = _f.filename.rsplit('.', 1)
            if len(_parts) != 2:
                return jsonify({'error': f'无法识别的文件格式: {_f.filename}'}), 400
            _ext = _parts[1].lower()
            if _ext not in _ALLOWED_EXT:
                return jsonify({
                    'error': '不支持的文件类型 .%s（%s）。支持：表格(xlsx/xls/csv)、图片(jpg/png/gif/bmp/webp)、'
                             '办公文档(pdf/doc/docx/ppt/pptx/rtf/odt/ods/odp)、压缩包(zip/rar/7z/tar/gz)、'
                             '文本(txt/md/json/xml/html/yaml/log)' % (_ext, _f.filename)
                }), 400

            # 原始文件名安全清理：防路径穿越与特殊字符
            _raw = os.path.basename((_f.filename or "").replace("\\", "/")).replace("\x00", "")
            _base_no_ext = os.path.splitext(_raw)[0]
            _safe_base = re.sub(r"[^\w一-龥.\-]+", "_", _base_no_ext).strip("._")[:120]
            _original = f"{_safe_base}.{_ext}" if _safe_base else f"upload_{uuid.uuid4().hex[:8]}.{_ext}"

            _tmp = os.path.join(user_upload_dir, f"{uuid.uuid4().hex}.{_ext}")
            _f.save(_tmp)
            _size: int = os.path.getsize(_tmp) if os.path.isfile(_tmp) else 0
            if _size <= 0:
                _cleanup_saved(saved, extra=_tmp)
                return jsonify({'error': f'文件为空: {_original}'}), 400
            # 个数不限，只卡总大小（后端也校验：前端限制只防君子，curl 直接传绕得过去）
            _total += _size
            if _total > _MAX_TOTAL:
                _cleanup_saved(saved, extra=_tmp)
                return jsonify({
                    'error': f'单次上传总大小不能超过 {_MAX_TOTAL // 1024 // 1024}MB，'
                             f'当前 {_total / 1024 / 1024:.1f}MB（含「{_original}」）'
                }), 400
            saved.append({'path': _tmp, 'name': _original, 'ext': _ext})

        # 第一个走正常流式处理（含 OCR/描述），其余推进沙箱、只把名字告诉模型
        first = saved[0]
        upload_path: str = first['path']
        original_name: str = first['name']
        _rest: list[dict] = saved[1:]
        _others: list[str] = _push_extras_to_sandbox(username, _rest)
        for item in _rest:
            try:
                os.remove(item['path'])
            except OSError:
                pass
        if _others:
            message = (message or "") + (
                "\n（本次还上传了：%s，都已放在你的沙箱 /sandbox/upload/ 目录下）"
                % "、".join(_others)
            )
        elif _rest:
            # 有额外附件但一个都没进沙箱：明确告诉模型，别让它以为文件在
            message = (message or "") + "\n（本次还选了 %d 个额外文件，但未能存入你的沙箱，请让用户重试）" % len(_rest)

        # 所有文件上传统一走 SSE 流式，避免长时间阻塞无响应
        def generate():
            try:
                yield from process_upload_stream(
                    session_id, username, upload_path, message,
                    original_name=original_name,
                )
            finally:
                # 流式结束后删除临时文件
                try:
                    os.remove(upload_path)
                except OSError:
                    pass

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            }
        ), 200
    except RequestEntityTooLarge:
        # 请求体超过 MAX_CONTENT_LENGTH：Flask 在解析 multipart 时就抛，
        # 必须在这里拦下，否则会被下面的 except Exception 吞成 500 + 原文
        return jsonify({'error': '上传内容太大：单次上传总大小不能超过 100MB'}), 413
    except Exception as e:
        log.error(f"文件上传错误: {str(e)}", exc_info=True)
        return jsonify({'error': _safe_error(e)}), 500


@ChatController.route('/api/sessions', methods=['GET'])
@login_required
def list_sessions() -> tuple[Response, int]:
    """分页获取用户的会话列表"""
    try:
        username = _get_username()
        page: int = int(request.args.get('page', 1))
        page_size: int = int(request.args.get('page_size', 20))
        archived: bool = str(request.args.get('archived', '0')).lower() in ('1', 'true', 'yes')
        if not username:
            return jsonify({'error': '用户名不能为空'}), 400
        result: dict = SessionDao.list_sessions(username, page, page_size, archived=archived)
        return jsonify(result), 200
    except Exception as e:
        log.error(f"获取会话列表失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@ChatController.route('/api/sessions/<session_id>/messages', methods=['GET'])
@login_required
def get_session_messages(session_id: str) -> tuple[Response, int]:
    """获取指定会话的消息列表"""
    try:
        username = _get_username()
        if not username:
            return jsonify({'error': '用户名不能为空'}), 400
        messages: list = SessionDao.get_messages_by_frontend_id(session_id, username)
        return jsonify(messages), 200
    except Exception as e:
        log.error(f"获取消息失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@ChatController.route('/api/artifacts/<session_id>/<path:filename>', methods=['GET'])
@login_required
def get_artifact(session_id: str, filename: str):
    """打开/下载沙箱里回传的产出文件

    安全要点：
    · 用户名只来自登录会话；宿主机路径 = artifacts/<用户名>/<会话id>/<文件名>，
      客户端指定不了目录 —— 天然跨不了用户
    · 再校验一次会话归属（纵深防御）
    · HTML/SVG 属于「模型生成内容」：加 CSP sandbox，脚本能跑但落在不透明源里，
      摸不到本站的 cookie / localStorage
    """
    try:
        username: str = _get_username()
        if not username:
            return jsonify({'error': '请先登录系统'}), 401

        name: str = os.path.basename((filename or '').replace('\\', '/'))
        if not name or name.startswith('.'):
            return jsonify({'error': '文件名不合法'}), 400

        # 不再要求会话仍存在：artifacts 是归档副本，会话删了文件还在，
        # 历史对话里的链接应当继续可用。跨用户隔离靠路径本身
        # （artifacts/<当前用户>/<会话>/ ），别人的会话 ID 在本人目录下不存在 → 404
        if not SessionDao.belongs_to(session_id, username):
            from src.agent.backends.sandbox import sandbox_manager as _sm0

            _probe = _sm0.artifact_path(username, session_id, os.path.basename((filename or '').replace(chr(92), '/')))
            if not _probe:
                return jsonify({'error': '无权访问该文件'}), 403

        from src.agent.backends.sandbox import sandbox_manager
        path: str | None = sandbox_manager.artifact_path(username, session_id, name)
        if not path:
            # 交付物宿主副本保留 90 天后由每日维护清掉（见 src/infra/Maintenance.py）。
            # 直接给 404 页面太生硬：浏览器里点旧链接的人不知道发生了什么。
            from src.infra.Maintenance import ARTIFACT_RETENTION_DAYS as _keep_days

            msg: str = (f'文件「{name}」已不存在：可能已超过 {_keep_days} 天保留期被清理，'
                        f'或从未生成过。如仍需要，请重新生成一次。')
            as_attachment = str(request.args.get('download', '')).lower() in ('1', 'true', 'yes')
            if 'text/html' in (request.headers.get('Accept') or '') and not as_attachment:
                html = (
                    '<!doctype html><meta charset="utf-8">'
                    '<title>文件已过期</title>'
                    '<div style="font:16px/1.7 -apple-system,Segoe UI,Microsoft YaHei,sans-serif;'
                    'max-width:640px;margin:12vh auto;padding:0 24px;color:#334155">'
                    '<h2 style="color:#0f172a">文件已过期或不存在</h2>'
                    f'<p>文件「<b>{name}</b>」可能已超过 {_keep_days} 天保留期被清理，或从未生成过。</p>'
                    '<p>如仍需要，请在对话里让助手重新生成一次。</p></div>'
                )
                return Response(html, status=404, mimetype='text/html')
            return jsonify({'error': msg}), 404

        as_attachment: bool = str(request.args.get('download', '')).lower() in ('1', 'true', 'yes')
        resp: Response = send_file(
            path, as_attachment=as_attachment, download_name=name, conditional=True
        )
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Cache-Control'] = 'private, no-store'
        if name.lower().endswith(('.html', '.htm', '.svg')):
            resp.headers['Content-Security-Policy'] = (
                'sandbox allow-scripts allow-popups allow-forms allow-modals'
            )
        log.info(f"产出文件下发: user={username}, session={session_id}, "
                 f"file={name}, download={as_attachment}")
        return resp
    except Exception as e:
        log.error(f"产出文件下发失败: {str(e)}", exc_info=True)
        return jsonify({'error': _safe_error(e)}), 500


@ChatController.route('/api/sessions/<session_id>/archive', methods=['POST'])
@login_required
def archive_session(session_id: str) -> tuple[Response, int]:
    """归档/恢复会话：只从默认列表隐藏，不删除任何数据"""
    try:
        username = _get_username()
        if not username:
            return jsonify({'error': '用户名不能为空'}), 400

        payload = request.get_json(silent=True) or {}
        archived: bool = bool(payload.get('archived', True))

        db = get_db()
        # 前端可能传数据库主键 id，也可能传业务会话号 session_id，两种都接受
        sess = (
            db.query(ChatSession)
            .filter(
                or_(ChatSession.id == session_id, ChatSession.session_id == session_id),
                ChatSession.username == username,
            )
            .first()
        )
        if sess is None:
            return jsonify({'error': '会话不存在或无权操作'}), 404

        sess.archived = archived
        db.commit()
        log.info(f"会话{'归档' if archived else '恢复'}: user={username}, session={session_id}")
        return jsonify({'ok': True, 'archived': archived}), 200
    except Exception as e:
        log.error(f"归档会话失败: {str(e)}", exc_info=True)
        return jsonify({'error': _safe_error(e)}), 500


@ChatController.route('/api/sessions/<session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id: str) -> tuple[Response, int]:
    """删除会话（MySQL + Redis + SQLite checkpoint + 内存缓存）"""
    try:
        username = _get_username()
        if not username:
            return jsonify({'error': '用户名不能为空'}), 400
        SessionDao.delete_session(session_id, username)
        evict_agent_from_cache(session_id, username)
        delete_thread(session_id, username)
        # 轨迹物理删除（审计表保留：合规留痕不随会话消失）
        try:
            from src.infra import TraceService

            TraceService.delete_by_session(session_id, username)
        except Exception as _te:
            log.warning(f"删除会话轨迹失败（忽略）: {_te}")
        return jsonify({'ok': True}), 200
    except Exception as e:
        log.error(f"删除会话失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def _build_dedup_key(session_id: str, username: str, message: str) -> str:
    """构造去重 key：基于 session + user + message 内容的 SHA256 前16位"""
    raw = f"{session_id}_{username}_{message}".encode("utf-8")
    return f"dedup:{hashlib.sha256(raw).hexdigest()[:16]}"


def _safe_error(e: Exception) -> str:
    """生产环境返回通用错误提示，避免泄露内部信息"""
    from config.Configs import cfg
    if cfg.env == 'pro':
        return _PROD_ERROR_MSG
    return str(e)


def _check_rate_limit(username: str) -> bool:
    """简单滑动窗口速率限制（Redis INCR + EXPIRE），返回 True 表示放行"""
    if not redis_util.available:
        return True
    try:
        key = f"rate:{username}"
        count = redis_util._client.incr(key)
        if count == 1:
            redis_util._client.expire(key, _RATE_LIMIT_WINDOW)
        return count <= _RATE_LIMIT_MAX
    except Exception:
        return True  # Redis 异常时放行


def _acquire_dedup_lock(dedup_key: str) -> bool:
    """尝试获取去重锁，返回 True 表示已存在（即重复提交）

    使用 Redis SET NX EX 原子操作，锁 TTL 为 _DEDUP_TTL 秒。
    Redis 不可用时降级放行（不阻塞正常请求）。
    """
    if not redis_util.available:
        return False
    try:
        # SET key 1 NX EX ttl → 成功返回 True（新锁），失败返回 None（已存在）
        acquired = redis_util._client.set(dedup_key, "1", nx=True, ex=_DEDUP_TTL)
        return acquired is None
    except Exception:
        # Redis 异常时降级放行
        return False


# ========== 健康检查 ==========

@ChatController.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点：返回 DB、Redis 连通性状态"""
    status = {'ok': True, 'db': 'unknown', 'redis': 'unknown'}
    try:
        from src.infra.db.database import get_db
        from sqlalchemy import text as sa_text, or_
        db = get_db()
        db.execute(sa_text('SELECT 1'))
        status['db'] = 'ok'
    except Exception as e:
        status['db'] = f'error: {e}'
        status['ok'] = False
    try:
        if redis_util.available:
            redis_util._client.ping()
            status['redis'] = 'ok'
        else:
            status['redis'] = 'disconnected'
    except Exception as e:
        status['redis'] = f'error: {e}'
        status['ok'] = False
    http_code = 200 if status['ok'] else 503
    return jsonify(status), http_code