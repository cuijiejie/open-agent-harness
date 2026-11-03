# 测试

```bash
bash test/run_all.sh        # 单元 + 前端纯函数 + 静态检查，一条命令
```

| 目录 | 内容 | 依赖 |
|---|---|---|
| `unit/` | 后端单元测试（unittest，零外部依赖） | 无 |

单跑某个文件：`python -m unittest test.unit.test_core -v`（`-t .` 让包内 `__init__.py` 生效，测试会自带占位环境变量） |
| `js/` | 前端纯函数测试（node） | node |
| `tools/` | 静态检查脚本（未定义名字等） | 无 |
| `hooks/` | 可选的 git pre-commit 钩子 | — |

约定：**提交进仓库的测试必须能在干净环境跑通**（不连 MySQL/Redis/沙箱/外网）。
集成与端到端测试请对着自己部署的实例做，不要塞进这里。
