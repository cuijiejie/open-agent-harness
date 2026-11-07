# 用 Miniconda 管理 Python 环境

> 本部署的 Python 环境**用 Miniconda**，不用 `venv`。
> 位置：`/opt/miniconda3`（base）+ `/opt/miniconda3/envs/oah`（项目环境，Python 3.12）。
> 服务通过 `ExecStart=/opt/miniconda3/envs/oah/bin/python -m uvicorn ...` 直接调用，
> 不经过 `conda activate`。
>
> 适用：Ubuntu 22.04 / 24.04，aarch64 或 x86_64。

---

## 0. 为什么用 conda 而不是 venv

| 对比 | venv + pip | Miniconda + conda-forge |
|---|---|---|
| 装本项目依赖 | 全部 pip wheel，可用 | 同样走 pip，可用 |
| 装其它科学计算包 | aarch64 上部分包没 wheel，要现场编译 | conda-forge 有预编译包，省事 |
| 磁盘 | 约 480 MB | 约 1.2 GB（base）+ 约 1 GB（环境） |
| 运行时开销 | 无 | 无（直接用环境里的 python） |
| 维护成本 | 低 | 多一层：conda 与 pip 混用要守规矩 |

**关键选择：只用 conda-forge 频道，不启用 Anaconda 的 `defaults` 频道。**
conda 26 起，`defaults`（`repo.anaconda.com`）要先接受服务条款才能用，而该频道对
200 人以上组织是收费的。用 `--override-channels -c conda-forge` 可以完全绕开，
既不用点同意，也没有商业限制。

> 替代方案：Miniforge3 是 conda-forge 的社区发行版，天生只用 conda-forge，
> 装法一样、只是安装包地址不同。两者选其一即可，本部署用 Miniconda。

---

## 1. 安装 Miniconda

```bash
cd /tmp
curl -fsSLO https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh
# x86_64 机器把 aarch64 换成 x86_64

sudo bash Miniconda3-latest-Linux-aarch64.sh -b -p /opt/miniconda3
sudo /opt/miniconda3/bin/conda --version        # 实测 26.9.4
```

`-b` 免交互、`-p` 指定前缀。**不要跑 `conda init`**——它改的是交互式 shell 的 rc，
systemd 不读那些文件；后面一律用绝对路径调环境的 python。

---

## 2. 建项目环境

```bash
sudo /opt/miniconda3/bin/conda create -y -n oah \
     --override-channels -c conda-forge python=3.12
sudo /opt/miniconda3/envs/oah/bin/python -V     # 实测 3.12.16
```

- `--override-channels -c conda-forge`：不碰 `defaults`，因此**不会弹 ToS 同意**。
  不加这个参数会直接失败：
  `CondaToSNonInteractiveError: Terms of Service have not been accepted ...`。
- 环境目录：`/opt/miniconda3/envs/oah`，解释器：`/opt/miniconda3/envs/oah/bin/python`。

---

## 3. 装项目依赖

```bash
cd /opt/open-agent-harness
sudo /opt/miniconda3/envs/oah/bin/python -m pip install -U pip
sudo /opt/miniconda3/envs/oah/bin/python -m pip install -r requirements.txt

# 验一遍
sudo /opt/miniconda3/envs/oah/bin/python -m pip check
sudo /opt/miniconda3/envs/oah/bin/python -c "import fastapi, uvicorn, asyncmy, psycopg, pandas; print('ok')"
```

混用 conda 与 pip 的三条规矩：

1. 先 conda 装二进制/系统级依赖，再 pip 装纯 Python 包，反过来容易互相覆盖；
2. 不在 base 环境里装项目依赖；
3. 装完跑 `pip check` 看冲突。

---

## 4. 用 `install.sh` 切换（推荐）

`deploy/install.sh` 已支持 conda，**不要再手工改 systemd 单元**：

```bash
cd /home/cuijie/open-agent-harness        # 源码目录
sudo APP_DIR=/opt/open-agent-harness PORT=5001 WORKERS=3 \
     USE_CONDA=1 SKIP_FRONTEND=1 \
     bash deploy/install.sh
```

它会：

1. 检查 `/opt/miniconda3/bin/conda` 是否存在，缺环境则用 conda-forge 建 `oah`；
2. 用环境里的 python 装 `requirements.txt`；
3. 把单元文件的 `ExecStart` 指到 `/opt/miniconda3/envs/oah/bin/python`；
4. 往 `.env` 写一行 `PA_INSTALL_USE_CONDA=1`。

**第 4 点是关键**：以后任何人重跑 `install.sh`（不传 `USE_CONDA`）都会读到这个标记，
继续用 conda，不会又去建 `.venv`。想切回 venv 就显式传 `USE_CONDA=0`。

相关变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `USE_CONDA` | 读 `.env` 的 `PA_INSTALL_USE_CONDA`，没有则 0 | 1 = 用 conda 环境 |
| `CONDA_PREFIX_DIR` | `/opt/miniconda3` | conda 安装根目录 |
| `CONDA_ENV` | `oah` | 环境名 |

---

## 5. 验收

```bash
cd /opt/open-agent-harness

# 1) 确认服务真的在用 conda 的 python
systemctl show open-agent-harness -p ExecStart | tr ' ' '\n' | grep -m1 python
#   期望 /opt/miniconda3/envs/oah/bin/python

# 2) 健康检查
curl -s http://127.0.0.1:5001/open-agent-harness/api/health
#   期望 {"ok":true,"db":"ok","redis":"ok"}

# 3) 项目验收脚本（含 104 个单测）
PY=/opt/miniconda3/envs/oah/bin/python bash deploy/verify.sh
#   期望 ✅ 验收通过

# 4) 端到端：登录后发一条对话，确认模型 + 沙箱工具链正常
```

实测结果（2026-11-07，Ubuntu 24.04 aarch64）：以上四步全部通过，
104 个单测 `OK (skipped=1)`，对话里沙箱执行返回正常。

---

## 6. 本机现状（部署后的实际值）

| 项 | 值 |
|---|---|
| conda | `/opt/miniconda3/bin/conda`，26.9.4 |
| 项目环境 | `/opt/miniconda3/envs/oah`，Python 3.12.16 |
| 服务解释器 | `/opt/miniconda3/envs/oah/bin/python`（`--workers 3`） |
| `.env` 标记 | `PA_INSTALL_USE_CONDA=1` |
| 旧 venv | `/opt/open-agent-harness/.venv` **已删除**（释放 478 MB） |
| 磁盘占用 | conda base 约 1.2 GB + 环境约 1 GB |

> 注意：`deploy/verify.sh` 默认用 `python`（系统解释器）跑单测，系统里没装项目依赖。
> 在 conda 部署下要显式传 `PY=/opt/miniconda3/envs/oah/bin/python`。

---

## 7. 回滚到 venv

```bash
cd /home/cuijie/open-agent-harness
sudo APP_DIR=/opt/open-agent-harness USE_CONDA=0 SKIP_FRONTEND=1 bash deploy/install.sh
```

脚本会重建 `.venv`、装依赖、把 `ExecStart` 改回去；`.env` 里的
`PA_INSTALL_USE_CONDA=1` 需要手工删掉，否则下次重跑又切回 conda：

```bash
sudo sed -i '/^PA_INSTALL_USE_CONDA=/d' /opt/open-agent-harness/.env
```

conda 本身可以留着（和 venv 不冲突），也可以删掉省 2 GB：

```bash
sudo rm -rf /opt/miniconda3
```

---

## 8. 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `CondaToSNonInteractiveError: Terms of Service have not been accepted` | 用了默认 `defaults` 频道 | 加 `--override-channels -c conda-forge` |
| 服务启动报 `libstdc++.so.6: version GLIBCXX_x not found` | conda 自带的 libstdc++ 压过系统库 | `sudo LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu systemctl restart ...`（或换回系统包的该库） |
| 手工 `conda run` 正常、服务不行 | 单元文件里还残留旧路径 / `PYTHONPATH` 不对 | `systemctl show -p ExecStart -p Environment` 逐项核对 |
| 磁盘暴涨 | conda 的包缓存 `pkgs/` 不自动清 | `sudo /opt/miniconda3/bin/conda clean -a -y` |
| `conda activate` 在 systemd 里不生效 | systemd 不读 shell rc | 一律用环境的绝对路径 python |
| 重跑 `install.sh` 后变回 venv | `.env` 里没有 `PA_INSTALL_USE_CONDA=1` | 手工补一行，或重跑时传 `USE_CONDA=1` |

---

## 9. 参考

- Miniconda 下载：<https://repo.anaconda.com/miniconda/>
- conda-forge（本部署使用的频道，无商业授权限制）：<https://conda-forge.org/>
- Miniforge（想彻底不碰 Anaconda 的发行版时用）：<https://github.com/conda-forge/miniforge>
- 频道授权说明：Anaconda 的 `defaults` 频道对 200 人以上组织收费
