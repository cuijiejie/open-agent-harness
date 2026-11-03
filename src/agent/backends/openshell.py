"""OpenShell 沙箱后端：让 deepagents 内置文件工具真正操作用户自己的沙箱

## 为什么需要这个模块

deepagents 的 `create_deep_agent` **内置**了一组文件工具（`ls` / `read_file` /
`write_file` / `edit_file` / `glob` / `grep`），这些工具的行为由 `backend` 参数驱动。

之前的实现**没有传 backend**，导致：
- 模型看到工具列表里有 `ls`/`glob`，处理文件任务时优先调用它们（这是模型的直觉）
- 但后端缺失 → 它们**静默返回空结果**（`content: []`）
- 模型据此判定「文件不存在 / 沙箱是空的」，**永远不会去用 `sandbox_shell`**

实测证据（agent 消息流）：
    [1] AIMessage  tool_calls: [{"name":"glob","args":{"pattern":"**/a.py"}}]
    [2] ToolMessage content: []                      ← 静默空返回
    [3] AIMessage  tool_calls: [{"name":"ls","args":{"path":"/sandbox"}}]
    [4] ToolMessage content: []                      ← 又是空
    [6] AI: "没有找到 a.py 文件。当前文件系统和沙箱都是空的。"   ← 错误结论

## 解决方式

`BaseSandbox` 已经把 ls/grep/glob/read/write/edit 全部实现好了 ——
它们最终都走 `execute()`（跑 shell 命令）和 `upload_files()`（传内容）。
子类只需实现这两个方法（外加 `download_files()` 和 `id`）。

本模块把它们接到 `SandboxManager`（→ openshell CLI → 该用户自己的沙箱），于是：
- agent 用 `ls` / `read_file` / `write_file` 等内置工具 = 直接操作用户沙箱的文件 ✅
- 用户之间依然内核级隔离（每个用户一个沙箱）✅
- `sandbox_shell` 工具保留，用于执行任意命令（补足内置工具覆盖不到的场景）

## 依赖

BaseSandbox 的 glob/grep/read 实现会执行 `python3 -c "..."`，
因此沙箱镜像**必须带 python3**（见 `docker/pa-sandbox.Dockerfile`）。
"""
from __future__ import annotations

import base64
import os
import shlex
import tempfile
import threading
import time

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from src.agent.backends.sandbox import sandbox_manager
from src.infra.utils.LogUtil import log

# read_file 走 exec 的 base64 通道：命令输出默认只保留 8000 字符（≈5.9KB 文件），
# 截断后的 base64 仍然能解码 → 模型会拿到"看似正常、其实被砍掉"的内容。
# 这里放大到 3MB，并且一旦真的截断就明确报 file_too_large，绝不静默给半份文件。
_READ_MAX_CHARS: int = 3_000_000
# 单条命令行参数有上限（Linux MAX_ARG_STRLEN 128KB），超过就改用上传通道
_WRITE_INLINE_LIMIT: int = 60_000

# ---- 「静态路径」读缓存（见 OpenShellSandboxBackend.read 的注释）----
# 只缓存这两类：AGENT.md（部署期固定）、技能目录列表（技能增删时主动失效）
_STATIC_READ_PATHS: tuple[str, ...] = ("/AGENT.md",)
_STATIC_LIST_PREFIXES: tuple[str, ...] = ("/sandbox/skills",)
_BACKEND_CACHE_TTL: float = 300.0
_BACKEND_CACHE: dict[tuple[str, str, str], tuple[float, object]] = {}
_BACKEND_CACHE_LOCK = threading.Lock()


def _cache_get(username: str, kind: str, key: str):
    with _BACKEND_CACHE_LOCK:
        hit = _BACKEND_CACHE.get((username, kind, key))
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > _BACKEND_CACHE_TTL:
            _BACKEND_CACHE.pop((username, kind, key), None)
            return None
        return val


def _cache_put(username: str, kind: str, key: str, val) -> None:
    with _BACKEND_CACHE_LOCK:
        _BACKEND_CACHE[(username, kind, key)] = (time.time(), val)


def invalidate_backend_cache(username: str = "") -> int:
    """清掉静态路径缓存（技能增删/改名后必须调，否则模型看不到新技能）

    username 为空 = 清全部用户的（技能变更影响面小、频率低，直接清全量更省心）
    """
    with _BACKEND_CACHE_LOCK:
        if not username:
            n = len(_BACKEND_CACHE)
            _BACKEND_CACHE.clear()
            return n
        keys = [k for k in _BACKEND_CACHE if k[0] == username]
        for k in keys:
            _BACKEND_CACHE.pop(k, None)
        return len(keys)


class OpenShellSandboxBackend(BaseSandbox):
    """把 deepagents 的文件工具路由到「指定用户自己的 OpenShell 沙箱」"""

    def __init__(self, username: str) -> None:
        self._username: str = username

    # ------------------------------------------------------------------ #
    # 少量「部署期静态、但每次新会话都会被读」的路径做短缓存
    #
    # 为什么需要：deepagents 的 memory 中间件每次新会话都要 read("/AGENT.md")、
    # skills 中间件要 ls("/sandbox/skills/")。每读一次就是一次沙箱 exec
    # （实测 1.1~3.7 秒），于是"新会话第一句"白白多等 2~6 秒。
    # 这两类路径内容几乎不变，短缓存 300 秒、并在技能增删时主动失效。
    #
    # **只缓存白名单路径**：模型自己写出来的文件（如 /sandbox/output/x.xlsx）
    # 绝不能走缓存，否则写完再读会读到旧内容。
    # ------------------------------------------------------------------ #
    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ):
        if offset == 0 and file_path in _STATIC_READ_PATHS:
            hit = _cache_get(self._username, "read", f"{file_path}:{limit}")
            if hit is not None:
                return hit
            res = super().read(file_path, offset=offset, limit=limit)
            _cache_put(self._username, "read", f"{file_path}:{limit}", res)
            return res
        return super().read(file_path, offset=offset, limit=limit)

    def ls(self, path: str):
        if path.startswith(_STATIC_LIST_PREFIXES):
            hit = _cache_get(self._username, "ls", path)
            if hit is not None:
                return hit
            res = super().ls(path)
            _cache_put(self._username, "ls", path, res)
            return res
        return super().ls(path)

    # ------------------------------------------------------------------ #
    # 必需实现：唯一标识
    # ------------------------------------------------------------------ #
    @property
    def id(self) -> str:
        """沙箱后端唯一标识（deepagents 用于日志/调试）"""
        return f"openshell-{sandbox_manager.sandbox_name(self._username)}"

    # ------------------------------------------------------------------ #
    # 必需实现：执行命令（ls / grep / glob / read / edit 都建立在此之上）
    # ------------------------------------------------------------------ #
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """在用户自己的沙箱内执行 shell 命令"""
        result = sandbox_manager.exec(self._username, command, timeout=timeout)

        # 合并 stdout / stderr / 错误信息（ExecuteResponse.output 为单一输出流）
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr] {result.stderr}")
        if result.error:
            parts.append(f"[error] {result.error}")
        output = "\n".join(parts)

        return ExecuteResponse(
            output=output,
            exit_code=result.returncode,
            truncated=result.truncated,
        )

    # ------------------------------------------------------------------ #
    # 必需实现：写入文件（BaseSandbox 的 write_file / edit_file 走这里）
    # ------------------------------------------------------------------ #
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """把内容写入沙箱内路径

        实现方式：内容 base64 编码后通过 execute() 在沙箱内解码落盘，
        免去宿主机与沙箱之间的文件流转（OpenShell 的 CLI upload 受 snap 限制）。
        按协议要求支持**部分成功**（逐个文件返回结果，不抛异常）。
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                b64 = base64.b64encode(content).decode("ascii")
                quoted_path = shlex.quote(path)
                if len(b64) > _WRITE_INLINE_LIMIT:
                    # 大文件不能塞进命令行（会撞 execve 单参数上限），改走 CLI 上传
                    if not self._upload_via_cli(path, content):
                        responses.append(FileUploadResponse(path=path, error="write_failed"))
                        continue
                    responses.append(FileUploadResponse(path=path, error=None))
                    continue
                # 先确保父目录存在，再解码写入
                cmd = (
                    f'mkdir -p "$(dirname {quoted_path})" && '
                    f"printf %s {shlex.quote(b64)} | base64 -d > {quoted_path}"
                )
                r = sandbox_manager.exec(self._username, cmd)
                if r.ok:
                    responses.append(FileUploadResponse(path=path, error=None))
                else:
                    log.warning(f"沙箱写文件失败: path={path}, err={r.error or r.stderr[:120]}")
                    responses.append(FileUploadResponse(path=path, error="write_failed"))
            except Exception as e:
                log.warning(f"沙箱写文件异常: path={path}, err={e}")
                responses.append(FileUploadResponse(path=path, error="write_failed"))
        return responses

    def _upload_via_cli(self, path: str, content: bytes) -> bool:
        """大文件：先落到宿主临时文件，再用 CLI 上传（不经过命令行参数）"""
        tmp_dir: str = tempfile.mkdtemp(prefix="pa_upload_")
        tmp_path: str = os.path.join(tmp_dir, os.path.basename(path) or "upload.bin")
        try:
            with open(tmp_path, "wb") as f:
                f.write(content)
            quoted_path = shlex.quote(path)
            sandbox_manager.exec(self._username, f'mkdir -p "$(dirname {quoted_path})"')
            return sandbox_manager.upload(self._username, tmp_path, path)
        except Exception as e:
            log.warning(f"沙箱大文件写入失败: path={path}, err={e}")
            return False
        finally:
            try:
                os.remove(tmp_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # 必需实现：读取文件内容（read_file / 下载走这里）
    # ------------------------------------------------------------------ #
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙箱内读取文件内容（base64 编码后取回，避免二进制损坏）

        按协议要求支持部分成功；不存在的文件返回 error="file_not_found"。
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                r = sandbox_manager.exec(
                    self._username,
                    f"base64 -w0 {shlex.quote(path)} 2>/dev/null",
                    max_output=_READ_MAX_CHARS,
                )
                payload = (r.stdout or "").strip()
                if r.truncated:
                    # 宁可不给，也不给半份：截断的 base64 仍能解码，静默截断最危险
                    log.warning(f"沙箱读文件被截断（文件过大）: path={path}")
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_too_large")
                    )
                    continue
                if r.ok and payload:
                    try:
                        content = base64.b64decode(payload)
                        responses.append(
                            FileDownloadResponse(path=path, content=content, error=None)
                        )
                    except Exception:
                        responses.append(
                            FileDownloadResponse(path=path, content=None, error="read_failed")
                        )
                else:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
            except Exception as e:
                log.warning(f"沙箱读文件异常: path={path}, err={e}")
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="read_failed")
                )
        return responses


def build_backend(username: str):
    """按用户构建沙箱后端；沙箱未启用或不可用时返回 None（回退默认行为）"""
    try:
        if not sandbox_manager.enabled:
            return None
        return OpenShellSandboxBackend(username)
    except Exception as e:  # 后端构建失败绝不能让 agent 创建失败
        log.warning(f"构建沙箱后端失败，回退默认: user={username}, err={e}")
        return None
