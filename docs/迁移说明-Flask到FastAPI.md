# 迁移说明：Flask → FastAPI + 全链路异步化

> 2026-11 完成。本文记录**为什么改、改了什么、踩过哪些坑、怎么验证、怎么回滚**。
> 面向后续维护者：如果你只想跑起来，看 [`部署说明.md`](部署说明.md)；想知道"这行为什么长这样"，看这里。

---

## 1. 结论

服务端从 **Flask 3 + gunicorn（gthread，6×4）** 换成 **FastAPI + uvicorn（多进程）**，
并把整条对话链路（流式、checkpointer、数据库、沙箱、Redis 缓存）改成**原生异步**。

效果：一条 SSE 对话不再独占一个线程。改造前每条流从头到尾占住一个 worker 线程
（上限 1300 秒），240 个线程槽就是天花板；改造后同样的并发只占 `asyncio.Task`，
线程数基本恒定。

**对外行为不变**：URL 前缀、路由、会话 cookie 格式、`/api/version` 口径全部保持原样，
所以切流时**用户不需要重新登录**。

---

## 2. 六个阶段

| 阶段 | 内容 | 关键文件 |
|---|---|---|
| **P0** 骨架 | 会话中间件、鉴权依赖、请求中间件、登录/登出/静态/探针 | `src/web/{session,security,middleware,http,pages}.py`、`src/app_fastapi.py` |
| **P1** 路由 | 10 个 controller（62 个端点）搬到 `src/web/routers/` | `routers/*.py` |
| **P2** 删 Flask | `src/app.py`、`controller/**`、`AuthUtil.py`、`AgentApp.py`、`gun_conf.py`、`gun_warm.py` 全删 | — |
| **P3** 数据层 | `AsyncSession` + `asyncmy`；DAO 全 async；请求级 session 由中间件管理 | `src/infra/db/database.py`、`src/infra/dao/` |
| **P4** 流式 | `agent.astream` + `AsyncPostgresSaver`；14 个函数 async；24 处阻塞调用 `to_thread`；收尾治理从后台线程改后台 task | `src/chat/stream.py`、`src/agent/main_agent.py`、`src/infra/CheckpointerManager.py`、4 个中间件补 `awrap_*` |
| **P5** 其余 IO | 沙箱双 API（`_acli/aexec/aupload/adownload/acollect_artifacts`）、`RedisUtil.aclient`、`CacheUtil.aget`、`rbac.ais_disabled` | `src/agent/backends/sandbox.py`、`src/infra/utils/*`、`src/infra/RbacService.py` |

启动方式：`PYTHONPATH=. .venv/bin/python -m uvicorn src.app_fastapi:app --host 0.0.0.0 --port 5001 --workers 4`

---

## 3. 行为契约（改动时**不能破**）

1. **会话 cookie**：名字 `session`、`[app] secret_key` 同一把钥匙、Flask 的
   TaggedJSON+签名格式。改这里 = 所有用户要重新登录。
2. **URL 前缀**：`/open-agent-harness`，业务接口都在 `/open-agent-harness/api/...`。
3. **未登录语义**：接口返回 `401 {"error":"未登录","login_url":...}`；页面 302 到登录页（带 `next`）。
4. **上传上限**：100MB（请求体上限留 8MB multipart 余量），超限返回 JSON 而不是框架默认页。
5. **`/api/version`**：返回 `{ok, frontend_js, index_mtime}`，前端靠它判断是否要强制刷新。
6. **`ask_user` 交互**：`interrupt/resume` 语义与 SSE 事件顺序（`content` → `typing:false` → `done`）不变。

---

## 4. 踩过的坑（都是真实发生过的）

### 4.1 新版 FastAPI 把 `include_router` 注册成 mount

**现象**：路由契约检查报"62 个端点全部缺失"，但接口明明是通的。
**根因**：FastAPI 0.16x / starlette 1.10 下，`include_router` 不再把子路由平铺进
`app.routes`，扫出来只有系统路由。
**修法**：契约检查改用 `app.openapi()["paths"]`（会把 mount 里的路由展开，路径参数已是 `{name}`）。
**顺带**：`app.routes` 在旧版本可用，别写成唯一判据。

### 4.2 登录页把 Jinja 语法漏到了页面上

**现象**：登录页显示 `{{ hint }}`、`{% if need_password %}` 原文。
**根因**：把 `AuthUtil._LOGIN_HTML` 抽到 `web/pages.py` 时只搬了模板字符串，
Flask 那边是 `render_template_string(...)` 渲染的，FastAPI 里直接 `HTMLResponse(LOGIN_HTML)`。
**修法**：`Template(LOGIN_HTML).render(need_password=..., hint=..., error=...)`，
变量口径与 Flask 完全一致；失败路径也渲染回登录页（而不是一句纯文本）。

### 4.3 `psycopg_pool.PoolTimeout`：PG 密码为空 + 目标库不存在

**现象**：FastAPI 启动即失败，`pool initialization incomplete after 10 sec`。
**根因两层**：
- `config/configs.cfg` 的 `[agent] pg_pass` 是**空的**（部署配置不在 git 里）；
- 更关键的是 `open_agent_harness` **这个库根本没建**。

**值得注意**：改造前的 Flask 版也是同样配置，它没报错是因为**checkpointer 初始化失败被吞了**，
也就是说跨轮记忆一直没真正落 PostgreSQL，只是失败得很安静。
**修法**：`CREATE DATABASE open_agent_harness OWNER postgres` + 补 `pg_pass`。

### 4.4 `frontend/dist` 不在 git 里

**现象**：切到 FastAPI 后首页 404、`/api/version` 返回 `frontend_js: ""`。
**修法**：部署时把 `frontend/dist` 一起拷过去（`npm run build` 产物，不进版本库）。

### 4.5 Starlette 的 `JSONResponse` 不会自动序列化 `datetime`

**现象**：知识列表接口 500，`Object of type datetime is not JSON serializable`。
**根因**：Flask 的 `jsonify` 会自动把 `datetime` 转成 RFC 822；Starlette 的 `JSONResponse`
直接 `json.dumps`，不走 `jsonable_encoder`。
**修法**：所有返回 DB 行的接口在 JSON 边界过一层 `jsonable_encoder(...)`。
**新接口注意**：只要返回 pymysql/SQLAlchemy 的行，就要想到这条。

### 4.6 流式写入不能用「请求级」DB session

**现象**（yixin 同源代码上实测）：高并发下 `greenlet_spawn has not been called;
can't call await_only() here`，**回复没落库**——界面上看得到，刷新就丢。
**根因**：流式生成器跑在 `StreamingResponse` 的独立任务里，与请求级 `AsyncSession`
不在同一个 greenlet 上下文。
**修法**：流式链路里的落库一律 `async with session_scope():` 用独立 session。

### 4.7 沙箱异步命令的两个边界

- **截断必须杀子进程**：读到超过上限只 `break` 会让子进程写满 64KB 管道缓冲后阻塞，
  `proc.wait()` 永不返回 → 命令假死到超时、输出全丢。同步版靠关管道发 EPIPE，异步版要显式 `kill`。
- **超时分支要等 drain 结束**再抛，否则 transport 在解释器退出时 `__del__` 报 `Event loop is closed`。

### 4.8 部署目录与 git 脱节

**现象**：服务器上的 `/data1/open-agent-harness` 有 95 个已改 + 4 个已删文件，
HEAD 还是若干天前的 `chore: initial open-source release`——它是**拷文件部署**的，不是从 git 拉的。
**影响**：直接 `git checkout`/`git pull` 会把这 95 个改动全部覆盖掉。
**修法**：新代码部署在**独立目录**（`/data1/oah-fastapi`，git worktree），
只把 `config/`、`.env`、`frontend/dist` 从老目录拷过去；老目录原样保留。

---

## 5. 部署要点（容易漏的三样）

| 项 | 说明 |
|---|---|
| `config/`（含 `configs.cfg`） | **不在 git 里**（或为空模板），必须从原部署目录拷。缺 `pg_pass` 起不来 |
| `.env` | 模型 key、库密码等；systemd 用 `EnvironmentFile` 注入 |
| `frontend/dist` | 前端构建产物，不进版本库；缺了首页 404 |

**PostgreSQL 需要单独的库**（本例 `open_agent_harness`），checkpointer 会在首次启动时 `setup()` 建表。
多 worker 并发首次建表可能撞 `UniqueViolation`，代码里有 3 次重试（幂等）。

worker 数：每个 worker 常驻约 300~400MB（含 langgraph 依赖），按内存给，默认 4。

---

## 6. 验证矩阵（做完这一步就查这些）

| 项 | 命令/方法 | 结果 |
|---|---|---|
| 路由完整性 | `python test/unit/route_contract.py` | 业务端点 60 ≥ 基线 58，关键接口抽查全在 → PASS |
| 冒烟（读+写） | 见 `deploy/verify.sh` 与本文附录 | 21 项：20 通过；`/api/audit` 403 是**正确行为**（该账号非管理员） |
| SSE 对话 | `curl -N` 打 `/api/chat` | 200、`content` → `typing:false` → `done` 顺序正确 |
| 登录页 | 浏览器打开 `/login` | 无 `{{ }}`/`{% %}` 残留，标题正确 |
| 会话持久化 | 多轮对话后刷新 | 历史在（checkpointer 真落库） |
| 静态资源 | 首页 + `assets/*.js` | 200 |
| 未登录 | 匿名打 `/api/*` | 401 + `login_url` |
| **未验证** | 沙箱异步命令 | 该部署 `openshell_enabled=false`，`aexec` 直接返回"沙箱功能未启用"。代码链路自洽但**没跑过真实命令** |

---

## 7. 回滚

改造前的 Flask 版本在提交 `8e7f4af`（`chore: initial open-source release` 之后那条主线）。

```bash
# 1) 恢复 gunicorn 单元（部署时已备份）
cp /root/oah-flask-backup.service /etc/systemd/system/open-agent-harness.service
# 2) 代码回到 Flask 版
cd /data1/oah-fastapi && git checkout 8e7f4af
systemctl daemon-reload && systemctl restart open-agent-harness
```

会话 cookie 是双向兼容的，**回滚也不会把用户踢下线**。

---

## 8. 后续可选（没做，不影响功能）

- `ToolMetaService` / `knowledge` / `user_prefs` / `Maintenance` 仍是 pymysql 同步直连
  （都在同步端点或 `to_thread` 里，不在每请求关键路径）。
- 沙箱的 `get_or_create` 等生命周期方法仍是同步的（冷路径，用 `to_thread` 包）。
- `warm` / `logout` 一个函数挂两个方法，OpenAPI 会报 duplicate operation ID（文档已禁用，无害）。
