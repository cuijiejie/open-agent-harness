"""业务插件加载器

用途：把"某个项目专属的工具"从内核里拿出来，做成可插拔目录。
加一个新项目 = 新建 plugins/<项目>/ 目录，不改内核代码。

目录约定：
    plugins/<name>/
        manifest.json     工具清单（名称 / 模块 / 属性 / 风险 / 分组 / 说明）
        *_tools.py        真正的 @tool 实现

安全约束（这是研发代码，不是用户上传的东西，但仍做静态扫描）：
    - 禁止 subprocess / os.system / eval / exec
    - 只能 import 内核公开的公共件（src.agent.tools.common）
    - 加载失败只记日志，不阻断启动（内核照常跑）

权限：加载 ≠ 授权。工具注册进 REGISTRY 后，仍然要经过 RBAC：
    用户的角色里没有这个工具名，模型就看不到它。
"""
import importlib.util
import json
import sys
from pathlib import Path

from src.infra.utils.LogUtil import log

# src/plugins（研发维护的业务插件，用户不可上传）
# parents[2] 依赖本文件位于 src/agent/tools/ 下——挪层级就会算错，
# 所以下面加断言：宁可在启动瞬间炸，也不要静默加载 0 个插件。
_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins"   # src/plugins

# 只做兜底校验：目录不存在时后面 _iter_plugins 会返回空，不影响启动。
# （早期这里硬断言某个业务插件必须存在，开源版把业务插件剥掉后就不适用了）
if not _PLUGIN_ROOT.is_dir():
    log.warning(f"插件根目录不存在，将跳过插件加载: {_PLUGIN_ROOT}")

_FORBIDDEN = ("subprocess", "os.system", "os.popen", "eval(", "exec(")


def _iter_plugins():
    if not _PLUGIN_ROOT.is_dir():
        return
    # 递归：支持 tools/<业务域>/ 这类分组层级
    for mf in sorted(_PLUGIN_ROOT.rglob("manifest.json")):
        if mf.is_file():
            yield mf.parent


def _load_module(py_file: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, str(py_file))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {py_file}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_plugin_specs() -> list:
    """扫描 plugins/，返回 ToolSpec 列表。任何单个插件出错都只跳过它。"""
    from src.agent.tools.registry import ToolSpec

    specs = []
    for pdir in _iter_plugins():
        try:
            manifest = json.loads((pdir / "manifest.json").read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"[plugins] 读取 manifest 失败 {pdir.name}: {e}")
            continue

        loaded_mods: dict = {}
        for item in manifest.get("tools") or []:
            name = str(item.get("name") or "").strip()
            module = str(item.get("module") or "").strip()
            attr = str(item.get("attr") or name).strip()
            if not name or not module:
                log.warning(f"[plugins] {pdir.name}: 工具声明不完整，跳过 {item}")
                continue

            # module 支持子目录：tools.product → tools/product.py
            mod_path = module.replace(".", "/") + ".py"
            py_file = pdir / mod_path
            if not py_file.is_file():
                log.warning(f"[plugins] {pdir.name}: 找不到 {py_file.name}")
                continue

            # 静态扫描：禁止危险调用
            try:
                src_text = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                log.warning(f"[plugins] {pdir.name}: 读取 {py_file.name} 失败: {e}")
                continue
            hit = [bad for bad in _FORBIDDEN if bad in src_text]
            if hit:
                log.warning(f"[plugins] {pdir.name}/{py_file.name} 含禁止调用 {hit}，跳过整个模块")
                continue

            try:
                mod = loaded_mods.get(module)
                if mod is None:
                    mod = _load_module(py_file, f"pa_plugin_{pdir.name}_{module.replace(chr(46), chr(95))}")
                    loaded_mods[module] = mod
                tool = getattr(mod, attr)
            except Exception as e:
                log.warning(f"[plugins] {pdir.name}: 加载 {module}.{attr} 失败: {e}")
                continue

            specs.append(ToolSpec(
                name,
                tool,
                str(item.get("risk") or "read"),
                note=str(item.get("note") or ""),
                extra={
                    "group": str(item.get("group") or pdir.name),
                    # 可选：manifest 里写 display_name，首次插库时作为中文名
                    "display_name": str(item.get("display_name") or ""),
                    "plugin": pdir.name,
                },
            ))

        if loaded_mods:
            log.info(f"[plugins] 已加载 {pdir.name}: {len(loaded_mods)} 个模块")

    if specs:
        log.info(f"[plugins] 插件工具共 {len(specs)} 个: {[s.name for s in specs]}")
    return specs


__all__ = ["load_plugin_specs"]
