"""gunicorn 钩子：每个 worker 起来后，后台预热「进程内缓存」

为什么需要
----------
MCP 工具清单是**进程内缓存**（`mcp_manager._tools_cache`），6 个 worker 各一份。
每个 worker 启动后第一次装配 agent 要真起子进程 + 握手 + list_tools，实测约 2.5 秒；
并发首轮时请求散到还没握手的 worker 上，用户就会莫名多等这 2.5 秒。

这里在每个 worker fork 出来后起一个后台线程，**先把清单拉好**，
等真正来请求时缓存已热（命中是 0ms），用户完全察觉不到。

用法（systemd ExecStart 里加）：
    --config /data1/open-agent-harness/gun_warm.py

注意
----
· 钩子失败绝不影响服务启动/请求（只打日志）；
· 不要在这里写 bind/workers/daemon 等配置——本文件只管预热；
· 延迟 2 秒再拉，避开 worker 刚 fork 时的 DB/Redis 连接初始化。
"""
import threading
import time


def post_worker_init(worker) -> None:
    """gunicorn 在 worker 启动完成后调用（每个 worker 一次）"""

    def _run() -> None:
        time.sleep(2)
        try:
            from src.agent.mcp.client import mcp_manager

            tools = mcp_manager.load_tools()
            print(f"[warm] worker {worker.pid} MCP 工具预热完成: {len(tools)} 个", flush=True)
        except Exception as e:  # 预热失败不影响任何功能，真正请求时会再试
            print(f"[warm] worker {worker.pid} MCP 预热失败（忽略）: {e}", flush=True)

    threading.Thread(target=_run, daemon=True, name=f"mcp-warm-{worker.pid}").start()
