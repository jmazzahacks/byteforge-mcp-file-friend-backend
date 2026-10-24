"""Loopback development server. Production serving uses Gunicorn."""

import os

from dotenv import load_dotenv

from byteforge_mcp_file_friend_backend import create_app

load_dotenv()
create_app().run(host="127.0.0.1", port=int(os.environ.get("FF_PORT", "8081")))
