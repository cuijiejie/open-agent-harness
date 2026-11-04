# deploy

部署与运维的**唯一文档**在 [`../docs/部署说明.md`](../docs/部署说明.md)。

本目录只放可直接用的文件：

| 文件 | 用途 |
|---|---|
| `install.sh` | **一键安装/升级**（venv + 依赖 + 前端构建 + .env + systemd + 探活） |
| `open-agent-harness.service` | 应用服务单元（gunicorn，gthread 6×4） |
| `pa-logs.service/.timer` | 每天 03:00 UTC 日志切分 + 清理 |
| `pa-artifacts.service/.timer` | 每天 03:10 UTC 交付物 90 天清理 |
| `pa-sandbox-idle.service/.timer` | 每天 03:20 UTC 闲置沙箱 stop（只停不删） |
| `pa-sandbox-orphan.service/.timer` | 每天 03:30 UTC 孤儿沙箱删除 |
| `install-maintenance-timers.sh` | 一键安装上面 4 组 timer |
| `verify.sh` | 发版后验收（服务状态 / 健康 / 前端包 / 未定义名字 / 启动 ERROR） |
| `run_bg.sh` | 长任务脱离当前会话跑（setsid） |
