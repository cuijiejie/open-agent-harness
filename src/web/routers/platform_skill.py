#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""平台技能：全体用户共享的技能库

存储：宿主 `<repo>/skills-platform/<条目>/`，每个条目是**不可变快照**
    office-file-toolkit/        第一次发布
    office-file-toolkit-v2/     同名再发布（不覆盖旧版）

关键语义（与用户确认过）：
    · 设为平台 **立即生效**，没有人工审核环节（仍跑与普通上传同一套硬性检查）
    · 同名再发布 = 新增一个版本条目，**不覆盖**原版本
    · 添加到自己沙箱 = 复制一份，之后两份完全独立
    · 删除权限：管理员可删全部；普通用户只能删**自己发布**的那条
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
import zipfile

from fastapi import APIRouter, Body, Depends, File, UploadFile
from fastapi.responses import JSONResponse, Response

from src.agent.backends.sandbox import sandbox_manager
from src.infra.utils.LogUtil import log
from src.infra.utils.SkillAudit import audit_skill_md, parse_skill_meta
from src.web.routers.skill import (
    ALLOWED_EXT,
    MAX_FILES,
    MAX_ZIP_BYTES,
    SKILLS_DIR,
    _archive_out,
    _bust_skills_cache,
    _evict,
    _safe_name,
    scan_output_paths,
)
from src.web.security import require_login

router: APIRouter = APIRouter()

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLATFORM_DIR: str = os.path.join(_REPO_ROOT, "skills-platform")
CACHE_KEY: str = "cache:platform:skills"
CACHE_TTL: int = 600
_VER_RE = re.compile(r"^(?P<base>.+?)-v(?P<v>\d+)$")


# ============================================================
# 基础工具
# ============================================================

def _base_of(name: str) -> str:
    """office-file-toolkit-v2 → office-file-toolkit"""
    m = _VER_RE.match(name or "")
    return m.group("base") if m else (name or "")


def _ver_of(name: str) -> int:
    m = _VER_RE.match(name or "")
    return int(m.group("v")) if m else 1


def _entry_dir(name: str) -> str:
    n = _safe_name(name)
    return os.path.join(PLATFORM_DIR, n)


def _bust() -> None:
    try:
        from src.infra.utils import CacheUtil

        CacheUtil.delete(CACHE_KEY)
    except Exception:
        pass


def _is_admin(username: str) -> bool:
    try:
        from src.infra.RbacService import rbac

        return bool(rbac.is_admin(username))
    except Exception as e:
        log.warning(f"平台技能：判定管理员失败 {username}: {e}")
        return False


def _meta_from_dir(name: str, d: str) -> dict:
    """没有 _platform.json 时按目录现算一份（兼容手工放进去的技能）"""
    import hashlib

    desc, files, size, h = "", 0, 0, hashlib.sha256()
    smd = os.path.join(d, "SKILL.md")
    if os.path.isfile(smd):
        try:
            txt = open(smd, encoding="utf-8", errors="replace").read(4000)
            mm = re.search(r"^description:\s*(.+)$", txt, re.M)
            if mm:
                desc = mm.group(1).strip().strip("\"'")[:200]
        except Exception:
            pass
    for dp, _dn, fn in os.walk(d):
        for f in sorted(fn):
            fp = os.path.join(dp, f)
            try:
                st = os.stat(fp)
                files += 1
                size += st.st_size
                h.update(os.path.relpath(fp, d).encode())
                with open(fp, "rb") as fh:
                    h.update(fh.read())
            except OSError:
                continue
    return {"name": name, "base_name": _base_of(name), "version": _ver_of(name),
            "description": desc, "contributor": "", "created_at": "",
            "files": files, "size": size, "sha256": h.hexdigest()[:16]}


def _scan() -> list[dict]:
    out: list[dict] = []
    if not os.path.isdir(PLATFORM_DIR):
        return out
    for name in sorted(os.listdir(PLATFORM_DIR)):
        d = os.path.join(PLATFORM_DIR, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        meta: dict = {}
        mp = os.path.join(d, "_platform.json")
        if os.path.isfile(mp):
            try:
                with open(mp, encoding="utf-8") as fh:
                    meta = json.load(fh) or {}
            except Exception:
                meta = {}
        if not meta:
            meta = _meta_from_dir(name, d)
        meta.setdefault("name", name)
        meta.setdefault("base_name", _base_of(name))
        meta.setdefault("version", _ver_of(name))
        out.append(meta)
    # 同一基础名：版本高的在前
    out.sort(key=lambda m: (m.get("base_name") or "", -(m.get("version") or 1)))
    return out


def _scan_map() -> dict:
    return {m.get("name"): m for m in _scan()}


def _next_entry_name(base: str) -> str:
    """同名再发布 → <base>-v2 / -v3 …"""
    if not os.path.isdir(os.path.join(PLATFORM_DIR, base)):
        return base
    v = 1
    for name in os.listdir(PLATFORM_DIR):
        if _base_of(name) == base:
            v = max(v, _ver_of(name))
    return f"{base}-v{v + 1}"


def _tgz_of_dir(src: str, arcname: str, exclude: set[str] | None = None) -> str:
    """把宿主目录打成 tgz（arcname 决定解包后的目录名）

    exclude：按文件名排除（平台元数据 _platform.json 不该落到用户沙箱里）
    """
    skip = exclude or set()
    path = os.path.join(tempfile.gettempdir(), f".plat-{uuid.uuid4().hex[:8]}.tgz")

    def _flt(ti: tarfile.TarInfo):
        return None if os.path.basename(ti.name) in skip else ti

    with tarfile.open(path, "w:gz") as tf:
        tf.add(src, arcname=arcname, filter=_flt)
    return path


def _pack_from_sandbox(username: str, skill: str) -> str | None:
    """把用户沙箱里的技能下载成宿主 tgz（**不删除**用户那份）"""
    remote = f"/sandbox/.tmp/.pub-{uuid.uuid4().hex[:8]}.tgz"
    r = sandbox_manager.exec(
        username, f"mkdir -p /sandbox/.tmp && cd {SKILLS_DIR} && tar -czf {remote} {skill}",
        timeout=90)
    if not r.ok:
        log.warning(f"平台技能：打包失败 {skill}: {(r.stderr or r.error or '')[:200]}")
        return None
    local = os.path.join(tempfile.gettempdir(), f".pub-{uuid.uuid4().hex[:8]}.tgz")
    try:
        if not sandbox_manager.download(username, remote, local):
            log.warning(f"平台技能：下载失败 {skill}")
            return None
        return local
    finally:
        sandbox_manager.exec(username, f"rm -f {remote}", timeout=20)


# ============================================================
# 端点
# ============================================================

@router.get("/api/platform-skills")
def list_platform_skills(username: str = Depends(require_login)):
    """平台技能列表（所有登录用户可见）"""
    cached = None
    try:
        from src.infra.utils import CacheUtil

        cached = CacheUtil.get(CACHE_KEY)
    except Exception:
        pass
    rows = cached if isinstance(cached, list) else _scan()
    if not isinstance(cached, list):
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.set(CACHE_KEY, rows, CACHE_TTL)
        except Exception:
            pass
    admin = _is_admin(username)
    # can_delete 因人而异，不能进缓存 → 拷贝一份再算
    out = []
    for m in rows:
        item = dict(m)
        item["can_delete"] = bool(admin or (item.get("contributor") and
                                            item["contributor"] == username))
        out.append(item)
    return JSONResponse({"skills": out, "is_admin": admin})


@router.get("/api/platform-skills/{name}/download")
def download_platform_skill(name: str, username: str = Depends(require_login)):
    """下载平台技能为 .zip（与上传格式一致，改完可直接传回）"""
    try:
        d = _entry_dir(name)
    except ValueError:
        return JSONResponse({"error": "技能名不合法"}, status_code=400)
    if not os.path.isdir(d):
        return JSONResponse({"error": "技能不存在"}, status_code=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, _dn, fn in os.walk(d):
            for f in sorted(fn):
                if f == "_platform.json":
                    continue
                fp = os.path.join(dp, f)
                arc = os.path.relpath(fp, d)
                try:
                    zf.write(fp, arc)
                except OSError:
                    continue
    log.info(f"下载平台技能: user={username}, name={name}")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@router.post("/api/platform-skills/{name}/add")
def add_to_sandbox(name: str, payload: dict | None = Body(None),
                   username: str = Depends(require_login)):
    """把平台技能复制一份到我的沙箱（之后两份独立）"""
    try:
        d = _entry_dir(name)
    except ValueError:
        return JSONResponse({"error": "技能名不合法"}, status_code=400)
    if not os.path.isdir(d):
        return JSONResponse({"error": "技能不存在"}, status_code=404)
    if not sandbox_manager.enabled:
        return JSONResponse({"error": "沙箱不可用"}, status_code=503)

    overwrite: bool = bool((payload or {}).get("overwrite"))
    meta = _scan_map().get(name) or {}
    local: str = _safe_name(meta.get("base_name") or _base_of(name))

    chk = sandbox_manager.exec(
        username, f"test -d {SKILLS_DIR}/{local} && echo yes || echo no", timeout=20)
    exists = "yes" in (chk.stdout or "")
    if exists and not overwrite:
        return JSONResponse({"error": "exists", "local_name": local,
                             "message": "你的沙箱里已有同名技能"}, status_code=409)
    if exists:
        # 覆盖前把旧版归档到宿主（误覆盖可找回）
        if not _archive_out(username, local):
            return JSONResponse({"error": "覆盖前备份旧技能失败，已中止"}, status_code=500)

    tgz = _tgz_of_dir(d, local, exclude={"_platform.json"})
    remote = f"/sandbox/.tmp/.add-{uuid.uuid4().hex[:8]}.tgz"
    try:
        if not sandbox_manager.upload(username, tgz, remote):
            return JSONResponse({"error": "上传到沙箱失败"}, status_code=500)
        cmd = (f"mkdir -p {SKILLS_DIR} && tar -xzf {remote} -C {SKILLS_DIR} "
               f"&& rm -f {remote} && test -d {SKILLS_DIR}/{local} && echo ok")
        r = sandbox_manager.exec(username, cmd, timeout=90)
        if not (r.ok and "ok" in (r.stdout or "")):
            return JSONResponse({"error": "解包失败: " + (r.stderr or "")[:150]}, status_code=500)
    finally:
        try:
            os.remove(tgz)
        except OSError:
            pass

    _evict(username, f"platform add {name}")
    _bust_skills_cache()
    log.info(f"平台技能已添加到沙箱: user={username}, platform={name}, local={local}")
    return JSONResponse({"ok": True, "local_name": local, "overwritten": exists})


@router.post("/api/platform-skills")
def publish_skill(payload: dict | None = Body(None), username: str = Depends(require_login)):
    """把我的技能设为平台技能（立即生效；同名 → 新增版本，不覆盖旧版）"""
    if not sandbox_manager.enabled:
        return JSONResponse({"error": "沙箱不可用"}, status_code=503)

    try:
        src: str = _safe_name(str((payload or {}).get("name") or ""))
    except ValueError:
        return JSONResponse({"error": "技能名不合法"}, status_code=400)

    chk = sandbox_manager.exec(
        username, f"test -d {SKILLS_DIR}/{src} && echo yes || echo no", timeout=20)
    if "yes" not in (chk.stdout or ""):
        return JSONResponse({"error": "你的沙箱里没有这个技能"}, status_code=404)

    tgz = _pack_from_sandbox(username, src)
    if not tgz:
        return JSONResponse({"error": "打包技能失败"}, status_code=500)
    try:
        # 硬性内容检查（与普通上传同一套规则，不通过就不放行）
        content = ""
        with tarfile.open(tgz, "r:gz") as tf:
            try:
                fh = tf.extractfile(f"{src}/SKILL.md")
                content = fh.read().decode("utf-8", "replace") if fh else ""
            except KeyError:
                content = ""
        if not content:
            return JSONResponse({"error": "技能缺少 SKILL.md"}, status_code=400)
        passed, blocked, warned = audit_skill_md(content)
        if not passed:
            log.warning(f"平台技能发布被拦: user={username}, name={src}, 命中={blocked}")
            return JSONResponse({"error": "技能内容检查未通过: " + "；".join(blocked)}, status_code=400)

        base = _base_of(src)
        entry = _next_entry_name(base)
        target = os.path.join(PLATFORM_DIR, entry)
        os.makedirs(PLATFORM_DIR, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix="pa_plat_")
        try:
            with tarfile.open(tgz, "r:gz") as tf:
                tf.extractall(tmp_dir)
            shutil.move(os.path.join(tmp_dir, src), target)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        meta = _meta_from_dir(entry, target)
        meta.update({
            "name": entry, "base_name": base, "version": _ver_of(entry),
            "contributor": username, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "warnings": warned[:5],
        })
        with open(os.path.join(target, "_platform.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        _bust()
        log.info(f"平台技能已发布: user={username}, entry={entry}, {meta.get('size')} 字节")
        return JSONResponse({"ok": True, "name": entry, "base_name": base,
                             "version": meta["version"], "warnings": warned[:5]})
    finally:
        try:
            os.remove(tgz)
        except OSError:
            pass


@router.post("/api/platform-skills/upload")
def upload_platform_skill(file: UploadFile = File(...), username: str = Depends(require_login)):
    """管理员直接把 zip 技能包传上平台"""
    if not _is_admin(username):
        return JSONResponse({"error": "只有管理员可以直接上传平台技能"}, status_code=403)
    if not file or not file.filename:
        return JSONResponse({"error": "文件为空"}, status_code=400)
    if not file.filename.lower().endswith(".zip"):
        return JSONResponse({"error": "只接受 .zip（技能包内需含 SKILL.md）"}, status_code=400)

    tmp_root = tempfile.mkdtemp(prefix="pa_plat_up_")
    try:
        zip_path = os.path.join(tmp_root, "pkg.zip")
        with open(zip_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        if os.path.getsize(zip_path) <= 0:
            return JSONResponse({"error": "文件为空"}, status_code=400)
        if os.path.getsize(zip_path) > MAX_ZIP_BYTES:
            return JSONResponse({"error": f"技能包过大（上限 {MAX_ZIP_BYTES // 1024 // 1024}MB）"},
                                status_code=400)

        extract_dir = os.path.join(tmp_root, "pkg")
        os.makedirs(extract_dir, exist_ok=True)
        total = count = 0
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                nm = info.filename.replace("\\", "/")
                if nm.startswith("/") or ".." in nm.split("/"):
                    return JSONResponse({"error": f"技能包内含非法路径: {nm}"}, status_code=400)
                if info.is_dir():
                    continue
                ext = os.path.splitext(nm)[1].lower()
                if ext not in ALLOWED_EXT:
                    return JSONResponse({"error": f"不允许的文件类型: {nm}"}, status_code=400)
                count += 1
                total += info.file_size
                if count > MAX_FILES or total > MAX_ZIP_BYTES:
                    return JSONResponse({"error": f"技能包文件过多或解压后过大（>{MAX_FILES} 个）"},
                                        status_code=400)
            zf.extractall(extract_dir)

        root = None
        for dp, _dn, fn in os.walk(extract_dir):
            if "SKILL.md" in fn:
                root = dp
                break
        if not root:
            return JSONResponse({"error": "技能包里没有 SKILL.md"}, status_code=400)

        content = open(os.path.join(root, "SKILL.md"), encoding="utf-8", errors="replace").read()
        passed, blocked, warned = audit_skill_md(content)
        if not passed:
            return JSONResponse({"error": "技能内容检查未通过: " + "；".join(blocked)}, status_code=400)

        meta_in = parse_skill_meta(content, fallback_name=os.path.basename(root) or "platform-skill")
        try:
            base = _base_of(_safe_name(str(meta_in.get("name") or os.path.basename(root))))
        except ValueError:
            return JSONResponse({"error": "技能名不合法（frontmatter 的 name）"}, status_code=400)

        entry = _next_entry_name(base)
        target = os.path.join(PLATFORM_DIR, entry)
        os.makedirs(PLATFORM_DIR, exist_ok=True)
        shutil.move(root, target)

        meta = _meta_from_dir(entry, target)
        meta.update({
            "name": entry, "base_name": base, "version": _ver_of(entry),
            "contributor": username, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "warnings": warned[:5] + scan_output_paths(target)[:5],
        })
        with open(os.path.join(target, "_platform.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        _bust()
        log.info(f"平台技能已上传: admin={username}, entry={entry}")
        return JSONResponse({"ok": True, "name": entry, "version": meta["version"]})
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@router.delete("/api/platform-skills/{name}")
def delete_platform_skill(name: str, username: str = Depends(require_login)):
    """删除平台技能：管理员可删全部，普通用户只能删自己发布的"""
    try:
        d = _entry_dir(name)
    except ValueError:
        return JSONResponse({"error": "技能名不合法"}, status_code=400)
    if not os.path.isdir(d):
        return JSONResponse({"error": "技能不存在"}, status_code=404)

    contributor = ""
    mp = os.path.join(d, "_platform.json")
    if os.path.isfile(mp):
        try:
            contributor = str((json.load(open(mp, encoding="utf-8")) or {}).get("contributor") or "")
        except Exception:
            contributor = ""

    if not (_is_admin(username) or (contributor and contributor == username)):
        return JSONResponse({"error": "只能删除自己发布的平台技能"}, status_code=403)

    shutil.rmtree(d, ignore_errors=False)
    _bust()
    log.warning(f"平台技能已删除: by={username}, entry={name}, contributor={contributor}")
    return JSONResponse({"ok": True, "name": name})
