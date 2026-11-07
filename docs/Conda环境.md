# 用 Miniforge 管理 Python 环境

> 本部署的 Python 环境用 **Miniforge**（conda-forge 社区发行版），不用 `venv`。
> 位置：`/home/cuijie/miniforge3`（base）+ `/home/cuijie/miniforge3/envs/oah`（项目环境，Python 3.12.16）。
> 服务直接调用 `/home/cuijie/miniforge3/envs/oah/bin/python -m uvicorn ...`，
> **不经过 `conda activate`**。
>
> 适用：Ubuntu 22.04 / 24.04，aarch64 或 x86_64。
> Miniconda 是等价替代（装法见 §7），本项目不用它。

---

## 0. 为什么用 Miniforge

| 对比 | venv + pip | Miniforge（conda-forge） |
|---|---|---|
| 装本项目依赖 | 全部 pip wheel，可用 | 同样走 pip，可用 |
| 装其它科学计算包 | aarch64 上部分包没 wheel，要现场编译 | conda-forge 有预编译包 |
| 频道授权 | — | **只用 conda-forge，不碰 Anaconda 的 `defaults`**，无商业限制 |
| 磁盘 | 约 480 MB | base 约 950 MB + 环境约 1 GB |
| 运行时开销 | 无 | 无（直接用环境里的 python） |

选 Miniforge 而不是 Miniconda，是因为它**天生只用 conda-forge**：不会弹 Anaconda
服务条款（conda 26 起 `defaults` 频道必须先接受 ToS，且对 200 人以上组织收费）。

---

## 1. 安装 Miniforge

```bash
cd /tmp
curl -fsSLO https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh
# x86_64 机器把 aarch64 换成 x86_64

bash Miniforge3-Linux-aarch64.sh -b -p "$HOME/miniforge3"
"$HOME/miniforge3/bin/conda" --version       # 实测 26.9.4
```

`-b` 免交互、`-p` 指定前缀。**不要跑 `conda init`**——它改的是交互式 shell 的 rc，
systemd 不读那些文件；后面一律用绝对路径调环境的 python。

> 装到哪个目录由你定。本机装在 `$HOME/miniforge3`；如果希望多用户共用，
> 可以 `sudo ... -p /opt/miniforge3`，服务同样能用（root 跑服务时更规整）。

---

## 2. 建项目环境

```bash
"$HOME/miniforge3/bin/conda" create -y -n oah \
     --override-channels -c conda-forge python=3.12
"$HOME/miniforge3/envs/oah/bin/python" -V       # 实测 3.12.16
```

- `--override-channels -c conda-forge`：显式只用 conda-forge，任何发行版下都不会触发 ToS。
- 环境目录：`<miniforge>/envs/oah`，解释器：`<miniforge>/envs/oah/bin/python`。

---

## 3. 装项目依赖

```bash
cd /opt/open-agent-harness
"$HOME/miniforge3/envs/oah/bin/python" -m pip install -U pip
"$HOME/miniforge3/envs/oah/bin/python" -m pip install -r requirements.txt

# 验一遍
"$HOME/miniforge3/envs/oah/bin/python" -m pip check
"$HOME/miniforge3/envs/oah/bin/python" -c "import fastapi, uvicorn, asyncmy, psycopg, pandas; print('ok')"
```

混用 conda 与 pip 的三条规矩：

1. 先 conda 装二进制/系统级依赖，再 pip 装纯 Python 包，反过来容易互相覆盖；
2. 不在 base 环境里装项目依赖；
3. 装完跑 `pip check` 看冲突。

---

## 4. 用 `install.sh` 切换（推荐，别手改单元文件）

```bash
cd /home/cuijie/open-agent-harness        # 源码目录
sudo APP_DIR=/opt/open-agent-harness PORT=5001 WORKERS=3 \
     USE_CONDA=1 SKIP_FRONTEND=1 \
     CONDA_PREFIX_DIR=/home/cuijie/miniforge3 CONDA_ENV=oah \
     bash deploy/install.sh
```

它会：

1. 检查 `$CONDA_PREFIX_DIR/bin/conda` 存在，缺环境则用 conda-forge 建 `oah`；
2. 用环境里的 python 装 `requirements.txt`；
3. 把单元文件的 `ExecStart` 指到 `<prefix>/envs/oah/bin/python`；
4. 往 `.env` 写三行标记，**记住这台机器的选择**。

相关变量（都能从 `.env` 读回，重跑不用再传）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `USE_CONDA` | 读 `PA_INSTALL_USE_CONDA`，没有则 0 | 1 = 用 conda 环境，不建 venv |
| `CONDA_PREFIX_DIR` | 读 `PA_INSTALL_CONDA_PREFIX`，没有则 `/opt/miniforge3` | conda 安装根目录 |
| `CONDA_ENV` | 读 `PA_INSTALL_CONDA_ENV`，没有则 `oah` | 环境名 |

**为什么要记这三个值**：否则以后任何人重跑 `install.sh` 都会按默认去建 `.venv`、
把服务切回 venv —— 而 `.venv` 已经删了，等于白装一遍。

---

## 5. 验收

```bash
cd /opt/open-agent-harness

# 1) 确认服务真的在用 conda 的 python
systemctl show open-agent-harness -p ExecStart | tr ' ' '\n' | grep -m1 python
#   期望 /home/cuijie/miniforge3/envs/oah/bin/python

# 2) 健康检查
curl -s http://127.0.0.1:5001/open-agent-harness/api/health
#   期望 {"ok":true,"db":"ok","redis":"ok"}

# 3) 项目验收脚本（含 104 个单测）——必须显式指定解释器
PY=/home/cuijie/miniforge3/envs/oah/bin/python bash deploy/verify.sh
#   期望 ✅ 验收通过

# 4) 端到端：登录后发一条对话，确认模型 + 沙箱工具链正常
```

实测（2026-11-07，Ubuntu 24.04 aarch64）：以上全部通过，104 单测 `OK (skipped=1)`，
对话里沙箱执行返回正常。

> `deploy/verify.sh` 默认用系统 `python`，而系统里没有项目依赖，必须传 `PY=`。

---

## 6. 本机现状

| 项 | 值 |
|---|---|
| conda | `/home/cuijie/miniforge3/bin/conda`，26.9.4 |
| 项目环境 | `/home/cuijie/miniforge3/envs/oah`，Python 3.12.16 |
| 服务解释器 | `/home/cuijie/miniforge3/envs/oah/bin/python`（`--workers 3`） |
| `.env` 标记 | `PA_INSTALL_USE_CONDA=1` / `PA_INSTALL_CONDA_PREFIX=/home/cuijie/miniforge3` / `PA_INSTALL_CONDA_ENV=oah` |
| 旧 venv | `/opt/open-agent-harness/.venv` 已删除（478 MB） |
| 旧 Miniconda | `/opt/miniconda3` 已删除（2.0 GB） |

> 服务以 root 运行，读的是 cuijie 家目录下的 conda 环境。Ubuntu 的 `/home/cuijie`
> 是 `750`，root 不受影响；但如果以后改了家目录权限或换用非 root 运行服务，
> 需要重新评估——必要时把 Miniforge 装到 `/opt/miniforge3` 更稳。

---

## 7. 改用 Miniconda（等价替代）

Miniconda 与 Miniforge 用法完全一样，只是安装包不同、且**必须**用
`--override-channels -c conda-forge`，否则 conda 26 会因 `defaults` 频道未接受
ToS 而失败：

```bash
curl -fsSLO https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh
bash Miniconda3-latest-Linux-aarch64.sh -b -p "$HOME/miniconda3"
"$HOME/miniconda3/bin/conda" create -y -n oah --override-channels -c conda-forge python=3.12
```

之后把 `CONDA_PREFIX_DIR` 指到 `$HOME/miniconda3` 重跑 `install.sh` 即可。

---

## 8. 回滚到 venv

```bash
cd /home/cuijie/open-agent-harness
sudo APP_DIR=/opt/open-agent-harness USE_CONDA=0 SKIP_FRONTEND=1 bash deploy/install.sh
sudo sed -i '/^PA_INSTALL_USE_CONDA=/d;/^PA_INSTALL_CONDA_PREFIX=/d;/^PA_INSTALL_CONDA_ENV=/d' \
     /opt/open-agent-harness/.env
```

conda 本身可以留着（和 venv 不冲突），也可以删掉省 2 GB。

---

## 9. 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `CondaToSNonInteractiveError: Terms of Service have not been accepted` | 用了 Anaconda 的 `defaults` 频道（Miniconda 默认） | 加 `--override-channels -c conda-forge`，或直接用 Miniforge |
| 服务启动报 `libstdc++.so.6: version GLIBCXX_x not found` | conda 自带的 libstdc++ 压过系统库 | `sudo LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu systemctl restart ...` |
| 手工 `conda run` 正常、服务不行 | 单元文件残留旧路径 / `PYTHONPATH` 不对 | `systemctl show -p ExecStart -p Environment` 逐项核对 |
| 重跑 `install.sh` 后变回 venv | `.env` 里缺 `PA_INSTALL_*` 三个标记 | 重跑时显式传 `USE_CONDA=1`，脚本会补上标记 |
| 磁盘暴涨 | conda 包缓存 `pkgs/` 不自动清 | `"$HOME/miniforge3/bin/conda" clean -a -y` |
| `conda activate` 在 systemd 里不生效 | systemd 不读 shell rc | 一律用环境的绝对路径 python |
| 非 root 运行服务时读不到环境 | 环境在家目录、权限 750 | 把 Miniforge 装到 `/opt/miniforge3`，或调整权限 |

---

## 10. 参考

- Miniforge：<https://github.com/conda-forge/miniforge>
- conda-forge（本部署使用的频道）：<https://conda-forge.org/>
- Miniconda 下载：<https://repo.anaconda.com/miniconda/>
- 频道授权说明：Anaconda 的 `defaults` 频道对 200 人以上组织收费
