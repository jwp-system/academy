"""DB 모델 (기획안 6절, MVP 범위). 2차 테이블은 해당 기능을 만들 때 마이그레이션으로 추가한다.

시각은 모두 학원 현지 시각(Asia/Seoul)의 naive datetime으로 저장한다.
"""

import datetime as dt
from datetime import date, datetime, time
from enum import StrEnum

from pydantic import NaiveDatetime
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now()


class Role(StrEnum):
    admin = "admin"
    teacher = "teacher"


class StudentStatus(StrEnum):
    active = "active"  # 재원
    paused = "paused"  # 휴원
    withdrawn = "withdrawn"  # 퇴원


class SessionStatus(StrEnum):
    normal = "normal"
    cancelled = "cancelled"
    makeup = "makeup"


class AttendanceStatus(StrEnum):
    present = "present"
    late = "late"
    absent = "absent"
    excused = "excused"


class InvoiceStatus(StrEnum):
    unpaid = "unpaid"
    paid = "paid"
    partial = "partial"


class PaymentMethod(StrEnum):
    cash = "cash"
    transfer = "transfer"
    card = "card"


class TxSource(StrEnum):
    sms = "sms"
    excel = "excel"


class MatchStatus(StrEnum):
    auto = "auto"
    review = "review"
    unmatched = "unmatched"
    manual = "manual"


class Channel(StrEnum):
    sms = "sms"
    push = "push"
    email = "email"
    kakao = "kakao"


class NotificationStatus(StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    password_hash: str
    role: Role = Role.teacher
    subject: str | None = None
    active: bool = True


class Student(SQLModel, table=True):
    __tablename__ = "students"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    school: str | None = None
    grade: str | None = None
    phone: str | None = None
    status: StudentStatus = StudentStatus.active
    enrolled_at: date = Field(default_factory=date.today)
    withdrawn_at: date | None = None  # 보관 기간 산정용
    memo: str | None = None
    view_token: str = Field(unique=True, index=True)
    payer_code: str = Field(unique=True, index=True)


class PayerAlias(SQLModel, table=True):
    __tablename__ = "payer_aliases"

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="students.id", ondelete="CASCADE", index=True)
    name: str = Field(index=True)


class Guardian(SQLModel, table=True):
    __tablename__ = "guardians"

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="students.id", ondelete="CASCADE", index=True)
    name: str
    relation: str | None = None
    phone: str | None = None
    email: str | None = None
    notify_sms: bool = True
    notify_email: bool = False


class ClassGroup(SQLModel, table=True):
    """반. `class`가 예약어라 클래스명만 ClassGroup으로 둔다."""

    __tablename__ = "classes"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    subject: str | None = None
    teacher_id: int | None = Field(default=None, foreign_key="users.id", index=True)
    room: str | None = None
    capacity: int | None = None
    monthly_fee: int = 0
    active: bool = True


class ClassSchedule(SQLModel, table=True):
    """정규 시간표. weekday: 0=월 ~ 6=일."""

    __tablename__ = "class_schedules"

    id: int | None = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="classes.id", ondelete="CASCADE", index=True)
    weekday: int
    start_time: time
    end_time: time


class Enrollment(SQLModel, table=True):
    __tablename__ = "enrollments"

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="students.id", index=True)
    class_id: int = Field(foreign_key="classes.id", index=True)
    start_date: date
    end_date: date | None = None


class ClassSession(SQLModel, table=True):
    """실제 수업 회차. 휴강·보강·대강은 이 테이블만 수정한다."""

    __tablename__ = "sessions"

    id: int | None = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="classes.id", index=True)
    date: dt.date = Field(index=True)
    start_time: time
    end_time: time
    room: str | None = None
    status: SessionStatus = SessionStatus.normal
    teacher_id: int | None = Field(default=None, foreign_key="users.id", index=True)
    note: str | None = None


class Attendance(SQLModel, table=True):
    __tablename__ = "attendances"
    __table_args__ = (UniqueConstraint("session_id", "student_id"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="sessions.id", ondelete="CASCADE", index=True)
    student_id: int = Field(foreign_key="students.id", index=True)
    status: AttendanceStatus = AttendanceStatus.present
    memo: str | None = None


class Invoice(SQLModel, table=True):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("student_id", "year_month"),)

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="students.id", index=True)
    year_month: str = Field(index=True)  # "2026-11"
    amount: int
    discount: int = 0
    due_date: date
    status: InvoiceStatus = InvoiceStatus.unpaid


class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_transactions"
    # 엑셀 업로드 중복 제거 기준 (5.4절)
    __table_args__ = (UniqueConstraint("received_at", "amount", "payer_name"),)

    id: int | None = Field(default=None, primary_key=True)
    received_at: NaiveDatetime
    payer_name: str
    amount: int
    raw_text: str | None = None
    source: TxSource = TxSource.sms
    match_status: MatchStatus = MatchStatus.unmatched
    student_id: int | None = Field(default=None, foreign_key="students.id", index=True)


class Payment(SQLModel, table=True):
    __tablename__ = "payments"

    id: int | None = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoices.id", index=True)
    paid_at: NaiveDatetime = Field(default_factory=now)
    amount: int
    method: PaymentMethod
    bank_transaction_id: int | None = Field(default=None, foreign_key="bank_transactions.id")
    cash_receipt_issued: bool = False
    memo: str | None = None


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: int | None = Field(default=None, primary_key=True)
    student_id: int | None = Field(default=None, foreign_key="students.id", index=True)
    channel: Channel = Channel.sms
    to: str
    template: str  # message_templates.key
    body: str
    status: NotificationStatus = Field(default=NotificationStatus.pending, index=True)
    attempts: int = 0
    scheduled_at: NaiveDatetime = Field(default_factory=now, index=True)
    sent_at: NaiveDatetime | None = None
    error: str | None = None


class MessageTemplate(SQLModel, table=True):
    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("key", "channel"),)

    id: int | None = Field(default=None, primary_key=True)
    key: str  # absence, invoice, reminder, overdue, payment_confirmed ...
    channel: Channel = Channel.sms
    body: str


class Consultation(SQLModel, table=True):
    __tablename__ = "consultations"

    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="students.id", ondelete="CASCADE", index=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    date: dt.date = Field(default_factory=dt.date.today)
    content: str
