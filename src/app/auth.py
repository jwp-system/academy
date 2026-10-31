import secrets
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Role, User
from app.templating import render

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


# --- CSRF -----------------------------------------------------------------

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


async def verify_csrf(request: Request) -> None:
    """HTMX 요청은 헤더(base.html의 hx-headers), 일반 폼은 csrf_token 필드로 검증한다."""
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get(CSRF_HEADER)
    if sent is None and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        sent = (await request.form()).get("csrf_token")
    expected = request.session.get("csrf")
    if not expected or not isinstance(sent, str) or not secrets.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


# --- 현재 사용자 / 권한 ----------------------------------------------------


class LoginRequired(Exception):
    """main.py의 예외 핸들러가 로그인 페이지로 보낸다 (HTMX 요청이면 HX-Redirect)."""


def current_user(request: Request, db: Annotated[Session, Depends(get_session)]) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.active:
        request.session.pop("user_id", None)
        raise LoginRequired
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: Role):
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="이 메뉴를 쓸 권한이 없어요.")
        return user

    return dependency


AdminUser = Annotated[User, Depends(require_role(Role.admin))]


# --- 로그인 라우트 ---------------------------------------------------------

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth/login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    user = db.exec(select(User).where(User.email == email.strip().lower())).first()
    if user is None or not user.active or not verify_password(user.password_hash, password):
        return render(
            request,
            "auth/login.html",
            {"error": "이메일 또는 비밀번호를 다시 확인해 주세요.", "email": email},
            status_code=400,
        )
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.add(user)
        db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
