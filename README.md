# Open Agent Harness

**一个能把活干完的智能体平台**：对话是入口，工具是手脚，沙箱是工作台，权限是围栏。

![对话：流式输出 + Markdown 表格渲染](docs/assets/screenshot-chat.jpg)

不是又一个"ChatGPT 套壳"。它要解决的是四件事：模型**会说不会做**、敢给**手**却不敢放权、
经验**沉淀不下来**、出了事**没法复盘**。

## 适合谁

| 你的情况 | 结论 |
|---|---|
| 想把内部流程（查数据、读表格、出报表、答制度）交给一个会自己动手的 Agent | ✅ 合适 |
| 需要按角色管控"谁能用哪个工具"，并且要求事后能复盘 | ✅ 合适 |
| 只想拖拽连线画工作流、不写代码 | ❌ Dify / Coze 这类平台更省事 |
| 需要开箱即用的 SaaS、多租户隔离 | ❌ 本项目是**单机自托管**，一套实例给一个团队用 |

## 和拖拽式平台的区别

| | Open Agent Harness | 常见工作流平台 |
|---|---|---|
| 谁决定下一步 | **模型自己**（ReAct + 工具），可以中途改道 | 人预先连线，路径固定 |
| 执行环境 | 每个用户一个隔离沙箱，能真跑 Python、读写文件 | 多以 API 调用为主 |
| 权限边界 | 工具**按角色装配**，没权限的工具不进模型上下文 | 多以应用 / API Key 为界 |
| 复盘 | 工具轨迹 + 管理审计分开记，可逐条回看 | 以运行日志为主 |

## 它具体做了什么

| 问题 | 平台的做法 |
|---|---|
| 模型只会说不会做 | 每个用户一个隔离沙箱，能在里面读写文件、跑 Python、生成报表 |
| 给了手就不敢放权 | 工具**按角色装配**：没权限的工具根本不进模型清单，越权在物理上不可能 |
| 经验沉淀不下来 | 技能（SKILL.md）可上传、可停用、**可发布到平台供全员复用** |
| 出了问题没法复盘 | 每次工具调用都有轨迹，管理操作另有审计日志 |

## 核心能力

- **流式对话**：SSE 逐字输出，支持中断恢复（LangGraph checkpointer 落 PostgreSQL）
- **工具装配**：内置工具 + 插件工具 + 外部 MCP 服务（管理员在页面上填 streamable_http 地址即可接入别人的 MCP），按角色过滤后交给模型
- **沙箱执行**：OpenShell 容器隔离；闲置只停机不删文件，用户不存在才回收
- **二级技能**：我的技能（沙箱内）/ 平台技能（全员可见、一键添加副本）
- **文档知识库**：上传 PDF / Word / Excel / PPT，LlamaIndex 解析切分建索引（pgvector + 本地嵌入模型），
  向量 + 关键词混合检索；扫描件走 OCR。文档不出内网
- **长短期记忆**：用户偏好与业务知识跨会话保留，可查询、可删除
- **可观测**：轨迹（模型做了什么）+ 审计（人做了什么），两类记录分开存

## 界面

| 知识库：上传即索引，检索带出处 | 轨迹：每次工具调用都可回看 |
|---|---|
| ![知识库](docs/assets/screenshot-kb.jpg) | ![轨迹](docs/assets/screenshot-trace.jpg) |

| 技能与工具 | 工具管理（按角色授权） |
|---|---|
| ![技能与工具](docs/assets/screenshot-skills.jpg) | ![工具管理](docs/assets/screenshot-tools.jpg) |

| 用户管理 | 角色与工具授权 |
|---|---|
| ![用户管理](docs/assets/screenshot-users.jpg) | ![角色与工具授权](docs/assets/screenshot-roles.jpg) |

![登录页](docs/assets/screenshot-login.jpg)

> 截图与演示视频均来自真实运行实例（1920×1080）。

## 演示视频

真机操作 + 中文旁白，3 分 24 秒分章节走完：对话（含中途插话与中断续跑）、技能与工具、
知识库、用户与角色权限、MCP 接入，最后是开源信息。

▶ [docs/assets/demo.mp4](docs/assets/demo.mp4)（1080p30，8.0 MB）

> 片头与示意图为静态卡片，其余画面为线上实例实录。

## 快速开始

### 生产 / 单机部署（一条命令）

```bash
# 1. 先建库（只需一次）
#    业务库 MySQL（必需）+ 会话状态 PostgreSQL（必需：连不上服务起不来）
mysql -uroot -p -e "CREATE DATABASE open_agent_harness DEFAULT CHARSET utf8mb4;
  CREATE USER 'agent'@'%' IDENTIFIED BY '你的密码';
  GRANT ALL PRIVILEGES ON open_agent_harness.* TO 'agent'@'%'; FLUSH PRIVILEGES;"
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '你的PG密码';" \
  -c "CREATE DATABASE open_agent_harness;"

# 2. 安装（建 venv、装依赖、构建前端、生成 .env、装 systemd、探活）
sudo PA_DB_MYSQL_PASS='你的密码' PA_AGENT_PG_PASS='你的PG密码' \
     PA_LLM_DEEPSEEK_API_KEY='sk-你的key' \
     USE_CONDA=1 CONDA_PREFIX_DIR="$HOME/miniforge3" CONDA_ENV=oah \
     bash deploy/install.sh

# 3. 建管理员（密码默认 admin，可用 --password 指定一个强密码）
cd /opt/open-agent-harness && PYTHONPATH=. "$HOME/miniforge3/envs/oah/bin/python" \
     deploy/seed_admin.py --password '换成你的强密码'
```

装完打开 `http://<服务器IP>:5001/open-agent-harness/`。

脚本幂等，重跑即升级；`APP_DIR` / `PORT` / `SERVICE` 可用环境变量覆盖。

### 本地试跑

```bash
# Python 环境用 Miniforge（例如 $HOME/miniforge3/envs/oah），见 docs/Conda环境.md
conda create -y -n oah --override-channels -c conda-forge python=3.12
conda activate oah
pip install -r requirements.txt
export PA_DB_MYSQL_PASS=xxx PA_LLM_DEEPSEEK_API_KEY=sk-xxx
export PA_APP_SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
cd frontend && npm ci && npm run build && cd ..
uvicorn src.app_fastapi:app --host 0.0.0.0 --port 5001 --workers 4   # http://localhost:5001/open-agent-harness/
```

首次登录先建管理员：`PYTHONPATH=. "$HOME/miniforge3/envs/oah/bin/python" deploy/seed_admin.py --password '换成你的强密码'`（旧版的免密直登 `/test-login` 已删除）。

**部署**（环境、依赖、建库、嵌入服务、MCP 接入、验收、排障全在里面）见
[`docs/部署说明.md`](docs/部署说明.md)；
Python 环境细节见 [`docs/Conda环境.md`](docs/Conda环境.md)；
文档知识库（RAG + 扫描件 OCR）见 [`docs/知识库部署说明.md`](docs/知识库部署说明.md)。

> 架构设计、分层口径、运维细则、MCP 实现细节属于内部文档，不随这个仓库发布。

## 目录结构

```
src/
├── agent/            智能体内核：中间件、工具注册、沙箱后端、MCP 客户端
├── chat/             SSE 流式、上下文注入、产物回传
├── web/              FastAPI 视图层（routers/ 10 个模块 + 会话/鉴权/中间件）
├── infra/            RBAC、缓存、审计、维护任务、异步数据层
├── plugins/tools/    业务插件（自带 demo 示例，照着加自己的工具）
└── app_fastapi.py    应用装配（uvicorn 入口）
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

## 项目状态

- 单机自托管，一套实例服务一个团队；目前**没有**多租户、SSO、分布式部署
- 已在真实环境跑通：对话 / 工具 / 沙箱 / 权限 / 知识库（含 OCR）/ MCP / 审计轨迹
- 质量门：143 个后端单测 + 前端纯函数测试 + 静态检查（`bash test/run_all.sh`）
- 检索有一组 7 条冒烟评测（`test/eval/run_eval.py`），改参数前后各跑一次
- 已知瓶颈：嵌入在 CPU 上跑（bge-m3 int8），大文档入库按分钟计；要快就上 GPU 或调 `kb_ingest_workers`

## 技术栈

FastAPI · LangGraph 1 · deepagents · LangChain 1 · Vue 3 · MySQL · PostgreSQL（必需）· Redis（可选）· OpenShell（可选）

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。

安全问题请勿公开提 issue，按 [SECURITY.md](SECURITY.md) 的流程反馈。
