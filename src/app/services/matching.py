"""입금 알림 문자 파싱과 청구서 자동 매칭 (5.4절, 7주차).

입금 1건 = bank_transactions 1행. 문자(웹훅)와 거래내역 엑셀이 같은 경로로 들어온다.
중복은 (분 단위 시각, 금액, 입금자명)으로 거른다.

매칭 순서
1. 입금자명이 학생의 입금자명(payer_code)이나 학습된 별칭과 같고, 남은 금액이 입금액과 같은 청구가 있음 → 자동 처리
2. 이름은 맞지만 금액이 다름(부분 납부·형제 합산 등) 또는 학생 이름만 비슷함 → 확인 필요
3. 일치 없음 → 미확인 입금 (원장이 직접 연결)

원장이 직접 연결하면 그 입금자명을 학생 별칭으로 저장해 다음부터 자동 처리한다.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, col, or_, select

from app.models import (
    BankTransaction,
    Guardian,
    Invoice,
    InvoiceStatus,
    MatchStatus,
    Notification,
    NotificationStatus,
    PayerAlias,
    Payment,
    PaymentMethod,
    Student,
    TxSource,
)
from app.services import billing
from app.services.notify import events

# --- 문자 파싱 ---------------------------------------------------------------


@dataclass
class ParsedDeposit:
    received_at: datetime
    payer_name: str
    amount: int


_BANK_WORDS = (
    "web발신",
    "국외발신",
    "국제발신",
    "kb",
    "국민",
    "신한",
    "우리",
    "하나",
    "농협",
    "nh",
    "기업",
    "ibk",
    "sc",
    "제일",
    "씨티",
    "카카오",
    "토스",
    "케이뱅크",
    "새마을",
    "우체국",
    "수협",
    "신협",
    "부산",
    "대구",
    "경남",
    "광주",
    "전북",
    "제주",
    "산업",
    "은행",
    "뱅크",
)
_STOP_PREFIX = (
    "입금",
    "출금",
    "잔액",
    "이체",
    "체크",
    "승인",
    "누적",
    "계좌",
    "통장",
    "알림",
    "원",
)
_AMOUNT_AFTER_DEPOSIT = re.compile(r"입금\s*:?\s*([\d,]+)\s*원?")
_AMOUNT_LINE = re.compile(r"^\s*([\d,]+)\s*원?\s*$")
_DATETIME = re.compile(r"(\d{1,2})[/.\-월]\s?(\d{1,2})일?\s*[,\s]?\s*(\d{1,2}):(\d{2})")
_NAME_TOKEN = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9()]{0,15}")


def _to_int(s: str) -> int | None:
    digits = s.replace(",", "")
    return int(digits) if digits.isdigit() else None


def _amount(text: str) -> int | None:
    if m := _AMOUNT_AFTER_DEPOSIT.search(text):
        if (v := _to_int(m.group(1))) and v > 0:
            return v
    # "입금" 다음 줄에 금액만 있는 형식 (KB 등)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "입금" and i + 1 < len(lines):
            if m := _AMOUNT_LINE.match(lines[i + 1]):
                return _to_int(m.group(1))
    return None


def _when(text: str, now: datetime) -> datetime:
    m = _DATETIME.search(text)
    if not m:
        return now.replace(second=0, microsecond=0)
    month, day, hour, minute = map(int, m.groups())
    try:
        t = datetime(now.year, month, day, hour, minute)
    except ValueError:
        return now.replace(second=0, microsecond=0)
    # 연말에 받은 1월 문자처럼 미래로 계산되면 작년
    return t.replace(year=now.year - 1) if t > now + timedelta(days=1) else t


def _is_stopword(token: str) -> bool:
    low = token.lower()
    return any(low.startswith(w) for w in _BANK_WORDS) or token.startswith(_STOP_PREFIX)


def _name(text: str, known: set[str]) -> str | None:
    tokens = [t.strip("[]()<>") for t in re.split(r"[\s,/]+", text) if t.strip("[]()<>")]
    # 1) 등록된 입금자명·별칭·학생 이름이 그대로 있으면 그것
    for t in tokens:
        if t in known:
            return t
    # 2) 은행명·입금·잔액 같은 단어를 뺀 첫 번째 이름 후보
    for t in tokens:
        if _NAME_TOKEN.fullmatch(t) and not _is_stopword(t) and "*" not in t:
            return t
    return None


def parse_bank_sms(
    text: str, known_names: set[str] = frozenset(), now: datetime | None = None
) -> ParsedDeposit | None:
    """입금 알림 문자에서 입금자명·금액·시각을 뽑는다. 입금 문자가 아니면 None."""
    now = now or datetime.now()
    if "입금" not in text or "입금취소" in text or "출금" in text:
        return None
    amount = _amount(text)
    name = _name(text, known_names)
    if not amount or not name:
        return None
    return ParsedDeposit(_when(text, now), name, amount)


def known_names(db: Session) -> set[str]:
    codes = set(db.exec(select(Student.payer_code)).all())
    aliases = set(db.exec(select(PayerAlias.name)).all())
    names = set(db.exec(select(Student.name)).all())
    return codes | aliases | names


# --- 매칭 -------------------------------------------------------------------


def open_invoices(db: Session, student_ids: list[int]) -> list[Invoice]:
    """완납 전인 청구 (오래된 달부터)."""
    if not student_ids:
        return []
    return list(
        db.exec(
            select(Invoice)
            .where(col(Invoice.student_id).in_(student_ids), Invoice.status != InvoiceStatus.paid)
            .order_by(Invoice.year_month, Invoice.id)
        ).all()
    )


def students_by_payer(db: Session, name: str) -> list[Student]:
    """입금자명·별칭이 정확히 같은 학생."""
    alias_ids = select(PayerAlias.student_id).where(PayerAlias.name == name)
    return list(
        db.exec(
            select(Student).where(or_(Student.payer_code == name, col(Student.id).in_(alias_ids)))
        ).all()
    )


def students_by_name(db: Session, name: str) -> list[Student]:
    """학생 이름이 같거나 입금자명이 학생 이름으로 시작 (예: '홍길동3', '홍길동엄마')."""
    return [s for s in db.exec(select(Student)).all() if name == s.name or name.startswith(s.name)]


def match_transaction(db: Session, tx: BankTransaction) -> None:
    """새 입금을 매칭한다. 커밋은 호출자가 한다."""
    exact = students_by_payer(db, tx.payer_name)
    if len(exact) == 1:
        student = exact[0]
        tx.student_id = student.id
        for inv in open_invoices(db, [student.id]):
            if billing.remaining(db, inv) == tx.amount:
                apply(db, tx, [inv], MatchStatus.auto)
                return
        tx.match_status = MatchStatus.review
    else:
        loose = exact or students_by_name(db, tx.payer_name)
        tx.student_id = loose[0].id if len(loose) == 1 else None
        tx.match_status = MatchStatus.review if loose else MatchStatus.unmatched
    db.add(tx)


def apply(
    db: Session, tx: BankTransaction, invoices: list[Invoice], status: MatchStatus
) -> list[Payment]:
    """입금액을 청구들에 순서대로 나눠 납부로 기록한다. 남는 돈은 마지막 청구에 더한다."""
    left = tx.amount
    payments = []
    for i, inv in enumerate(invoices):
        last = i == len(invoices) - 1
        pay = left if last else min(left, max(billing.remaining(db, inv), 0))
        if pay <= 0:
            continue
        p = billing.add_payment(
            db,
            inv,
            amount=pay,
            method=PaymentMethod.transfer,
            paid_at=tx.received_at,
            memo="입금 매칭",
            bank_transaction_id=tx.id,
        )
        db.flush()
        payments.append(p)
        events.payment_confirmed(db, p, inv)
        left -= pay
    tx.student_id = invoices[0].student_id if invoices else tx.student_id
    tx.match_status = status
    db.add(tx)
    return payments


def unlink(db: Session, tx: BankTransaction) -> int:
    """매칭 되돌리기: 이 입금으로 만든 납부를 지우고 다시 '확인 필요'로."""
    payments = db.exec(select(Payment).where(Payment.bank_transaction_id == tx.id)).all()
    invoices = {p.invoice_id for p in payments}
    for p in payments:
        for n in db.exec(
            select(Notification).where(
                Notification.ref == f"payment:{p.id}",
                Notification.status == NotificationStatus.pending,
            )
        ).all():
            n.status = NotificationStatus.cancelled
            db.add(n)
        db.delete(p)
    db.flush()
    for inv_id in invoices:
        billing.refresh_status(db, db.get(Invoice, inv_id))
    tx.match_status = MatchStatus.review if tx.student_id else MatchStatus.unmatched
    db.add(tx)
    return len(payments)


def remember_alias(db: Session, name: str, student: Student) -> str | None:
    """입금자명을 학생 별칭으로 저장. 다른 학생이 이미 쓰는 이름이면 저장하지 않고 이유를 돌려준다."""
    if name == student.payer_code:
        return None
    owner = db.exec(select(PayerAlias).where(PayerAlias.name == name)).first()
    if owner and owner.student_id != student.id:
        return f"'{name}'은 다른 학생의 별칭이라 저장하지 않았어요"
    if not owner:
        db.add(PayerAlias(student_id=student.id, name=name))
    return None


def siblings(db: Session, student_id: int) -> list[Student]:
    """보호자 연락처가 같은 다른 학생 (형제 합산 입금 후보)."""
    phones = select(Guardian.phone).where(
        Guardian.student_id == student_id, col(Guardian.phone).is_not(None)
    )
    ids = select(Guardian.student_id).where(
        col(Guardian.phone).in_(phones), Guardian.student_id != student_id
    )
    return list(
        db.exec(select(Student).where(col(Student.id).in_(ids)).order_by(Student.name)).all()
    )


# --- 입금 받기 ----------------------------------------------------------------


@dataclass
class IngestResult:
    tx: BankTransaction | None
    created: bool
    reason: str = ""


def ingest(
    db: Session,
    *,
    received_at: datetime,
    payer_name: str,
    amount: int,
    source: TxSource,
    raw_text: str | None = None,
) -> IngestResult:
    """입금 1건을 저장하고 매칭한다. 이미 있는 입금이면 created=False. 커밋은 호출자가 한다."""
    received_at = received_at.replace(second=0, microsecond=0)
    payer_name = payer_name.strip()
    dup = db.exec(
        select(BankTransaction).where(
            BankTransaction.received_at == received_at,
            BankTransaction.amount == amount,
            BankTransaction.payer_name == payer_name,
        )
    ).first()
    if dup:
        return IngestResult(dup, False, "이미 받은 입금이에요")
    tx = BankTransaction(
        received_at=received_at,
        payer_name=payer_name,
        amount=amount,
        raw_text=raw_text,
        source=source,
    )
    db.add(tx)
    db.flush()
    match_transaction(db, tx)
    return IngestResult(tx, True)


def ingest_sms(db: Session, text: str, received_at: datetime | None = None) -> IngestResult:
    parsed = parse_bank_sms(text, known_names(db), now=received_at)
    if parsed is None:
        return IngestResult(None, False, "입금 문자가 아니거나 입금자명·금액을 찾지 못했어요")
    return ingest(
        db,
        received_at=parsed.received_at,
        payer_name=parsed.payer_name,
        amount=parsed.amount,
        source=TxSource.sms,
        raw_text=text,
    )


# --- 화면용 ----------------------------------------------------------------


@dataclass
class TxRow:
    tx: BankTransaction
    student: Student | None
    payments: list[tuple[Payment, Invoice]] = field(default_factory=list)


def tx_rows(db: Session, statuses: list[MatchStatus] | None, limit: int = 200) -> list[TxRow]:
    stmt = select(BankTransaction).order_by(col(BankTransaction.received_at).desc()).limit(limit)
    if statuses:
        stmt = stmt.where(col(BankTransaction.match_status).in_(statuses))
    txs = db.exec(stmt).all()
    pays: dict[int, list[tuple[Payment, Invoice]]] = {}
    for p, inv in db.exec(
        select(Payment, Invoice)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(col(Payment.bank_transaction_id).in_([t.id for t in txs]))
    ).all():
        pays.setdefault(p.bank_transaction_id, []).append((p, inv))
    students = {
        s.id: s
        for s in db.exec(
            select(Student).where(col(Student.id).in_({t.student_id for t in txs}))
        ).all()
    }
    return [TxRow(t, students.get(t.student_id), pays.get(t.id, [])) for t in txs]
