"""沙箱文件管理 —— 网盘式浏览：目录导航 / 新建文件夹 / 下载 / 删除

安全边界（四条）：
  1. 用户名**只从登录会话取**，不接受参数
  2. 路径必须落在 /sandbox 下，`..` 一律拒绝
  3. 系统目录 in/ out/ 不可删除（删了归拢规则会乱）
  4. 隐藏目录 .tmp/ skills* 不展示、不可操作

目录约定（AGENT.md 7.12）：
  /sandbox/upload/  用户上传（系统固定落点）
  /sandbox/output/     生成产物（根目录交付物每轮自动归拢到这里）
  /sandbox/.tmp/    中间产物（隐藏）
  /sandbox/skills/  技能包（隐藏）
  其余目录        用户/模型自建，可随意建删
"""
import posixpath

from flask import Blueprint, Response, jsonify, request

from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

SandboxController = Blueprint("sandbox", __name__)

_ROOT: str = "/sandbox"
_SYSTEM_DIRS: tuple[str, ...] = ("upload", "output")                      # 不可删
_HIDDEN_TOP: tuple[str, ...] = (".tmp", "skills", "skills-disabled", "skills-trash")
_SKIP_EXTS: set = {
    ".json", ".jsonl", ".csv", ".tsv", ".txt",
    ".py", ".sh", ".ipynb", ".log", ".tmp", ".bak", ".pyc",
    ".sql", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
}
_INLINE: set = {".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".svg", ".txt", ".md"}
_MAX_ENTRIES: int = 500


def _username() -> str:
    from flask import session

    return str(session.get("USERNAME") or "").strip()


def _safe_dir(raw: str) -> str:
    """归一化目录路径；非法抛 ValueError"""
    p: str = posixpath.normpath(str(raw or _ROOT))
    if ".." in p.split("/"):
        raise ValueError("路径非法")
    if not (p == _ROOT or p.startswith(_ROOT + "/")):
        raise ValueError("路径非法")
    rel = p[len(_ROOT):].strip("/")
    first = rel.split("/")[0] if rel else ""
    if first in _HIDDEN_TOP or first.startswith("skills"):
        raise ValueError("该目录不允许访问")
    return p


def _q(path: str) -> str:
    """shell 安全引用（单引号包裹 + 转义内部单引号）"""
    return "'" + str(path).replace("'", "'\\''") + "'"


def _invalidate_files_hint(username: str) -> None:
    """沙箱文件变了 → 清「沙箱文件清单」缓存（否则模型还会看到旧清单）"""
    try:
        from src.chat.stream import invalidate_sandbox_files_hint

        invalidate_sandbox_files_hint(username)
    except Exception:
        pass


def _entries_cmd(cur_dir: str, max_entries: int = _MAX_ENTRIES) -> str:
    """一条命令同时产出「子目录」和「当前目录文件」

    为什么要合并：每次 `openshell sandbox exec` 是一次跨进程 + 跨容器 + 网关往返，
    实测 1.3~3.5 秒（同一个 echo 连跑两次都能差 2 秒）。原来列一个目录 exec 两次
    （一次算子目录、一次列文件），光往返就 4 秒多；合并成一次后实测 4.2s → 1.3s。

    输出协议（TAB 分隔，Python 端按首列分流）：
        D\\t<目录名>\\t<文件数>\\t<最新修改时间>
        F\\t<字节数>\\t<修改时间>\\t<文件名>
    """
    return (
        f"cd {_q(cur_dir)} 2>/dev/null || exit 0; "
        # --- 子目录：文件数 + 最新修改时间（深度 3）---
        f"for d in */; do [ -d \"$d\" ] || continue; "
        f"n=$(find \"$d\" -maxdepth 1 -type f ! -name '.*' 2>/dev/null | wc -l); "
        f"t=$(find \"$d\" -maxdepth 3 -type f -printf '%TY-%Tm-%Td %TH:%TM\\n' 2>/dev/null | sort -r | head -1); "
        f"printf 'D\\t%s\\t%s\\t%s\\n' \"${{d%/}}\" \"$n\" \"$t\"; "
        f"done 2>/dev/null | head -{max_entries}; "
        # --- 当前目录文件 ---
        f"find . -maxdepth 1 -type f ! -name '.*' "
        f"-printf 'F\\t%s\\t%TY-%Tm-%Td %TH:%TM\\t%f\\n' 2>/dev/null | head -{max_entries}"
    )


def _parse_entries(stdout: str, cur_dir: str) -> tuple[list[dict], list[dict], int]:
    """把 `_entries_cmd` 的输出解析成 (dirs, files, total_bytes)（纯函数，便于单测）"""
    dirs: list[dict] = []
    files: list[dict] = []
    total: int = 0

    for line in (stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        kind = parts[0]
        if kind == "D" and len(parts) >= 3:
            name = parts[1].strip()
            if not name or name.startswith(".") or name.startswith("skills"):
                continue
            try:
                cnt = int(parts[2] or 0)
            except ValueError:
                cnt = 0
            dirs.append({"name": name, "count": cnt, "mtime": (parts[3].strip() if len(parts) > 3 else "")})
        elif kind == "F" and len(parts) >= 4:
            name = parts[3].strip()
            if not name:
                continue
            try:
                size = int(parts[1])
            except ValueError:
                size = 0
            total += size
            ext = posixpath.splitext(name)[1].lower()
            files.append({
                "name": name,
                "path": f"{cur_dir}/{name}",
                "size": size,
                "mtime": parts[2],
                "ext": ext,
                "inline": ext in _INLINE,
            })

    files.sort(key=lambda x: x["mtime"], reverse=True)
    dirs.sort(key=lambda x: (x["name"] not in _SYSTEM_DIRS, x["name"]))
    return dirs, files, total


@SandboxController.route("/api/warm", methods=["GET", "POST"])
@login_required
def warm() -> tuple:
    """预热沙箱（前端打开页面时调一次，立即返回）

    为什么需要：登录成功时已经预热过（AuthUtil._init_sandbox），但那只覆盖
    「今天第一次登录」。登录会话能维持很久，用户第二天直接打开页面**不会重走登录**，
    沙箱可能已被维护任务 stop → 第一句话又要等 start + 技能读取。

    这里只做一件事：后台触发 ensure（幂等，已在跑就复用），接口立刻返回，
    用户还在看界面时容器就起来了，第一句话不用等。
    """
    user = _username()
    if user:
        try:
            from src.agent.backends.sandbox import init_user_sandbox_async

            init_user_sandbox_async(user)
        except Exception as e:
            log.warning(f"预热沙箱失败（忽略）: user={user}, err={e}")
    return jsonify({"ok": True}), 200


@SandboxController.route("/api/sandbox/files", methods=["GET"])
@login_required
def list_dir() -> tuple[Response, int]:
    """列出一个目录：子目录 + 文件"""
    username: str = _username()
    if not username:
        return jsonify({"error": "未登录"}), 401
    try:
        cur_dir: str = _safe_dir(request.args.get("path") or _ROOT)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        from src.agent.backends.sandbox import SandboxManager

        mgr = SandboxManager()
        if not mgr.enabled:
            return jsonify({"ok": True, "path": cur_dir, "dirs": [], "files": [], "total": 0}), 200

        # 一次 exec 同时拿子目录和文件（原来是两次，光进程往返就 4 秒多）
        res = mgr.exec(username, _entries_cmd(cur_dir), timeout=60)
        dirs, files, total = _parse_entries(res.stdout or "", cur_dir)

        parent: str = ""
        if cur_dir != _ROOT:
            up = posixpath.dirname(cur_dir)
            parent = up if up.startswith(_ROOT) else _ROOT

        return jsonify({
            "ok": True, "path": cur_dir, "parent": parent,
            "dirs": dirs, "files": files, "total": total,
            "count": len(files) + len(dirs),
        }), 200
    except Exception as e:
        log.error(f"列目录失败: {e}", exc_info=True)
        return jsonify({"error": "列目录失败"}), 500


@SandboxController.route("/api/sandbox/mkdir", methods=["POST"])
@login_required
def mkdir() -> tuple[Response, int]:
    """在当前目录下新建文件夹"""
    username: str = _username()
    if not username:
        return jsonify({"error": "未登录"}), 401

    body = request.get_json(silent=True) or {}
    try:
        cur_dir: str = _safe_dir(body.get("path") or _ROOT)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    name: str = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "文件夹名不能为空"}), 400
    if "/" in name or name in (".", "..") or name.startswith("."):
        return jsonify({"error": "文件夹名不能含 / 或以 . 开头"}), 400
    if len(name) > 60:
        return jsonify({"error": "文件夹名太长"}), 400

    target: str = f"{cur_dir}/{name}"
    try:
        from src.agent.backends.sandbox import SandboxManager

        mgr = SandboxManager()
        r = mgr.exec(username, f"mkdir -p {_q(target)} && echo ok", timeout=20)
        if "ok" not in (r.stdout or ""):
            return jsonify({"error": "创建失败: " + (r.stderr or "")[:100]}), 500
        _invalidate_files_hint(username)
        log.info(f"新建沙箱目录: user={username}, path={target}")
        return jsonify({"ok": True, "path": target}), 200
    except Exception as e:
        log.error(f"新建目录失败: {e}", exc_info=True)
        return jsonify({"error": "创建失败"}), 500


def _op_cmd(dest: str, srcs: list[str], copy: bool) -> str:
    """一条命令完成「目标目录校验 + 批量移动/复制 + 逐个报告结果」

    原来每个条目 2~3 次 exec（判存在 → mv/cp → 复查），每次往返 1~3.5 秒：
    移动 10 个 = 30 次往返。现在 1 次。

    输出：
        NODEST                      目标目录不存在（什么都不做）
        OK<tab>src                  成功
        MISS<tab>src                源不存在
        CONFLICT<tab>src            目标位置已有同名条目（移动时）
        FAIL<tab>src                操作失败
    """
    targets = " ".join(_q(p) for p in srcs)
    head = (
        f'd={_q(dest)}; '
        f'if [ ! -d "$d" ]; then echo NODEST; exit 0; fi; '
        f"for s in {targets}; do "
        f'b=$(basename "$s"); t="$d/$b"; '
        f'if [ ! -e "$s" ]; then printf \'MISS\\t%s\\n\' "$s"; continue; fi; '
    )
    if copy:
        # 同名自动加 _1/_2 后缀，不覆盖（GNU cp 对同名目录会嵌套，所以自己选名字）
        # 注意：候选名必须用独立变量（c），不能复用 t —— t 已经是 "$d/$b" 完整路径，
        # 再拼 "$d/$t" 会变成 "$d/$d/$b"，cp 直接失败（这个 bug 实测才暴露）
        body = (
            'case "$b" in *.*) pre="${b%.*}"; suf=".${b##*.}";; *) pre="$b"; suf="";; esac; '
            'n=0; c="$b"; '
            'while [ -e "$d/$c" ]; do n=$((n+1)); c="${pre}_$n${suf}"; done; '
            'if cp -r "$s" "$d/$c" 2>/dev/null; then printf \'OK\\t%s\\n\' "$s"; '
            'else printf \'FAIL\\t%s\\n\' "$s"; fi; '
        )
    else:
        body = (
            'if [ -e "$t" ]; then printf \'CONFLICT\\t%s\\n\' "$s"; continue; fi; '
            'if mv "$s" "$t" 2>/dev/null && [ -e "$t" ]; then printf \'OK\\t%s\\n\' "$s"; '
            'else printf \'FAIL\\t%s\\n\' "$s"; fi; '
        )
    return head + body + "done"


def _parse_op_result(stdout: str) -> tuple[list[str], list[dict], bool]:
    """解析 `_op_cmd` 的输出 → (成功列表, 失败列表, 目标目录是否不存在)"""
    ok: list[str] = []
    failed: list[dict] = []
    nodest: bool = False
    for line in (stdout or "").splitlines():
        parts = line.split("\t")
        tag = parts[0].strip()
        if tag == "NODEST":
            nodest = True
            continue
        if len(parts) < 2:
            continue
        path = parts[1].strip()
        if tag == "OK":
            ok.append(path)
        elif tag == "MISS":
            failed.append({"path": path, "reason": "不存在"})
        elif tag == "CONFLICT":
            failed.append({"path": path, "reason": "目标位置已有同名条目"})
        elif tag == "FAIL":
            failed.append({"path": path, "reason": "操作失败（沙箱内命令返回非 0）"})
    return ok, failed, nodest


def _delete_cmd(path: str, force: bool) -> str:
    """一条命令完成：判类型 → （目录且有文件时按需拒绝）→ 删 → 复查

    原来删一个条目要 exec 3~4 次（test -d / find|wc / rm / test -e），
    每次往返实测 1~3.5 秒。合并后只需要一次往返。

    输出（KEY=VALUE，逐行）：
        KIND=none|file|dir
        COUNT=<n>                      仅目录
        RESULT=need_confirm|deleted|still_there
    """
    p = _q(path)
    check_confirm = "" if force else (
        'if [ "${n:-0}" -gt 0 ]; then echo RESULT=need_confirm; exit 0; fi; '
    )
    return (
        f"p={p}; "
        f'if [ ! -e "$p" ]; then echo KIND=none; exit 0; fi; '
        f'if [ -f "$p" ]; then echo KIND=file; '
        f'else echo KIND=dir; n=$(find "$p" -maxdepth 3 -type f 2>/dev/null | wc -l); echo COUNT=$n; fi; '
        + check_confirm
        + 'if [ -d "$p" ]; then rm -rf "$p"; else rm -f "$p"; fi; '
        f'if [ -e "$p" ]; then echo RESULT=still_there; else echo RESULT=deleted; fi'
    )


def _parse_kv(stdout: str) -> dict:
    """解析 KEY=VALUE 行（纯函数，便于单测）"""
    out: dict = {}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _batch_delete_cmd(paths: list[str]) -> str:
    """一条命令删完所有条目并逐个报告结果（原来每个条目 3 次 exec）

    输出：OK<tab>路径 / MISS<tab>路径（原本就不存在）/ FAIL<tab>路径（删了还在）
    """
    targets = " ".join(_q(p) for p in paths)
    return (
        f"for p in {targets}; do "
        'if [ ! -e "$p" ]; then printf \'MISS\\t%s\\n\' "$p"; continue; fi; '
        'if [ -d "$p" ]; then rm -rf "$p"; else rm -f "$p"; fi; '
        'if [ -e "$p" ]; then printf \'FAIL\\t%s\\n\' "$p"; else printf \'OK\\t%s\\n\' "$p"; fi; '
        "done"
    )


def _parse_batch(stdout: str) -> tuple[list[str], list[dict]]:
    """把批量删除的输出分成 (deleted, failed)（纯函数，便于单测）"""
    deleted: list[str] = []
    failed: list[dict] = []
    for line in (stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        tag, path = parts[0].strip(), parts[1].strip()
        if tag == "OK":
            deleted.append(path)
        elif tag == "MISS":
            failed.append({"path": path, "reason": "不存在"})
        elif tag == "FAIL":
            failed.append({"path": path, "reason": "删除未生效（目标仍然存在）"})
    return deleted, failed


@SandboxController.route("/api/sandbox/files", methods=["DELETE"])
@login_required
def delete_entry() -> tuple[Response, int]:
    """删除文件或空目录。系统目录 in/out 拒绝删除。"""
    username: str = _username()
    if not username:
        return jsonify({"error": "未登录"}), 401

    raw: str = str(request.args.get("path") or "").strip()
    try:
        path: str = _safe_dir(raw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if path == _ROOT:
        return jsonify({"error": "不能删除根目录"}), 400
    # 只禁止删 in/out 目录本身；里面的文件/子目录可以删
    if path in (f"{_ROOT}/upload", f"{_ROOT}/output"):
        return jsonify({"error": f"{path[len(_ROOT):].strip('/')}/ 是系统目录，不能删除"}), 400

    try:
        from src.agent.backends.sandbox import SandboxManager

        mgr = SandboxManager()
        force: bool = bool(request.args.get("force"))
        res = mgr.exec(username, _delete_cmd(path, force), timeout=60)
        if not res.ok and not (res.stdout or "").strip():
            # 沙箱不可用/命令超时：不能落到下面去假报"删除成功"
            return jsonify({"error": f"沙箱不可用，删除未执行: {(res.error or res.stderr or '')[:120]}"}), 500

        kv = _parse_kv(res.stdout or "")
        kind: str = kv.get("KIND", "")
        result: str = kv.get("RESULT", "")
        if kind == "none":
            return jsonify({"error": "不存在"}), 404
        if result == "need_confirm":
            try:
                n = int(kv.get("COUNT") or 0)
            except ValueError:
                n = 0
            return jsonify({"error": f"目录里还有 {n} 个文件，确认后再删", "need_confirm": True, "count": n}), 409
        if result == "still_there":
            return jsonify({"error": "删除未生效（目标仍然存在）"}), 500
        if result != "deleted":
            return jsonify({"error": f"删除失败: {(res.stderr or res.error or '')[:120]}"}), 500

        _invalidate_files_hint(username)
        log.info(f"删除沙箱条目: user={username}, path={path}, kind={kind}")
        return jsonify({"ok": True, "path": path, "kind": kind}), 200
    except Exception as e:
        log.error(f"删除失败: {e}", exc_info=True)
        return jsonify({"error": "删除失败"}), 500


@SandboxController.route("/api/sandbox/files/batch-delete", methods=["POST"])
@login_required
def batch_delete() -> tuple[Response, int]:
    """批量删除文件（多选后一次提交）——**一次 exec 删完**，不再逐个往返"""
    username: str = _username()
    if not username:
        return jsonify({"error": "未登录"}), 401
    body = request.get_json(silent=True) or {}
    raw_paths = body.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        return jsonify({"error": "没有要删除的文件"}), 400
    if len(raw_paths) > 200:
        return jsonify({"error": "一次最多删 200 个"}), 400

    from src.agent.backends.sandbox import SandboxManager

    mgr = SandboxManager()
    failed: list[dict] = []
    valid: list[str] = []
    for rp in raw_paths:
        try:
            path = _safe_dir(str(rp))
            if path == _ROOT or path in (f"{_ROOT}/upload", f"{_ROOT}/output"):
                raise ValueError("系统目录不可删")
            valid.append(path)
        except Exception as e:
            failed.append({"path": str(rp), "reason": str(e)})

    deleted: list[str] = []
    if valid:
        res = mgr.exec(username, _batch_delete_cmd(valid), timeout=min(200, 60 + 2 * len(valid)))
        if not res.ok and not (res.stdout or "").strip():
            failed.extend({"path": p, "reason": "沙箱不可用，删除未执行"} for p in valid)
        else:
            deleted, batch_failed = _parse_batch(res.stdout or "")
            failed.extend(batch_failed)
            # 命令没回结果的那些，不能当成功
            reported = set(deleted) | {f["path"] for f in batch_failed}
            for p in valid:
                if p not in reported:
                    failed.append({"path": p, "reason": "未执行（沙箱无返回）"})

    if deleted:
        _invalidate_files_hint(username)
    log.info(f"批量删除: user={username}, 成功={len(deleted)}, 失败={len(failed)}")
    return jsonify({"ok": True, "deleted": deleted, "failed": failed}), 200


@SandboxController.route("/api/sandbox/move", methods=["POST"])
@login_required
def move_entries() -> tuple[Response, int]:
    """移动文件/目录到目标目录。支持批量（多选后一次提交）。

    目标必须是已存在的目录；in/out 可以作为目标（往里放），但不能被移动走。
    """
    username: str = _username()
    if not username:
        return jsonify({"error": "未登录"}), 401

    body = request.get_json(silent=True) or {}
    paths = body.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return jsonify({"error": "没有要移动的条目"}), 400
    if len(paths) > 200:
        return jsonify({"error": "一次最多移动 200 个"}), 400
    try:
        dest: str = _safe_dir(body.get("dest") or "")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if dest == _ROOT:
        return jsonify({"error": "目标目录非法"}), 400

    from src.agent.backends.sandbox import SandboxManager

    mgr = SandboxManager()
    moved: list[str] = []
    failed: list[dict] = []
    valid: list[str] = []
    for rp in paths:
        try:
            src = _safe_dir(str(rp))
            if src == _ROOT:
                raise ValueError("不能移动根目录")
            if src == dest or dest.startswith(src + "/"):
                raise ValueError("不能移动到自身或子目录")
            # 只禁止移动 in/out 目录本身；它们里面的文件可以随意移出
            if src in (f"{_ROOT}/upload", f"{_ROOT}/output"):
                raise ValueError("系统目录不可移动")
            valid.append(src)
        except Exception as e:
            failed.append({"path": str(rp), "reason": str(e)})

    if valid:
        # 一次 exec 完成「目标校验 + 批量 mv + 逐个回报」（原来每个条目 3 次往返）
        res = mgr.exec(username, _op_cmd(dest, valid, copy=False),
                       timeout=min(200, 60 + 2 * len(valid)))
        if not res.ok and not (res.stdout or "").strip():
            failed.extend({"path": p, "reason": "沙箱不可用，移动未执行"} for p in valid)
        else:
            moved, op_failed, nodest = _parse_op_result(res.stdout or "")
            if nodest:
                return jsonify({"error": "目标目录不存在"}), 400
            failed.extend(op_failed)
            reported = set(moved) | {f["path"] for f in op_failed}
            for p in valid:
                if p not in reported:
                    failed.append({"path": p, "reason": "未执行（沙箱无返回）"})

    if moved:
        _invalidate_files_hint(username)
    log.info(f"移动沙箱条目: user={username}, dest={dest}, 成功={len(moved)}, 失败={len(failed)}")
    return jsonify({"ok": True, "moved": moved, "failed": failed, "dest": dest}), 200


@SandboxController.route("/api/sandbox/copy", methods=["POST"])
@login_required
def copy_entries() -> tuple[Response, int]:
    """复制文件/目录到目标目录（支持批量）。同名时自动加 _1/_2 后缀，不覆盖。"""
    username: str = _username()
    if not username:
        return jsonify({"error": "未登录"}), 401

    body = request.get_json(silent=True) or {}
    paths = body.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return jsonify({"error": "没有要复制的条目"}), 400
    if len(paths) > 200:
        return jsonify({"error": "一次最多复制 200 个"}), 400
    try:
        dest: str = _safe_dir(body.get("dest") or "")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if dest == _ROOT:
        return jsonify({"error": "目标目录非法"}), 400

    from src.agent.backends.sandbox import SandboxManager

    mgr = SandboxManager()
    copied: list[str] = []
    failed: list[dict] = []
    valid: list[str] = []
    for rp in paths:
        try:
            src = _safe_dir(str(rp))
            if src == _ROOT:
                raise ValueError("不能复制根目录")
            if src == dest or dest.startswith(src + "/"):
                raise ValueError("不能复制到自身或子目录")
            valid.append(src)
        except Exception as e:
            failed.append({"path": str(rp), "reason": str(e)})

    if valid:
        # 一次 exec 完成「目标校验 + 批量 cp（同名自动加序号）+ 逐个回报」
        res = mgr.exec(username, _op_cmd(dest, valid, copy=True),
                       timeout=min(300, 90 + 3 * len(valid)))
        if not res.ok and not (res.stdout or "").strip():
            failed.extend({"path": p, "reason": "沙箱不可用，复制未执行"} for p in valid)
        else:
            copied, op_failed, nodest = _parse_op_result(res.stdout or "")
            if nodest:
                return jsonify({"error": "目标目录不存在"}), 400
            failed.extend(op_failed)
            reported = set(copied) | {f["path"] for f in op_failed}
            for p in valid:
                if p not in reported:
                    failed.append({"path": p, "reason": "未执行（沙箱无返回）"})

    if copied:
        _invalidate_files_hint(username)
    log.info(f"复制沙箱条目: user={username}, dest={dest}, 成功={len(copied)}, 失败={len(failed)}")
    return jsonify({"ok": True, "copied": copied, "failed": failed, "dest": dest}), 200
