"""技能管理接口：列出 / 启停 / 删除 / 上传（zip 技能包）

技能放在**用户自己的沙箱**里（`/sandbox/skills/<名>/`），这里只做三件事：
    ① 读目录生成清单（名字/说明/文件数/体积/指纹/是否带工具/是否启用）
    ② 搬运目录实现启停（`/sandbox/skills-disabled/`）
    ③ 上传 zip：宿主侧解包 + 审计 → 打包成 tar.gz 推进沙箱解压

为什么停用是"移出目录"而不是打标记：
`SkillsMiddleware` 只扫 `/sandbox/skills/`，技能工具只扫 `/sandbox/skills/*/tools.json`
—— 目录搬走，技能说明和工具就一起消失，不需要任何额外判断（少一处状态 = 少一处不一致）。

为什么删除是"移到回收站"：
`/sandbox/skills-trash/<名>-<时间戳>/`，误删还能手工找回；沙箱重建后自然清空。
"""
from __future__ import annotations

import json
import os
import re
import re
import shutil
import tarfile
import tempfile
import time
import uuid

from flask import Blueprint, Response, current_app, jsonify, request
from src.infra.utils.AuthUtil import login_required

from src.agent.backends.sandbox import sandbox_manager
from src.infra.utils.LogUtil import log
from src.infra.utils.SkillAudit import audit_skill_md, parse_skill_meta
from .ChatController import _get_username

SkillController = Blueprint('SkillController', __name__)

SKILLS_DIR: str = "/sandbox/skills"
DISABLED_DIR: str = "/sandbox/skills-disabled"
TRASH_DIR: str = "/sandbox/skills-trash"
UPLOAD_DIR: str = "/sandbox/.pa-upload"

# ---------- 停用技能的宿主归档 ----------
# 停用 = 整目录打包**取出沙箱**存到宿主归档，沙箱内不留副本。
# 为什么不再 mv 到 /sandbox/skills-disabled/：模型用 sandbox_shell 仍能
# `ls /sandbox/skills-disabled && cat */SKILL.md` 把停用技能的内容翻出来
# ——停用只是「不注入提示」，内容照样可读。要真正不可见，内容必须离开沙箱。
_ARCHIVE_ROOT: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "artifacts", "skills-archive",
)
_MAX_ARCHIVE_BYTES: int = 20 * 1024 * 1024


def _user_archive_dir(username: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", username or "") or "_"
    d = os.path.join(_ARCHIVE_ROOT, safe)
    os.makedirs(d, exist_ok=True)
    return d


def _archived_meta(username: str) -> list[dict]:
    """宿主归档里该用户「已停用」的技能（同名取最新一份）"""
    d = _user_archive_dir(username)
    latest: dict[str, dict] = {}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    for f in names:
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                meta = json.load(fh)
        except Exception:
            continue
        nm = meta.get("name") or ""
        if nm:
            latest[nm] = meta
    return list(latest.values())


def _archive_out(username: str, n: str) -> bool:
    """打包技能目录 → 下载到宿主归档 → 删沙箱副本（走 upload/download 二进制通道）"""
    ts = time.strftime("%Y%m%d%H%M%S")
    # 只能用 /sandbox 下的路径：snap 版 CLI 对 /tmp 的 download 会直接失败
    remote = f"/sandbox/.tmp/.skp-{uuid.uuid4().hex[:8]}.tgz"
    r = sandbox_manager.exec(username, f"mkdir -p /sandbox/.tmp && cd {SKILLS_DIR} && tar -czf {remote} {n}",
                             timeout=90)
    if not r.ok:
        log.warning(f"技能打包失败 {n}: {(r.stderr or r.error or '')[:200]}")
        return False

    local = os.path.join(tempfile.gettempdir(), f".skp-{uuid.uuid4().hex[:8]}.tgz")
    try:
        if not sandbox_manager.download(username, remote, local):
            log.warning(f"技能归档下载失败: {n}")
            return False
        with open(local, "rb") as fh:
            blob = fh.read()
    finally:
        try:
            os.remove(local)
        except OSError:
            pass

    if not blob:
        log.warning(f"技能归档为空: {n}")
        return False
    if len(blob) > _MAX_ARCHIVE_BYTES:
        log.warning(f"技能超过归档上限 {n}: {len(blob)} 字节")
        return False

    desc, files_n, tools_n = "", 0, 0
    try:
        import io as _io

        with tarfile.open(fileobj=_io.BytesIO(blob), mode="r:gz") as tf:
            files_n = sum(1 for m in tf.getmembers() if m.isfile())
            for cand in (f"{n}/SKILL.md", f"{n}/tools.json"):
                try:
                    fh = tf.extractfile(cand)
                except KeyError:
                    fh = None
                if not fh:
                    continue
                txt = fh.read().decode("utf-8", "replace")
                if cand.endswith("SKILL.md"):
                    mm = re.search(r"^description:\s*(.+)$", txt, re.M)
                    if mm:
                        desc = mm.group(1).strip().strip("\"'")[:200]
                else:
                    try:
                        tools_n = len((json.loads(txt) or {}).get("tools") or [])
                    except Exception:
                        tools_n = 0
    except Exception as e:
        log.warning(f"解析归档技能失败 {n}: {e}")

    d = _user_archive_dir(username)
    with open(os.path.join(d, f"{n}-{ts}.tgz"), "wb") as fh:
        fh.write(blob)
    meta = {"name": n, "description": desc, "files": files_n, "size": len(blob),
            "sha256": "", "tools": tools_n, "enabled": False, "archived_at": ts}
    with open(os.path.join(d, f"{n}-{ts}.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)

    sandbox_manager.exec(username, f"rm -f {remote} && rm -rf {SKILLS_DIR}/{n}",
                         timeout=30)
    log.info(f"技能已归档停用: user={username}, name={n}, {len(blob)} 字节")
    return True


def _restore_in(username: str, n: str) -> bool:
    """从宿主归档把技能解回沙箱（upload 二进制通道，一次传完）"""
    d = _user_archive_dir(username)
    try:
        cands = sorted(f for f in os.listdir(d)
                       if f.startswith(f"{n}-") and f.endswith(".tgz"))
    except OSError:
        cands = []
    if not cands:
        return False
    p = os.path.join(d, cands[-1])

    remote = f"/sandbox/.tmp/.rs-{uuid.uuid4().hex[:8]}.tgz"
    if not sandbox_manager.upload(username, p, remote):
        log.warning(f"技能归档上传失败: {n}")
        return False
    cmd = (f"mkdir -p {SKILLS_DIR} && tar -xzf {remote} -C {SKILLS_DIR} "
           f"&& rm -f {remote} && test -d {SKILLS_DIR}/{n} && echo ok")
    r = sandbox_manager.exec(username, cmd, timeout=90)
    if not (r.ok and "ok" in (r.stdout or "")):
        log.warning(f"技能归档解包失败 {n}: {(r.stderr or '')[:200]}")
        return False
    # 还原成功后清掉归档，避免下次停用再叠一份
    for f in cands:
        try:
            os.remove(os.path.join(d, f))
            os.remove(os.path.join(d, f[:-4] + ".json"))
        except OSError:
            pass
    log.info(f"技能已从归档启用: user={username}, name={n}")
    return True

MAX_ZIP_BYTES: int = 20 * 1024 * 1024
MAX_FILES: int = 200
ALLOWED_EXT: set[str] = {".md", ".py", ".sh", ".txt", ".json", ".yaml", ".yml", ".csv", ".html", ".jinja", ".j2"}
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# 沙箱内生成清单的脚本（一次 exec 拿全部技能，避免每个技能一次 CLI 调用）
_LIST_SCRIPT: str = r'''
import hashlib, json, os
roots = [("/sandbox/skills", True), ("/sandbox/skills-disabled", False)]
out = []
for root, enabled in roots:
    if not os.path.isdir(root):
        continue
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p) or name.startswith("."):
            continue
        desc = ""
        smd = os.path.join(p, "SKILL.md")
        if os.path.isfile(smd):
            try:
                txt = open(smd, encoding="utf-8", errors="replace").read(4000)
                if txt.startswith("---"):
                    end = txt.find("\n---", 3)
                    fm = txt[3:end if end > 0 else 400]
                    lines = fm.splitlines()
                    for i, line in enumerate(lines):
                        s = line.strip()
                        if not s.lower().startswith("description:"):
                            continue
                        val = s.split(":", 1)[1].strip()
                        if val in (">", "|", ">-", "|-", ">+", "|+"):
                            # YAML 折叠/字面块：把后续缩进行拼起来（否则只会得到 ">"）
                            parts = []
                            for nxt in lines[i + 1:]:
                                if nxt.strip() and not nxt.startswith((" ", "\t")):
                                    break
                                parts.append(nxt.strip())
                            desc = " ".join(x for x in parts if x)
                        else:
                            desc = val.strip("\"'")
                        break
            except Exception:
                pass
        files, size, h = 0, 0, hashlib.sha256()
        for dp, dn, fn in os.walk(p):
            dn.sort()
            for f in sorted(fn):
                fp = os.path.join(dp, f)
                try:
                    st = os.stat(fp)
                    files += 1
                    size += st.st_size
                    h.update(os.path.relpath(fp, p).encode())
                    with open(fp, "rb") as fh:
                        h.update(fh.read())
                except OSError:
                    continue
        tools = 0
        tj = os.path.join(p, "tools.json")
        if os.path.isfile(tj):
            try:
                spec = json.load(open(tj, encoding="utf-8"))
                tools = len(spec.get("tools") or []) if isinstance(spec, dict) else 0
            except Exception:
                tools = -1
        out.append({"name": name, "description": desc[:200], "files": files, "size": size,
                    "sha256": h.hexdigest()[:16], "tools": tools, "enabled": enabled})
print(json.dumps(out, ensure_ascii=False))
'''



# ========== 目录规范检查（只警告，不拦截） ==========
# AGENT.md 7.12 规定：产物写 /sandbox/output/，用户上传在 /sandbox/upload/，
# 中间文件写 /sandbox/.tmp/。技能脚本若写死 /sandbox 根路径或自建顶层目录，
# 会被系统兜底归拢，但对用户来说目录结构会变乱——上传时提示一声。
_BAD_PATH_RE = re.compile(r"""["']/sandbox/(?!output|upload|\.tmp|skills)[^"'
]*["']""")
_SCAN_EXTS = {".py", ".sh", ".md", ".json"}


def scan_output_paths(skill_root: str) -> list[str]:
    warnings: list[str] = []
    for dp, _dn, fn in os.walk(skill_root):
        for name in fn:
            if os.path.splitext(name)[1].lower() not in _SCAN_EXTS:
                continue
            fp = os.path.join(dp, name)
            try:
                text = open(fp, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for hit in _BAD_PATH_RE.findall(text):
                rel = os.path.relpath(fp, skill_root)
                warnings.append(f"{rel}: {hit} → 建议改成 /sandbox/output/ 下的路径")
                if len(warnings) >= 5:
                    return warnings
    return warnings


def _safe_name(name: str) -> str:
    n: str = (name or "").strip()
    if not _NAME_RE.match(n) or n in (".", ".."):
        raise ValueError(f"技能名不合法: {name!r}")
    return n


def _evict(username: str, why: str) -> None:
    """技能变动后清 agent 缓存，让下一轮重新加载技能与工具"""
    try:
        from src.agent.main_agent import evict_agent_cache_by_user

        evict_agent_cache_by_user(username)
        log.info(f"技能变动已清缓存（{why}）: user={username}")
    except Exception as e:
        log.warning(f"清 agent 缓存失败（{why}）, 用户需新会话生效: {e}")


_SKILLS_CACHE: dict = {"t": 0.0, "k": "", "v": None}


def _bust_skills_cache() -> None:
    """技能增删/启停后清缓存（Redis 前缀删 + 换版本号）

    只删 key 不够：删除/停用要在沙箱里 mv 目录（1~4 秒），这期间前端/其它请求
    发出的 GET 会把**旧列表**重新写回缓存，于是刚删掉的技能几秒后又"复活"。
    用版本号做 key 前缀，旧请求只能写进已废弃的 key。
    """
    try:
        import time as _t

        from src.infra.utils import CacheUtil

        CacheUtil.set("cache:skver", str(_t.time()), 30 * 24 * 3600)
        CacheUtil.delete_prefix("cache:skills:")
        CacheUtil.delete_prefix("cache:skilltools:")
    except Exception:
        pass
    # 技能目录变了：backend 里「/sandbox/skills 列表」的短缓存必须跟着失效，
    # 否则新会话读到的还是旧技能列表（缓存 300 秒会更明显）
    try:
        from src.agent.backends.openshell import invalidate_backend_cache

        invalidate_backend_cache()
    except Exception:
        pass


@SkillController.route('/api/skills', methods=['GET'])
@login_required
def list_skills() -> tuple[Response, int]:
    """当前用户沙箱里的技能清单（含已停用）"""
    username = _get_username()
    if not sandbox_manager.enabled:
        return jsonify({'skills': [], 'sandbox': False, 'error': '沙箱未启用'}), 200
    import shlex
    import time as _t3

    # 容器内要遍历目录 + 算 sha256（535KB / 19 文件 ≈ 0.9s）→ Redis 缓存 10 分钟
    from src.infra.utils import CacheUtil as _CU

    # 缓存键带「技能版本号」：增删/启停会换版本，旧请求写不进新 key
    _ver: str = str(_CU.get("cache:skver") or "0")
    _ck: str = f"cache:skills:{_ver}:{username}"
    _cached = _CU.get(_ck)
    if _cached is not None:
        return jsonify({'skills': _cached, 'sandbox': True, 'cached': True}), 200

    res = sandbox_manager.exec(username, f"python3 -c {shlex.quote(_LIST_SCRIPT)}", timeout=60)
    if not res.ok:
        log.warning(f"列技能失败: user={username}, err={(res.stderr or res.error or '')[:200]}")
        return jsonify({'skills': [], 'sandbox': True, 'error': '读取技能目录失败'}), 200
    try:
        skills: list = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception as e:
        log.warning(f"解析技能清单失败: {e}")
        return jsonify({'skills': [], 'sandbox': True, 'error': '解析清单失败'}), 200
    # 补充「已停用」：停用的技能已搬出沙箱，条目在宿主归档里
    try:
        _known = {s.get('name') for s in skills}
        for meta in _archived_meta(username):
            if meta.get('name') not in _known:
                skills.append({k: meta.get(k) for k in
                               ('name', 'description', 'files', 'size', 'sha256',
                                'tools', 'enabled')})
    except Exception as e:
        log.warning(f"合并技能归档失败: {e}")
    skills.sort(key=lambda s: (not s.get('enabled'), s.get('name') or ''))
    _CU.set(_ck, skills, 600)   # 上传/删除/启停会主动删，TTL 只为兜底
    return jsonify({'skills': skills, 'sandbox': True}), 200


@SkillController.route('/api/skills/<name>/download', methods=['GET'])
@login_required
def download_skill(name: str):
    """把沙箱里的技能包打包成 **.zip** 下载（与上传格式一致，改完可直接传回）

    实现：容器内 `tar czf - | base64`，一次性把整个目录读回来，
    宿主机再转成 zip。不走 openshell download —— snap 命名空间限制写不进外部路径。
    """
    import base64
    import io as _io
    import tarfile
    import zipfile

    from flask import send_file

    try:
        username: str = _get_username()
        if not username:
            return jsonify({'error': '未登录'}), 401

        safe_name: str = str(name or '').strip()
        if not safe_name or '/' in safe_name or '..' in safe_name:
            return jsonify({'error': '技能名不合法'}), 400

        from src.agent.backends.sandbox import sandbox_manager

        if not sandbox_manager.enabled:
            return jsonify({'error': '沙箱不可用'}), 503

        q = "'" + safe_name.replace("'", "'\''") + "'"
        chk = sandbox_manager.exec(username, f"test -d /sandbox/skills/{q} && echo yes || echo no", timeout=15)
        if 'yes' not in (chk.stdout or ''):
            return jsonify({'error': '技能不存在'}), 404

        # 容器内：打 tar 到 stdout，base64 编码（避免二进制经过 stdout 被破坏）
        r = sandbox_manager.exec(
            username,
            f"cd /sandbox/skills && tar czf - --exclude='__pycache__' --exclude='.venv' "
            f"--exclude='*.pyc' {q} 2>/dev/null | base64 -w0",
            timeout=120,
            max_output=0,      # 0 = 不截断，整包读回
        )
        b64: str = (r.stdout or '').strip()
        if not b64:
            log.warning(f"打包技能为空: {safe_name}, err={(r.stderr or '')[:200]}")
            return jsonify({'error': '打包失败（目录可能为空）'}), 500

        raw: bytes = base64.b64decode(b64)

        # 宿主机：tar.gz → zip（zipfile 是标准库，不依赖容器装没装 zip）
        buf = _io.BytesIO()
        count = 0
        with tarfile.open(fileobj=_io.BytesIO(raw), mode='r:gz') as tf,                 zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                fh = tf.extractfile(m)
                if fh is None:
                    continue
                zf.writestr(m.name, fh.read())
                count += 1
        buf.seek(0)

        log.info(f"技能包下载: user={username}, skill={safe_name}, 文件数={count}, zip={buf.getbuffer().nbytes}B")
        return send_file(
            buf, mimetype='application/zip', as_attachment=True,
            download_name=f"{safe_name}.zip",
        )
    except Exception as e:
        log.error(f"下载技能失败: {e}", exc_info=True)
        return jsonify({'error': '下载失败'}), 500


@SkillController.route('/api/skills/<name>/disable', methods=['POST'])
@login_required
def disable_skill(name: str) -> tuple[Response, int]:
    _bust_skills_cache()
    username = _get_username()
    import shlex

    try:
        n = _safe_name(name)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not _archive_out(username, n):
        # 兜底：归档失败退回老路径（至少能保证不再注入提示）
        cmd = (f"mkdir -p {DISABLED_DIR} && mv {shlex.quote(f'{SKILLS_DIR}/{n}')} "
               f"{shlex.quote(f'{DISABLED_DIR}/{n}')} && echo ok")
        res = sandbox_manager.exec(username, cmd, timeout=30)
        if not res.ok or 'ok' not in (res.stdout or ''):
            return jsonify({'error': (res.stderr or res.error or '停用失败').strip()[:200]}), 400
    _evict(username, f"disable {n}")
    _bust_skills_cache()   # 操作**之后**再清一次：期间并发请求可能把旧列表写回缓存
    return jsonify({'ok': True, 'name': n, 'enabled': False}), 200


@SkillController.route('/api/skills/<name>/enable', methods=['POST'])
@login_required
def enable_skill(name: str) -> tuple[Response, int]:
    _bust_skills_cache()
    username = _get_username()
    import shlex

    try:
        n = _safe_name(name)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not _restore_in(username, n):
        # 兜底：老路径（历史上被 mv 到 skills-disabled 的技能）
        cmd = (f"mkdir -p {SKILLS_DIR} && mv {shlex.quote(f'{DISABLED_DIR}/{n}')} "
               f"{shlex.quote(f'{SKILLS_DIR}/{n}')} && echo ok")
        res = sandbox_manager.exec(username, cmd, timeout=30)
        if not res.ok or 'ok' not in (res.stdout or ''):
            return jsonify({'error': (res.stderr or res.error or '启用失败').strip()[:200]}), 400
    _evict(username, f"enable {n}")
    _bust_skills_cache()   # 操作之后再清一次，避免并发请求写回旧列表
    return jsonify({'ok': True, 'name': n, 'enabled': True}), 200


@SkillController.route('/api/skills/<name>', methods=['DELETE'])
@login_required
def delete_skill(name: str) -> tuple[Response, int]:
    _bust_skills_cache()
    """删除 = 移到回收站（误删可找回；沙箱重建后自然清空）"""
    username = _get_username()
    import shlex

    try:
        n = _safe_name(name)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    ts = time.strftime("%Y%m%d%H%M%S")
    cmd = (f"mkdir -p {TRASH_DIR} && (mv {shlex.quote(f'{SKILLS_DIR}/{n}')} "
           f"{shlex.quote(f'{TRASH_DIR}/{n}-{ts}')} 2>/dev/null || mv "
           f"{shlex.quote(f'{DISABLED_DIR}/{n}')} {shlex.quote(f'{TRASH_DIR}/{n}-{ts}')}) && echo ok")
    res = sandbox_manager.exec(username, cmd, timeout=30)
    if not res.ok or 'ok' not in (res.stdout or ''):
        return jsonify({'error': (res.stderr or res.error or '删除失败').strip()[:200]}), 400
    _evict(username, f"delete {n}")
    # 归档里同名的一并清掉，否则删除后还会以「已停用」出现在列表里
    try:
        _d = _user_archive_dir(username)
        for f in os.listdir(_d):
            if f.startswith(f"{n}-") and (f.endswith(".tgz") or f.endswith(".json")):
                os.remove(os.path.join(_d, f))
    except OSError:
        pass
    _bust_skills_cache()   # 操作之后再清一次：否则删除期间的并发 GET 会把旧列表写回缓存
    return jsonify({'ok': True, 'name': n}), 200


@SkillController.route('/api/skills/upload', methods=['POST'])
@login_required
def upload_skill() -> tuple[Response, int]:

    _bust_skills_cache()
    """上传 zip 技能包（SKILL.md + scripts/ + tools.json）

    安全：宿主侧先解包并逐项校验（禁绝对路径/`..`/符号链接、限文件数与体积、限后缀），
    **审计 SKILL.md 不通过直接拒绝**；之后只在沙箱里落盘，脚本也只在沙箱里执行。
    """
    username = _get_username()
    if 'file' not in request.files:
        return jsonify({'error': '请选择 .zip 技能包'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'error': '文件为空'}), 400
    if not f.filename.lower().endswith('.zip'):
        return jsonify({'error': '只接受 .zip（技能包内需含 SKILL.md）'}), 400

    tmp_root: str = tempfile.mkdtemp(prefix="pa_skill_")
    zip_path: str = os.path.join(tmp_root, "pkg.zip")
    extract_dir: str = os.path.join(tmp_root, "pkg")
    try:
        f.save(zip_path)
        # 0 字节的"空 zip"：以前只校验 filename，zipfile 会抛 BadZipFile → 500 + 内部报错
        if os.path.getsize(zip_path) <= 0:
            return jsonify({'error': '文件为空'}), 400
        if os.path.getsize(zip_path) > MAX_ZIP_BYTES:
            return jsonify({'error': f'技能包过大（上限 {MAX_ZIP_BYTES // 1024 // 1024}MB）'}), 400

        os.makedirs(extract_dir, exist_ok=True)
        total: int = 0
        count: int = 0
        with _open_zip(zip_path) as zf:
            for info in zf.infolist():
                name: str = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    return jsonify({'error': f'技能包内含非法路径: {name}'}), 400
                if info.is_dir():
                    continue
                ext: str = os.path.splitext(name)[1].lower()
                if ext not in ALLOWED_EXT:
                    return jsonify({'error': f'不允许的文件类型: {name}（只允许 {sorted(ALLOWED_EXT)}）'}), 400
                count += 1
                total += info.file_size
                if count > MAX_FILES or total > MAX_ZIP_BYTES:
                    return jsonify({'error': f'技能包文件过多或解压后过大（>{MAX_FILES} 个）'}), 400
            zf.extractall(extract_dir)

        # 定位技能根目录：取含 SKILL.md 的那一层（允许 zip 里多包一层目录）
        skill_root: str | None = None
        for dp, dn, fn in os.walk(extract_dir):
            if "SKILL.md" in fn:
                skill_root = dp
                break
        if not skill_root:
            return jsonify({'error': '技能包里没有 SKILL.md'}), 400

        # 目录规范检查（只警告不拦截）：脚本里写死 /sandbox 根路径时提示一声
        path_warnings: list[str] = scan_output_paths(skill_root)

        with open(os.path.join(skill_root, "SKILL.md"), encoding="utf-8", errors="replace") as fh:
            content: str = fh.read()
        passed, blocked, warned = audit_skill_md(content)
        if not passed:
            log.warning(f"技能包审计未通过: user={username}, 命中={blocked}")
            return jsonify({'error': '技能审计未通过: ' + '；'.join(blocked)}), 400

        meta: dict = parse_skill_meta(content, fallback_name=os.path.basename(skill_root) or "user-skill")
        raw_name: str = str(meta.get("name") or "").strip() or os.path.basename(skill_root)
        try:
            skill_name = _safe_name(raw_name)
        except ValueError:
            return jsonify({'error': f'技能名不合法（frontmatter 的 name）: {raw_name!r}'}), 400

        # 打包成 tar.gz 推进沙箱，再在沙箱内解压到目标目录（一次上传，避免逐文件 CLI）
        tgz: str = os.path.join(tmp_root, "pkg.tar.gz")
        with tarfile.open(tgz, "w:gz") as tf:
            for fname in os.listdir(skill_root):
                tf.add(os.path.join(skill_root, fname), arcname=fname)

        remote_tgz: str = f"{UPLOAD_DIR}/{uuid.uuid4().hex[:12]}.tar.gz"
        if not sandbox_manager.upload(username, tgz, remote_tgz):
            return jsonify({'error': '上传到沙箱失败'}), 500
        import shlex

        script = (
            "import os, tarfile, shutil\n"
            f"dst = {skill_name!r}\n"
            "root = '/sandbox/skills'\n"
            "os.makedirs(root, exist_ok=True)\n"
            "target = os.path.join(root, dst)\n"
            "if os.path.exists(target):\n"
            "    shutil.rmtree(target)\n"
            "os.makedirs(target)\n"
            f"with tarfile.open({remote_tgz!r}, 'r:gz') as tf:\n"
            "    tf.extractall(target, filter='data')\n"
            f"os.remove({remote_tgz!r})\n"
            "print('ok')\n"
        )
        res = sandbox_manager.exec(username, f"python3 -c {shlex.quote(script)}", timeout=120)
        if not res.ok or 'ok' not in (res.stdout or ''):
            log.warning(f"技能包解压失败: user={username}, err={(res.stderr or '')[:300]}")
            return jsonify({'error': '技能包落盘失败: ' + (res.stderr or res.error or '')[:200]}), 500

        _evict(username, f"upload {skill_name}")
        log.info(f"技能包安装成功(zip): user={username}, skill={skill_name}, files={count}, bytes={total}")
        return jsonify({
            'ok': True, 'name': skill_name, 'files': count, 'bytes': total,
            'description': str(meta.get("description") or ""),
            'warnings': list(warned or []) + path_warnings,
            'path': f"{SKILLS_DIR}/{skill_name}",
        }), 200
    except Exception as e:
        # 不是 zip / 压缩包损坏 → 用户侧错误（400），不要把内部异常文案当 500 抛出去
        import zipfile as _zipfile

        if isinstance(e, _zipfile.BadZipFile):
            log.warning(f"技能包不是有效 zip: user={username}, err={e}")
            return jsonify({'error': '不是有效的 .zip 技能包（文件损坏或不是压缩包）'}), 400
        log.warning(f"技能包上传异常: user={username}, err={e}")
        return jsonify({'error': '上传失败（详见服务端日志）'}), 500
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _open_zip(path: str):
    import zipfile

    return zipfile.ZipFile(path)
