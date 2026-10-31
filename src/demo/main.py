"""데모판 ASGI 앱: uvicorn demo.main:app"""

from demo import prepare_env

prepare_env()

from app.main import create_app  # noqa: E402
from demo.edition import build  # noqa: E402

app = create_app(build())
