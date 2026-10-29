"""학부모 조회 전용 토큰 링크 (2차)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from academy.db import get_session
from academy.models import Student
from academy.templating import render

router = APIRouter(prefix="/p")


@router.get("/{token}")
def parent_view(request: Request, token: str, db: Annotated[Session, Depends(get_session)]):
    student = db.exec(select(Student).where(Student.view_token == token)).first()
    if student is None:
        raise HTTPException(status_code=404)
    return render(request, "placeholder.html", {"title": f"{student.name} 학생", "week": "2차"})
