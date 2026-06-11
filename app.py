"""
功能：提供 llmtre Flask 应用的本地启动入口。
"""

from __future__ import annotations

from web_api import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
