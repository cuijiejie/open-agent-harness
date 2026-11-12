# 测试

```bash
bash test/run_all.sh        # 单元 + 前端纯函数 + 静态检查，一条命令
```

| 目录 | 内容 | 依赖 |
|---|---|---|
| `unit/` | 后端单元测试（unittest，零外部依赖） | 无 |

单跑某个文件：`python -m unittest test.unit.test_core -v`（`-t .` 让包内 `__init__.py` 生效，测试会自带占位环境变量） |
| `js/` | 前端纯函数测试（node） | node |
| `integration/` | 端到端自检（不参与 run_all）：`kb_e2e.py` 需真 PG+pgvector | PG + 嵌入服务 |
| `mcp_weather/` | MCP 示例服务（streamable_http）+ 离线自检 `check.py` | 无网络也可跑（stub） |
| `tools/` | 静态检查脚本（未定义名字等） | 无 |
| `hooks/` | 可选的 git pre-commit 钩子 | — |

约定：**提交进仓库的测试必须能在干净环境跑通**（不连 MySQL/Redis/沙箱/外网）。

`integration/` 里的脚本不进 `run_all.sh`（要么连真库、要么起进程），按需手动跑：
`PYTHONPATH=. <env>/bin/python test/mcp_weather/check.py`。
`mcp_weather/` 里的服务既是自检用的桩（`MCP_WEATHER_STUB=1`），也是线上 `pa-mcp-weather` 的接入示例。
