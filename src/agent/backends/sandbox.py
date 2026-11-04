"""OpenShell 沙箱管理：为每个登录用户提供独立的隔离执行环境

设计要点：
- **一个用户一个沙箱**：沙箱名 `agent-{username}`，用户之间内核级隔离
- **懒创建**：首次使用时创建，之后复用（进程内缓存 + CLI 查询兜底）
- **同机部署**：通过 snap 版 openshell CLI 操作（subprocess）
- **策略约束**：文件系统 / 网络由 OpenShell 策略 YAML 限制
  （见 cfg.openshellPolicy，沙箱创建时应用，文件系统策略不可热改）
- **并发安全**：创建加锁，避免 gunicorn 多 worker 重复创建同一沙箱
- **优雅降级**：未启用或 CLI 不可用时返回明确错误，不影响其他功能

沙箱内隔离能力（由 OpenShell 内核级保证）：
- 文件系统：Landlock 限制只能访问策略允许的路径，触碰不到宿主
- 网络：默认拒绝所有出站，仅放行策略列出的端点（精确到调用二进制）
- 进程：以非 root 身份运行，seccomp 限制危险系统调用
- 凭证：真实密钥不进入沙箱，由 supervisor 在出站请求时注入
"""
import json
import os
from pathlib import Path
import hashlib
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from config.Configs import cfg
from src.infra.utils.LogUtil import log


# ========== 产出文件（沙箱 → 宿主机 → 前端可打开/下载） ==========
# 智能体把交付物写在沙箱里（约定目录 /sandbox/output/），沙箱是容器文件系统，
# 用户在聊天界面点不到；这里在会话收尾时把文件捞到宿主机，再由
# /open-agent-harness/api/artifacts/... 路由下发给本人。
# 产出文件放**项目根**的 artifacts/，不放在 src/ 里——
# 放源码树有被 git clean / 部署脚本误删的风险（已在 .gitignore 里）。
_ARTIFACT_DIR: str = str(Path(__file__).resolve().parents[3] / "artifacts")
# 交付物识别规则：
#   · /sandbox/output/  —— 约定的交付目录，**里面的文件全部回收（不限类型）**
#   · /sandbox 根目录 —— 兼容历史行为：除下面这些内部/中间产物外的文件也回收
# 用「黑名单」而不是「白名单」，这样 zip / tar.gz / ipynb / docx / parquet 等
# 任何智能体产出、需要用户下载的文件都不会漏。
_ARTIFACT_SKIP_NAMES: set = {"agent_memory.md", "AGENT.md", ".index.json"}
# 约定交付目录（含一层子目录）。用户写 output/ 也算交付物 —— 之前只扫 out/ 会漏
_ARTIFACT_DIRS: list[str] = [
    "/sandbox/output", "/sandbox/outputs", "/sandbox/deliverables",
]
_ARTIFACT_MAX_FILES: int = 5      # 产品口径：**每轮回传最多 5 个**（超出不下载、也不删除）
_ARTIFACT_MAX_BYTES: int = 100 * 1024 * 1024     # 单文件上限 100MB
# 还没有指纹索引（首次扫描）时，只把"最近 10 分钟内写过"的文件算本轮新产出。
# 初衷是避免第一次部署上来就把一周前的旧报告全贴一遍；但如果一律当"历史"，
# 新用户第一次生成的报告就一个链接都拿不到（实测踩到）。
_ARTIFACT_NEW_WINDOW_S: int = 600

# 跨进程创建锁的目录/超时（同一台宿主机上的多个 gunicorn worker）
_SANDBOX_LOCK_DIR: str = os.path.join(tempfile.gettempdir(), "pa-sandbox-locks")
_SANDBOX_LOCK_TIMEOUT: int = 300

# 中间产物：不进交付清单（模型生成过程中的草稿，用户看不懂也不需要）
# 2026-10-26：之前只挡了 .py/.log 之类，导致一次分析把 9 个 .json/.csv 草稿全列给了用户。
# 交付请用 .xlsx / .docx / .pptx / .pdf / .html / .png / .md / .zip。
_ARTIFACT_SKIP_EXTS: set = {
    ".json", ".jsonl", ".csv", ".tsv", ".txt",
    ".py", ".sh", ".ipynb", ".log", ".tmp", ".bak", ".pyc",
    ".sql", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
}

# 浏览器能直接「打开」的类型；其余只给「下载」（点开也只会触发下载或乱码）
_INLINE_EXTS: set = {
    ".html", ".htm", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".pdf", ".txt", ".md", ".csv", ".json", ".xml",
    ".mp4", ".webm", ".mp3", ".wav", ".m4a",
}


def inline_displayable(name: str) -> bool:
    """这个文件适合在浏览器里直接打开吗（否则只提供下载）"""
    return os.path.splitext((name or "").lower())[1] in _INLINE_EXTS


# ========== 当前登录用户上下文 ==========
# Agent 工具在流式执行时可能不在 Flask request context 内（SSE 长连接），
# 用 ContextVar 传递 username 是最可靠的方式（按线程/task 隔离）。
_current_user: ContextVar[str] = ContextVar("sandbox_current_user", default="")


def set_sandbox_user(username: str):
    """设置当前执行上下文的用户（由 ChatService 在流开始时调用）

    返回 token：调用方必须在 finally 里 reset_sandbox_user(token)。
    只 set 不 reset 的话，gthread 复用线程后这个值会残留，
    任何"没先 set 就跑工具"的入口都会读到上一个用户（写操作会记到别人名下）。
    """
    return _current_user.set(username or "")


def reset_sandbox_user(token) -> None:
    """还原到 set 之前的用户上下文（token 来自 set_sandbox_user）"""
    try:
        _current_user.reset(token)
    except Exception:
        pass


def get_sandbox_user() -> str:
    """获取当前执行上下文的用户"""
    return _current_user.get()


@dataclass
class CliResult:
    """openshell CLI 的调用结果（替代 subprocess.CompletedProcess，带截断标记）"""

    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    elapsed: float = 0.0


@dataclass
class ExecResult:
    """沙箱命令执行结果"""
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    truncated: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.error

    def text(self) -> str:
        """给 Agent 的可读输出（优先 stdout，回退 stderr/error）"""
        body = (self.stdout or "").strip() or (self.stderr or "").strip()
        if self.error:
            return f"[沙箱错误] {self.error}"
        if not body:
            return "[命令执行成功，无输出]"
        if self.truncated:
            body += "\n\n[输出过长已截断]"
        return body


def _strip_frontmatter(text: str) -> str:
    """去掉 SKILL.md 的 YAML frontmatter，返回正文（没有 frontmatter 就原样返回）"""
    t: str = (text or "").lstrip()
    if not t.startswith("---"):
        return t
    parts: list = t.split("---", 2)
    return parts[2] if len(parts) >= 3 else ""


def _stage_policy_under_home(policy: str, home: str | None = None) -> str:
    """把沙箱策略文件落到 $HOME 内，返回 CLI 真正能读到的路径。

    为什么必须这样：OpenShell 的 CLI 是 snap 包（strict confinement），它在自己的
    mount namespace 里**看不到 $HOME 之外的自定义目录**（例如项目部署在 /data1 时，
    /data1/... 一律 No such file or directory），于是 sandbox create 直接失败：
    `failed to inspect sandbox policy /data1/.../sandbox-policy.yaml`。
    项目本来就在 $HOME 下时原样返回；否则在 $HOME/pa-policy/ 下复制一份并使用它。
    注意目录名**不能带前导点**（如 $HOME/.pa-policy）：snap 的 home 规则会拒读
    $HOME 下的隐藏路径，实测报 `failed to read sandbox policy from .../.pa-policy/...`。
    复制是幂等的（内容相同不重写），策略跟随项目更新后重启即生效。
    """
    if not policy or not os.path.isfile(policy):
        return policy
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    try:
        real_home = os.path.realpath(home)
        real_policy = os.path.realpath(policy)
    except OSError:
        return policy
    if real_policy.startswith(real_home.rstrip(os.sep) + os.sep):
        return policy  # 已在 $HOME 内，snap CLI 直接可读
    dest_dir = os.path.join(real_home, "pa-policy")  # 不能带前导点：snap home 规则拒读隐藏路径
    dest = os.path.join(dest_dir, os.path.basename(policy))
    try:
        with open(policy, "rb") as f:
            data = f.read()
        old: bytes | None = None
        if os.path.isfile(dest):
            with open(dest, "rb") as f:
                old = f.read()
        if old != data:
            os.makedirs(dest_dir, exist_ok=True)
            tmp = f"{dest}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            log.info("沙箱策略不在 $HOME 内，已复制到 %s（snap CLI 只能读 $HOME 内的路径）", dest)
    except OSError as e:
        # 复制失败（只读/权限）时退回原路径，让 CLI 报它自己的错，避免静默换路径
        log.warning("沙箱策略复制到 $HOME 失败（%s），仍使用原路径 %s", e, policy)
        return policy
    return dest


@contextmanager
def _sandbox_create_lock(name: str):
    """**跨进程**的「创建/恢复同一个沙箱」互斥锁（fcntl.flock）

    为什么需要：gunicorn 6 个 worker 各有自己的进程锁，登录初始化（后台线程）
    和首轮对话（另一进程）会同时判断 phase=None 并各自 create，后到者直接收到
    gateway 的 `invalid argument`。之前只做了"失败后复查"，那是事后补救。

    为什么用文件锁而不是 Redis：创建动作发生在**运行 openshell CLI 的那台机器**上，
    文件锁天然对应"同一个 gateway"；不额外依赖 Redis（Redis 挂了也不能挡创建）。
    Windows 没有 fcntl → 退化为无锁（沙箱只在 Linux 上跑）。
    """
    try:
        import fcntl
    except ImportError:                     # Windows 本地开发
        yield
        return
    fh = None
    try:
        os.makedirs(_SANDBOX_LOCK_DIR, exist_ok=True)
        fh = open(os.path.join(_SANDBOX_LOCK_DIR, f"{name}.lock"), "w")
    except OSError as e:
        log.warning(f"创建沙箱锁不可用，降级为无锁: {e}")
        yield
        return
    try:
        deadline: float = time.time() + _SANDBOX_LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise RuntimeError(
                        f"等待沙箱创建锁超时({_SANDBOX_LOCK_TIMEOUT}s)，可能有其它进程卡在创建: {name}"
                    )
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass


class SandboxManager:
    """按用户名管理 OpenShell 沙箱（线程安全）"""

    _class_lock: threading.Lock = threading.Lock()   # 兼容保留：仅保护锁表本身
    _name_locks: dict = {}                           # 沙箱名 → 线程锁
    _known_sandboxes: set[str] = set()  # 进程内已确认存在的沙箱

    @classmethod
    def _lock_for(cls, name: str) -> threading.Lock:
        """按沙箱名取线程锁：不同用户互不阻塞，同名仍然串行

        为什么按名加锁：创建动作是 IO（openshell CLI 建容器，5~10 秒）。
        用一把类级大锁会让「20 个新用户同时上线」变成一个一个建，
        首句从 10 秒级变成 40 秒级；同名并发才需要互斥（否则 gateway 报 invalid argument）。
        """
        with cls._class_lock:
            lk = cls._name_locks.get(name)
            if lk is None:
                lk = cls._name_locks[name] = threading.Lock()
            return lk

    def __init__(self) -> None:
        """从配置读取 OpenShell 参数（cfg 为单例，模块导入时已初始化）"""
        self.bin: str = getattr(cfg, "openshellBin", "/snap/bin/openshell")
        # 策略文件：支持相对路径（相对项目根），便于策略跟随项目一起部署
        # snap 限制：CLI（strict confinement）只能读 $HOME 内的文件，项目部署在
        # $HOME 之外（如 /data1）时，下面会把策略复制到 $HOME/.pa-policy/ 再用它
        _raw_policy: str = getattr(cfg, "openshellPolicy", "")
        if _raw_policy and not os.path.isabs(_raw_policy):
            # 本文件在 <repo>/src/agent/backends/sandbox.py → parents[3] 才是**仓库根**
            # （曾经少算一层，算出 <repo>/src/agent/config/... → 文件不存在 → 策略被静默跳过）
            _project_root: str = str(Path(__file__).resolve().parents[3])
            self.policy: str = os.path.join(_project_root, _raw_policy)
        else:
            self.policy: str = _raw_policy
        self.policy = _stage_policy_under_home(self.policy)
        # 配了策略却找不到文件 → 沙箱会以默认策略启动（等于没有隔离），必须显式告警
        if _raw_policy and not os.path.isfile(self.policy):
            log.error(
                "沙箱策略文件不存在，沙箱将以 OpenShell 默认策略启动（无网络/文件系统限制）: %s"
                "（检查 [agent] openshell_policy 是否相对仓库根）", self.policy,
            )
        # 自定义沙箱工作负载镜像（默认 nvcr.io/nvidia/base/ubuntu:24.04 只有 bash，跑不了 Python）
        self.image: str = getattr(cfg, "openshellImage", "")
        # 每用户沙箱资源上限（OpenShell 是命令行参数，不是策略文件）
        # 没有它，一个用户跑死循环 / 吃满内存会拖慢整台宿主机
        self.cpu: str = str(getattr(cfg, "sandboxCpu", "") or "")
        self.memory: str = str(getattr(cfg, "sandboxMemory", "") or "")
        self.timeout: int = getattr(cfg, "sandboxExecTimeout", 30)
        self.max_output: int = getattr(cfg, "sandboxMaxOutput", 8000)

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return bool(getattr(cfg, "openshellEnabled", False))

    def sandbox_name(self, username: str) -> str:
        """沙箱名：一个用户一个，且必须满足 gateway 的命名约束

        gateway 只接受「小写字母 / 数字 / 连字符」，且**总长 ≤ 19**。之前是
        `agent-{把非字母数字换成 _}`，于是：
          · `cui.jie` → `agent-cui_jie`（下划线非法）→ 创建直接失败；
          · 中文用户名（如「唐子龙」）→ 含中文，同样失败；
          · 用户名超过 13 字符 → 撞 19 字符上限。
        这些账号的沙箱功能长期是"静默不可用"（exec 返回错误，用户只看到执行失败）。
        现在：非法字符压成连字符；一旦发生有损映射或超长，就补 6 位哈希，
        既保证合法、又不会让两个账号撞进同一个沙箱。
        """
        raw0: str = (username or "anon").strip()
        raw: str = raw0.lower()
        budget: int = 19 - len("agent-")
        safe: str = re.sub(r"[^a-z0-9]+", "-", raw).strip("-") or "u"
        if safe != raw or len(safe) > budget or raw0 != raw:
            tag: str = hashlib.sha1(raw0.encode("utf-8")).hexdigest()[:6]
            safe = f"{(safe[: budget - 7].rstrip('-') or 'u')}-{tag}"
        return f"agent-{safe}"

    def _cli(self, args: list[str], timeout: int = 60,
             max_chars: int = 2_000_000) -> CliResult:
        """调用 openshell CLI（同机部署）

        为什么不用 `subprocess.run(capture_output=True)`：它会把子进程输出**全量**读进
        内存再返回，沙箱里一条 `yes` 就能在超时前把 worker 内存吃爆（OOM kill）。
        这里改成边读边计数，超过 max_chars 立刻关闭管道（子进程收到 EPIPE 退出），
        内存上限恒定，并把 truncated 标记带回去。
        """
        env = dict(os.environ)
        home: str = env.setdefault("HOME", os.path.expanduser("~"))
        # snap 限制：CLI 无法在 $HOME 建隐藏临时目录（.tmpXXXX），
        # upload/download 需要 TMPDIR 指向 snap 可写目录，否则报 Permission denied
        env["TMPDIR"] = os.path.join(home, "snap", "openshell", "common")

        t0: float = time.time()
        proc = subprocess.Popen(
            [self.bin, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        out_buf: list[bytes] = []
        err_buf: list[bytes] = []
        flag: dict = {"truncated": False}
        lock: threading.Lock = threading.Lock()

        def _drain(stream, sink: list) -> None:
            total: int = 0
            try:
                while True:
                    chunk: bytes = stream.read1(65536)   # type: ignore[attr-defined]
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_chars:
                        with lock:
                            flag["truncated"] = True
                        try:
                            stream.close()               # 关管道 → 子进程 EPIPE 立刻退出
                        except Exception:
                            pass
                        break
                    with lock:
                        sink.append(chunk)
            except Exception:
                pass

        threads: list[threading.Thread] = [
            threading.Thread(target=_drain, args=(proc.stdout, out_buf), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, err_buf), daemon=True),
        ]
        for th in threads:
            th.start()
        try:
            rc: int = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            raise
        for th in threads:
            th.join(timeout=5)

        stdout: str = b"".join(out_buf).decode("utf-8", "replace")
        stderr: str = b"".join(err_buf).decode("utf-8", "replace")
        if flag["truncated"]:
            stderr += f"\n[CliResult] 输出超过 {max_chars} 字符，已截断并终止子进程"
        return CliResult(returncode=rc, stdout=stdout, stderr=stderr,
                         truncated=bool(flag["truncated"]), elapsed=time.time() - t0)

    # ------------------------------------------------------------------ #
    # 沙箱生命周期（核心原则：**永不自动删除**，只创建或恢复）
    # ------------------------------------------------------------------ #
    def _get_phase(self, name: str) -> str | None:
        """查询沙箱生命周期阶段

        返回: None=不存在 | "Ready"=可执行 | "Stopped"/其他=需启动恢复
        """
        try:
            r = self._cli(["sandbox", "get", name], timeout=30)
        except Exception as e:
            log.warning(f"查询沙箱状态失败: name={name}, err={e}")
            return None
        if r.returncode != 0:
            return None
        for line in (r.stdout or "").splitlines():
            s = line.strip()
            if s.startswith("Phase:"):
                return s.split(":", 1)[1].strip()
        return None

    def _exists(self, name: str) -> bool:
        """沙箱是否存在（含 Stopped 等非 Ready 状态）"""
        return self._get_phase(name) is not None

    # ------------------------------------------------------------------ #
    # 维护接口：最后使用时间 / 列出 / 停止 / 删除
    # 供每日维护（闲置停止、孤儿清理）与测试脚本收尾使用
    # ------------------------------------------------------------------ #
    _last_touch: dict = {}  # 进程内节流：同一沙箱 60 秒只写一次 Redis

    def _touch_last_used(self, name: str) -> None:
        """记录「沙箱最后被使用」的时间（Redis，60 秒节流）

        为什么要记：维护任务要停掉长期没人用的沙箱（只 stop，保留 workspace）。
        记在 Redis 而不是文件：多 worker 共享，重启不丢（TTL 30 天，过期说明真没人用了）。
        """
        try:
            import time as _t

            now = _t.time()
            if now - self._last_touch.get(name, 0.0) < 60:
                return
            self._last_touch[name] = now
            from src.infra.utils import CacheUtil

            CacheUtil.set(f"sb:last:{name}", str(int(now)), 30 * 86400)
        except Exception:
            pass

    def last_used(self, name: str) -> float | None:
        """沙箱最后使用时间（epoch 秒）；没有记录返回 None"""
        try:
            from src.infra.utils import CacheUtil

            v = CacheUtil.get(f"sb:last:{name}")
            return float(v) if v else None
        except Exception:
            return None

    def set_last_used(self, name: str, ts: float) -> None:
        try:
            from src.infra.utils import CacheUtil

            CacheUtil.set(f"sb:last:{name}", str(int(ts)), 30 * 86400)
        except Exception:
            pass

    def list_items(self) -> list[tuple[str, float]]:
        """列出全部沙箱：[(名字, 创建时间 epoch)]，按名字稳定排序

        解析 `openshell sandbox list` 的表格（NAME / CREATED / PHASE 三列），
        创建时间用于「从没记录过使用时间」的兜底判断。
        """
        try:
            r = self._cli(["sandbox", "list", "--page-size", "2000"], timeout=90)
        except Exception as e:
            log.warning(f"列出沙箱失败: {e}")
            return []
        if r.returncode != 0:
            log.warning(f"列出沙箱失败: rc={r.returncode}, err={(r.stderr or '')[:200]}")
            return []
        out: list[tuple[str, float]] = []
        for line in (r.stdout or "").splitlines():
            s = line.strip()
            if not s or s.startswith("NAME") or s.startswith("Next page token"):
                continue
            parts = s.split()
            if len(parts) < 3 or parts[0] == "No":
                continue
            name: str = parts[0]
            try:
                from datetime import datetime

                created = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
                ts = created.timestamp()
            except Exception:
                ts = 0.0
            out.append((name, ts))
        out.sort(key=lambda x: x[0])
        return out

    def list_names(self) -> list[str]:
        return [n for n, _ in self.list_items()]

    def stop_sandbox(self, name: str) -> bool:
        """停止沙箱（**保留 workspace**，下次 exec 时 get_or_create 自动 start）"""
        try:
            r = self._cli(["sandbox", "stop", name], timeout=120)
        except Exception as e:
            log.warning(f"停止沙箱异常: {name}, {e}")
            return False
        if r.returncode == 0:
            self._known_sandboxes.discard(name)
            log.info(f"沙箱已停止（workspace 保留）: {name}")
            return True
        log.warning(f"停止沙箱失败: {name}, err={(r.stderr or '')[:200]}")
        return False

    def delete_sandbox(self, name: str) -> bool:
        """删除沙箱（**会丢文件**，只在孤儿/测试清理时用）"""
        try:
            r = self._cli(["sandbox", "delete", name], timeout=180)
        except Exception as e:
            log.warning(f"删除沙箱异常: {name}, {e}")
            return False
        if r.returncode == 0:
            self._known_sandboxes.discard(name)
            log.info(f"沙箱已删除: {name}")
            return True
        log.warning(f"删除沙箱失败: {name}, err={(r.stderr or '')[:200]}")
        return False

    @staticmethod
    def _looks_stopped(stderr: str) -> bool:
        """判断 CLI 报错是否表示「沙箱未运行」（需要 start 恢复而非重建）"""
        s = (stderr or "").lower()
        return any(k in s for k in (
            "not running", "stopped", "no such sandbox", "sandbox not found",
            "not ready", "sandbox is not",
        ))

    def get_or_create(self, username: str, verify: bool = False) -> str:
        """获取该用户的沙箱，必要时懒创建或**启动恢复**

        重要原则：**永不删除已有沙箱**（唯一例外见下）。
        沙箱里保存着用户的工作文件（脚本、数据、产出），因此：
        - 沙箱不存在 → 创建
        - 沙箱存在但未运行（如宿主机重启后）→ 只 start 恢复，**不重建**
          （重建会清空用户文件）
        - 沙箱已就绪 → 直接复用
        - 沙箱处于 Error 且 **start 被拒**（超管/基础设施级失败，实测 CLI 报
          `sandbox must be Stopped, Completed, or a failed main-process Error to start`）
          → 这种沙箱既进不去也取不出文件，只能删除重建；
          ⚠️ 重建后沙箱内原有的技能、pip 包、用户文件**全部丢失**（本系统不做备份），故打 WARNING

        verify=True：即使命中进程缓存，也向 CLI **复核沙箱是否真实存在**。
        用于登录初始化等低频场景 —— 沙箱可能被外部删除（运维手工清理、
        gateway 重建等），而 _known_sandboxes 是进程内缓存，察觉不到这类
        外部变化，会出现「沙箱已删但系统以为还在」→ 既不重建也不恢复。
        """
        if not self.enabled:
            raise RuntimeError("OpenShell 沙箱功能未启用（配置 openshell_enabled=false）")

        name = self.sandbox_name(username)

        # 快速路径：缓存命中且无需复核（对话中执行命令的热路径）
        if name in self._known_sandboxes and not verify:
            return name

        # 加锁：**按沙箱名**的进程内线程锁 + **跨进程文件锁**
        # 以前用一把类级大锁，等于把「不同用户建沙箱」也串起来：20 个新用户同时
        # 登录/首聊时，6 个进程各建一个、其余排队 → 首句 40 秒。
        # 不同沙箱名之间本来就互不相干，真正需要互斥的是「同一个名字」。
        with self._lock_for(name):
            if name in self._known_sandboxes and not verify:
                return name

            with _sandbox_create_lock(name):
                # 拿到跨进程锁后**再查一次**：等锁期间别的进程可能已经建好了
                phase = self._get_phase(name)

                # 缓存说"在"，CLI 说"不在" → 缓存已过期（沙箱被外部删除），清掉后重建
                if phase is None and name in self._known_sandboxes:
                    log.warning(f"缓存中的沙箱已不存在（可能被外部删除），将重建: {name}")
                    self._known_sandboxes.discard(name)

                if phase is None:
                    try:
                        self._create(name)
                    except RuntimeError:
                        # 极小概率仍撞上（锁不可用/超时）：建失败后再确认一次
                        if self._get_phase(name) is None:
                            raise
                        log.info(f"沙箱已被其它进程创建，直接复用: {name}")
                elif phase == "Error":
                    self._recover_or_recreate(username, name)
                elif phase != "Ready":
                    self._resume(name, phase)
                else:
                    log.info(f"沙箱已就绪，复用: {name}")

                self._known_sandboxes.add(name)

        return name

    def _recover_or_recreate(self, username: str, name: str) -> bool:
        """Error 沙箱的处理：优先 start 恢复；被 CLI 拒绝时删除重建。

        返回 True 表示发生了重建（沙箱内文件全部丢失）。

        为什么必须区分：Error 有两种 ——
          · main 进程失败型：`sandbox start` 可以救回来（用户文件保留）
          · 超管/基础设施型：CLI 直接拒绝 start
            （实测 `must be Stopped, Completed, or a failed main-process Error to start`），
            既进不去也取不出文件，只能删除重建。若不处理，用户会永久卡在坏沙箱上。
        """
        try:
            self._resume(name, "Error")
            return False
        except RuntimeError as e:
            log.warning(f"沙箱 Error 且无法 start 恢复，删除重建: {name}, err={str(e)[:200]}")

        # 注意：这一步会丢弃该沙箱内的用户文件；上面已打 WARNING 留痕
        if not self.destroy(username):
            raise RuntimeError(f"沙箱 {name} 处于 Error 且删除失败，需人工介入")
        self._create(name)
        return True

    def _create(self, name: str) -> None:
        """创建新沙箱（仅在沙箱不存在时调用）"""
        log.info(f"创建新沙箱: name={name}")
        args = ["sandbox", "create", "--name", name]
        if self.policy and os.path.exists(self.policy):
            args += ["--policy", self.policy]
            log.info(f"应用沙箱策略: {self.policy}")
        if self.image:
            args += ["--from", self.image]
            log.info(f"使用自定义沙箱镜像: {self.image}")
        if self.cpu:
            args += ["--cpu", self.cpu]
        if self.memory:
            args += ["--memory", self.memory]
        if self.cpu or self.memory:
            log.info(f"沙箱资源上限: cpu={self.cpu or '默认'}, memory={self.memory or '默认'}")
        try:
            r = self._cli(args, timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"创建沙箱超时: {name}")
        if r.returncode != 0:
            raise RuntimeError(f"创建沙箱失败: {name}, stderr={r.stderr.strip()[:300]}")
        log.info(f"沙箱创建完成: {name}")

    def _resume(self, name: str, phase: str) -> None:
        """启动恢复已有沙箱（不删除、不重建 → 用户文件全部保留）"""
        log.info(f"沙箱处于 {phase} 状态，启动恢复: {name}（原有文件保留）")
        try:
            r = self._cli(["sandbox", "start", name], timeout=180)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"启动沙箱超时: {name}")
        if r.returncode != 0:
            raise RuntimeError(f"启动沙箱失败: {name}, stderr={r.stderr.strip()[:300]}")
        log.info(f"沙箱已恢复运行: {name}")

    def destroy(self, username: str) -> bool:
        """删除该用户的沙箱（**会永久删除沙箱内的全部用户文件**）

        ⚠️ 危险操作，仅限用户明确要求清空自己沙箱时手动调用。
        系统**绝不会**自动调用：沙箱长期保留，宿主机/服务重启后自动 start 恢复。
        """
        name = self.sandbox_name(username)
        try:
            r = self._cli(["sandbox", "delete", name], timeout=120)
            self._known_sandboxes.discard(name)
            if r.returncode == 0:
                log.info(f"沙箱已删除: {name}")
                return True
            log.warning(f"删除沙箱失败: {name}, stderr={r.stderr.strip()[:200]}")
            return False
        except Exception as e:
            log.warning(f"删除沙箱异常: {name}, err={e}")
            return False

    @staticmethod
    def _safe_name(raw: str, fallback: str = "skill") -> str:
        """技能名/用户名安全化（防路径穿越）

        只保留字母数字/中文/`.`/`_`/`-`，并去掉首尾的 `.`/`_`/`-` —— 这样拼出来的
        路径里不会出现 `.` / `..` 这类特殊目录名，沙箱名/产出目录不会被穿越出去。
        """
        s: str = re.sub(r"[^\w.\-]+", "_", raw or "").strip().strip("._-")[:64]
        return s or fallback

    # ------------------------------------------------------------------ #
    # 产出文件回传（沙箱 → 宿主机，用户可打开/下载）
    # ------------------------------------------------------------------ #
    def _user_artifact_dir(self, username: str) -> str:
        """该用户的产物根目录（宿主机侧）

        `_safe_name` 是**有损**映射（"a b" 与 "a_b" 会归一化成同一个目录），
        一旦两个账号撞到同一目录，产物/下载就会互相读得到。这里用 `.owner`
        把"这个目录属于谁"钉死：发现归属不一致就换一个带哈希的目录，不迁移旧目录
        （旧目录继续归原主人，历史链接不会 404）。
        """
        base: str = os.path.join(_ARTIFACT_DIR, self._safe_name(username, "anon"))
        try:
            if os.path.isdir(base):
                marker: str = os.path.join(base, ".owner")
                owner: str = ""
                if os.path.isfile(marker):
                    with open(marker, encoding="utf-8") as f:
                        owner = f.read().strip()
                if owner and owner != username:
                    alt_name: str = (
                        f"{os.path.basename(base)}-"
                        f"{hashlib.sha1(username.encode('utf-8')).hexdigest()[:6]}"
                    )
                    alt: str = os.path.join(_ARTIFACT_DIR, alt_name)
                    log.warning(
                        f"产物目录用户名冲突，改用独立目录: {base} 已属于 {owner!r} → {alt}"
                    )
                    base = alt
            os.makedirs(base, exist_ok=True)
            marker = os.path.join(base, ".owner")
            if not os.path.isfile(marker):
                with open(marker, "w", encoding="utf-8") as f:
                    f.write(username)
        except OSError as e:
            log.warning(f"产物目录归属标记失败（忽略）: {base}, err={e}")
        return base

    def _artifact_root(self, username: str, session_id: str) -> str:
        """该用户该会话的产出目录（宿主机侧）"""
        return os.path.join(
            self._user_artifact_dir(username),
            self._safe_name(session_id, "session"),
        )

    def artifact_path(self, username: str, session_id: str, filename: str) -> str | None:
        """取某个产出文件的宿主机路径（不存在返回 None）

        安全：路径只由【登录会话里的用户名】+【前端会话 id】+【文件名基名】拼成，
        客户端指定不了目录 —— 天然跨不了用户、也穿越不出去。
        """
        name: str = os.path.basename((filename or "").replace("\\", "/"))
        if not name or name.startswith("."):
            return None
        path: str = os.path.join(self._artifact_root(username, session_id), name)
        if os.path.isfile(path):
            return path

        # 兜底：会话 ID 可能已经变了（刷新/新建会话后旧的 artifacts 目录对不上），
        # 但文件是按文件名扁平存的 —— 在该用户下按文件名找，取最新的那个。
        # 安全边界不变：只在自己的 artifacts/<username>/ 里找，跨不了用户。
        try:
            import glob as _glob

            user_dir: str = os.path.join(_ARTIFACT_DIR, self._safe_name(username, "anon"))
            hits = _glob.glob(os.path.join(user_dir, "*", name))
            hits = [h for h in hits if os.path.isfile(h)]
            if hits:
                hits.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return hits[0]
        except Exception:
            pass
        return None

    def _artifact_index_path(self, username: str, session_id: str | None = None) -> str:
        """内容指纹索引（**按用户**存，不按会话）

        为什么按用户：索引若按会话存，每开一个新会话就"没见过任何文件" →
        全部历史文件都会被判成"本轮新产出"并挂到回复后面（实测：问一句"你能干什么"，
        底下会列出一堆上周的报告）。按用户存之后，"新产出"才真的是新产出。
        """
        user_dir: str = self._user_artifact_dir(username)
        if session_id is None:
            return os.path.join(user_dir, ".index.json")
        return os.path.join(self._artifact_root(username, session_id), ".index.json")

    def _load_artifact_index(self, username: str, session_id: str | None = None) -> dict:
        try:
            with open(self._artifact_index_path(username, session_id), "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _save_artifact_index(self, username: str, session_id: str | None, index: dict) -> None:
        try:
            with open(self._artifact_index_path(username, session_id), "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False)
        except Exception as e:
            log.warning(f"产出指纹索引写入失败: user={username}, session={session_id}, err={e}")

    def _remote_meta(self, username: str, remotes: list[str]) -> dict:
        """一次 CLI 调用取回一批文件的 (大小, 指纹)

        指纹 = 大小:mtime。用 stat 而不是 sha256：**大文件不会被整份读一遍**，
        开销恒定；同尺寸同秒内改写这种极端情况忽略不计。
        """
        if not remotes:
            return {}
        args: str = " ".join(shlex.quote(p) for p in remotes)
        # 注意：格式里不要用 \t 这类转义（实测在沙箱里不会被解释，输出是字面量 \t）；
        # 用 ":" 分隔，从右往左 split(2) 取，文件名里的冒号也不会破坏解析
        r = self.exec(username, f"stat -c %s:%Y:%n -- {args} 2>/dev/null || true", timeout=30)
        found: dict = {}
        for line in (r.stdout or "").splitlines():
            parts: list = line.split(":", 2)
            if len(parts) < 3:
                continue
            size_s: str = parts[0].strip()
            if not size_s.isdigit():
                continue
            mtime_s: str = parts[1].strip()
            path: str = parts[2].strip()
            found.setdefault(path, (int(size_s), f"{size_s}:{mtime_s}"))
        # 文件名带特殊字符时 GNU 会给路径加引号 → 用 basename 兜底匹配
        if len(found) < len(remotes):
            by_name: dict = {os.path.basename(p): v for p, v in found.items()}
            for p in remotes:
                if p not in found and os.path.basename(p) in by_name:
                    found[p] = by_name[os.path.basename(p)]
        return found

    def list_artifacts(self, username: str) -> list[str]:
        """列出沙箱内的交付物（沙箱内 POSIX 路径）

        · 约定交付目录里的文件（**含一层子目录**，比如 out/charts/*.png）
          /sandbox/output、/sandbox/output、/sandbox/outputs、/sandbox/deliverables
          （之前只扫 /sandbox/output 且不递归 → 用户写 /sandbox/output 就永远取不到）
        · /sandbox 根目录里、不属于内部/中间产物的文件（兼容历史行为）

        同名文件（不同子目录里都叫 report.html）会加上父目录前缀，避免互相覆盖。
        """
        files: list[str] = []
        seen: dict[str, int] = {}

        dirs: str = " ".join(_ARTIFACT_DIRS)
        res = self.exec(
            username,
            f"find {dirs} -maxdepth 3 -type f ! -name '.*' 2>/dev/null | head -200 || true",
            timeout=25,
        )
        for p in (res.stdout or "").splitlines():
            p = p.strip()
            if not p or p.startswith("/sandbox/output/.tmp"):
                continue
            name: str = os.path.basename(p)
            if name in _ARTIFACT_SKIP_NAMES:
                continue
            if os.path.splitext(name)[1].lower() in _ARTIFACT_SKIP_EXTS:
                continue
            # 同名去重：用父目录名做前缀
            n: int = seen.get(name, 0)
            seen[name] = n + 1
            files.append(p)

        res = self.exec(username, "ls -1 -p -A /sandbox/ 2>/dev/null || true", timeout=20)
        for n in (res.stdout or "").splitlines():
            if not n or n.endswith("/") or n.startswith("."):
                continue
            if n in _ARTIFACT_SKIP_NAMES:
                continue
            # 中间产物/脚本/日志不上交付清单（要交付就放交付目录）
            if os.path.splitext(n)[1].lower() in _ARTIFACT_SKIP_EXTS:
                continue
            if n in ("skills", "skills-disabled", "skills-trash", "out", "output", "outputs", "deliverables"):
                continue
            files.append(f"/sandbox/{n}")
        return files

    def collect_artifacts(self, username: str, session_id: str) -> tuple[list[dict], int]:
        """把沙箱里的交付物回传到宿主机

        返回 ( 本轮回传的文件列表, 因上限被跳过的数量 )

        产品口径（已确认）：**单文件 100MB、每轮最多回传 5 个**。
        超出 5 个的部分不下载、也**不删除**，用户仍可在「我的文件」里看到并下载；
        跳过数量会带回给回复文案提示一句，避免"生成了 8 个只给 5 个链接"的困惑。

        成本：每轮收尾扫一次（2 次 ls + 1 次 stat），**只有内容变了才下载**
        （指纹存 .index.json）；已经生成过、内容没变的文件只补本地副本，不重复搬。
        """
        result: list[dict] = []
        if not self.enabled:
            return result, 0
        try:
            remotes_all: list[str] = self.list_artifacts(username)
            meta: dict = self._remote_meta(username, remotes_all)
        except Exception as e:
            log.warning(f"列出产出文件失败: user={username}, err={e}")
            return result, 0
        if not remotes_all:
            return result, 0

        dst: str = self._artifact_root(username, session_id)
        os.makedirs(dst, exist_ok=True)
        index: dict = self._load_artifact_index(username)      # 用户级指纹（跨会话共享）
        # 首次运行（还没有用户级索引）→ 把现有文件当作"历史已知"，不算本轮新产出
        first_run: bool = not os.path.isfile(self._artifact_index_path(username))
        index_dirty: bool = False

        # 同名文件（不同子目录里都叫 report.html）加父目录名区分，避免互相覆盖
        base_counts: dict[str, int] = {}
        for r in remotes_all:
            b: str = os.path.basename(r)
            base_counts[b] = base_counts.get(b, 0) + 1
        name_map: dict[str, str] = {}
        used_names: set[str] = set()
        for r in remotes_all:
            base: str = os.path.basename(r)
            if base_counts.get(base, 0) > 1:
                parent: str = os.path.basename(os.path.dirname(r)) or "out"
                base = f"{parent}_{base}"
            nm: str = base
            i: int = 1
            while nm in used_names:
                i += 1
                nm = f"{os.path.splitext(base)[0]}_{i}{os.path.splitext(base)[1]}"
            used_names.add(nm)
            name_map[r] = nm

        # 先算出"是不是本轮新产出 / 要不要搬"，让**新产出**优先占 5 个名额，
        # 否则旧文件按 find 顺序排前面，新报告反而拿不到链接
        now_ts: float = time.time()
        candidates: list[tuple[str, str, str, bool, bool]] = []   # (remote, name, digest, changed, need_copy)
        for remote in remotes_all:
            name: str = name_map.get(remote) or os.path.basename(remote)
            info = meta.get(remote)
            if not info:
                continue
            remote_size, digest = info
            if remote_size <= 0 or remote_size > _ARTIFACT_MAX_BYTES:
                continue                                    # 单文件超过 100MB：下载前挡掉
            local: str = os.path.join(dst, name)
            # digest = "size:mtime"；mtime 是沙箱内的 unix 时间戳（同一台宿主机，可直接比）
            mtime_i: int = 0
            try:
                mtime_i = int(str(digest).split(":", 1)[1])
            except (IndexError, ValueError):
                mtime_i = 0
            recent: bool = mtime_i > 0 and (now_ts - mtime_i) <= _ARTIFACT_NEW_WINDOW_S
            changed: bool = recent if first_run else (index.get(name) != digest)
            need_copy: bool = changed or not os.path.isfile(local)
            candidates.append((remote, name, digest, changed, need_copy))
        candidates.sort(key=lambda x: (0 if x[3] else 1, 0 if x[4] else 1))
        picked = candidates[:_ARTIFACT_MAX_FILES]
        skipped: int = max(0, len(candidates) - len(picked))

        for remote, name, digest, changed, need_copy in picked:
            local = os.path.join(dst, name)
            if need_copy:
                if not self.download(username, remote, local):
                    continue
                size: int = os.path.getsize(local) if os.path.isfile(local) else 0
                if size <= 0 or size > _ARTIFACT_MAX_BYTES:
                    try:
                        os.remove(local)          # 实际落盘超限也不保留
                    except OSError:
                        pass
                    continue
                if changed or first_run:
                    index[name] = digest
                    index_dirty = True
            else:
                size = os.path.getsize(local)
            result.append({"name": name, "size": size, "changed": changed})
        if index_dirty:
            self._save_artifact_index(username, None, index)
        if result or skipped:
            log.info(f"产出文件回传: user={username}, session={session_id}, "
                     f"files={[a['name'] for a in result]}, "
                     f"changed={[a['name'] for a in result if a['changed']]}, "
                     f"skipped(超出每轮 5 个上限)={skipped}")
        return result, skipped

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def exec(self, username: str, command: str, timeout: int | None = None,
             max_output: int | None = None) -> ExecResult:
        """在该用户自己的沙箱内执行 shell 命令"""
        if not command or not command.strip():
            return ExecResult(returncode=1, error="命令为空")

        t = timeout or self.timeout
        max_out = self.max_output if max_output is None else int(max_output)

        try:
            name = self.get_or_create(username)
        except Exception as e:
            return ExecResult(returncode=1, error=str(e))
        self._touch_last_used(name)  # 记录活跃时间：维护任务据此只停长期没人用的沙箱

        try:
            r = self._cli(
                ["sandbox", "exec", name, "--", "bash", "-lc", command],
                timeout=t + 15,  # CLI 侧留余量
                # 允许比展示上限多留 4 倍余量，超出就断管道（防 `yes` 把内存吃爆）
                max_chars=max(max_out * 4, 65536) if max_out > 0 else 2_000_000,
            )
        except subprocess.TimeoutExpired:
            log.warning(f"沙箱命令超时: user={username}, timeout={t}s")
            return ExecResult(returncode=124, error=f"命令执行超过 {t} 秒已中止")
        except FileNotFoundError:
            return ExecResult(returncode=127, error="未找到 openshell CLI，请确认已安装")
        except Exception as e:
            return ExecResult(returncode=1, error=f"{type(e).__name__}: {e}")

        # 沙箱可能被外部停止（缓存已失效）→ 清缓存、自动恢复后重试一次
        # 语义：只 start 恢复，绝不重建，用户文件保留
        if r.returncode != 0 and self._looks_stopped(r.stderr or ""):
            log.warning(f"沙箱疑似未运行，尝试自动恢复: {name}")
            self._known_sandboxes.discard(name)
            try:
                name = self.get_or_create(username)
                r = self._cli(
                    ["sandbox", "exec", name, "--", "bash", "-lc", command],
                    timeout=t + 15,
                    max_chars=max(max_out * 4, 65536) if max_out > 0 else 2_000_000,
                )
                log.info(f"自动恢复后重试成功: {name}")
            except Exception as e:
                log.warning(f"自动恢复失败: {name}, err={e}")

        stdout, stderr = r.stdout or "", r.stderr or ""
        truncated = bool(r.truncated)
        if max_out > 0 and len(stdout) > max_out:
            stdout, truncated = stdout[:max_out], True
        if max_out > 0 and len(stderr) > max_out:
            stderr = stderr[:max_out]

        log.info(
            f"沙箱执行: user={username}, rc={r.returncode}, "
            f"out={len(stdout)}B, err={len(stderr)}B, cmd={command[:120]!r}"
        )
        # 失败时把 stderr 记入日志（否则诊断时看不到脚本真实报错）
        if r.returncode != 0 and stderr.strip():
            log.warning(
                f"沙箱命令失败详情: user={username}, cmd={command[:120]!r}, "
                f"stderr={stderr.strip()[:500]}"
            )
        return ExecResult(stdout=stdout, stderr=stderr,
                          returncode=r.returncode, truncated=truncated)

    # ------------------------------------------------------------------ #
    # 文件交换
    # ------------------------------------------------------------------ #
    def _stage_in_home(self, local_path: str) -> str:
        """把宿主文件暂存到 $HOME 内，返回 snap 版 CLI **真正读得到**的路径。

        为什么必须这么做（实测）：OpenShell 是 snap 严格封装的，它的 mount
        命名空间里**看不到 $HOME 之外的任何路径**。本项目部署在 /data1，上传目录、
        产出目录、技能备份全在 /data1 下 —— 把这些路径直接交给 CLI 会报
        `local path does not exist`（同一个文件放进 $HOME 内就 100% 成功）。
        与「策略文件必须暂存到 $HOME」是同一个根因。

        已在 $HOME 内则原样返回（省一次复制）。
        """
        try:
            home: str = os.path.realpath(os.path.expanduser("~"))
            if os.path.realpath(local_path).startswith(home + os.sep):
                return local_path
        except Exception:
            pass
        stage_dir: str = os.path.join(os.path.expanduser("~"), "pa-stage")
        os.makedirs(stage_dir, exist_ok=True)
        dst: str = os.path.join(
            stage_dir, f"{uuid.uuid4().hex[:12]}_{os.path.basename(local_path)}"
        )
        shutil.copy2(local_path, dst)
        log.info(f"文件暂存到 $HOME（snap 才能读到）: {local_path} -> {dst}")
        return dst

    def upload(self, username: str, local_path: str, remote_path: str) -> bool:
        """把宿主文件上传进**该用户自己的沙箱**（隔离：其他用户绝对看不到）

        隔离保证：
        - 目标沙箱名固定为 `agent-{username}`，文件只会写进该用户自己的沙箱
        - 沙箱之间是独立容器 + 内核级隔离，跨用户无法访问

        注意（踩过的坑）：
        - **本地路径必须在 $HOME 内**：snap 命名空间看不到 /data1 等外部路径，
          否则报 `local path does not exist`（见 _stage_in_home）
        - 必须加 `--no-git-ignore`：CLI 默认按 .gitignore 过滤上传内容，
          在非 git 目录下会直接报错失败
        - 需要 TMPDIR 指向 snap 可写目录（已在 _cli 中统一处理）

        Args:
            username: 上传者域账号（决定落到哪个沙箱）
            local_path: 宿主侧文件路径（可在 $HOME 外，内部会暂存）
            remote_path: 沙箱内目标路径（建议 /sandbox/<文件名>）

        Returns:
            是否上传成功
        """
        if not self.enabled:
            log.warning(f"沙箱未启用，跳过文件上传: user={username}")
            return False
        try:
            name: str = self.get_or_create(username)
        except Exception as e:
            log.warning(f"上传前获取沙箱失败: user={username}, err={e}")
            return False

        try:
            staged: str = self._stage_in_home(local_path)
        except Exception as e:
            log.warning(f"上传前暂存失败，放弃: user={username}, err={e}")
            return False

        try:
            r = self._cli(
                ["sandbox", "upload", "--no-git-ignore", name, staged, remote_path],
                timeout=240,
            )
        except subprocess.TimeoutExpired:
            log.warning(f"上传文件到沙箱超时: user={username}, file={local_path}")
            return False
        except Exception as e:
            log.warning(f"上传文件到沙箱异常: user={username}, err={e}")
            return False
        finally:
            # 暂存副本用完即删（原始文件由调用方管理）
            if staged != local_path:
                try:
                    os.remove(staged)
                except OSError:
                    pass

        if r.returncode != 0:
            log.warning(
                f"上传文件到沙箱失败: sandbox={name}, rc={r.returncode}, "
                f"stderr={(r.stderr or '').strip()[:300]}"
            )
            return False

        log.info(f"上传文件到沙箱成功: sandbox={name}, {local_path} -> {remote_path}")
        return True

    def download(self, username: str, remote_path: str, local_path: str) -> bool:
        """从用户沙箱下载文件到宿主

        注意：snap 版 CLI **写不进 $HOME 之外的路径**（同 upload 的根因），
        所以先下到 $HOME/pa-stage/，再由本方法搬到真正的 local_path。
        调用方传的是**文件**路径（不是目录），因此可以安全 move。
        """
        try:
            name = self.get_or_create(username)
            stage_dir: str = os.path.join(os.path.expanduser("~"), "pa-stage")
            os.makedirs(stage_dir, exist_ok=True)
            tmp_local: str = os.path.join(
                stage_dir, f"{uuid.uuid4().hex[:12]}_{os.path.basename(local_path)}"
            )
            r = self._cli(
                ["sandbox", "download", name, remote_path, tmp_local], timeout=180
            )
            if r.returncode != 0:
                log.warning(
                    f"下载沙箱文件失败: sandbox={name}, {remote_path}, "
                    f"stderr={(r.stderr or '').strip()[:200]}"
                )
                return False
            if not os.path.exists(tmp_local):
                log.warning(f"下载命令返回成功但文件不存在: {tmp_local}")
                return False
            # 落地到真正的目标路径（目录不存在则补建）
            parent: str = os.path.dirname(local_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.move(tmp_local, local_path)
            return True
        except Exception as e:
            log.warning(f"下载沙箱异常: {e}")
            return False

    def list_sandboxes(self) -> list[dict]:
        """列出所有沙箱（运维用）"""
        try:
            r = self._cli(["sandbox", "list", "-o", "json"], timeout=30)
            if r.returncode != 0:
                return []
            return json.loads(r.stdout or "[]")
        except Exception:
            return []


# 全局单例
sandbox_manager: SandboxManager = SandboxManager()


# ========== 登录触发的沙箱初始化 ==========

def init_user_sandbox_async(username: str) -> None:
    """登录成功后，后台异步创建（或恢复）该用户的沙箱

    设计取舍：登录即拥有自己的沙箱（符合「每人一个沙箱」的直觉），
    但创建耗时 5~10 秒，因此放到后台线程执行，**不阻塞登录响应**。

    幂等且并发安全：
    - get_or_create() 内部有进程锁 + 已就绪缓存
    - 同一用户重复登录只会复用同一个沙箱
    - 宿主机重启后沙箱为 Stopped 时，这里会自动 start 恢复（不重建、不丢文件）
    """
    if not sandbox_manager.enabled or not username:
        return

    def _worker() -> None:
        try:
            # verify=True：登录是低频操作，值得花一次 CLI 调用确认沙箱真实存在
            # （否则沙箱被外部删除后，系统会一直以为它还在，既不重建也不恢复）
            name = sandbox_manager.get_or_create(username, verify=True)
            # 注：曾经在这里再跑一条 `true` 想唤醒容器内 shell，实测**反效果**——
            # 新容器第一次 exec 要 8~10 秒，20 人同时上线就把预热拖到 40 秒，
            # 反而和首句对话抢资源（首句从 17s 变 21s）。只建容器即可。
            log.info(f"登录初始化沙箱完成: {name}")
        except Exception as e:
            # 初始化失败不影响登录与聊天，后续真正执行命令时会再试一次
            log.warning(f"登录初始化沙箱失败: user={username}, err={e}")

    threading.Thread(
        target=_worker, daemon=True, name=f"sandbox-init-{username}"
    ).start()
