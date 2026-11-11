#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全局配置单例，支持多环境切换与环境变量覆盖"""
import configparser
import os
import threading
from typing import Callable, Any

from src.infra.utils.LogUtil import log


class Configs(object):
    """应用配置单例（双重检查锁线程安全）

    配置优先级：环境变量 PA_{SECTION}_{KEY} > configs.cfg 文件 > fallback 默认值
    """
    _instance: 'Configs | None' = None
    _lock: threading.Lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> 'Configs':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(Configs, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if Configs._initialized:
            return

        with Configs._lock:
            if Configs._initialized:
                return

            self.__config = configparser.RawConfigParser()
            self.__initConfigs()

            Configs._initialized = True

    def __initConfigs(self) -> None:
        """加载配置：config/configs.cfg（可用 PA_CONFIG_FILE 指定别的路径）

        一条原则：**文件只放非敏感项，口令与密钥只从环境变量来**。
        """
        config_path: str = os.path.dirname(__file__)
        config_file: str = (os.getenv('PA_CONFIG_FILE') or '').strip() or os.path.join(
            config_path, 'configs.cfg')

        if not os.path.isfile(config_file):
            log.warning(
                f'未找到配置文件 {config_file}，将只使用环境变量 / 默认值；'
                f'请复制 config/configs.cfg 并填好非敏感项'
            )

        # 必须显式 utf-8：文件里有中文注释，Windows 默认 GBK 会 UnicodeDecodeError
        self.__config.read(config_file, encoding='utf-8')
        log.info(f'配置文件：{config_file}')

        def _get(section: str, key: str, fallback: str = '',
                 type_fn: Callable[[str], Any] = str) -> Any:
            """优先读取环境变量（PA_{SECTION}_{KEY}），其次读配置文件"""
            env_key: str = f"PA_{section.upper()}_{key.upper()}"
            env_val: str | None = os.getenv(env_key)
            if env_val is not None:
                return type_fn(env_val)
            return type_fn(self.__config.get(section, key, fallback=fallback))

        # 数据库配置
        self.mysqlUser: str = _get('db', 'mysql_user')
        self.mysqlPass: str = _get('db', 'mysql_pass')
        self.mysqlHost: str = _get('db', 'mysql_host')
        self.mysqlPort: int = _get('db', 'mysql_port', '3306', int)
        self.mysqlDB: str = _get('db', 'mysql_db')

        # LLM 模型配置（对话与视觉分离：对话需强 function calling 能力，OCR 只需视觉）
        # 不在代码里放任何兜底密钥：缺配置就让调用方看到明确错误（日志/接口），
        # 免得"某个环境忘了配"时悄悄用上仓库里那把 key。
        self.deepseekApiKey: str = _get('llm', 'deepseek_api_key', '')
        self.deepseekModel: str = _get('llm', 'deepseek_model', 'deepseek-v4-pro')
        # 注：不再单独配置视觉模型 —— deepseek-v4-flash 自带图片模态
        #     （模型列表 input_modalities 含 image），OCR 直接复用对话模型
        self.deepseekBaseUrl: str = _get('llm', 'deepseek_base_url',
                                         'https://api.deepseek.com/v1')

        # Redis 配置
        self.redisHost: str = _get('redis', 'redis_host', 'localhost')
        self.redisPort: int = _get('redis', 'redis_port', '6379', int)
        self.redisPassword: str = _get('redis', 'redis_password')
        self.redisDB: int = _get('redis', 'redis_db', '0', int)
        self.redisCluster: bool = _get('redis', 'redis_cluster', 'False',
                                       lambda v: v.lower() in ('true', '1', 'yes'))
        raw_nodes: str = _get('redis', 'redis_nodes')
        self.redisNodes: list[str] = [n.strip() for n in raw_nodes.split(',') if n.strip()] if raw_nodes else []

        self.shellTimeout: int = _get('agent', 'shell_timeout', '30', int)

        # LangGraph checkpointer PostgreSQL 连接（多机共享会话状态）
        self.pgHost: str = _get('agent', 'pg_host', '')
        self.pgPort: int = _get('agent', 'pg_port', '5432', int)
        self.pgDb: str = _get('agent', 'pg_db', 'postgres')
        self.pgUser: str = _get('agent', 'pg_user', 'postgres')
        self.pgPass: str = _get('agent', 'pg_pass', '')

        # ---------------- 知识库（文档 RAG） ----------------
        # 开关：关掉后 /api/kb/* 全部 404，kb_search 工具也会拒绝
        self.kbEnabled: bool = _get('kb', 'enabled', 'true', lambda v: str(v).strip().lower() in ('1', 'true', 'yes', 'on'))
        # 独立 PG 库（pgvector）；口令走 PA_KB_PG_PASS
        self.kbPgHost: str = _get('kb', 'pg_host', '') or self.pgHost
        self.kbPgPort: int = _get('kb', 'pg_port', '5432', int)
        self.kbPgDb: str = _get('kb', 'pg_db', 'kb')
        self.kbPgUser: str = _get('kb', 'pg_user', '') or self.pgUser
        self.kbPgPass: str = _get('kb', 'pg_pass', '') or self.pgPass
        self.kbMaxConns: int = _get('kb', 'max_conns', '4', int)
        # 本地嵌入/重排服务（独立进程，见 src/kb_embed_server.py）
        self.kbEmbedUrl: str = _get('kb', 'embed_url', 'http://127.0.0.1:5110')
        self.kbDim: int = _get('kb', 'dim', '768', int)
        self.kbRerankTimeoutMs: int = _get('kb', 'rerank_timeout_ms', '200', int)
        # 向量表（LlamaIndex 自建：<table> 元数据表 + data_<table> 节点表）
        self.kbTableName: str = _get('kb', 'table_name', 'kb_nodes')
        # 切分与召回（chunk 长度按字符计，中文友好）
        self.kbChunkSize: int = _get('kb', 'chunk_size', '700', int)
        self.kbChunkOverlap: int = _get('kb', 'chunk_overlap', '100', int)
        self.kbVecTop: int = _get('kb', 'vec_top', '40', int)
        self.kbFuseTop: int = _get('kb', 'fuse_top', '20', int)
        # 上传与索引
        self.kbMaxFileMb: int = _get('kb', 'max_file_mb', '20', int)
        self.kbDataDir: str = _get('kb', 'data_dir', '')

        # ---- OCR（扫描件：无文字层的 PDF）----
        # 走 OpenAI 协议的视觉接口；网关在内网，文档不出内网
        self.kbOcrEnabled: bool = _get('kb', 'ocr_enabled', 'true',
                                       lambda v: str(v).strip().lower() in ('1', 'true', 'yes', 'on'))
        self.kbOcrBaseUrl: str = _get('kb', 'ocr_base_url', '')
        self.kbOcrApiKey: str = _get('kb', 'ocr_api_key', '')
        self.kbOcrModel: str = _get('kb', 'ocr_model', '')
        self.kbOcrDpi: int = _get('kb', 'ocr_dpi', '150', int)
        self.kbOcrMaxPages: int = _get('kb', 'ocr_max_pages', '30', int)
        self.kbOcrTimeoutS: int = _get('kb', 'ocr_timeout_s', '120', int)

        # OpenShell 沙箱配置（每个登录用户独立的隔离执行环境）
        # 沙箱是**必需组件**：没有开关，CLI 不可用时服务拒绝启动（见 app_fastapi.py）
        self.openshellBin: str = _get('agent', 'openshell_bin', '/snap/bin/openshell')
        self.openshellPolicy: str = _get('agent', 'openshell_policy', '')
        # 自定义沙箱镜像（含 python3 等运行时）；留空则用 OpenShell 默认镜像
        self.openshellImage: str = _get('agent', 'openshell_image', '')
        self.sandboxExecTimeout: int = _get('agent', 'sandbox_exec_timeout', '30', int)
        self.sandboxMaxOutput: int = _get('agent', 'sandbox_max_output', '8000', int)

        # 说明：**本系统没有审批流**（不做"待办/复核人"）。写类工具直接执行，把关靠：
        # ① RBAC 授权（没权限的工具不进模型清单）② 工具内参数硬校验
        # ③ 对话里要求模型先把改动汇总给用户确认。原来的 approval_required_tools
        # 配置项从未被任何代码消费，属假承诺，已删除。

        # 上下文治理：单会话上下文预算（字符数）。超过后先压工具输出、再滚动摘要。
        self.contextCharBudget: int = _get('agent', 'context_char_budget', '60000', int)

        # 单轮护栏：超过时长或步数就收尾（实测有 1005 秒的一轮）
        self.turnMaxSeconds: int = _get('agent', 'turn_max_seconds', '600', int)
        self.turnMaxSteps: int = _get('agent', 'turn_max_steps', '40', int)
        # 沙箱资源上限（OpenShell create 参数；空=不限制）
        self.sandboxCpu: str = _get('agent', 'sandbox_cpu', '')
        self.sandboxMemory: str = _get('agent', 'sandbox_memory', '')
        # RBAC：角色 → 工具集合；用户 → 角色（见 service/RbacService.py）
        # 引导管理员：RBAC 表为空时也能有一个可以分配角色的账号
        # 迁移既有用户为 operator（仅 dev；生产要求显式分配）
        self.langsmithTracing: bool = _get('observability', 'langsmith_tracing', 'false').lower() == 'true'
        self.langsmithApiKey: str = _get('observability', 'langsmith_api_key', '')
        self.langsmithProject: str = _get('observability', 'langsmith_project', 'open-agent-harness')
        self.langsmithEndpoint: str = _get('observability', 'langsmith_endpoint',
                                           'https://api.smith.langchain.com')

        # 工具授权（谁能看到/调用哪些工具）：写类工具默认只给管理员；
        # 没权限的工具不进模型清单（见 tools/registry.py）
        self.toolAdminUsers: str = _get('agent', 'tool_admin_users', '')
        self.toolWriteScope: str = _get('agent', 'tool_write_scope', 'all')
        self.toolExecScope: str = _get('agent', 'tool_exec_scope', 'all')
        # 技能工具按需披露：true = 只有读过 SKILL.md 的技能才露出它的工具（技能多了再开）
        self.skillToolsLazy: bool = _get('agent', 'skill_tools_lazy', 'false').lower() == 'true'
        # 子代理：默认关（单场景用不到；批量/长链路任务时打开）
        self.enableSubagent: bool = _get('agent', 'enable_subagent', 'false').lower() == 'true'

        # MCP（外部工具生态）：**只能管理员在配置里声明**，用户不可自装
        self.mcpEnabled: bool = _get('mcp', 'enabled', 'false').lower() == 'true'
        self.mcpServers: str = _get('mcp', 'servers', '')
        self.mcpTimeout: int = _get('mcp', 'timeout', '30', int)

        # 本地登录口令（为空=免密）
        # 本地登录：口令为空 = 免密（内网演示 / 本机试用）
        self.loginPassword: str = _get('app', 'login_password', '')

        # 应用配置
        self.secretKey: str = _get('app', 'secret_key')

        # ⚠️ 测试登录（免密）：仅限开发/测试环境，生产必须为 false
        self.testLoginEnabled: bool = _get(
            'app', 'test_login_enabled', 'false',
            lambda v: str(v).strip().lower() in ('true', '1', 'yes', 'on'))

        # 新账号首次登录时自动分配的默认角色（role_code）
        # 只对**新建档**的账号生效，已有账号不动（管理员手工收回的角色不会被补回）
        self.defaultRole: str = _get('app', 'default_role', 'default')
        # 跨域：逗号分隔的允许来源；留空 = 同源部署，不发任何 CORS 头
        # （之前是 allow_origins=["*"] + credentials=true，等于对任意站点开放带凭据读取）
        self.corsOrigins: str = _get('app', 'cors_origins', '')
        # 接口报错是否回显原始异常（false=只给通用提示，避免泄露内部细节）
        self.errorDetail: bool = _get(
            'app', 'error_detail', 'false',
            lambda v: str(v).strip().lower() in ('1', 'true', 'yes', 'on'))


cfg: Configs = Configs()
