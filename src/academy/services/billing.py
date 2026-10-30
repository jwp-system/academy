"""수납 (5주차): 월 청구 생성, 납부 기록, 상태 계산.

- 청구는 학생당 한 달에 하나. 그 달에 하루라도 수강하는 운영 중인 반의 월 수강료 합계.
- 재원생만 청구한다(휴원·퇴원 제외). 이미 청구서가 있으면 건드리지 않는다.
- 청구 금액 = amount - discount. 납부 합계로 상태(미납/부분 납부/완납)를 다시 계산한다.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session, col, func, select

from academy.models import (
    ClassGroup,
    Enrollment,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    Student,
    StudentStatus,
)

METHOD_LABELS = {
    PaymentMethod.transfer: "계좌이체",
    PaymentMethod.cash: "현금",
    PaymentMethod.card: "카드",
}
STATUS_LABELS = {
    InvoiceStatus.unpaid: "미납",
    InvoiceStatus.partial: "부분 납부",
    InvoiceStatus.paid: "완납",
}
# 현금영수증 발급 대상: 계좌이체와 현금 (카드는 단말기에서 처리)
RECEIPT_METHODS = (PaymentMethod.transfer, PaymentMethod.cash)


def parse_month(value: str | None, today: date | None = None) -> date:
    """'2026-11' → date(2026, 11, 1). 비어 있으면 이번 달. 형식이 틀리면 ValueError."""
    if not value:
        return (today or date.today()).replace(day=1)
    return date.fromisoformat(f"{value}-01")


def ym(month: date) -> str:
    return month.strftime("%Y-%m")


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def due_date_for(month: date, due_day: int) -> date:
    return month.replace(day=min(due_day, calendar.monthrange(month.year, month.month)[1]))


def shift_month(month: date, delta: int) -> date:
    y, m = divmod(month.year * 12 + month.month - 1 + delta, 12)
    return date(y, m + 1, 1)


# --- 청구 대상 계산 --------------------------------------------------------------


def classes_by_student(db: Session, month: date) -> dict[int, list[ClassGroup]]:
    """그 달에 하루라도 수강하는 운영 중인 반 (학생별)."""
    end = month_end(month)
    rows = db.exec(
        select(Enrollment.student_id, ClassGroup)
        .join(ClassGroup, ClassGroup.id == Enrollment.class_id)
        .where(
            ClassGroup.active,
            Enrollment.start_date <= end,
            (col(Enrollment.end_date).is_(None)) | (col(Enrollment.end_date) >= month),
        )
        .order_by(ClassGroup.name)
    ).all()
    out: dict[int, list[ClassGroup]] = {}
    for student_id, cls in rows:
        if cls.id not in {c.id for c in out.get(student_id, [])}:
            out.setdefault(student_id, []).append(cls)
    return out


@dataclass
class Billable:
    student: Student
    classes: list[ClassGroup]

    @property
    def amount(self) -> int:
        return sum(c.monthly_fee for c in self.classes)


def billable(db: Session, month: date) -> list[Billable]:
    """아직 청구서가 없는, 청구할 재원생."""
    by_student = classes_by_student(db, month)
    invoiced = set(db.exec(select(Invoice.student_id).where(Invoice.year_month == ym(month))).all())
    students = db.exec(
        select(Student)
        .where(Student.status == StudentStatus.active, col(Student.id).in_(list(by_student)))
        .order_by(Student.name)
    ).all()
    out = [Billable(s, by_student[s.id]) for s in students if s.id not in invoiced]
    return [b for b in out if b.amount > 0]


def generate_invoices(db: Session, month: date, due_day: int) -> int:
    """청구서를 만든다. 생성 개수를 반환. 커밋은 호출자가 한다."""
    due = due_date_for(month, due_day)
    targets = billable(db, month)
    for b in targets:
        db.add(
            Invoice(student_id=b.student.id, year_month=ym(month), amount=b.amount, due_date=due)
        )
    return len(targets)


# --- 납부 -----------------------------------------------------------------


def total_due(inv: Invoice) -> int:
    return max(inv.amount - inv.discount, 0)


def paid_amounts(db: Session, invoice_ids: list[int]) -> dict[int, int]:
    if not invoice_ids:
        return {}
    return dict(
        db.exec(
            select(Payment.invoice_id, func.sum(Payment.amount))
            .where(col(Payment.invoice_id).in_(invoice_ids))
            .group_by(Payment.invoice_id)
        ).all()
    )


def refresh_status(db: Session, inv: Invoice) -> None:
    db.flush()
    paid = paid_amounts(db, [inv.id]).get(inv.id, 0)
    if paid >= total_due(inv):
        inv.status = InvoiceStatus.paid
    elif paid > 0:
        inv.status = InvoiceStatus.partial
    else:
        inv.status = InvoiceStatus.unpaid
    db.add(inv)


def add_payment(
    db: Session,
    inv: Invoice,
    *,
    amount: int,
    method: PaymentMethod,
    paid_at: datetime | None = None,
    memo: str | None = None,
    bank_transaction_id: int | None = None,
) -> Payment:
    p = Payment(
        invoice_id=inv.id,
        amount=amount,
        method=method,
        paid_at=paid_at or datetime.now(),
        memo=memo,
        bank_transaction_id=bank_transaction_id,
    )
    db.add(p)
    refresh_status(db, inv)
    return p


def remaining(db: Session, inv: Invoice) -> int:
    return total_due(inv) - paid_amounts(db, [inv.id]).get(inv.id, 0)


def mark_paid(db: Session, inv: Invoice, method: PaymentMethod) -> Payment | None:
    """완납 체크: 남은 금액만큼 납부를 기록한다. 이미 완납이면 아무것도 하지 않는다."""
    rest = remaining(db, inv)
    if rest <= 0:
        refresh_status(db, inv)
        return None
    return add_payment(db, inv, amount=rest, method=method)


class PaymentLocked(Exception):
    """입금 매칭으로 들어온 납부는 매칭 화면에서만 되돌린다 (7주차)."""


def clear_payments(db: Session, inv: Invoice) -> int:
    """완납 체크 해제: 이 청구의 납부 기록을 모두 지운다."""
    payments = db.exec(select(Payment).where(Payment.invoice_id == inv.id)).all()
    if any(p.bank_transaction_id for p in payments):
        raise PaymentLocked
    for p in payments:
        db.delete(p)
    refresh_status(db, inv)
    return len(payments)


def delete_payment(db: Session, p: Payment) -> Invoice:
    if p.bank_transaction_id:
        raise PaymentLocked
    inv = db.get(Invoice, p.invoice_id)
    db.delete(p)
    refresh_status(db, inv)
    return inv


# --- 월별 수납 현황 -------------------------------------------------------------


@dataclass
class BillingRow:
    invoice: Invoice
    student: Student
    classes: list[ClassGroup]
    paid: int
    last_payment: Payment | None

    @property
    def due(self) -> int:
        return total_due(self.invoice)

    @property
    def rest(self) -> int:
        return self.due - self.paid

    def overdue(self, today: date) -> bool:
        return self.rest > 0 and self.invoice.due_date < today


@dataclass
class Summary:
    count: int = 0
    billed: int = 0
    paid: int = 0
    unpaid_count: int = 0
    overdue_count: int = 0
    missing: int = 0  # 아직 청구서가 없는 청구 대상 수
    by_status: dict[str, int] = field(default_factory=dict)

    @property
    def rest(self) -> int:
        return max(self.billed - self.paid, 0)


def month_rows(db: Session, month: date) -> list[BillingRow]:
    pairs = db.exec(
        select(Invoice, Student)
        .join(Student, Student.id == Invoice.student_id)
        .where(Invoice.year_month == ym(month))
        .order_by(Student.name)
    ).all()
    ids = [inv.id for inv, _ in pairs]
    paid = paid_amounts(db, ids)
    last: dict[int, Payment] = {}
    for p in db.exec(
        select(Payment).where(col(Payment.invoice_id).in_(ids)).order_by(Payment.paid_at)
    ).all():
        last[p.invoice_id] = p
    by_student = classes_by_student(db, month)
    return [
        BillingRow(inv, s, by_student.get(s.id, []), paid.get(inv.id, 0), last.get(inv.id))
        for inv, s in pairs
    ]


def row_for(db: Session, inv: Invoice) -> BillingRow:
    month = parse_month(inv.year_month)
    student = db.get(Student, inv.student_id)
    payments = db.exec(
        select(Payment).where(Payment.invoice_id == inv.id).order_by(Payment.paid_at)
    ).all()
    return BillingRow(
        inv,
        student,
        classes_by_student(db, month).get(student.id, []),
        sum(p.amount for p in payments),
        payments[-1] if payments else None,
    )


def summarize(db: Session, month: date, rows: list[BillingRow], today: date) -> Summary:
    s = Summary(count=len(rows), missing=len(billable(db, month)))
    for r in rows:
        s.billed += r.due
        s.paid += min(r.paid, r.due)
        if r.rest > 0:
            s.unpaid_count += 1
        if r.overdue(today):
            s.overdue_count += 1
        s.by_status[r.invoice.status] = s.by_status.get(r.invoice.status, 0) + 1
    return s


# --- 현금영수증 ---------------------------------------------------------------


def receipt_rows(
    db: Session, issued: bool | None = False
) -> list[tuple[Payment, Invoice, Student]]:
    stmt = (
        select(Payment, Invoice, Student)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .join(Student, Student.id == Invoice.student_id)
        .where(col(Payment.method).in_(RECEIPT_METHODS))
        .order_by(col(Payment.paid_at).desc())
    )
    if issued is not None:
        stmt = stmt.where(Payment.cash_receipt_issued == issued)
    return list(db.exec(stmt).all())
