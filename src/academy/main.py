"""구축판 ASGI 앱: uvicorn academy.main:app"""

from academy.edition import build
from app.main import create_app

app = create_app(build())
