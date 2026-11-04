# Open Agent Harness

> 一个能把活干完的智能体平台：对话为入口，工具为手脚，沙箱为工作台，权限为围栏。

不是"又一个 ChatGPT 套壳"。它解决的是四件事：

| 问题 | 平台的做法 |
|---|---|
| 模型只会说不会做 | 每个用户一个隔离沙箱，能在里面读写文件、跑 Python、生成报表 |
| 给了手就不敢放权 | 工具**按角色装配**：没权限的工具根本不进模型清单，越权在物理上不可能 |
| 经验沉淀不下来 | 技能（SKILL.md）可上传、可停用、**可发布到平台供全员复用** |
| 出了问题没法复盘 | 每次工具调用都有轨迹，管理操作另有审计日志 |

## 核心能力

- **流式对话**：SSE 逐字输出，支持中断恢复（LangGraph checkpointer 落 PostgreSQL）
- **工具装配**：内置工具 + 插件工具 + 外部 MCP 服务，按角色过滤后交给模型
- **沙箱执行**：OpenShell 容器隔离；闲置只停机不删文件，用户不存在才回收
- **二级技能**：我的技能（沙箱内）/ 平台技能（全员可见、一键添加副本）
- **长短期记忆**：用户偏好与业务知识跨会话保留，可查询、可删除
- **可观测**：轨迹（模型做了什么）+ 审计（人做了什么），两类记录分开存

## 快速开始

### 生产 / 单机部署（一条命令）

```bash
# 1. 先建库（只需一次）
mysql -uroot -p -e "CREATE DATABASE open_agent_harness DEFAULT CHARSET utf8mb4;
  CREATE USER 'agent'@'%' IDENTIFIED BY '你的密码';
  GRANT ALL PRIVILEGES ON open_agent_harness.* TO 'agent'@'%'; FLUSH PRIVILEGES;"

# 2. 安装（建 venv、装依赖、构建前端、生成 .env、装 systemd、探活）
sudo PA_DB_MYSQL_PASS='你的密码' PA_LLM_DEEPSEEK_API_KEY='sk-你的key' \
     bash deploy/install.sh
```

装完打开 `http://<服务器IP>:5001/open-agent-harness/`。

脚本幂等，重跑即升级；`APP_DIR` / `PORT` / `SERVICE` 可用环境变量覆盖。

### 本地试跑

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PA_DB_MYSQL_PASS=xxx PA_LLM_DEEPSEEK_API_KEY=sk-xxx
export PA_APP_SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
cd frontend && npm ci && npm run build && cd ..
python AgentApp.py                                 # http://localhost:5001/open-agent-harness/
```

开发环境可用免密直登：`/open-agent-harness/test-login`（对外开放务必关闭）。

**逐步骤部署、可选组件（沙箱 / MCP / PG / Redis）、设管理员、Nginx、运维、排障**
见 [`docs/部署说明.md`](docs/部署说明.md)。

## 目录结构

```
src/
├── agent/            智能体内核：中间件、工具注册、沙箱后端、MCP 客户端
├── chat/             SSE 流式、上下文注入、产物回传
├── controller/       REST 接口（10 个蓝图）
├── infra/            RBAC、缓存、审计、维护任务
├── plugins/tools/    业务插件（自带 demo 示例，照着加自己的工具）
└── app.py            Flask 应用装配
frontend/             Vue 3 单页应用
config/               Configs.py + configs.cfg（只放非敏感项，密钥走环境变量）
deploy/               systemd 单元、验收脚本、维护定时器
docs/                 部署说明
```

## 加一个自己的工具

不用改内核，加一个目录即可（详见 `src/plugins/tools/demo/`）：

```
src/plugins/tools/hello/
├── manifest.json   声明工具名、模块、risk(read/write/delete)
├── hello.py        @tool 函数（要有 docstring，模型靠它判断何时调用）
└── __init__.py
```

重启后在「工具管理」里就能看到；再把它分配给某个角色，模型才会拿到它。

## 技术栈

Flask 3 · LangGraph 1 · deepagents · LangChain 1 · Vue 3 · MySQL · PostgreSQL · Redis（可选）· OpenShell（可选）

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。

安全问题请勿公开提 issue，按 [SECURITY.md](SECURITY.md) 的流程反馈。
