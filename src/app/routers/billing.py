"""수납 (원장 전용): 월별 청구·납부, 완납 체크, 엑셀 내보내기, 현금영수증 발급 필요 목록.

입금 자동 매칭(7주차)은 같은 화면의 '입금 매칭' 탭으로 붙는다.
"""

from datetime import date, datetime, time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
from sqlmodel import Session, select

from app.auth import AdminUser
from app.config import get_settings
from app.db import DBSession
from app.models import Invoice, InvoiceStatus, Payment, PaymentMethod, Student
from app.schemas import InvoiceIn, PaymentIn, form_errors
from app.services import billing as svc
from app.services.excel import billing_workbook
from app.services.notify import events
from app.templating import FormData, hx_trigger, render, trigger_headers

router = APIRouter(prefix="/billing")

FILTERS = [
    ("", "전체"),
    ("unpaid", "미납"),
    ("partial", "부분 납부"),
    ("paid", "완납"),
    ("overdue", "기한 지남"),
]


def _month(value: str | None) -> date:
    try:
        return svc.parse_month(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="월을 확인해 주세요.") from None


def _get_invoice(db: Session, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404)
    return inv


def _filter(rows: list[svc.BillingRow], status: str, q: str, today: date) -> list[svc.BillingRow]:
    if status == "overdue":
        rows = [r for r in rows if r.overdue(today)]
    elif status in InvoiceStatus.__members__:
        rows = [r for r in rows if r.invoice.status == status]
    if q := q.strip():
        rows = [r for r in rows if q in r.student.name or q in r.student.payer_code]
    return rows


def _board_ctx(db: Session, month: date, status: str = "", q: str = "", method: str = "") -> dict:
    today = date.today()
    rows = svc.month_rows(db, month)
    return {
        "month": month,
        "ym": svc.ym(month),
        "prev_month": svc.ym(svc.shift_month(month, -1)),
        "next_month": svc.ym(svc.shift_month(month, 1)),
        "rows": _filter(rows, status, q, today),
        "summary": svc.summarize(db, month, rows, today),
        "unnotified": len(events.unnotified_invoices(db, month)),
        "status": status,
        "q": q,
        "method": method or PaymentMethod.transfer,
        "filters": FILTERS,
        "today": today,
        "labels": svc.STATUS_LABELS,
        "methods": svc.METHOD_LABELS,
    }


# --- 월별 청구·납부 ------------------------------------------------------------


@router.get("")
def index(
    request: Request,
    user: AdminUser,
    db: DBSession,
    month: str = "",
    status: str = "",
    q: str = "",
    method: str = "",
):
    ctx = {"user": user, **_board_ctx(db, _month(month), status, q, method)}
    return render(request, "billing/index.html", ctx, partial="billing/_board.html")


@router.post("/generate")
def generate(user: AdminUser, db: DBSession, month: str = ""):
    m = _month(month)
    n = svc.generate_invoices(db, m, get_settings().billing_due_day)
    db.commit()
    msg = f"{m.month}월 청구 {n}건을 만들었어요" if n else "새로 만들 청구가 없어요"
    return hx_trigger("invoiceSaved", toast=msg)


@router.post("/notify")
def notify(user: AdminUser, db: DBSession, month: str = ""):
    """청구 안내 문자를 아직 보내지 않은 청구에 보낸다 (대기열에 넣고 30초 안에 발송)."""
    n = events.send_invoice_notices(db, _month(month))
    db.commit()
    msg = (
        f"청구 안내 {n}건을 보낼게요"
        if n
        else "보낼 청구 안내가 없어요 (문자 받는 보호자가 없거나 이미 보냈어요)"
    )
    return hx_trigger("invoiceSaved", toast=msg)


@router.patch("/invoices/{invoice_id}/paid")
def toggle_paid(request: Request, user: AdminUser, db: DBSession, invoice_id: int, form: FormData):
    """완납 체크박스. 체크하면 남은 금액을 고른 결제 수단으로 기록하고, 해제하면 납부 기록을 지운다."""
    inv = _get_invoice(db, invoice_id)
    if form.get("paid") == "on":
        try:
            method = PaymentMethod(form.get("method") or PaymentMethod.transfer)
        except ValueError:
            raise HTTPException(status_code=400, detail="결제 수단을 확인해 주세요.") from None
        svc.mark_paid(db, inv, method)
        toast = f"{svc.METHOD_LABELS[method]} 완납으로 기록했어요"
    else:
        try:
            svc.clear_payments(db, inv)
        except svc.PaymentLocked:
            raise HTTPException(
                status_code=409,
                detail="입금 매칭으로 들어온 납부예요. 입금 매칭 화면에서 되돌려 주세요.",
            ) from None
        toast = "납부 기록을 지웠어요"
    db.commit()
    month = svc.parse_month(inv.year_month)
    ctx = _board_ctx(db, month, method=form.get("method", ""))
    return render(
        request,
        "billing/_row.html",
        ctx | {"r": svc.row_for(db, inv), "oob_summary": True},
        headers=trigger_headers(toast=toast),
    )


# --- 청구 상세 (시트) -------------------------------------------------------------


def _sheet_ctx(db: Session, inv: Invoice, **extra) -> dict:
    payments = db.exec(
        select(Payment).where(Payment.invoice_id == inv.id).order_by(Payment.paid_at)
    ).all()
    row = svc.row_for(db, inv)
    return {
        "inv": inv,
        "r": row,
        "payments": payments,
        "labels": svc.STATUS_LABELS,
        "methods": svc.METHOD_LABELS,
        "i_form": inv.model_dump(),
        "i_errors": {},
        "p_form": {
            "amount": max(row.rest, 0) or "",
            "method": PaymentMethod.transfer,
            "paid_on": date.today(),
        },
        "p_errors": {},
        "today": date.today(),
        **extra,
    }


@router.get("/invoices/{invoice_id}")
def invoice_sheet(request: Request, user: AdminUser, db: DBSession, invoice_id: int):
    inv = _get_invoice(db, invoice_id)
    return render(request, "billing/_sheet.html", _sheet_ctx(db, inv))


def _sheet_body(request, db, inv, toast=None, **extra):
    """시트 안쪽만 다시 그린다. 저장했으면 뒤의 목록도 새로고침한다."""
    headers = trigger_headers("invoiceSaved", toast=toast) if toast else None
    return render(
        request, "billing/_sheet_body.html", _sheet_ctx(db, inv, **extra), headers=headers
    )


@router.put("/invoices/{invoice_id}")
def update_invoice(
    request: Request, user: AdminUser, db: DBSession, invoice_id: int, form: FormData
):
    inv = _get_invoice(db, invoice_id)
    try:
        data = InvoiceIn.model_validate(form)
    except ValidationError as e:
        return _sheet_body(request, db, inv, i_form=form, i_errors=form_errors(e))
    inv.sqlmodel_update(data.model_dump())
    svc.refresh_status(db, inv)
    db.commit()
    return _sheet_body(request, db, inv, toast="청구 금액을 바꿨어요")


@router.post("/invoices/{invoice_id}/payments")
def add_payment(request: Request, user: AdminUser, db: DBSession, invoice_id: int, form: FormData):
    inv = _get_invoice(db, invoice_id)
    try:
        data = PaymentIn.model_validate(form)
    except ValidationError as e:
        return _sheet_body(request, db, inv, p_form=form, p_errors=form_errors(e))
    # 오늘이면 지금 시각, 지난 날짜면 그날 0시로 기록
    paid_at = (
        datetime.now() if data.paid_on == date.today() else datetime.combine(data.paid_on, time())
    )
    svc.add_payment(
        db, inv, amount=data.amount, method=data.method, paid_at=paid_at, memo=data.memo
    )
    db.commit()
    return _sheet_body(request, db, inv, toast=f"{data.amount:,}원 납부를 기록했어요")


@router.delete("/payments/{payment_id}")
def delete_payment(request: Request, user: AdminUser, db: DBSession, payment_id: int):
    p = db.get(Payment, payment_id)
    if p is None:
        raise HTTPException(status_code=404)
    try:
        inv = svc.delete_payment(db, p)
    except svc.PaymentLocked:
        raise HTTPException(
            status_code=409,
            detail="입금 매칭으로 들어온 납부예요. 입금 매칭 화면에서 되돌려 주세요.",
        ) from None
    db.commit()
    return _sheet_body(request, db, inv, toast="납부 기록을 지웠어요")


@router.delete("/invoices/{invoice_id}")
def delete_invoice(user: AdminUser, db: DBSession, invoice_id: int):
    """청구 취소. 납부 기록이 있으면 먼저 지워야 한다."""
    inv = _get_invoice(db, invoice_id)
    if db.exec(select(Payment.id).where(Payment.invoice_id == inv.id)).first():
        raise HTTPException(
            status_code=409,
            detail="납부 기록이 있는 청구는 취소할 수 없어요. 납부 기록을 먼저 지워 주세요.",
        )
    db.delete(inv)
    db.commit()
    return hx_trigger("invoiceSaved", toast="청구를 취소했어요")


# --- 엑셀 내보내기 -------------------------------------------------------------


@router.get("/export.xlsx")
def export(user: AdminUser, db: DBSession, month: str = ""):
    m = _month(month)
    rows = svc.month_rows(db, m)
    payments = db.exec(
        select(Payment, Invoice)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(Invoice.year_month == svc.ym(m))
        .order_by(Payment.paid_at)
    ).all()
    students = {r.invoice.id: r.student for r in rows}
    data = billing_workbook(
        f"{m.month}월",
        rows,
        [(p, inv, students[inv.id]) for p, inv in payments],
        svc.STATUS_LABELS,
        svc.METHOD_LABELS,
    )
    filename = quote(f"수납_{svc.ym(m)}.xlsx")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# --- 현금영수증 발급 필요 목록 -------------------------------------------------------


@router.get("/receipts")
def receipts(request: Request, user: AdminUser, db: DBSession, show: str = ""):
    issued = None if show == "all" else False
    return render(
        request,
        "billing/receipts.html",
        {
            "user": user,
            "rows": svc.receipt_rows(db, issued),
            "pending": len(svc.receipt_rows(db, False)),
            "show": show,
            "methods": svc.METHOD_LABELS,
        },
        partial="billing/_receipt_rows.html",
    )


@router.patch("/payments/{payment_id}/receipt")
def toggle_receipt(
    request: Request, user: AdminUser, db: DBSession, payment_id: int, form: FormData
):
    p = db.get(Payment, payment_id)
    if p is None or p.method not in svc.RECEIPT_METHODS:
        raise HTTPException(status_code=404)
    p.cash_receipt_issued = form.get("issued") == "on"
    db.add(p)
    db.commit()
    inv = db.get(Invoice, p.invoice_id)
    return render(
        request,
        "billing/_receipt_row.html",
        {
            "p": p,
            "inv": inv,
            "s": db.get(Student, inv.student_id),
            "methods": svc.METHOD_LABELS,
            "pending": len(svc.receipt_rows(db, False)),
            "oob_pending": True,
        },
        headers=trigger_headers(
            toast="발급 완료로 표시했어요" if p.cash_receipt_issued else "미발급으로 되돌렸어요"
        ),
    )
