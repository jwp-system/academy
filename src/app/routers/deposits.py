"""입금 매칭 (원장 전용): 자동 처리 내역, 확인 필요, 미확인 입금, 거래내역 업로드, 입금 문자 테스트."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlmodel import Session, col, func, select

from app.auth import AdminUser
from app.config import get_settings
from app.db import DBSession
from app.models import BankTransaction, Invoice, MatchStatus, Payment, Student, TxSource
from app.services import billing, matching
from app.services.excel import parse_bank_statement
from app.templating import FormData, MultiFormData, hx_trigger, render, trigger_headers

router = APIRouter(prefix="/billing/deposits")

FILTERS = {
    "": ("확인 필요", [MatchStatus.review, MatchStatus.unmatched]),
    "auto": ("자동 처리", [MatchStatus.auto]),
    "manual": ("직접 연결", [MatchStatus.manual]),
    "ignored": ("무시", [MatchStatus.ignored]),
    "all": ("전체", None),
}
STATUS = {
    "auto": ("자동 처리", "blue"),
    "manual": ("직접 연결", "green"),
    "review": ("확인 필요", "orange"),
    "unmatched": ("미확인", "red"),
    "ignored": ("무시", "grey"),
}


def _get_tx(db: Session, tx_id: int) -> BankTransaction:
    tx = db.get(BankTransaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404)
    return tx


def _counts(db: Session) -> dict[str, int]:
    return dict(
        db.exec(
            select(BankTransaction.match_status, func.count()).group_by(
                BankTransaction.match_status
            )
        ).all()
    )


def _webhook_status(db: Session) -> dict:
    s = get_settings()
    last = db.exec(
        select(BankTransaction.received_at)
        .where(BankTransaction.source == TxSource.sms)
        .order_by(col(BankTransaction.received_at).desc())
    ).first()
    method = (
        "signing_key" if s.sms_webhook_signing_key else ("token" if s.sms_webhook_token else None)
    )
    return {
        "method": method,
        "senders": sorted(s.bank_senders),
        "last_sms": last,
        "ready": bool(method and s.bank_senders),
    }


def _samples(db: Session) -> list[tuple[str, str]]:
    """테스트용 입금 문자 예시. 청구가 있는 학생의 실제 입금자명·남은 금액으로 만든다."""
    now = datetime.now()
    stamp = f"{now.month:02d}/{now.day:02d} {now:%H:%M}"
    inv = db.exec(select(Invoice).where(Invoice.status != "paid").order_by(Invoice.id)).first()
    code, amount = "홍길동1", 280000
    if inv:
        code = db.get(Student, inv.student_id).payer_code
        amount = max(billing.remaining(db, inv), 1)
    return [
        (
            "자동 처리될 문자 (국민)",
            f"[KB]{stamp}\n123456**789\n{code}\n입금\n{amount:,}\n잔액1,234,567",
        ),
        (
            "금액이 다른 문자 (신한)",
            f"[Web발신]\n신한{stamp}\n110-***-123456\n입금 {amount // 2:,}\n잔액 2,345,678\n{code}",
        ),
        (
            "모르는 이름 (우리)",
            f"[Web발신]\n우리 {stamp}\n1002***123456\n입금 150,000원\n김모름\n잔액 3,456,789원",
        ),
        (
            "출금 문자 (무시돼요)",
            f"[Web발신]\n하나 {stamp}\n출금 50,000원\n관리비\n잔액 1,000,000원",
        ),
    ]


# --- 목록 -----------------------------------------------------------------


def _list_ctx(db: Session, show: str) -> dict:
    show = show if show in FILTERS else ""
    return {
        "rows": matching.tx_rows(db, FILTERS[show][1]),
        "show": show,
        "filters": FILTERS,
        "counts": _counts(db),
        "st": STATUS,
        "methods": billing.METHOD_LABELS,
    }


@router.get("")
def index(request: Request, user: AdminUser, db: DBSession, show: str = ""):
    ctx = {
        "user": user,
        **_list_ctx(db, show),
        "hook": _webhook_status(db),
        "samples": _samples(db),
        "base_url": str(request.base_url),
    }
    return render(request, "deposits/index.html", ctx, partial="deposits/_list.html")


# --- 직접 연결 시트 -------------------------------------------------------------


def _invoice_options(db: Session, students: list[Student]) -> list[dict]:
    by_id = {s.id: s for s in students}
    return [
        {"inv": inv, "student": by_id[inv.student_id], "rest": billing.remaining(db, inv)}
        for inv in matching.open_invoices(db, list(by_id))
    ]


@router.get("/{tx_id}/link")
def link_sheet(request: Request, user: AdminUser, db: DBSession, tx_id: int):
    tx = _get_tx(db, tx_id)
    suggested: list[Student] = []
    if tx.student_id:
        main = db.get(Student, tx.student_id)
        suggested = [main, *matching.siblings(db, main.id)]
    else:
        suggested = matching.students_by_name(db, tx.payer_name)
    return render(
        request,
        "deposits/_link.html",
        {"tx": tx, "options": _invoice_options(db, suggested), "suggested": suggested},
    )


@router.get("/{tx_id}/search")
def search(request: Request, user: AdminUser, db: DBSession, tx_id: int, q: str = ""):
    tx = _get_tx(db, tx_id)
    students = []
    if q := q.strip():
        students = db.exec(
            select(Student)
            .where(col(Student.name).contains(q) | col(Student.payer_code).contains(q))
            .order_by(Student.name)
            .limit(10)
        ).all()
    return render(
        request,
        "deposits/_options.html",
        {"tx": tx, "options": _invoice_options(db, list(students)), "searched": bool(q)},
    )


@router.post("/{tx_id}/link")
def link(request: Request, user: AdminUser, db: DBSession, tx_id: int, form: MultiFormData):
    tx = _get_tx(db, tx_id)
    if tx.match_status in (MatchStatus.auto, MatchStatus.manual):
        raise HTTPException(
            status_code=409, detail="이미 연결된 입금이에요. 되돌린 뒤 다시 연결해 주세요."
        )
    ids = [int(i) for i in form.getlist("invoice_id") if str(i).isdigit()]
    invoices = [inv for inv in (db.get(Invoice, i) for i in ids) if inv is not None]
    if not invoices:
        raise HTTPException(status_code=400, detail="연결할 청구를 골라 주세요.")
    invoices.sort(key=lambda i: (i.year_month, i.id))
    matching.apply(db, tx, invoices, MatchStatus.manual)
    note = ""
    if form.get("remember") == "on":
        note = (
            matching.remember_alias(db, tx.payer_name, db.get(Student, invoices[0].student_id))
            or ""
        )
    db.commit()
    toast = f"{tx.payer_name} 입금 {tx.amount:,}원을 연결했어요" + (f" · {note}" if note else "")
    return hx_trigger("depositsChanged", "invoiceSaved", toast=toast)


@router.post("/{tx_id}/unlink")
def unlink(user: AdminUser, db: DBSession, tx_id: int):
    tx = _get_tx(db, tx_id)
    n = matching.unlink(db, tx)
    db.commit()
    return hx_trigger(
        "depositsChanged", "invoiceSaved", toast=f"연결을 되돌렸어요 (납부 기록 {n}건 삭제)"
    )


@router.post("/{tx_id}/ignore")
def ignore(user: AdminUser, db: DBSession, tx_id: int):
    tx = _get_tx(db, tx_id)
    if db.exec(select(Payment.id).where(Payment.bank_transaction_id == tx.id)).first():
        raise HTTPException(status_code=409, detail="연결된 입금은 먼저 되돌려 주세요.")
    tx.match_status = MatchStatus.ignored
    db.add(tx)
    db.commit()
    return hx_trigger("depositsChanged", toast="수강료가 아닌 입금으로 표시했어요")


@router.post("/{tx_id}/restore")
def restore(user: AdminUser, db: DBSession, tx_id: int):
    tx = _get_tx(db, tx_id)
    if tx.match_status != MatchStatus.ignored:
        raise HTTPException(status_code=400)
    matching.match_transaction(db, tx)
    db.commit()
    return hx_trigger("depositsChanged", "invoiceSaved", toast="다시 매칭했어요")


# --- 거래내역 업로드 -------------------------------------------------------------


@router.post("/upload")
def upload(request: Request, user: AdminUser, db: DBSession, file: Annotated[UploadFile, File()]):
    data = file.file.read()
    parsed = parse_bank_statement(data, file.filename or "")
    summary = {"auto": 0, "review": 0, "unmatched": 0, "duplicate": 0}
    if not parsed.error:
        for row in parsed.rows:
            r = matching.ingest(
                db,
                received_at=row.received_at,
                payer_name=row.payer_name,
                amount=row.amount,
                source=TxSource.excel,
                raw_text=f"{file.filename} {row.row_no}행",
            )
            key = r.tx.match_status if r.created else "duplicate"
            summary[key] = summary.get(key, 0) + 1
        db.commit()
    return render(
        request,
        "deposits/_upload_result.html",
        {"error": parsed.error, "total": len(parsed.rows), "summary": summary},
        headers=trigger_headers("depositsChanged", "invoiceSaved") if not parsed.error else None,
    )


# --- 입금 문자 테스트 -------------------------------------------------------------


@router.post("/preview")
def preview(request: Request, user: AdminUser, db: DBSession, form: FormData):
    text = form.get("text") or ""
    parsed = matching.parse_bank_sms(text, matching.known_names(db)) if text.strip() else None
    return render(request, "deposits/_preview.html", {"parsed": parsed, "text": text})


@router.post("/simulate")
def simulate(user: AdminUser, db: DBSession, form: FormData):
    """게이트웨이 웹훅과 같은 처리(파싱 → 저장 → 매칭)를 화면에서 돌려 본다. 발신번호 확인만 건너뛴다."""
    text = form.get("text") or ""
    r = matching.ingest_sms(db, text)
    db.commit()
    if r.tx is None:
        toast = r.reason
    elif not r.created:
        toast = r.reason
    else:
        toast = f"{r.tx.payer_name} {r.tx.amount:,}원 → {STATUS[r.tx.match_status][0]}"
    return hx_trigger("depositsChanged", "invoiceSaved", "notificationsChanged", toast=toast)
