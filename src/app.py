#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Flask 应用入口：注册路由、中间件、配置"""
import os
import uuid as _uuid
import time as _time

# LangSmith 必须在 langchain 首次 import 之前设置环境变量 → 这两行放在最前面
from config.Configs import cfg as _early_cfg
from src.infra.MetricsService import enable_langsmith as _enable_langsmith

_enable_langsmith(_early_cfg)

from flask import Flask, Blueprint, send_from_directory, jsonify, session, redirect, g, request
from src.controller.AuditController import AuditController
from src.controller.TraceController import TraceController
from src.controller.SandboxController import SandboxController
from src.controller.ChatController import ChatController
from src.controller.KnowledgeController import KnowledgeController
from src.controller.PrefsController import PrefsController
from src.controller.McpController import McpController
from src.controller.SkillController import SkillController
from src.controller.PlatformSkillController import PlatformSkillController
from flask_cors import CORS
from src.infra.db.database import close_db
from src.infra.utils.AuthUtil import init_cas, get_current_user, get_current_display_name, login_required
from config.Configs import cfg


app: Flask = Flask(__name__)
CORS(app)

# 上传口径（已确认）：**文件个数不限，单次上传总大小 ≤ 100MB**。
# 请求体上限比"文件总大小"略高，给 multipart 边界/头部留余量；
# 真正的 100MB 由 /api/chat/upload 按文件大小求和校验 —— 这样超限能返回一句人话（JSON），
# 而不是 Flask 的 413 默认页面。
TOTAL_UPLOAD_BYTES: int = 100 * 1024 * 1024
app.config['MAX_CONTENT_LENGTH'] = TOTAL_UPLOAD_BYTES + 8 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])


# ========== 请求 ID 中间件（追踪日志） ==========

@app.errorhandler(413)
def _payload_too_large(_e):
    """请求体超限：统一回 JSON（前端 await res.json() 才拿得到提示）"""
    return jsonify({'error': '上传内容太大：单次上传总大小不能超过 100MB'}), 413

@app.before_request
def assign_request_id():
    """为每个请求注入唯一 request_id 和开始时间戳"""
    g.request_id = request.headers.get('X-Request-ID') or _uuid.uuid4().hex[:12]
    g.request_start = _time.time()


@app.after_request
def log_request(response):
    """记录请求耗时"""
    elapsed = _time.time() - getattr(g, 'request_start', _time.time())
    rid = getattr(g, 'request_id', '-')
    # 跳过静态资源日志
    if not request.path.startswith('/open-agent-harness/assets/'):
        from src.infra.utils.LogUtil import log
        log.info(f"[{rid}] {request.method} {request.path} → {response.status_code} ({elapsed:.3f}s)")
    response.headers['X-Request-ID'] = rid
    # HTML 一律不缓存：否则浏览器会一直用旧的 index.html（它指向旧的 JS 文件名），
    # 表现为"代码明明更新了，界面一点变化都没有"。
    # API 也一律不缓存：JSON 没有缓存头时浏览器会按启发式规则复用旧响应，
    # 表现为"保存成功了、权限也生效了，界面还显示旧勾选"（角色工具勾选踩过）。
    try:
        if response.mimetype == 'text/html' or request.path.startswith('/open-agent-harness/api/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
    except Exception:
        pass
    return response


# ========== 初始化 CAS 单点登录 ==========
init_cas(app)

app.register_blueprint(AuditController, url_prefix='/open-agent-harness')
app.register_blueprint(TraceController, url_prefix='/open-agent-harness')
app.register_blueprint(SandboxController, url_prefix='/open-agent-harness')
app.register_blueprint(ChatController, url_prefix='/open-agent-harness')
app.register_blueprint(PrefsController, url_prefix='/open-agent-harness')
app.register_blueprint(KnowledgeController, url_prefix='/open-agent-harness')
app.register_blueprint(SkillController, url_prefix='/open-agent-harness')
app.register_blueprint(PlatformSkillController, url_prefix='/open-agent-harness')
app.register_blueprint(McpController, url_prefix='/open-agent-harness')
from src.controller.RbacController import RbacController
app.register_blueprint(RbacController, url_prefix='/open-agent-harness')
app.teardown_appcontext(close_db)

main_bp = Blueprint('main', __name__, url_prefix='/open-agent-harness')

# ========== 根路径重定向 ==========

@app.route('/')
def root_redirect():
    """访问根路径重定向到 /open-agent-harness/"""
    return redirect('/open-agent-harness/')

# ========== 托管前端静态文件 ==========

_DIST_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'dist'
)


@main_bp.route('/assets/<path:filename>')
def serve_assets(filename: str):
    """托管 dist/assets/ 下的 JS/CSS 文件"""
    return send_from_directory(os.path.join(_DIST_DIR, 'assets'), filename)


@main_bp.route('/api/version')
def api_version():
    """当前前端构建信息（排缓存用）：JS 文件名 + index.html 修改时间"""
    try:
        idx: str = os.path.join(_DIST_DIR, 'index.html')
        html: str = open(idx, encoding='utf-8').read() if os.path.isfile(idx) else ''
        import re as _re

        js: str = (_re.findall(r'assets/main-[A-Za-z0-9_-]+\.js', html) or [''])[0]
        mtime: float = os.path.getmtime(idx) if os.path.isfile(idx) else 0
        return jsonify({'ok': True, 'frontend_js': js, 'index_mtime': mtime}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 200


@main_bp.route('/test.html')
def serve_test():
    return send_from_directory(_DIST_DIR, 'test.html')


@main_bp.route('/')
@login_required
def serve_index():
    return send_from_directory(_DIST_DIR, 'index.html')


# ========== 用户认证相关路由 ==========

@main_bp.route('/logout')
def logout():
    """本地登出 + CAS 单点登出（对应 mars 的 /mars/logout）"""
    session.clear()
    cas_logout_url = f"{cfg.casServer}/logout?service={cfg.casAfterLogin}"
    return redirect(cas_logout_url)


@main_bp.route('/api/user/currentUser')
@login_required
def get_current_user_api():
    """获取当前登录用户信息（对应 mars 的 /user/currentUser）"""
    username = get_current_user()
    display_name = get_current_display_name()
    return jsonify({
        'userAccount': username,
        'username': username,
        'displayName': display_name,
    })


app.register_blueprint(main_bp)


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8000, use_reloader=False)