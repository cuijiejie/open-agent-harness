# 用 Miniforge 管理 Python 环境（可选）

> 默认部署用 `python3.12 -m venv .venv`，见 [`部署说明.md`](部署说明.md)。
> 本文是**把 Python 环境换成 Miniforge** 的完整操作手册，用于需要 conda 生态
> （科学计算、预编译二进制、跨语言依赖）的场景。
>
> 适用：Ubuntu 22.04 / 24.04，aarch64 或 x86_64。
> 现状：本文档描述的切换**尚未在现有部署上执行**，默认仍是 venv。

---

## 0. 先想清楚要不要换

| 对比 | venv + pip | Miniforge（conda-forge） |
|---|---|---|
| 装本项目依赖 | 全部是 pip wheel，顺利 | 同样走 pip，**没有区别** |
| 装科学计算包 | 部分包在 aarch64 上没有 wheel，要现场编译 | conda-forge 有预编译包，省事 |
| 磁盘 | 约 1 GB | 约 2.5~3 GB（base 环境 + 项目环境 + 包缓存） |
| 启动开销 | 无 | 无（直接用环境里的 python，不经过 conda activate） |
| 维护复杂度 | 低 | 多一层：conda 与 pip 混用要守规矩 |

**结论**：只为跑这个项目，venv 足够；换 Miniforge 的收益是"以后装别的包方便"。
不要指望性能提升。

---

## 1. 安装 Miniforge3

```bash
# 下载（aarch64；x86_64 把文件名里的 aarch64 换成 x86_64）
cd /tmp
curl -fLO https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh

# 校验（可选但建议）：和发布页的 sha256 对一下
sha256sum Miniforge3-Linux-aarch64.sh

# 静默安装到 /opt/miniforge3（-b 免交互，-p 指定前缀）
sudo bash Miniforge3-Linux-aarch64.sh -b -p /opt/miniforge3

# 归属交给运行服务的账号（本部署里服务以 root 运行，装到 /opt 即可）
sudo /opt/miniforge3/bin/conda --version
```

> **不要**跑 `conda init`。它改的是交互式 shell 的 rc 文件，systemd 不读那些；
> 后面直接用绝对路径调环境的 python，最稳。

---

## 2. 建项目环境

```bash
sudo /opt/miniforge3/bin/conda create -y -n oah python=3.12
sudo /opt/miniforge3/bin/conda run -n oah python -V      # 期望 Python 3.12.x
```

环境目录：`/opt/miniforge3/envs/oah`，解释器：`/opt/miniforge3/envs/oah/bin/python`。

---

## 3. 装项目依赖

```bash
cd /opt/open-agent-harness
sudo /opt/miniforge3/envs/oah/bin/pip install -U pip
sudo /opt/miniforge3/envs/oah/bin/pip install -r requirements.txt
```

规矩（混用 conda 与 pip 时）：

1. **先 conda 装系统级/二进制依赖，再 pip 装纯 Python 包**，反过来容易把包覆盖坏；
2. 不要在 base 环境里装项目依赖；
3. 装完用 `pip check` 验一遍依赖冲突。

```bash
sudo /opt/miniforge3/envs/oah/bin/pip check
```

---

## 4. 把服务指向新环境

改 systemd 单元里的 `ExecStart`（`deploy/open-agent-harness.service` 里是模板，
线上在 `/etc/systemd/system/open-agent-harness.service`）：

```diff
- ExecStart=/opt/open-agent-harness/.venv/bin/python -m uvicorn \
+ ExecStart=/opt/miniforge3/envs/oah/bin/python -m uvicorn \
    src.app_fastapi:app \
    --host 0.0.0.0 --port 5001 --workers 3 \
    --no-server-header
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart open-agent-harness
sudo systemctl status open-agent-harness --no-pager
```

**不需要**设置 `CONDA_PREFIX`、也不用 `conda activate`：绝对路径的 python 自带
正确的 `sys.prefix`，conda 环境能正常找到自己的 `site-packages` 和动态库。

---

## 5. 验收

```bash
cd /opt/open-agent-harness

# 1) 确认服务真的在用 conda 的 python
sudo systemctl show open-agent-harness -p ExecStart | tr ' ' '\n' | grep -m1 python

# 2) 健康检查
curl -s http://127.0.0.1:5001/open-agent-harness/api/health
#   期望 {"ok":true,"db":"ok","redis":"ok"}

# 3) 项目自带的验收脚本（含 104 个单测）
PY=/opt/miniforge3/envs/oah/bin/python bash deploy/verify.sh

# 4) 端到端：登录后发一条对话，确认模型与工具链正常
```

任何一步不过，按第 7 节回滚。

---

## 6. 让 `deploy/install.sh` 支持（可选）

现在脚本写死 `"$PY" -m venv .venv`。想长期用 conda，建议加一个开关，
而不是每次手工改单元文件。改法（示意）：

```bash
# install.sh 顶部
USE_CONDA=${USE_CONDA:-0}
CONDA_PREFIX_DIR=${CONDA_PREFIX_DIR:-/opt/miniforge3}
CONDA_ENV=${CONDA_ENV:-oah}

# 第 2 步「Python 虚拟环境 + 依赖」替换为：
if [ "$USE_CONDA" = "1" ]; then
  [ -x "$CONDA_PREFIX_DIR/bin/conda" ] || die "找不到 $CONDA_PREFIX_DIR/bin/conda（先装 Miniforge）"
  "$CONDA_PREFIX_DIR/bin/conda" env list | grep -qE "^$CONDA_ENV " \
    || "$CONDA_PREFIX_DIR/bin/conda" create -y -n "$CONDA_ENV" python=3.12
  PY_BIN="$CONDA_PREFIX_DIR/envs/$CONDA_ENV/bin/python"
  "$PY_BIN" -m pip install -q -U pip
  "$PY_BIN" -m pip install -q -r requirements.txt
else
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  PY_BIN="$APP_DIR/.venv/bin/python"
  "$PY_BIN" -m pip install -q -U pip
  "$PY_BIN" -m pip install -q -r requirements.txt
fi

# 第 6 步生成单元文件时，用 $PY_BIN 替换 .venv 路径：
sed -e "s#/opt/open-agent-harness#$APP_DIR#g" \
    -e "s#/opt/open-agent-harness/.venv/bin/python#$PY_BIN#g" \
    -e "s#--port 5001#--port $PORT#g" \
    -e "s#--workers 4#--workers $WORKERS#g" \
    deploy/open-agent-harness.service > "/etc/systemd/system/$SERVICE.service"
```

调用方式：

```bash
sudo USE_CONDA=1 PA_DB_MYSQL_PASS=... PA_AGENT_PG_PASS=... \
     PA_LLM_DEEPSEEK_API_KEY=... bash deploy/install.sh
```

> 这段是**设计稿**，没有提交进 `install.sh`。真要启用，先把上面的验收跑通，
> 再改脚本，避免"脚本一改、新机器装不上"。

---

## 7. 回滚到 venv

```bash
# 1) 单元文件改回 venv
sudo sed -i 's#^ExecStart=/opt/miniforge3/envs/oah/bin/python#ExecStart=/opt/open-agent-harness/.venv/bin/python#' \
     /etc/systemd/system/open-agent-harness.service
sudo systemctl daemon-reload && sudo systemctl restart open-agent-harness

# 2) 确认 .venv 还在（当初没删的话）
ls -l /opt/open-agent-harness/.venv/bin/python

# 3) 删掉 conda（可选，省 3 GB）
sudo rm -rf /opt/miniforge3
```

venv 与 conda 环境**互不影响**，可以并存，切换只是改一行 `ExecStart`。

---

## 8. 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 服务启动报 `libstdc++.so.6: version 'GLIBCXX_3.4.32' not found` | conda 环境自带的 libstdc++ 比系统旧/新，被优先加载 | `sudo /opt/miniforge3/envs/oah/bin/pip install --force-reinstall psycopg[binary]` 或设 `LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu` |
| 服务找不到模块，但手工 `conda run` 正常 | 单元文件里还残留 `--workers`、`PYTHONPATH` 指向旧路径 | `systemctl show -p ExecStart`、`Environment` 逐项核对 |
| 装包后行为变了 | conda 与 pip 装了同一个包的两个版本 | `pip check`；必要时 `conda list` 看来源 |
| 磁盘暴涨 | conda 的包缓存 `pkgs/` 不自动清 | `conda clean -a -y` |
| `conda` 命令在 systemd 里不生效 | systemd 不走 shell 的 rc，`conda activate` 无效 | 一律用环境的**绝对路径** python，别用 activate |

---

## 9. 参考

- Miniforge（conda-forge 社区发行版，默认只用 conda-forge 频道）：<https://github.com/conda-forge/miniforge>
- 频道与商业授权说明：Anaconda 的 `defaults` 频道对 200 人以上组织收费，conda-forge 无此限制
- 本项目默认部署方式：[`部署说明.md`](部署说明.md)
