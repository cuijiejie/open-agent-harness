"""兼容入口（旧路径保留）

systemd 单元、部署脚本、评测脚本仍然用 `gunicorn AgentApp:app`，
真正的应用在 src/app.py。等所有引用都切过去之后再删掉本文件。
"""
from src.app import app  # noqa: F401

if __name__ == "__main__":
    import os

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
