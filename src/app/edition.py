"""판(edition): 같은 앱을 '구축판(academy, 학원 PC)'과 '데모판(demo, 네이버클라우드)'으로 나눠 띄우기 위한 훅.

공통 코드(app)는 판에 따라 달라지는 부분을 직접 알지 않고, 여기 Edition 값만 본다.
각 판의 패키지(src/academy, src/demo)가 Edition을 만들어 app.main.create_app(edition)에 넘긴다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter


@dataclass
class Edition:
    key: str  # "academy" | "demo" | "base"(테스트)
    label: str = ""  # 화면에 붙는 이름. 예: "데모"
    # False면 설정과 상관없이 문자·메일을 절대 실제로 보내지 않는다 (테스트 수신함에만 쌓임)
    allow_live_notifications: bool = True
    # False면 /webhooks/* 를 등록하지 않는다 (외부에서 입금 문자를 넣을 수 없음)
    allow_webhooks: bool = True
    # 공통 템플릿보다 먼저 찾을 템플릿 폴더 (같은 이름이면 덮어씀)
    template_dirs: list[Path] = field(default_factory=list)
    # 판 전용 화면 (CSRF 검증이 붙는다)
    routers: list[APIRouter] = field(default_factory=list)
    # 로그인 안 한 방문자가 첫 주소(/)로 오면 보낼 곳. None이면 로그인 화면 (데모판: 소개 페이지)
    landing_path: str | None = None
    # 원장 메뉴에 덧붙일 링크 [(주소, 이름, 아이콘)]
    nav_admin: list[tuple[str, str, str]] = field(default_factory=list)
    # 판 전용 스케줄러 작업 등록
    add_jobs: Callable[[BackgroundScheduler], None] | None = None
    # 앱 시작 시 한 번 (예: 데모 데이터 채우기)
    on_startup: Callable[[], None] | None = None


BASE = Edition(key="base")
_current: Edition = BASE


def current() -> Edition:
    return _current


def activate(edition: Edition) -> None:
    global _current
    _current = edition
