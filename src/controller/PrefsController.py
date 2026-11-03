"""用户偏好接口：只操作当前登录人自己的偏好"""
from flask import Blueprint, jsonify, request

from src.agent.memory import user_prefs as prefs
from src.controller.ChatController import _get_username
from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

PrefsController = Blueprint('prefs', __name__)


@PrefsController.route('/api/prefs', methods=['GET'])
@login_required
def list_prefs():
    username = _get_username()
    if not username:
        return jsonify({'error': '请先登录系统'}), 400
    rows = prefs.list_prefs(username)
    return jsonify({
        'items': [{'id': r['id'], 'content': r['content']} for r in rows],
        'max_items': prefs.MAX_ITEMS,
        'max_content': prefs.MAX_CONTENT,
    }), 200


@PrefsController.route('/api/prefs', methods=['POST'])
@login_required
def add_pref():
    username = _get_username()
    if not username:
        return jsonify({'error': '请先登录系统'}), 400
    content = str((request.get_json(silent=True) or {}).get('content') or '')
    ok, msg = prefs.add_pref(username, content)
    return jsonify({'ok': ok, 'message': msg}), (200 if ok else 400)


@PrefsController.route('/api/prefs/<int:pref_id>', methods=['DELETE'])
@login_required
def del_pref(pref_id: int):
    username = _get_username()
    if not username:
        return jsonify({'error': '请先登录系统'}), 400
    ok, msg = prefs.del_pref(username, pref_id=pref_id)
    return jsonify({'ok': ok, 'message': msg}), (200 if ok else 404)
