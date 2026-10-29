from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from academy.auth import CurrentUser
from academy.db import get_session
from academy.models import (
    Attendance,
    AttendanceStatus,
    BankTransaction,
    ClassSession,
    Invoice,
    InvoiceStatus,
    MatchStatus,
    Notification,
    NotificationStatus,
    Role,
    SessionStatus,
)
from academy.templating import render

router = APIRouter()


def _count(db: Session, stmt) -> int:
    return db.exec(select(func.count()).select_from(stmt.subquery())).one()


def _stats(db: Session, user) -> dict[str, int]:
    today = date.today()
    sessions_q = select(ClassSession.id).where(
        ClassSession.date == today, ClassSession.status != SessionStatus.cancelled
    )
    if user.role == Role.teacher:
        sessions_q = sessions_q.where(ClassSession.teacher_id == user.id)
    stats = {
        "today_sessions": _count(db, sessions_q),
        "absent_today": _count(
            db,
            select(Attendance.id).where(
                Attendance.session_id.in_(sessions_q),
                Attendance.status == AttendanceStatus.absent,
            ),
        ),
    }
    if user.role == Role.admin:
        stats |= {
            "unpaid": _count(
                db,
                select(Invoice.id).where(
                    Invoice.year_month == today.strftime("%Y-%m"),
                    Invoice.status != InvoiceStatus.paid,
                ),
            ),
            "match_review": _count(
                db,
                select(BankTransaction.id).where(
                    BankTransaction.match_status.in_([MatchStatus.review, MatchStatus.unmatched])
                ),
            ),
            "send_failed": _count(
                db,
                select(Notification.id).where(Notification.status == NotificationStatus.failed),
            ),
        }
    return stats


def _today_label() -> str:
    d = date.today()
    return f"{d.month}월 {d.day}일 ({'월화수목금토일'[d.weekday()]})"


@router.get("/")
def dashboard(request: Request, user: CurrentUser, db: Annotated[Session, Depends(get_session)]):
    return render(
        request,
        "dashboard/index.html",
        {"user": user, "stats": _stats(db, user), "today": _today_label()},
        partial="dashboard/_cards.html",
    )
