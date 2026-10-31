"""알림을 만드는 규칙 (5.3절 표). 받는 사람은 '문자 받기'를 켠 보호자.

결석    수업 시작 10분 뒤. 그 전에 출석으로 바뀌면 취소. 당일 수업만.
청구    매월 1일 10시 (또는 수납 화면에서 바로)
리마인드 납부 기한 3일 전
미납    기한 다음 날, 이후 7일 간격 최대 2회

매일 10시 작업이 그날 보낼 것을 만든다. PC가 꺼져 있던 날이 있어도 다음 실행 때 빠진 것을 보낸다
(단, 미납 안내는 밀린 회차를 몰아 보내지 않고 가장 최근 회차 하나만).
"""

from datetime import date, datetime, timedelta

from sqlmodel import Session, col, select

from app.config import get_settings
from app.models import (
    Channel,
    ClassGroup,
    ClassSession,
    Guardian,
    Invoice,
    InvoiceStatus,
    Notification,
    Payment,
    Student,
)
from app.services import billing
from app.services.notify import outbox
from app.services.notify.templates import render

ABSENCE_DELAY = timedelta(minutes=10)
REMINDER_DAYS_BEFORE = 3
OVERDUE_INTERVAL_DAYS = 7
OVERDUE_MAX = 3  # 기한 다음 날 1회 + 이후 7일 간격 2회


def sms_recipients(db: Session, student_id: int) -> list[str]:
    rows = db.exec(
        select(Guardian.phone).where(Guardian.student_id == student_id, Guardian.notify_sms)
    ).all()
    return sorted({p for p in rows if p})


def email_recipients(db: Session, student_id: int) -> list[str]:
    rows = db.exec(
        select(Guardian.email).where(Guardian.student_id == student_id, Guardian.notify_email)
    ).all()
    return sorted({e for e in rows if e})


def _md(d: date) -> str:
    return f"{d.month}월 {d.day}일"


# --- 결석 -------------------------------------------------------------------


def absence_ref(session_id: int) -> str:
    return f"absence:{session_id}"


def schedule_absence(
    db: Session, session: ClassSession, student_id: int, now: datetime | None = None
) -> int:
    """결석으로 체크됐을 때. 지난 날짜 수업을 나중에 체크한 경우에는 보내지 않는다."""
    now = now or datetime.now()
    if session.date != now.date():
        return 0
    cls = db.get(ClassGroup, session.class_id)
    student = db.get(Student, student_id)
    body = render(
        db,
        "absence",
        {
            "학생명": student.name,
            "반명": cls.name,
            "날짜": _md(session.date),
            "시작시각": session.start_time.strftime("%H:%M"),
        },
    )
    when = max(now, datetime.combine(session.date, session.start_time) + ABSENCE_DELAY)
    made = 0
    for phone in sms_recipients(db, student_id):
        if outbox.enqueue(
            db,
            to=phone,
            template="absence",
            body=body,
            student_id=student_id,
            scheduled_at=when,
            ref=absence_ref(session.id),
        ):
            made += 1
    return made


def cancel_absence(db: Session, session: ClassSession, student_id: int) -> int:
    return outbox.cancel(db, ref=absence_ref(session.id), student_id=student_id)


# --- 수납 -------------------------------------------------------------------


def _invoice_values(db: Session, inv: Invoice, student: Student) -> dict[str, object]:
    month = billing.parse_month(inv.year_month)
    return {
        "학생명": student.name,
        "월": month.month,
        "금액": f"{billing.total_due(inv):,}",
        "남은금액": f"{max(billing.remaining(db, inv), 0):,}",
        "기한": _md(inv.due_date),
        "입금자명": student.payer_code,
        "계좌": get_settings().bank_account or "학원 계좌",
    }


def _notify_invoice(db: Session, inv: Invoice, key: str, ref: str) -> int:
    student = db.get(Student, inv.student_id)
    values = _invoice_values(db, inv, student)
    made = 0
    for channel, recipients in (
        (Channel.sms, sms_recipients(db, student.id)),
        (Channel.email, email_recipients(db, student.id)),
    ):
        # 이메일은 청구 안내만 (5.3절 표)
        if channel == Channel.email and key != "invoice":
            continue
        body = render(db, key, values, channel)
        for to in recipients:
            if outbox.enqueue(
                db, to=to, template=key, body=body, channel=channel, student_id=student.id, ref=ref
            ):
                made += 1
    return made


def payment_confirmed(db: Session, payment: Payment, inv: Invoice) -> int:
    """입금 매칭으로 납부가 확인되면 '납부 확인' 문자."""
    student = db.get(Student, inv.student_id)
    values = _invoice_values(db, inv, student) | {"금액": f"{payment.amount:,}"}
    body = render(db, "payment_confirmed", values)
    made = 0
    for to in sms_recipients(db, student.id):
        if outbox.enqueue(
            db,
            to=to,
            template="payment_confirmed",
            body=body,
            student_id=student.id,
            ref=f"payment:{payment.id}",
            dedupe=False,
        ):
            made += 1
    return made


def invoice_ref(inv: Invoice, kind: str) -> str:
    return f"invoice:{inv.id}:{kind}"


def unnotified_invoices(db: Session, month: date) -> list[Invoice]:
    """청구 안내를 아직 보내지 않은, 완납 전인 청구."""
    invoices = db.exec(
        select(Invoice).where(
            Invoice.year_month == billing.ym(month), Invoice.status != InvoiceStatus.paid
        )
    ).all()
    sent = set(
        db.exec(
            select(Notification.ref).where(col(Notification.ref).like("invoice:%:invoice"))
        ).all()
    )
    return [i for i in invoices if invoice_ref(i, "invoice") not in sent]


def send_invoice_notices(db: Session, month: date) -> int:
    return sum(
        _notify_invoice(db, inv, "invoice", invoice_ref(inv, "invoice"))
        for inv in unnotified_invoices(db, month)
    )


def overdue_round(inv: Invoice, today: date) -> int | None:
    """오늘 보낼 미납 안내 회차(0부터). 아직이면 None. 밀렸으면 가장 최근 회차."""
    first = inv.due_date + timedelta(days=1)
    if today < first:
        return None
    return min((today - first).days // OVERDUE_INTERVAL_DAYS, OVERDUE_MAX - 1)


def send_payment_reminders(db: Session, today: date | None = None) -> int:
    """리마인드(기한 3일 전 ~ 기한 전날)와 미납 안내."""
    today = today or date.today()
    made = 0
    unpaid = db.exec(select(Invoice).where(Invoice.status != InvoiceStatus.paid)).all()
    for inv in unpaid:
        if billing.remaining(db, inv) <= 0:
            continue
        if inv.due_date - timedelta(days=REMINDER_DAYS_BEFORE) <= today < inv.due_date:
            made += _notify_invoice(db, inv, "reminder", invoice_ref(inv, "reminder"))
        r = overdue_round(inv, today)
        if r is not None:
            made += _notify_invoice(db, inv, "overdue", f"{invoice_ref(inv, 'overdue')}:{r}")
    return made


def run_daily(db: Session, today: date | None = None) -> dict[str, int]:
    """매일 10시: 1일이면 청구 생성 → 청구 안내 → 리마인드·미납 안내."""
    today = today or date.today()
    month = today.replace(day=1)
    out = {"invoices": 0}
    if today.day == 1:
        out["invoices"] = billing.generate_invoices(db, month, get_settings().billing_due_day)
        db.flush()
    out["invoice_notices"] = send_invoice_notices(db, month)
    out["reminders"] = send_payment_reminders(db, today)
    db.commit()
    return out
