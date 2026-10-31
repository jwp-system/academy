"""대시보드 (9절 2번): 오늘 수업, 결석자, 이번 달 수납, 확인 필요 입금, 발송 실패, 할 일.
60초마다 본문 조각만 다시 그린다."""

from dataclasses import dataclass
from datetime import date, datetime

from fastapi import APIRouter, Request
from sqlmodel import Session, col, select

from app.auth import CurrentUser
from app.db import DBSession
from app.models import (
    Attendance,
    AttendanceStatus,
    BankTransaction,
    ClassGroup,
    ClassSession,
    Guardian,
    MatchStatus,
    Notification,
    NotificationStatus,
    Role,
    SessionStatus,
    Student,
    User,
)
from app.services import attendance, billing
from app.services.notify import channels, events
from app.services.notify import templates as tpl
from app.services.sessions import WEEKDAYS, teacher_names
from app.templating import render

router = APIRouter()


@dataclass
class Todo:
    label: str
    count: int
    href: str
    tone: str = "orange"  # orange | red | blue


def _today_label(d: date) -> str:
    return f"{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"


def _absentees(
    db: Session, session_ids: list[int]
) -> list[tuple[Student, ClassGroup, ClassSession, bool]]:
    """오늘 결석자. 마지막 값은 문자 받을 보호자가 있는지."""
    if not session_ids:
        return []
    rows = db.exec(
        select(Student, ClassGroup, ClassSession)
        .join(Attendance, Attendance.student_id == Student.id)
        .join(ClassSession, ClassSession.id == Attendance.session_id)
        .join(ClassGroup, ClassGroup.id == ClassSession.class_id)
        .where(
            col(Attendance.session_id).in_(session_ids),
            Attendance.status == AttendanceStatus.absent,
        )
        .order_by(ClassSession.start_time, Student.name)
    ).all()
    with_phone = set(
        db.exec(
            select(Guardian.student_id).where(Guardian.notify_sms, col(Guardian.phone).is_not(None))
        ).all()
    )
    return [(s, c, sess, s.id in with_phone) for s, c, sess in rows]


def _unchecked_past(sessions: list[ClassSession], counts: dict, now: datetime) -> int:
    """이미 끝났는데 미체크 학생이 남은 오늘 수업 수."""
    return sum(
        1
        for s in sessions
        if s.status != SessionStatus.cancelled
        and s.end_time <= now.time()
        and counts[s.id].unchecked
    )


def _admin_ctx(db: Session, today: date, sessions, counts, now: datetime) -> dict:
    month = today.replace(day=1)
    rows = billing.month_rows(db, month)
    summary = billing.summarize(db, month, rows, today)
    review = db.exec(
        select(BankTransaction)
        .where(col(BankTransaction.match_status).in_([MatchStatus.review, MatchStatus.unmatched]))
        .order_by(col(BankTransaction.received_at).desc())
    ).all()
    failed = db.exec(
        select(Notification, Student)
        .join(Student, Student.id == Notification.student_id, isouter=True)
        .where(Notification.status == NotificationStatus.failed)
        .order_by(col(Notification.scheduled_at).desc())
    ).all()
    receipts = len(billing.receipt_rows(db, False))
    unnotified = len(events.unnotified_invoices(db, month))
    todos = [
        Todo(f"{month.month}월 청구를 만들지 않은 재원생", summary.missing, "/billing", "blue"),
        Todo("청구 안내 문자를 보내지 않은 청구", unnotified, "/billing", "blue"),
        Todo("기한이 지난 미납", summary.overdue_count, "/billing?status=overdue", "red"),
        Todo("확인이 필요한 입금", len(review), "/billing/deposits"),
        Todo("발송에 실패한 알림", len(failed), "/notifications?status=failed", "red"),
        Todo("발급할 현금영수증", receipts, "/billing/receipts"),
        Todo(
            "끝났는데 출결을 다 체크하지 않은 수업",
            _unchecked_past(sessions, counts, now),
            "/attendance",
        ),
    ]
    return {
        "summary": summary,
        "month": month,
        "review": review[:5],
        "failed": failed[:5],
        "todos": [t for t in todos if t.count],
        "test_mode": channels.any_test_mode(),
        "tpl_label": tpl.label,
    }


def _ctx(db: Session, user: User) -> dict:
    now = datetime.now()
    today = now.date()
    pairs = attendance.sessions_on(db, user, today)
    sessions = [s for s, _ in pairs]
    counts = attendance.day_counts(db, sessions)
    active = [s for s in sessions if s.status != SessionStatus.cancelled]
    absentees = _absentees(db, [s.id for s in active])
    ctx = {
        "user": user,
        "today": today,
        "today_label": _today_label(today),
        "pairs": pairs,
        "counts": counts,
        "teachers": teacher_names(db),
        "ongoing": {s.id for s in sessions if attendance.is_ongoing(s, now)},
        "absentees": absentees,
        "stats": {
            "today_sessions": len(active),
            "absent_today": len(absentees),
            "unchecked": sum(counts[s.id].unchecked for s in active if s.start_time <= now.time()),
        },
        "now": now,
    }
    if user.role == Role.admin:
        ctx |= _admin_ctx(db, today, sessions, counts, now)
    else:
        n = _unchecked_past(sessions, counts, now)
        ctx["todos"] = (
            [Todo("끝났는데 출결을 다 체크하지 않은 수업", n, "/attendance")] if n else []
        )
    return ctx


@router.get("/")
def dashboard(request: Request, user: CurrentUser, db: DBSession):
    return render(request, "dashboard/index.html", _ctx(db, user), partial="dashboard/_body.html")
