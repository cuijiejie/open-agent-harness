"""登录与会话工具模块（本地账号登录，不依赖任何单点登录系统）

设计取舍：
- 本项目定位是"可自托管的智能体平台"，默认不假设你有一台 CAS / OAuth 服务器。
- 登录态就是一个签名的 session cookie：`USERNAME` + `DISPLAY_NAME`。
- 口令是可选的：`[app] login_password` 为空时免密（适合内网演示 / 本机试用），
  设置后所有用户共用这一个口令（再配合 `test_login_enabled=false` 关闭免密入口）。
  真要做多用户账号体系，替换本文件的 `_authenticate()` 即可，其余代码只认 session。
"""
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template_string, request, session

from config.Configs import cfg
from src.infra.utils.LogUtil import log

# session 里的两个键：用户名、显示名
SESSION_USER = 'USERNAME'
SESSION_DISPLAY = 'DISPLAY_NAME'
SESSION_NEXT = 'AFTER_LOGIN_URL'

auth_bp = Blueprint('auth', __name__)


def init_auth(app) -> None:
    """注册登录路由与 session 密钥

    没有 SECRET_KEY 时**直接启动失败**：随机生成会让 6 个 worker 各拿一把钥匙，
    表现为"登录了又掉、换台机器又要登"，排查时很难想到是密钥问题。
    """
    secret = cfg.secretKey
    if not secret:
        raise RuntimeError(
            "SECRET_KEY 未配置（[app] secret_key / PA_APP_SECRET_KEY）："
            "多 worker 会各自随机生成导致登录态随机失效，请先配置再启动"
        )
    app.config['SECRET_KEY'] = secret
    app.register_blueprint(auth_bp, url_prefix='/open-agent-harness')


def get_current_user() -> str:
    """当前登录用户名，未登录返回空字符串"""
    return session.get(SESSION_USER, '')


def get_current_display_name() -> str:
    """当前用户显示名，未登录/未设置返回空字符串"""
    return session.get(SESSION_DISPLAY, '')


def login_required(f):
    """登录校验：未登录时接口返回 401，页面跳登录页（带 next 回跳）"""

    @wraps(f)
    def decorated(*args, **kwargs):
        user: str = get_current_user() or ""
        if not user:
            if request.path.startswith('/open-agent-harness/api/'):
                return jsonify({'error': '未登录', 'login_url': '/open-agent-harness/login'}), 401
            nxt = request.full_path if request.query_string else request.path
            return redirect(f"/open-agent-harness/login?next={nxt}")

        # 停用账号：连已登录的会话也立刻失效（管理员停用后 30 秒内生效）
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


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 · Open Agent Harness</title>
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
  .err { margin-top: 14px; font-size: 13px; color: #c0392b; }
</style>
</head>
<body>
  <form class="card" method="post">
    <h1>Open Agent Harness</h1>
    <p class="sub">{{ hint }}</p>
    <label for="username">用户名</label>
    <input id="username" name="username" placeholder="例如 alice" autofocus
           autocomplete="username" required>
    {% if need_password %}
    <label for="password" style="margin-top:14px">口令</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    {% endif %}
    <button type="submit">进入</button>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
  </form>
</body>
</html>"""


def _need_password() -> bool:
    return bool(str(getattr(cfg, 'loginPassword', '') or '').strip())


def _authenticate(username: str, password: str) -> bool:
    """校验口令：未配置口令 = 免密（内网演示）；配置了则必须一致"""
    if not _need_password():
        return True
    return password == cfg.loginPassword


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """账号登录：用户名 +（可选）口令"""
    if request.method == 'GET':
        if get_current_user():
            return redirect(request.args.get('next') or '/open-agent-harness/')
        return render_template_string(
            _LOGIN_HTML,
            need_password=_need_password(),
            hint='口令由部署者设置（[app] login_password）；留空即免密登录。'
                 if _need_password() else '输入用户名即可进入。',
            error='',
        )

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    if not username:
        return render_template_string(
            _LOGIN_HTML, need_password=_need_password(),
            hint='输入用户名即可进入。', error='用户名不能为空'), 400
    if not _authenticate(username, password):
        log.warning(f"登录失败（口令错误）: user={username}, ip={request.remote_addr}")
        return render_template_string(
            _LOGIN_HTML, need_password=True,
            hint='口令由部署者设置。', error='用户名或口令不正确'), 401

    from src.infra.RbacService import rbac as _rbac

    if _rbac.is_disabled(username):
        log.warning(f"停用账号尝试登录: user={username}, ip={request.remote_addr}")
        return render_template_string(
            _LOGIN_HTML, need_password=_need_password(),
            hint='输入用户名即可进入。', error='账号已停用，请联系管理员'), 403

    _rbac.touch_user(username, "")
    session[SESSION_USER] = username
    session[SESSION_DISPLAY] = ''
    log.info(f"登录成功: user={username}, ip={request.remote_addr}")
    _init_sandbox(username)
    nxt = str(session.pop(SESSION_NEXT, '') or request.args.get('next') or '')
    if not nxt.startswith('/open-agent-harness'):
        nxt = '/open-agent-harness/'
    return redirect(nxt)


@auth_bp.route('/logout')
def logout():
    """退出登录"""
    user = get_current_user()
    session.clear()
    log.info(f"退出登录: user={user}")
    return redirect('/open-agent-harness/login')


@auth_bp.route('/test-login', methods=['GET', 'POST'])
def test_login():
    """免密直登（脚本/冒烟测试用），需 test_login_enabled=true

    - 仅当 `[app] test_login_enabled` 为真时可用，默认关闭
    - GET 显示登录页；POST（表单或 JSON：{"username": "..."}）直接建立会话
    """
    if not cfg.testLoginEnabled:
        log.warning(f"test-login 被拒绝（配置未启用）: ip={request.remote_addr}")
        return '测试登录功能未启用（[app] test_login_enabled=false）', 403

    if request.method == 'GET':
        return redirect('/open-agent-harness/login')

    if request.is_json:
        username = str((request.get_json(silent=True) or {}).get('username', '')).strip()
    else:
        username = (request.form.get('username') or '').strip()
    if not username:
        return '用户名不能为空', 400

    from src.infra.RbacService import rbac as _rbac

    if _rbac.is_disabled(username):
        log.warning(f"停用账号尝试测试登录: user={username}, ip={request.remote_addr}")
        return '账号已停用，请联系管理员', 403
    _rbac.touch_user(username, "")
    session[SESSION_USER] = username
    session[SESSION_DISPLAY] = ''
    log.warning(f"【测试登录】免密登录成功: user={username}, ip={request.remote_addr}")
    _init_sandbox(username)
    return redirect('/open-agent-harness/')


def _init_sandbox(username: str) -> None:
    """登录后异步初始化该用户的沙箱（失败不影响登录）"""
    try:
        from src.agent.backends.sandbox import init_user_sandbox_async
        init_user_sandbox_async(username)
    except Exception as e:  # 沙箱不可用时不能影响登录
        log.warning(f"触发沙箱初始化失败: user={username}, err={e}")
