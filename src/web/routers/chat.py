#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""聊天相关接口（原 `src/controller/ChatController.py`，FastAPI 版）

端点全部是**同步 `def`**：内部是阻塞调用（沙箱 CLI、pymysql、LangGraph 同步流），
FastAPI 会把它们丢进线程池执行（每进程默认 40 线程），语义与 Flask gthread 的
「一线程一请求」一致——先求等价，异步化（P2~P5）在后续阶段单独做。

安全约束保持不变：**用户名只从会话取**，绝不接受前端传入。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid

from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import or_, select

from src.agent.main_agent import delete_thread, evict_agent_from_cache
from src.chat.stream import (
    invalidate_sandbox_files_hint,
    process_chat_stream,
    process_upload_stream,
)
from src.infra.dao import SessionDao
from src.infra.db.database import get_db
from src.infra.dto.ChatDto import ChatRequest
from src.infra.model.SessionModel import ChatSession
from src.infra.utils.LogUtil import log
from src.infra.utils.RedisUtil import redis_util
from src.web.http import (
    SSE_HEADERS,
    TOTAL_UPLOAD_BYTES,
    UPLOAD_FOLDER,
    wants_attachment,
)
from src.web.security import require_login

router: APIRouter = APIRouter()

_DEDUP_TTL: int = 5           # 去重锁 TTL（秒），同一消息在此时间内不可重复提交
_PROD_ERROR_MSG: str = "服务器繁忙，请稍后重试"
_RATE_LIMIT_WINDOW: int = 60  # 速率限制窗口（秒）
_RATE_LIMIT_MAX: int = 30     # 每窗口最大请求数
# 会话号：前端生成 32 位 hex；DB 列是 VARCHAR(32)，超长会在建会话时抛 DataError，
# 而那时流式响应已经 200 开始，只能断流。入口先挡掉。
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _int_param(raw, default: int, lo: int, hi: int) -> int:
    """宽容解析整数参数：非法/缺省用默认值，并夹到 [lo, hi]"""
    try:
        v: int = int(str(raw).strip()) if raw is not None and str(raw).strip() else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(str(session_id or "")))


# ============================================================
# 内部工具
# ============================================================

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


def _build_dedup_key(session_id: str, username: str, message: str) -> str:
    """构造去重 key：基于 session + user + message 内容的 SHA256 前16位"""
    raw = f"{session_id}_{username}_{message}".encode("utf-8")
    return f"dedup:{hashlib.sha256(raw).hexdigest()[:16]}"


def _safe_error(e: Exception) -> str:
    """返回给用户的错误文案：默认只给通用提示，避免泄露内部细节

    注意：不要读 `cfg.env` —— Configs 从来没有这个属性，之前这里会抛 AttributeError，
    把「业务异常」变成「异常处理器里再抛异常」（500 连 JSON 都回不去）。
    口径以 `[app] error_detail` 为准（默认 false = 只说人话）。
    """
    from config.Configs import cfg

    try:
        verbose: bool = bool(getattr(cfg, "errorDetail", False))
    except Exception:
        verbose = False
    return str(e) if verbose else _PROD_ERROR_MSG


async def _acheck_rate_limit(username: str) -> bool:
    """`_check_rate_limit` 的异步版（redis.asyncio）；Redis 不可用或异常 → 放行。"""
    try:
        c = redis_util.aclient if redis_util.available else None
    except Exception:
        c = None
    if c is None:
        return True
    try:
        key = f"rate:{username}"
        count = await c.incr(key)
        if count == 1:
            await c.expire(key, _RATE_LIMIT_WINDOW)
        return count <= _RATE_LIMIT_MAX
    except Exception:
        return True


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


async def _aacquire_dedup_lock(dedup_key: str) -> bool:
    """`_acquire_dedup_lock` 的异步版（redis.asyncio SET NX EX），True 表示重复提交

    语义一致：Redis 不可用或异常 → 放行。聊天入口用这个，
    避免在事件循环里做同步 Redis 往返。
    """
    try:
        c = redis_util.aclient if redis_util.available else None
    except Exception:
        c = None
    if c is None:
        return False
    try:
        acquired = await c.set(dedup_key, "1", nx=True, ex=_DEDUP_TTL)
        return acquired is None
    except Exception:
        return False


def _acquire_dedup_lock(dedup_key: str) -> bool:
    """尝试获取去重锁，返回 True 表示已存在（即重复提交）

    使用 Redis SET NX EX 原子操作，锁 TTL 为 _DEDUP_TTL 秒。
    Redis 不可用时降级放行（不阻塞正常请求）。
    """
    if not redis_util.available:
        return False
    try:
        acquired = redis_util._client.set(dedup_key, "1", nx=True, ex=_DEDUP_TTL)
        return acquired is None
    except Exception:
        return False


# ============================================================
# 端点
# ============================================================

@router.post("/api/chat")
async def chat(payload: dict = Body(...), username: str = Depends(require_login)):
    """发起一轮对话（SSE 流式返回）"""
    try:
        chat_request: ChatRequest = ChatRequest(**payload)

        if not chat_request.session_id:
            return JSONResponse({"error": "请先登录系统"}, status_code=400)
        if not _valid_session_id(chat_request.session_id):
            return JSONResponse({"error": "会话号不合法（1~32 位字母/数字/下划线/短横线）"},
                                status_code=400)

        # resume 请求时 message 可为空（仅携带 resume_value）
        if not chat_request.message and not chat_request.resume_value:
            return JSONResponse({"error": "消息不能为空"}, status_code=400)

        if not await _acheck_rate_limit(username):
            return JSONResponse({"error": "请求过于频繁，请稍后再试"}, status_code=429)

        # 防重复提交：对 session+user+message 做 hash，加 Redis 锁
        # resume_value 可能是列表（多选），统一序列化为字符串用于去重
        _dedup_payload = chat_request.message or ""
        if chat_request.resume_value:
            _rv = chat_request.resume_value
            _dedup_payload = _rv if isinstance(_rv, str) else ",".join(str(x) for x in _rv)
        dedup_key = _build_dedup_key(chat_request.session_id, username, _dedup_payload)
        if await _aacquire_dedup_lock(dedup_key):
            log.warning(f"重复提交已被拦截: session=<{chat_request.session_id}>, "
                        f"user=<{username}>, msg=<{_dedup_payload}>")
            return JSONResponse({"error": "请勿重复提交"}, status_code=429)

        async def generate():
            async for _chunk in process_chat_stream(
                chat_request.session_id,
                username,
                chat_request.message,
                resume_value=chat_request.resume_value,
            ):
                yield _chunk

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers=SSE_HEADERS
        )
    except Exception as e:
        log.error(f"错误: {str(e)}", exc_info=True)
        return JSONResponse({"error": _safe_error(e)}, status_code=500)


@router.get("/api/tools")
def list_tools(request: Request, username: str = Depends(require_login)):
    """内置工具清单（只读）：风险等级 / 授权范围 / 当前账号是否可见 / 是否需审批

    为什么要有这个接口：内置工具是按权限下发的（见 tools/registry.py），
    界面上看不到"我有哪些工具、为什么少了一个"，排障和给业务方解释都很费劲。
    """
    try:
        from src.agent.tools import describe, resolve_tools

        visible: set[str] = {getattr(t, "name", "") for t in resolve_tools(username)}
        all_rows: list[dict] = []
        for spec in describe():
            name: str = str(spec.get("name") or "")
            all_rows.append({**spec, "visible": name in visible})

        qp = request.query_params
        kw: str = str(qp.get("q") or "").strip().lower()
        if kw:
            all_rows = [
                r for r in all_rows
                if any(kw in str(r.get(k) or "").lower()
                       for k in ("name", "display_name", "note", "grp"))
            ]

        try:
            page: int = max(1, int(qp.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size: int = max(1, min(100, int(qp.get("page_size") or 10)))
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
        return JSONResponse({
            "tools": rows, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "visible_count": len(visible), "can_manage": can_manage,
        })
    except Exception as e:
        log.warning(f"读取内置工具清单失败: {e}")
        return JSONResponse({"tools": [], "error": str(e)[:200]})


@router.patch("/api/tools/{name}")
def update_tool_meta(name: str, payload: dict | None = Body(None),
                     username: str = Depends(require_login)):
    """修改工具元数据（说明/分组/风险/停用/人人可用）——仅管理员"""
    try:
        from src.infra.RbacService import rbac

        if not rbac.is_admin(username):
            return JSONResponse({"ok": False, "error": "仅管理员可修改工具配置"}, status_code=403)
        from src.infra.ToolMetaService import tool_meta

        body: dict = payload or {}
        if str(name).startswith("mcp_"):
            return JSONResponse(
                {"ok": False,
                 "error": "MCP 工具的元数据由 MCP 服务自己声明，不能在此修改（可在角色管理里授权）"},
                status_code=400,
            )
        res: dict = tool_meta.update(name, body, actor=username)
        if res.get("ok"):
            # 停用/启用工具是**全局**开关：必须让所有 worker 丢弃已装配的 agent
            try:
                from src.agent.main_agent import evict_all_agents

                evict_all_agents()
            except Exception as _ee:
                log.warning(f"工具元数据变更后清理 agent 缓存失败: {_ee}")
            if "always_on" in body or "enabled" in body:
                try:
                    from src.infra.RbacService import rbac as _rbac3
                    from src.infra.utils import CacheUtil

                    CacheUtil.delete_prefix("rbac:perm:")
                    CacheUtil.delete_prefix("cache:rbac:catalog:")
                    _rbac3._bump_roles_ver()
                except Exception as _ce:
                    log.warning(f"工具可见性变更后清权限缓存失败: {_ce}")
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)
    except Exception as e:
        log.error(f"修改工具元数据失败: {name}, err={e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "修改失败（详见服务端日志）"}, status_code=500)


@router.get("/api/traces")
def list_traces(request: Request, username: str = Depends(require_login)):
    """最近的对话 trace（仅管理员可看全站）：每轮一条，含每个工具的名字/耗时/状态"""
    try:
        from src.infra.RbacService import rbac

        is_admin: bool = rbac.is_admin(username)
        from src.infra.MetricsService import recent_traces, trace_stats

        qp = request.query_params
        try:
            limit: int = max(1, min(200, int(qp.get("limit") or 50)))
        except (TypeError, ValueError):
            limit = 50
        sess: str = str(qp.get("session") or "")
        rows = recent_traces(limit, session=sess)
        stats: dict
        if not is_admin:
            # 普通用户只能看自己的轨迹（统计也只按自己的行算）
            rows = [r for r in rows if str(r.get("user") or "") == username]
            stats = trace_stats(200, rows=rows)
        else:
            stats = trace_stats(200)
        return JSONResponse({"traces": rows[:limit], "stats": stats, "is_admin": is_admin})
    except Exception as e:
        log.error(f"读取 trace 失败: {e}")
        return JSONResponse({"traces": [], "error": "读取失败"}, status_code=500)


@router.get("/api/metrics/summary")
def metrics_summary(request: Request, username: str = Depends(require_login)):
    """每轮指标聚合（默认近 1 天）：仅管理员，返回全站聚合"""
    try:
        from src.infra.RbacService import rbac

        if not rbac.is_admin(username):
            return JSONResponse({"error": "只有管理员可以查看全站指标"}, status_code=403)
    except Exception:
        return JSONResponse({"error": "权限校验失败"}, status_code=403)
    try:
        days: int = max(1, min(30, int(request.query_params.get("days", 1))))
    except (TypeError, ValueError):
        days = 1
    from src.infra.MetricsService import summary

    return JSONResponse(summary(days))


@router.post("/api/chat/upload")
async def chat_upload(
    request: Request,
    session_id: str = Form(""),
    message: str = Form(""),
    files: list[UploadFile] | None = File(None),
    file: list[UploadFile] | None = File(None),
    username: str = Depends(require_login),
):
    """上传文件并发起一轮对话（SSE 流式返回）

    兼容两种字段名：老前端用 `file`（单个），新前端用 `files`（多个）。
    """
    try:
        if not session_id:
            return JSONResponse({"error": "请先登录系统"}, status_code=400)
        if not _valid_session_id(session_id):
            return JSONResponse({"error": "会话号不合法（1~32 位字母/数字/下划线/短横线）"},
                                status_code=400)

        if not await _acheck_rate_limit(username):
            return JSONResponse({"error": "请求过于频繁，请稍后再试"}, status_code=429)

        # —— 支持一次上传多个文件：**个数不限，总大小 ≤ 100MB** ——
        _incoming = list(files or []) + list(file or [])
        _files = [f for f in _incoming if f and (f.filename or "").strip()]
        if not _files:
            return JSONResponse({"error": "请选择要上传的文件"}, status_code=400)

        # 允许：表格 / 图片 / 常见办公文档 / 压缩包 / 纯文本
        _ALLOWED_EXT = {
            "xlsx", "xls", "csv",
            "jpg", "jpeg", "png", "gif", "bmp", "webp",
            "pdf", "doc", "docx", "ppt", "pptx", "rtf", "odt", "ods", "odp",
            "zip", "rar", "7z", "tar", "gz",
            "txt", "md", "json", "xml", "html", "htm", "yaml", "yml", "log",
        }

        safe_user: str = re.sub(r"[^\w.\-]+", "_", username)[:64] or "unknown"
        user_upload_dir: str = os.path.join(UPLOAD_FOLDER, safe_user)
        os.makedirs(user_upload_dir, exist_ok=True)

        saved: list[dict] = []
        _total: int = 0
        for _f in _files:
            _parts = (_f.filename or "").rsplit(".", 1)
            if len(_parts) != 2:
                return JSONResponse({"error": f"无法识别的文件格式: {_f.filename}"}, status_code=400)
            _ext = _parts[1].lower()
            if _ext not in _ALLOWED_EXT:
                return JSONResponse({
                    "error": "不支持的文件类型 .%s（%s）。支持：表格(xlsx/xls/csv)、图片(jpg/png/gif/bmp/webp)、"
                             "办公文档(pdf/doc/docx/ppt/pptx/rtf/odt/ods/odp)、压缩包(zip/rar/7z/tar/gz)、"
                             "文本(txt/md/json/xml/html/yaml/log)" % (_ext, _f.filename)
                }, status_code=400)

            # 原始文件名安全清理：防路径穿越与特殊字符
            _raw = os.path.basename((_f.filename or "").replace("\\", "/")).replace("\x00", "")
            _base_no_ext = os.path.splitext(_raw)[0]
            _safe_base = re.sub(r"[^\w一-龥.\-]+", "_", _base_no_ext).strip("._")[:120]
            _original = f"{_safe_base}.{_ext}" if _safe_base else f"upload_{uuid.uuid4().hex[:8]}.{_ext}"

            _tmp = os.path.join(user_upload_dir, f"{uuid.uuid4().hex}.{_ext}")
            # 已知大小就先卡总量，别等整包落盘才拒绝（否则一次请求就能把磁盘写满）
            _declared: int = int(getattr(_f, "size", 0) or 0)
            if _declared > 0 and _total + _declared > TOTAL_UPLOAD_BYTES:
                _cleanup_saved(saved)
                return JSONResponse({
                    "error": f"单次上传总大小不能超过 {TOTAL_UPLOAD_BYTES // 1024 // 1024}MB"
                }, status_code=400)
            with open(_tmp, "wb") as _out:
                shutil.copyfileobj(_f.file, _out)
            _size: int = os.path.getsize(_tmp) if os.path.isfile(_tmp) else 0
            if _size <= 0:
                _cleanup_saved(saved, extra=_tmp)
                return JSONResponse({"error": f"文件为空: {_original}"}, status_code=400)
            # 个数不限，只卡总大小（前端限制只防君子，curl 直接传绕得过去）
            _total += _size
            if _total > TOTAL_UPLOAD_BYTES:
                _cleanup_saved(saved, extra=_tmp)
                return JSONResponse({
                    "error": f"单次上传总大小不能超过 {TOTAL_UPLOAD_BYTES // 1024 // 1024}MB，"
                             f"当前 {_total / 1024 / 1024:.1f}MB（含「{_original}」）"
                }, status_code=400)
            saved.append({"path": _tmp, "name": _original, "ext": _ext})

        # 第一个走正常流式处理（含 OCR/描述），其余推进沙箱、只把名字告诉模型
        first = saved[0]
        upload_path: str = first["path"]
        original_name: str = first["name"]
        _rest: list[dict] = saved[1:]
        _others: list[str] = _push_extras_to_sandbox(username, _rest)
        for item in _rest:
            try:
                os.remove(item["path"])
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

        async def generate():
            try:
                async for _chunk in process_upload_stream(
                    session_id, username, upload_path, message,
                    original_name=original_name,
                ):
                    yield _chunk
            finally:
                # 流式结束后删除临时文件
                try:
                    os.remove(upload_path)
                except OSError:
                    pass

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers=SSE_HEADERS
        )
    except Exception as e:
        log.error(f"文件上传错误: {str(e)}", exc_info=True)
        return JSONResponse({"error": _safe_error(e)}, status_code=500)


@router.get("/api/sessions")
async def list_sessions(request: Request, username: str = Depends(require_login)):
    """分页获取用户的会话列表"""
    try:
        qp = request.query_params
        page: int = _int_param(qp.get("page"), 1, 1, 1_000_000)
        page_size: int = _int_param(qp.get("page_size"), 20, 1, 100)
        archived: bool = str(qp.get("archived", "0")).lower() in ("1", "true", "yes")
        result: dict = await SessionDao.list_sessions(username, page, page_size, archived=archived)
        return JSONResponse(result)
    except Exception as e:
        log.error(f"获取会话列表失败: {str(e)}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, username: str = Depends(require_login)):
    """获取指定会话的消息列表"""
    try:
        messages: list = await SessionDao.get_messages_by_frontend_id(session_id, username)
        return JSONResponse(messages)
    except Exception as e:
        log.error(f"获取消息失败: {str(e)}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/artifacts/{session_id}/{filename:path}")
async def get_artifact(session_id: str, filename: str, request: Request,
                 username: str = Depends(require_login)):
    """打开/下载沙箱里回传的产出文件

    安全要点：
    · 用户名只来自 CAS 会话；宿主机路径 = artifacts/<用户名>/<会话id>/<文件名>，
      客户端指定不了目录 —— 天然跨不了用户
    · 再校验一次会话归属（纵深防御）
    · HTML/SVG 属于「模型生成内容」：加 CSP sandbox，脚本能跑但落在不透明源里，
      摸不到本站的 cookie / localStorage
    """
    try:
        name: str = os.path.basename((filename or "").replace("\\", "/"))
        if not name or name.startswith("."):
            return JSONResponse({"error": "文件名不合法"}, status_code=400)

        # 不再要求会话仍存在：artifacts 是归档副本，会话删了文件还在，
        # 历史对话里的链接应当继续可用。跨用户隔离靠路径本身
        if not await SessionDao.belongs_to(session_id, username):
            from src.agent.backends.sandbox import sandbox_manager as _sm0

            _probe = _sm0.artifact_path(username, session_id,
                                        os.path.basename((filename or "").replace(chr(92), "/")))
            if not _probe:
                return JSONResponse({"error": "无权访问该文件"}, status_code=403)

        from src.agent.backends.sandbox import sandbox_manager

        path: str | None = sandbox_manager.artifact_path(username, session_id, name)
        if not path:
            # 交付物宿主副本保留 90 天后由每日维护清掉（见 src/infra/Maintenance.py）
            from src.infra.Maintenance import ARTIFACT_RETENTION_DAYS as _keep_days

            msg: str = (f"文件「{name}」已不存在：可能已超过 {_keep_days} 天保留期被清理，"
                        f"或从未生成过。如仍需要，请重新生成一次。")
            as_attachment = wants_attachment(request)
            if "text/html" in (request.headers.get("Accept") or "") and not as_attachment:
                # 文件名来自 URL，必须转义：以前直接插进 HTML，`<img src=x onerror=...>`
                # 这类名字会在同源执行脚本（自 XSS）。另加 CSP 兜底。
                from html import escape as _esc

                html = (
                    '<!doctype html><meta charset="utf-8">'
                    '<title>文件已过期</title>'
                    '<div style="font:16px/1.7 -apple-system,Segoe UI,Microsoft YaHei,sans-serif;'
                    'max-width:640px;margin:12vh auto;padding:0 24px;color:#334155">'
                    '<h2 style="color:#0f172a">文件已过期或不存在</h2>'
                    f'<p>文件「<b>{_esc(name)}</b>」可能已超过 {_keep_days} 天保留期被清理，或从未生成过。</p>'
                    '<p>如仍需要，请在对话里让助手重新生成一次。</p></div>'
                )
                return HTMLResponse(html, status_code=404, headers={
                    "Content-Security-Policy":
                        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                    "X-Content-Type-Options": "nosniff",
                })
            return JSONResponse({"error": msg}, status_code=404)

        as_attachment: bool = wants_attachment(request)
        resp = FileResponse(
            path,
            filename=name,
            content_disposition_type="attachment" if as_attachment else "inline",
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, no-store"
        if name.lower().endswith((".html", ".htm", ".svg")):
            resp.headers["Content-Security-Policy"] = (
                "sandbox allow-scripts allow-popups allow-forms allow-modals"
            )
        log.info(f"产出文件下发: user={username}, session={session_id}, "
                 f"file={name}, download={as_attachment}")
        return resp
    except Exception as e:
        log.error(f"产出文件下发失败: {str(e)}", exc_info=True)
        return JSONResponse({"error": _safe_error(e)}, status_code=500)


@router.post("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str, payload: dict | None = Body(None),
                    username: str = Depends(require_login)):
    """归档/恢复会话：只从默认列表隐藏，不删除任何数据"""
    try:
        archived: bool = bool((payload or {}).get("archived", True))

        db = get_db()
        # 前端可能传数据库主键 id，也可能传业务会话号 session_id，两种都接受
        sess = (await db.execute(
            select(ChatSession).where(
                or_(ChatSession.id == session_id, ChatSession.session_id == session_id),
                ChatSession.username == username,
            )
        )).scalars().first()
        if sess is None:
            return JSONResponse({"error": "会话不存在或无权操作"}, status_code=404)

        sess.archived = archived
        await db.commit()
        log.info(f"会话{'归档' if archived else '恢复'}: user={username}, session={session_id}")
        return JSONResponse({"ok": True, "archived": archived})
    except Exception as e:
        log.error(f"归档会话失败: {str(e)}", exc_info=True)
        return JSONResponse({"error": _safe_error(e)}, status_code=500)


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, username: str = Depends(require_login)):
    """删除会话（MySQL + Redis + PostgreSQL checkpoint + 内存缓存）"""
    try:
        await SessionDao.delete_session(session_id, username)
        evict_agent_from_cache(session_id, username)
        await delete_thread(session_id, username)
        # 轨迹物理删除（审计表保留：合规留痕不随会话消失）
        try:
            from src.infra import TraceService

            TraceService.delete_by_session(session_id, username)
        except Exception as _te:
            log.warning(f"删除会话轨迹失败（忽略）: {_te}")
        return JSONResponse({"ok": True})
    except Exception as e:
        log.error(f"删除会话失败: {str(e)}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ============================================================
# 健康检查
# ============================================================

@router.get("/api/health")
async def health_check():
    """健康检查端点：返回 DB、Redis 连通性状态（不需要登录，便于探活）

    只回 ok/error，**不回异常原文**：这里不需要登录，异常里可能带库地址/账号等内部信息。
    """
    status = {"ok": True, "db": "unknown", "redis": "unknown"}
    try:
        from sqlalchemy import text as sa_text

        db = get_db()
        await db.execute(sa_text("SELECT 1"))
        status["db"] = "ok"
    except Exception as e:
        log.warning(f"健康检查：数据库不可用: {e}")
        status["db"] = "error"
        status["ok"] = False
    try:
        if redis_util.available:
            redis_util._client.ping()
            status["redis"] = "ok"
        else:
            status["redis"] = "disconnected"
    except Exception as e:
        log.warning(f"健康检查：Redis 不可用: {e}")
        status["redis"] = "error"
        status["ok"] = False
    return JSONResponse(status, status_code=200 if status["ok"] else 503)
