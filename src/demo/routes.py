"""데모 전용: 원장·강사로 바로 둘러보기, 데모 초기화."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import select

from app.db import DBSession
from app.models import User
from demo import seed

router = APIRouter(prefix="/demo")


@router.post("/login/{role}")
def quick_login(request: Request, db: DBSession, role: str):
    email = seed.ACCOUNTS.get(role, (None,))[0]
    user = db.exec(select(User).where(User.email == email)).first() if email else None
    if user is None or not user.active:
        raise HTTPException(status_code=404, detail="데모 계정이 없어요. 데모를 초기화해 주세요.")
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/reset")
def reset(request: Request):
    seed.reset()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
