# Open Agent Harness · OpenShell 沙箱工作负载镜像
#
# 版本演进：
#   1.0  只有 python3 + pandas/openpyxl（能跑脚本，但没字体、没转换工具）
#   1.1  + 中文字体、poppler/imagemagick/ghostscript、weasyprint 等（中文 PDF/图片可用）
#   1.2  + 无头浏览器（Chromium/Playwright）、pandoc/ffmpeg/jq/git 等 CLI、25 个 Python 库，
#        并把常用能力封装成 HTML/Office → PDF/图片 的命令：
#          html2pdf / html2png / docx2pdf / xlsx2pdf
#        （不含 LibreOffice：实测在 OpenShell 沙箱里 soffice 必崩，见第二节说明）
#   1.3  + 常用语言运行时：Node 18/npm/TypeScript、JDK 21(headless)、C/C++ 工具链(gcc/g++/make)、
#        Go 1.22、Rust(rustc/cargo) —— 让技能脚本不再只能用 Python 写
#
# 为什么必须预装（而不是让用户自己装）：
#   沙箱内是非 root + /usr 只读 + 无 sudo → apt 永远装不了；
#   pip 只能装纯 Python 包（系统级依赖装不上）。所以系统级能力只能进镜像。
#
# 构建（在 VM 上，需 sudo）：
#   sudo DOCKER_BUILDKIT=1 docker build -t pa-sandbox:1.3 -f docker/pa-sandbox.Dockerfile .
#
# ⚠️ 必须开 BuildKit（并装 docker-buildx）：本文件用了 8 处 `RUN python3 - <<'PY'` heredoc，
#    经典 builder 不支持 heredoc —— 它不会报错，而是把正文丢掉、跑一个空脚本，
#    于是抽 SC 字形 / 写 matplotlibrc / Chromium 自检全部静默跳过，镜像里中文全是豆腐块，
#    最后才在 `xlsx2pdf /tmp/c5.xlsx` 自检上以 "No such file" 暴露。踩过一次，别再用经典 builder。
#
# 使用：config/<env>/configs.cfg 里 openshell_image=pa-sandbox:1.3
#       （镜像在沙箱创建时固定，已存在的沙箱需重建才会升级）

FROM nvcr.io/nvidia/base/ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# ⚠️ 重要教训：OpenShell 运行时【会清空镜像的 ENV】（沙箱里只剩 HOME/PATH/TLS 那几个），
#    所以下面这些 ENV 只在 docker build / 直接 docker run 时生效，【沙箱里一律看不到】。
#    凡是要在沙箱里生效的配置，必须落到【文件】上（/etc/pip.conf、matplotlibrc 等）。
# pip 走腾讯云镜像（实测最快：pandas 12MB 13.8s，官方源 19.6s）
ENV PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV MPLBACKEND=Agg
# Playwright 浏览器路径：必须和运行时一致（运行时 HOME=/sandbox，默认就是 /sandbox/.cache/ms-playwright）
ENV PLAYWRIGHT_BROWSERS_PATH=/sandbox/.cache/ms-playwright

# ===========================================================================
# 一、基础运行环境 + 中文字体 + 文档/图片工具链
# ===========================================================================
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
      ca-certificates curl wget \
      fonts-noto-cjk fonts-dejavu-core fontconfig \
      poppler-utils \
      zip unzip xz-utils p7zip-full \
      imagemagick ghostscript \
      file tree jq sqlite3 git rsync \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# ===========================================================================
# 二、文档转换：pandoc（md / html ↔ docx / pptx …）
# ===========================================================================
# ⚠️ 这里【刻意不装 LibreOffice】：实测在 OpenShell 沙箱里 soffice 一律 abort
#    （terminate called after throwing 'com::sun::star::deployment::DeploymentException'），
#    同一镜像用普通 docker 以 uid 1000 跑却完全正常 → 是沙箱进程模型的限制，不是缺包。
#    所以 office→PDF 改用 pandoc + weasyprint 链路（见 /usr/local/bin/docx2pdf、xlsx2pdf）。
#    白装 1GB 且用户一跑就崩，不值得；哪天 OpenShell 放开了再把这行加回来即可。
RUN apt-get update \
 && apt-get install -y --no-install-recommends pandoc \
 && rm -rf /var/lib/apt/lists/*

# ===========================================================================
# 二·五、常用语言运行时（1.3 新增）
#   Node 18 + npm + TypeScript / JDK 21(headless) / gcc-g++-make / Go / Rust
#   —— 技能脚本与 MCP server 之外的自定义代码，不再只能写 Python
#   构建期就自检一遍：任何一个装失败/不可用，镜像构建直接失败（不留半残镜像）
# ===========================================================================
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      nodejs npm \
      default-jdk-headless \
      build-essential \
      golang-go \
      rustc cargo \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g --silent typescript \
 && echo "--- 语言自检 ---" \
 && node -v && npm -v && tsc -v \
 && java -version 2>&1 | head -1 \
 && (gcc --version | head -1) && (g++ --version | head -1) && (make -v | head -1) \
 && go version \
 && rustc --version && cargo --version

# ===========================================================================
# 三、媒体处理：ffmpeg（音视频）+ 图像批处理依赖
# ===========================================================================
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# ===========================================================================
# 四、Python 库（数据 / 图表 / PDF / Office / 解析 / 数据库）
# ===========================================================================
RUN pip3 install --no-cache-dir --break-system-packages --retries 8 --timeout 120 \
      pandas==2.2.3 openpyxl==3.1.5 xlsxwriter xlrd \
      matplotlib==3.9.2 Pillow seaborn scipy statsmodels sympy pyarrow \
      weasyprint reportlab pypdf fpdf2 pdfplumber \
      python-docx python-pptx odfpy \
      lxml beautifulsoup4 jinja2 markdown pyyaml \
      requests tabulate chardet python-magic \
      plotly \
      sqlalchemy pymysql \
      fonttools yq

# ===========================================================================
# 五、无头浏览器：Chromium（HTML → PDF/截图，支持 JS）
# ===========================================================================
# 为什么不用 apt 的 chromium-browser：Ubuntu 24.04 那个包只是个 snap 跳板，
# 在镜像里装不起来。用 Playwright 自带的 Chromium（稳定、版本可控）。
#
# ⚠️ 必须走国内镜像：VM 直连 cdn.playwright.dev 实测 0 B/s（191MB 要一小时），
#    npmmirror 实测 3.9 MB/s（约 50 秒）。只在构建这一步用，不写进镜像 ENV。
# ⚠️ 浏览器必须装到【运行时默认路径】(HOME=/sandbox → /sandbox/.cache/ms-playwright)，
#    否则沙箱里的 playwright 找不到（镜像 ENV 在沙箱里会被清掉）。
RUN pip3 install --no-cache-dir --break-system-packages --retries 8 --timeout 120 playwright \
 && PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
      playwright install --with-deps chromium \
 && rm -rf /root/.cache/pip

# ===========================================================================
# 六、工作区 + 沙箱内生效的配置（pip / matplotlib）
# ===========================================================================
RUN mkdir -p /sandbox && chown 1000:1000 /sandbox
WORKDIR /sandbox

# ① pip 源：写进 /etc/pip.conf（文件才能穿透 OpenShell 的 ENV 清理）
RUN printf '[global]\nindex-url = https://mirrors.cloud.tencent.com/pypi/simple\n' > /etc/pip.conf \
 && pip3 config list

# ⚠️ 踩坑记录：Ubuntu 的 fonts-noto-cjk 是【.ttc 集合】，matplotlib 虽然能读，
#    但只枚举到集合里的第一个字面（Noto Sans CJK **JP**），配 "Noto Sans CJK SC"
#    会报 "Glyph missing from font(s) DejaVu Sans" → 中文变豆腐块。
#    这里用 fontTools 把 SC 字面抽成独立 .ttf，matplotlib 才能真正用上简体字形。
RUN python3 - <<'PY'
import os
from fontTools.ttLib import TTCollection

pairs = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.ttf"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "NotoSansCJKsc-Bold.ttf"),
]
out_dir = "/usr/local/share/fonts"
os.makedirs(out_dir, exist_ok=True)
for src, dst in pairs:
    if not os.path.exists(src):
        print("跳过（不存在）:", src)
        continue
    coll = TTCollection(src)
    for font in coll.fonts:
        name = font["name"].getDebugName(4) or ""
        if "CJK SC" in name:
            font.save(os.path.join(out_dir, dst))
            print(f"抽出 {name} -> {dst}")
            break
PY

RUN fc-cache -f >/dev/null 2>&1 || true

# ② matplotlib 配置：必须写到【运行时真实配置目录】/sandbox/.config/matplotlib
#    （镜像 ENV MPLCONFIGDIR 在沙箱里会被清掉，所以之前写到 .mplconfig 是无效的；
#      两处都写一份，保证直接 docker run 的场合也对）
RUN python3 - <<'PY'
import os

content = (
    "backend: Agg\n"
    "font.sans-serif: Noto Sans CJK SC, Noto Sans CJK JP, DejaVu Sans\n"
    "axes.unicode_minus: False\n"
)
for d in ("/sandbox/.config/matplotlib", "/sandbox/.mplconfig"):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "matplotlibrc"), "w") as f:
        f.write(content)
    print("matplotlibrc 已写入:", d)
PY

# ③ 目录属主交还沙箱用户（uid 1000）：浏览器等文件以 root 装，必须让沙箱用户读得到
RUN chown -R 1000:1000 /sandbox && chmod -R a+rX /sandbox \
 && ls -ld /sandbox/.cache/ms-playwright/* | head -3

# ===========================================================================
# 七、把常用能力封装成命令（智能体不用自己猜 API）
# ===========================================================================
COPY docker/sandbox-bin/html2pdf /usr/local/bin/html2pdf
COPY docker/sandbox-bin/html2png /usr/local/bin/html2png
COPY docker/sandbox-bin/docx2pdf /usr/local/bin/docx2pdf
COPY docker/sandbox-bin/xlsx2pdf /usr/local/bin/xlsx2pdf
# 保险：Windows 上编辑会带 CRLF，而 CRLF 的 shebang 会让脚本报 "bad interpreter"
RUN sed -i 's/\r$//' /usr/local/bin/html2pdf /usr/local/bin/html2png \
      /usr/local/bin/docx2pdf /usr/local/bin/xlsx2pdf \
 && chmod +x /usr/local/bin/html2pdf /usr/local/bin/html2png \
      /usr/local/bin/docx2pdf /usr/local/bin/xlsx2pdf \
 && for f in html2pdf html2png docx2pdf xlsx2pdf; do bash -n /usr/local/bin/$f || exit 1; done \
 && echo "✅ 四个命令脚本语法自检通过"

# ===========================================================================
# 八、构建期自检（镜像坏了要在这里就发现）
# ===========================================================================
RUN python3 -c "import pandas, openpyxl, matplotlib, seaborn, scipy, sympy, PIL, weasyprint, reportlab, pypdf, pdfplumber, docx, pptx, bs4, lxml, sqlalchemy, pymysql, plotly, pyarrow; print('✅ Python 库自检通过: pandas', pandas.__version__, '| matplotlib', matplotlib.__version__)"
RUN which pdftoppm pdfinfo pdftotext zip unzip 7z convert gs pandoc ffmpeg jq sqlite3 git file
RUN fc-list | grep -ci "noto sans cjk" | grep -qv '^0$' && echo "✅ 中文字体已装"

# matplotlib 中文渲染自检
RUN python3 - <<'PY'
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
fig = plt.figure()
fig.text(0.1, 0.5, "中文渲染自检 一二三 245.3%")
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    fig.savefig("/tmp/c1.png")
    missing = [x for x in w if "missing" in str(x.message).lower()]
if missing:
    raise SystemExit("❌ matplotlib 中文字体未生效: " + str(missing[0].message))
print("✅ matplotlib 中文渲染自检通过")
PY

# weasyprint 中文 PDF 自检
RUN python3 - <<'PY'
import subprocess

from weasyprint import HTML

HTML(string="<p style='font-family:\"Noto Sans CJK SC\"'>中文 PDF 自检</p>").write_pdf("/tmp/c2.pdf")
out = subprocess.run(["pdftotext", "/tmp/c2.pdf", "-"], capture_output=True, text=True).stdout
assert "中文" in out, f"PDF 文本层异常: {out!r}"
print("✅ weasyprint 中文 PDF 自检通过")
PY

# 无头浏览器（Chromium）自检：渲染一段带 JS 的 HTML 并转 PDF
# ⚠️ 启动参数必须与沙箱内一致：OpenShell 下 zygote 模型起不来，缺 --no-zygote 会 "Target crashed"
RUN python3 - <<'PY'
from playwright.sync_api import sync_playwright

ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote"]
with sync_playwright() as p:
    b = p.chromium.launch(args=ARGS)
    page = b.new_page()
    page.set_content("<h1 id='t'>中文</h1><script>document.getElementById('t').innerText='中文 JS 已执行'</script>")
    page.wait_for_timeout(300)
    text = page.inner_text("#t")
    page.pdf(path="/tmp/c3.pdf", format="A4")
    b.close()
assert "JS 已执行" in text, text
data = open("/tmp/c3.pdf", "rb").read()
assert data[:4] == b"%PDF", "PDF 头异常"
print("✅ Chromium 无头渲染自检通过（JS 生效，PDF 已生成）")
PY

# ★ 关键自检：模拟沙箱运行时（OpenShell 会清空 ENV 且以非 root 运行），再验一遍
#   历史教训：构建时有 ENV、沙箱里没有 → 浏览器找不到（而且原先装在只有 root 能读的路径）
RUN cat > /tmp/rt_check.sh <<'SH'
set -e
CLEAN="env -i HOME=/sandbox PATH=/usr/local/bin:/usr/bin:/bin"
$CLEAN python3 - <<'PY'
from playwright.sync_api import sync_playwright

ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote"]
with sync_playwright() as p:
    b = p.chromium.launch(args=ARGS)
    page = b.new_page()
    page.set_content("<h1>中文</h1>")
    page.wait_for_timeout(200)
    b.close()
print("  ✅ 空 ENV 下 Chromium 可用（含 new_page，与沙箱运行时一致）")
PY
$CLEAN pip3 config list | grep -q tencent && echo "  ✅ 空 ENV 下 /etc/pip.conf 生效（pip 默认腾讯镜像）"
$CLEAN python3 -c "import matplotlib; print('  ✅ 空 ENV 下 matplotlib 配置目录:', matplotlib.get_configdir())"
$CLEAN python3 -c "open('/tmp/rt.html','w').write('<h1>中文自检</h1>')"
$CLEAN html2pdf /tmp/rt.html /tmp/rt.pdf
SH
RUN chmod +x /tmp/rt_check.sh \
 && setpriv --reuid=1000 --regid=1000 --clear-groups /tmp/rt_check.sh \
 && echo "✅ 沙箱运行时仿真自检通过（非 root + 空 ENV）"

# LibreOffice 已移除（沙箱里跑不起来），改为验证 pandoc + weasyprint 链路
RUN python3 -c "from docx import Document; d=Document(); d.add_heading('中文自检',0); d.add_paragraph('正文中文内容'); d.save('/tmp/c4.docx')"
RUN docx2pdf /tmp/c4.docx /tmp/c4.pdf
RUN pdftotext /tmp/c4.pdf - | head -3
RUN python3 - <<'PY'
import openpyxl

wb = openpyxl.Workbook()
ws = wb.active
ws.append(["班级", "总分"])
ws.append(["一班", 259])
wb.save("/tmp/c5.xlsx")
PY
RUN xlsx2pdf /tmp/c5.xlsx /tmp/c5.pdf
RUN pdftotext /tmp/c5.pdf - | head -3

RUN rm -f /tmp/c1.png /tmp/c2.pdf /tmp/c3.pdf /tmp/c4.docx /tmp/c4.pdf /tmp/c5.xlsx /tmp/c5.pdf /tmp/rt.html /tmp/rt.pdf
