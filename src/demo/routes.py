"""데모 전용: 원장·강사로 바로 둘러보기, 데모 초기화."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import select

from app.db import DBSession
from app.models import User
from app.templating import render
from demo import seed

router = APIRouter(prefix="/demo")
intro_router = APIRouter()

# 소개 페이지·풋터에 보이는 정보
SITE = {
    "product": "Academy",
    "operator": "박종욱",
    "email": "jw.park363@gmail.com",
    "inquiry_url": "https://jwp-system.duckdns.org/inquiries",  # JWP-SYSTEM 문의 게시판
    "copyright": "JWP-SYSTEM",
}

# 소개 페이지의 '체험 코스': (제목, 설명, 역할, 바로 열 화면)
SCENARIOS = [
    ("아침에 할 일 확인", "대시보드에서 미납·확인할 입금·발송 실패를 한눈에", "admin", "/"),
    ("출결 체크", "휴대폰으로 이름을 톡톡 눌러 출석·지각·결석", "teacher", "/attendance"),
    (
        "입금 자동 확인",
        "은행 입금 문자를 넣으면 청구와 자동으로 맞춰져요",
        "admin",
        "/billing/deposits",
    ),
    (
        "문자 알림 미리보기",
        "결석·청구·미납 문자가 학부모에게 어떻게 가는지",
        "admin",
        "/notifications/test",
    ),
    ("수납 관리", "이번 달 청구, 완납 체크, 엑셀 내보내기", "admin", "/billing"),
    ("주간 시간표", "반별 색으로 보는 일주일, 휴강·보강까지", "admin", "/classes/calendar"),
]


def _safe_next(value: str | None) -> str:
    """사이트 안 주소만 허용 (//evil.com 같은 외부 주소로 보내지 않음)."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


@intro_router.get("/intro")
def intro(request: Request, db: DBSession):
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    return render(request, "intro.html", {"me": user, "scenarios": SCENARIOS, "site": SITE})


@router.post("/login/{role}")
def quick_login(request: Request, db: DBSession, role: str, next: str | None = None):
    email = seed.ACCOUNTS.get(role, (None,))[0]
    user = db.exec(select(User).where(User.email == email)).first() if email else None
    if user is None or not user.active:
        raise HTTPException(status_code=404, detail="데모 계정이 없어요. 데모를 초기화해 주세요.")
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/reset")
def reset(request: Request):
    seed.reset()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
