"""Redis 工具类：连接管理（限流、去重锁等底层能力）。

会话状态持久化已迁移到 LangGraph PostgresSaver（见 service/AgentService.py），
此处不再负责 state 的序列化/反序列化，仅提供底层 Redis 连接。
"""
import threading
import time

from src.infra.utils.LogUtil import log
from config.Configs import cfg

# 断线后重连冷却（秒）：Redis 起不来时不能让每个请求都去连一次
_RECONNECT_COOLDOWN: float = 60.0
_RECONNECT_TIMEOUT: int = 3


class RedisUtil:
    """Redis 连接管理

    支持单机 / 集群两种模式，连接失败时降级运行（_client 置 None）。
    限流、去重锁等能力通过 available + _client 直接使用。
    """
    _instance: 'RedisUtil | None' = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> 'RedisUtil':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._initialized = True
        self._client = None
        self._last_try: float = 0.0
        self._connect()

    # ========== 连接初始化 ==========

    def _connect(self, connect_timeout: int = 10) -> None:
        """建立 Redis 连接，失败时降级"""
        try:
            import redis
            password = cfg.redisPassword or None
            if cfg.redisCluster:
                self._client = self._create_cluster(redis, password, connect_timeout)
            else:
                self._client = self._create_single(redis, password, connect_timeout)
            self._client.ping()
            log.info(f"Redis 连接成功, 模式: {'cluster' if cfg.redisCluster else 'single'}")
        except Exception as e:
            self._client = None
            self._last_try = time.time()
            log.warning(f"Redis 连接失败, 限流/去重降级为放行: {e}")

    def _reconnect_if_due(self) -> None:
        """断线后按冷却期惰性重连

        之前只在 import 时连一次，连不上就永久降级：该 worker 的限流/防重复提交
        直到重启前全部放行（而健康检查只显示 disconnected，很容易被忽略）。
        """
        now: float = time.time()
        if now - self._last_try < _RECONNECT_COOLDOWN:
            return
        with self._lock:
            if self._client is not None or time.time() - self._last_try < _RECONNECT_COOLDOWN:
                return
            self._last_try = time.time()
            log.info("Redis 断线，尝试重连…")
            self._connect(connect_timeout=_RECONNECT_TIMEOUT)

    @staticmethod
    def _create_cluster(redis: 'RedisModule', password: str,
                        connect_timeout: int = 10) -> 'RedisCluster':
        """创建 Redis 集群连接"""
        startup_nodes: list = []
        for node_addr in cfg.redisNodes:
            node_addr = node_addr.strip()
            if not node_addr:
                continue
            if ":" in node_addr:
                host, port = node_addr.rsplit(":", 1)
                startup_nodes.append(redis.cluster.ClusterNode(host, int(port)))
            else:
                startup_nodes.append(redis.cluster.ClusterNode(node_addr, 6379))
        if not startup_nodes:
            startup_nodes = [redis.cluster.ClusterNode(cfg.redisHost, cfg.redisPort)]
        return redis.RedisCluster(
            startup_nodes=startup_nodes,
            password=password,
            decode_responses=False,                 # 存储 bytes，不做自动解码
            socket_connect_timeout=connect_timeout,  # 连接超时
            socket_keepalive=True,
        )

    @staticmethod
    def _create_single(redis: 'RedisModule', password: str,
                       connect_timeout: int = 10) -> 'Redis':
        """创建 Redis 单机连接"""
        return redis.Redis(
            host=cfg.redisHost,
            port=cfg.redisPort,
            password=password,
            db=cfg.redisDB,
            decode_responses=False,
            socket_connect_timeout=connect_timeout,
            socket_keepalive=True,
            retry_on_timeout=True,
        )

    # ========== 公共接口 ==========

    @property
    def available(self) -> bool:
        """Redis 连接是否可用（不可用时按冷却期尝试重连一次）"""
        if self._client is not None:
            return True
        self._reconnect_if_due()
        return self._client is not None


redis_util = RedisUtil()
