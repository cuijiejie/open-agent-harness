"""页面模板的唯一来源（Flask 侧改为从这里 import，避免两份 HTML 漂移）"""

LOGIN_HTML: str = """<!DOCTYPE html>
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
