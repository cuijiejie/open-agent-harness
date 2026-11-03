"""CAS 单点登录工具模块

基于 CAS 2.0 协议实现，参考 mars 项目的 CAS + Shiro 集成方案。
不依赖第三方 CAS 库（flask-cas/flask-cas-ng 均不兼容 Flask 3.x）。
"""
import xml.etree.ElementTree as ET
import json
from functools import wraps

import requests
from flask import Blueprint, session, redirect, request, current_app, jsonify

from config.Configs import cfg
from src.infra.utils.LogUtil import log


# CAS 命名空间
_CAS_NS = 'http://www.yale.edu/tp/cas'

# Blueprint，挂载 CAS 回调路由
cas_bp = Blueprint('cas', __name__)


def init_cas(app):
    """初始化 CAS 单点登录，注册 Blueprint 与 session 密钥

    没有 SECRET_KEY 时**直接启动失败**：以前是随机生成一个，于是 6 个 worker
    各拿一把钥匙、重启即失效 —— 表现为"登录了又掉、换台机器又要登"，
    而且排查时很难想到是密钥问题。
    """
    secret = cfg.secretKey
    if not secret:
        raise RuntimeError(
            "SECRET_KEY 未配置（[app] secret_key / PA_APP_SECRET_KEY）："
            "多 worker 会各自随机生成导致登录态随机失效，请先配置再启动"
        )
    app.config['SECRET_KEY'] = secret
    app.register_blueprint(cas_bp, url_prefix='/open-agent-harness')


def get_current_user() -> str:
    """获取当前 CAS 登录用户名，未登录返回空字符串"""
    return session.get('CAS_USERNAME', '')


def get_current_display_name() -> str:
    """获取当前用户的中文显示名，未登录或无中文名返回空字符串"""
    return session.get('CAS_DISPLAY_NAME', '')


def login_required(f):
    """CAS 登录校验装饰器，未登录时重定向到 CAS 登录页

    测试环境特例：cfg.testLoginEnabled=true 时改为引导到免密测试登录页，
    避免本地/VM 测试时被跳去内网 CAS。
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        user: str = get_current_user() or ""
        if not user:
            # 统一走 CAS 登录；/test-login 仅作为"直接访问该 URL 可用"的调试入口，
            # 不再是默认跳转目标（dev 与 pro/uat 行为保持一致，避免环境差异踩坑）
            callback_url = _build_service_url()
            cas_login_url = f"{cfg.casServer}/login?service={callback_url}"
            if request.path.startswith('/open-agent-harness/api/'):
                return jsonify({'error': '未登录', 'login_url': cas_login_url}), 401
            return redirect(cas_login_url)

        # 停用账号：连已登录的会话也立刻失效（管理员在「用户」页签停用后 30 秒内生效）
        try:
            from src.infra.RbacService import rbac as _rbac

            if _rbac.is_disabled(user):
                if request.path.startswith('/open-agent-harness/api/'):
                    return jsonify({'error': '账号已停用，请联系管理员'}), 403
                return '账号已停用，请联系管理员', 403
        except Exception:
            pass

        return f(*args, **kwargs)

    return decorated


# ========== CAS 回调路由（挂载在 cas_bp 下） ==========

@cas_bp.route('/cas')
def cas_callback():
    """CAS ticket 校验回调（对应 mars 的 /mars/shiro-cas）

    CAS 登录成功后，CAS 服务器会回调此 URL 并附带 ticket 参数。
    """
    ticket = request.args.get('ticket', '')
    if not ticket:
        return redirect('/open-agent-harness/')

    result = _validate_ticket(ticket)
    if result:
        username, display_name = result
        # 如果 CAS 未返回中文名，则调用 USC 查询
        if not display_name:
            display_name = _query_usc_display_name(username)
        session['CAS_USERNAME'] = username
        session['CAS_DISPLAY_NAME'] = display_name or ''
        log.info(f"CAS 登录成功: user={username}, displayName={display_name}")
        # 停用账号 → 拒绝登录；正常账号登录即建档（出现在「用户」页签里）
        from src.infra.RbacService import rbac as _rbac

        if _rbac.is_disabled(username):
            log.warning(f"停用账号尝试登录（CAS）: user={username}, ip={request.remote_addr}")
            session.clear()
            return '账号已停用，请联系管理员', 403
        _rbac.touch_user(username, display_name or "")
        # 登录即初始化该用户的沙箱（后台线程，不阻塞跳转）
        _init_sandbox(username)
        after_login = session.pop('CAS_AFTER_LOGIN_SESSION_URL', None)
        return redirect(after_login or '/open-agent-harness/')
    else:
        log.warning(f"CAS ticket 校验失败: ticket={ticket[:20]}...")
        return 'CAS 认证失败，请重试', 401


# ========== 登录后触发沙箱初始化 ==========

def _init_sandbox(username: str) -> None:
    """登录成功后触发该用户的沙箱初始化（后台线程，失败不影响登录）

    效果：每个登录用户都会拥有自己的 agent-{username} 沙箱，
    首次登录后约 5~10 秒内就绪（可在沙箱列表中看到）。
    """
    try:
        from src.agent.backends.sandbox import init_user_sandbox_async
        init_user_sandbox_async(username)
    except Exception as e:  # 沙箱不可用时不能影响登录
        log.warning(f"触发沙箱初始化失败: user={username}, err={e}")


# ========== 测试登录（免密，仅开发/测试环境） ==========

_TEST_LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>测试登录 · Open Agent Harness Harness</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center;
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #1E3A5F 0%, #16304d 100%);
  }
  .card {
    width: 380px; padding: 36px 32px; background: #fff; border-radius: 16px;
    box-shadow: 0 20px 50px rgba(0,0,0,.3);
  }
  .badge {
    display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 12px;
    background: #F4A261; color: #fff; margin-bottom: 14px;
  }
  h1 { margin: 0 0 6px; font-size: 20px; color: #1E3A5F; }
  p.sub { margin: 0 0 22px; font-size: 13px; color: #6b7a8d; line-height: 1.5; }
  label { display: block; font-size: 13px; color: #334155; margin-bottom: 6px; }
  input {
    width: 100%; padding: 11px 13px; font-size: 14px; border: 1.5px solid #d8e0e8;
    border-radius: 9px; outline: none; transition: border-color .15s;
  }
  input:focus { border-color: #2EC4B6; }
  button {
    width: 100%; margin-top: 18px; padding: 12px; font-size: 15px; font-weight: 600;
    color: #fff; background: #1E3A5F; border: 0; border-radius: 9px; cursor: pointer;
    transition: background .15s;
  }
  button:hover { background: #2EC4B6; }
  .warn {
    margin-top: 18px; padding: 10px 12px; font-size: 12px; line-height: 1.6;
    color: #8a5a00; background: #FFF6E5; border-left: 3px solid #F4A261;
    border-radius: 6px;
  }
</style>
</head>
<body>
  <form class="card" method="post">
    <span class="badge">测试环境</span>
    <h1>Open Agent Harness Harness</h1>
    <p class="sub">输入域账号即可进入（无需密码），用于开发/测试环境验证功能。</p>
    <label for="username">域账号</label>
    <input id="username" name="username" placeholder="例如 wangxin03" autofocus
           autocomplete="off" required>
    <button type="submit">进入智能助手</button>
    <div class="warn">
      ⚠️ 此入口<b>跳过 CAS 单点登录</b>，仅供开发/测试使用；生产环境已禁用。
    </div>
  </form>
</body>
</html>"""


@cas_bp.route('/test-login', methods=['GET', 'POST'])
def test_login():
    """测试登录（免密）：输入用户名直接建立登录态

    安全约束（重要）：
    - 仅在 cfg.testLoginEnabled=true 时可用，默认 false；**生产环境必须关闭**
    - 每次调用都记录 WARNING 日志（含来源 IP），便于审计
    - 两种提交方式：HTML 表单 / JSON（便于脚本与 curl 测试）

    用途：开发/测试环境绕过 CAS，直接以指定用户身份与智能体对话。
    """
    if not cfg.testLoginEnabled:
        log.warning(f"测试登录被拒绝（配置未启用）: ip={request.remote_addr}")
        return '测试登录功能未启用', 403

    if request.method == 'GET':
        return _TEST_LOGIN_HTML

    # 兼容表单提交与 JSON 提交
    if request.is_json:
        username = str((request.get_json(silent=True) or {}).get('username', '')).strip()
    else:
        username = (request.form.get('username') or '').strip()

    if not username:
        return '用户名不能为空', 400

    # 停用账号 → 拒绝登录；正常账号登录即建档
    from src.infra.RbacService import rbac as _rbac

    if _rbac.is_disabled(username):
        log.warning(f"停用账号尝试测试登录: user={username}, ip={request.remote_addr}")
        return '账号已停用，请联系管理员', 403
    _rbac.touch_user(username, "")

    session['CAS_USERNAME'] = username
    session['CAS_DISPLAY_NAME'] = f'{username}（测试）'
    log.warning(f"【测试登录】免密登录成功: user={username}, ip={request.remote_addr}")
    # 登录即初始化该用户的沙箱（后台线程，不阻塞跳转）
    _init_sandbox(username)
    return redirect('/open-agent-harness/')


# ========== 内部辅助函数 ==========

def _build_service_url() -> str:
    """构造 CAS 回调地址（service 参数）"""
    # 优先使用配置文件中显式指定的 service URL
    if cfg.casServiceUrl:
        log.info(f"CAS service URL (from config): {cfg.casServiceUrl}")
        return cfg.casServiceUrl
    # 其次使用 nginx 代理传递的真实 host
    host = request.headers.get('X-Forwarded-Host') or request.host
    if host:
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        service_url = f"{scheme}://{host}/open-agent-harness/cas"
        log.info(f"CAS service URL: {service_url} (host={request.host}, fwd_host={request.headers.get('X-Forwarded-Host')})")
        return service_url
    return cfg.casAfterLogin


def _validate_ticket(ticket: str):
    """向 CAS 服务器验证 ticket，成功返回 (username, display_name) 元组，失败返回 None"""
    service_url = _build_service_url()
    validate_url = (
        f"{cfg.casServer}/serviceValidate"
        f"?service={service_url}&ticket={ticket}"
    )
    log.info(f"CAS validate URL: {validate_url}")
    try:
        resp = requests.get(validate_url, timeout=10)
    except requests.RequestException as e:
        log.error(f"CAS ticket 校验请求异常: {e}")
        return None

    if resp.status_code != 200:
        log.error(f"CAS ticket 校验返回非 200: {resp.status_code}, body={resp.text[:300]}")
        return None

    return _parse_cas_response(resp.text)


def _parse_cas_response(xml_text: str):
    """解析 CAS serviceValidate 返回的 XML

    成功响应示例：
      <cas:serviceResponse xmlns:cas='http://www.yale.edu/tp/cas'>
          <cas:authenticationSuccess>
              <cas:user>wangxin03</cas:user>
              <cas:attributes>
                  <cas:displayName>王鑫</cas:displayName>
              </cas:attributes>
          </cas:authenticationSuccess>
      </cas:serviceResponse>

    返回 (username, display_name) 元组，失败返回 None
    """
    log.info(f"CAS 响应 XML: {xml_text[:500]}")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.error(f"CAS 响应 XML 解析失败: {e}")
        return None

    # 查找 authenticationSuccess 节点
    success = root.find(f'{{{_CAS_NS}}}authenticationSuccess')
    if success is not None:
        user_el = success.find(f'{{{_CAS_NS}}}user')
        if user_el is not None and user_el.text:
            username = user_el.text.strip()
            # 尝试从 attributes 中提取中文名（带 cas 命名空间）
            display_name = ''
            # 常见的 CAS 返回属性名
            display_attr_names = ('displayName', 'cn', 'name', 'givenName', 'nickname', 'realName', 'fullName', 'userName', 'sn')

            # 1) 先尝试 cas:attributes 下的 cas:xxx
            attrs = success.find(f'{{{_CAS_NS}}}attributes')
            if attrs is not None:
                for attr_name in display_attr_names:
                    attr_el = attrs.find(f'{{{_CAS_NS}}}{attr_name}')
                    if attr_el is not None and attr_el.text and attr_el.text.strip():
                        display_name = attr_el.text.strip()
                        log.info(f"CAS 提取中文名(cas:attributes): attr={attr_name}, value={display_name}")
                        break
                if not display_name:
                    attr_keys = [el.tag.replace(f'{{{_CAS_NS}}}', '') for el in attrs]
                    log.info(f"CAS attributes 可用字段: {attr_keys}")

            # 2) 再尝试直接在 authenticationSuccess 下查找（无 attributes 包裹层）
            if not display_name:
                for attr_name in display_attr_names:
                    attr_el = success.find(f'{{{_CAS_NS}}}{attr_name}')
                    if attr_el is not None and attr_el.text and attr_el.text.strip():
                        display_name = attr_el.text.strip()
                        log.info(f"CAS 提取中文名(直接): attr={attr_name}, value={display_name}")
                        break

            return (username, display_name)

    # 查找 authenticationFailure 节点（记录日志）
    failure = root.find(f'{{{_CAS_NS}}}authenticationFailure')
    if failure is not None:
        code = failure.get('code', 'UNKNOWN')
        msg = failure.text.strip() if failure.text else ''
        log.warning(f"CAS 认证失败: code={code}, msg={msg}")

    return None


def _query_usc_display_name(username: str) -> str:
    """调用 USC（用户服务中心）查询用户中文名（参考 mars 的 CustomCasRealm.queryUserDto）

    对应的业务系统调用：
      HttpRequestUtils.httpPost(uscRestUrl + "/api/otherService/findResourceByUser?username=" + username, "")

    返回中文名，失败返回空字符串
    """
    if not cfg.uscRestUrl:
        log.warning("USC REST URL 未配置，跳过中文名查询")
        return ''
    try:
        url = f"{cfg.uscRestUrl}/api/otherService/findResourceByUser?username={username}"
        resp = requests.post(url, data='', timeout=5)
        if resp.status_code != 200:
            log.warning(f"USC 查询失败: status={resp.status_code}, body={resp.text[:200]}, username={username}")
            return ''
        data = resp.json()
        log.info(f"USC 响应: keys={list(data.keys())}, username={username}")
        # 尝试多种可能的中文名字段
        for key in ('cnName', 'userName', 'realName', 'name', 'displayName', 'chineseName'):
            val = (data.get(key) or '').strip()
            if val and val != username:
                log.info(f"USC 提取中文名成功: key={key}, value={val}, username={username}")
                return val
        log.warning(f"USC 返回数据中无中文名字段: data={json.dumps(data, ensure_ascii=False)[:300]}, username={username}")
        return ''
    except Exception as e:
        log.error(f"USC 查询异常: {e}, username={username}")
        return ''
