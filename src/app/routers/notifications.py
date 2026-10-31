"""알림 (원장 전용): 발송 이력, 실패 재발송, 템플릿 편집, 테스트 발송·테스트 수신함."""

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session, col, select

from app.auth import AdminUser
from app.db import DBSession
from app.models import Channel, Notification, NotificationStatus, Student
from app.schemas import normalize_phone
from app.services.notify import channels, outbox
from app.services.notify import templates as tpl
from app.templating import FormData, hx_trigger, render, trigger_headers

router = APIRouter(prefix="/notifications")

STATUS = {
    "pending": ("대기", "grey"),
    "sent": ("발송", "blue"),
    "failed": ("실패", "red"),
    "cancelled": ("취소", "grey"),
}
PAGE = 50


def _common(user) -> dict:
    return {
        "user": user,
        "st": STATUS,
        "tpl_label": tpl.label,
        "channel_status": channels.statuses(),
        "test_mode": channels.any_test_mode(),
    }


# --- 발송 이력 ----------------------------------------------------------------


def _history_rows(db: Session, status: str, template: str, limit: int):
    stmt = (
        select(Notification, Student)
        .join(Student, Student.id == Notification.student_id, isouter=True)
        .order_by(col(Notification.scheduled_at).desc(), col(Notification.id).desc())
        .limit(limit)
    )
    if status in STATUS:
        stmt = stmt.where(Notification.status == status)
    if template:
        stmt = stmt.where(Notification.template == template)
    return db.exec(stmt).all()


@router.get("")
def history(
    request: Request,
    user: AdminUser,
    db: DBSession,
    status: str = "",
    template: str = "",
    limit: int = PAGE,
):
    limit = max(PAGE, min(limit, 500))
    rows = _history_rows(db, status, template, limit + 1)
    ctx = _common(user) | {
        "rows": rows[:limit],
        "more": len(rows) > limit,
        "limit": limit,
        "status": status,
        "template": template,
        "counts": outbox.counts(db),
        "templates": [(k, s.label) for k, s in tpl.SPECS.items()],
    }
    return render(request, "notifications/history.html", ctx, partial="notifications/_history.html")


@router.post("/process")
def process_now(user: AdminUser, db: DBSession):
    n = outbox.process_pending(db)
    msg = f"{n}건을 처리했어요" if n else "지금 보낼 알림이 없어요 (예약 시각 전이거나 야간이에요)"
    return hx_trigger("notificationsChanged", toast=msg)


def _get(db: Session, nid: int) -> Notification:
    n = db.get(Notification, nid)
    if n is None:
        raise HTTPException(status_code=404)
    return n


@router.post("/{nid}/retry")
def retry(user: AdminUser, db: DBSession, nid: int):
    n = _get(db, nid)
    if n.status != NotificationStatus.failed:
        raise HTTPException(status_code=400, detail="실패한 알림만 다시 보낼 수 있어요.")
    outbox.retry(db, n)
    db.commit()
    return hx_trigger("notificationsChanged", toast="다시 보내도록 대기열에 넣었어요")


@router.post("/{nid}/cancel")
def cancel(user: AdminUser, db: DBSession, nid: int):
    n = _get(db, nid)
    if n.status != NotificationStatus.pending:
        raise HTTPException(status_code=400, detail="대기 중인 알림만 취소할 수 있어요.")
    n.status = NotificationStatus.cancelled
    db.add(n)
    db.commit()
    return hx_trigger("notificationsChanged", toast="발송을 취소했어요")


# --- 템플릿 -----------------------------------------------------------------


def _tpl_ctx(db: Session, key: str, body: str | None = None, error: str | None = None) -> dict:
    spec = tpl.SPECS[key]
    return {
        "spec": spec,
        "body": body if body is not None else tpl.get_body(db, key),
        "custom": tpl.is_custom(db, key),
        "error": error,
        "sample": tpl.SAMPLE | {"학원명": tpl.render_body("{학원명}", {})},
    }


@router.get("/templates")
def templates_page(request: Request, user: AdminUser, db: DBSession):
    return render(
        request,
        "notifications/templates.html",
        _common(user) | {"items": [_tpl_ctx(db, k) for k in tpl.SPECS]},
    )


def _spec_or_404(key: str):
    if key not in tpl.SPECS:
        raise HTTPException(status_code=404)


@router.put("/templates/{key}")
def save_template(request: Request, user: AdminUser, db: DBSession, key: str, form: FormData):
    _spec_or_404(key)
    body = (form.get("body") or "").strip()
    if err := tpl.validate(key, body):
        return render(request, "notifications/_template.html", {"it": _tpl_ctx(db, key, body, err)})
    tpl.save(db, key, body)
    db.commit()
    return render(
        request,
        "notifications/_template.html",
        {"it": _tpl_ctx(db, key)},
        headers=trigger_headers(toast=f"{tpl.SPECS[key].label} 문구를 저장했어요"),
    )


@router.post("/templates/{key}/reset")
def reset_template(request: Request, user: AdminUser, db: DBSession, key: str):
    _spec_or_404(key)
    tpl.reset(db, key)
    db.commit()
    return render(
        request,
        "notifications/_template.html",
        {"it": _tpl_ctx(db, key)},
        headers=trigger_headers(toast="기본 문구로 되돌렸어요"),
    )


# --- 테스트 -----------------------------------------------------------------


def _inbox(db: Session, to: str = "") -> tuple[list, list[str]]:
    base = select(Notification).where(Notification.provider == "test")
    recipients = sorted(
        set(db.exec(select(Notification.to).where(Notification.provider == "test")).all())
    )
    stmt = base.order_by(col(Notification.sent_at).desc(), col(Notification.id).desc()).limit(100)
    if to:
        stmt = stmt.where(Notification.to == to)
    return db.exec(stmt).all(), recipients


def _test_ctx(
    db: Session, user, to: str = "", form: dict | None = None, errors: dict | None = None
) -> dict:
    messages, recipients = _inbox(db, to)
    return _common(user) | {
        "messages": messages,
        "recipients": recipients,
        "to": to,
        "fail": channels.TEST.fail,
        "form": form or {"channel": "sms"},
        "errors": errors or {},
    }


@router.get("/test")
def test_page(request: Request, user: AdminUser, db: DBSession, to: str = ""):
    return render(
        request,
        "notifications/test.html",
        _test_ctx(db, user, to),
        partial="notifications/_inbox.html",
    )


@router.get("/check")
def check(request: Request, user: AdminUser):
    """게이트웨이에 실제로 연결해 본다."""
    return render(
        request,
        "notifications/_channels.html",
        {"channel_status": channels.statuses(check=True), "checked": True},
    )


@router.post("/test/send")
def test_send(request: Request, user: AdminUser, db: DBSession, form: FormData):
    """테스트 발송. 실제 모드면 실제로 간다 (게이트웨이 동작 확인용)."""
    channel = Channel.email if form.get("channel") == "email" else Channel.sms
    body = (form.get("body") or "").strip()
    errors = {}
    to = (form.get("to") or "").strip()
    if channel == Channel.sms:
        try:
            to = normalize_phone(to) or ""
        except ValueError as e:
            errors["to"] = str(e)
    if not to and "to" not in errors:
        errors["to"] = "받는 곳을 입력해 주세요."
    if channel == Channel.email and to and "@" not in to:
        errors["to"] = "이메일 형식을 확인해 주세요."
    if not body:
        errors["body"] = "내용을 입력해 주세요."
    if errors:
        return render(
            request, "notifications/_test_form.html", _test_ctx(db, user, form=form, errors=errors)
        )
    n = outbox.enqueue(db, to=to, template="test", body=body, channel=channel, dedupe=False)
    outbox.send_now(db, n)
    db.commit()
    live = channels.mode(channel) == "live"
    if n.status == NotificationStatus.sent:
        toast = (
            "실제로 보냈어요. 휴대폰에서 확인해 주세요" if live else "테스트 수신함에 도착했어요"
        )
    else:
        toast = f"보내지 못했어요: {n.error}"
    return render(
        request,
        "notifications/_test_form.html",
        _test_ctx(db, user, form={"channel": form.get("channel"), "to": form.get("to")}),
        headers=trigger_headers("notificationsChanged", toast=toast),
    )


def _require_test_mode() -> None:
    if not channels.any_test_mode():
        raise HTTPException(status_code=400, detail="테스트 모드일 때만 쓸 수 있어요.")


@router.post("/test/flush")
def test_flush(user: AdminUser, db: DBSession):
    """예약 시각을 무시하고 테스트 모드 채널의 대기 알림을 모두 보낸다 (결석 10분 대기 등 확인용)."""
    _require_test_mode()
    n = outbox.process_pending(db, test_flush=True)
    return hx_trigger(
        "notificationsChanged", toast=f"{n}건을 보냈어요" if n else "대기 중인 테스트 알림이 없어요"
    )


@router.post("/test/fail")
def test_fail(user: AdminUser, form: FormData):
    """발송 실패 흉내 켜기/끄기 (서버를 다시 켜면 꺼진다)."""
    _require_test_mode()
    channels.TEST.fail = form.get("fail") == "on"
    return hx_trigger(
        "notificationsChanged",
        toast="이제부터 테스트 발송이 실패해요"
        if channels.TEST.fail
        else "테스트 발송이 다시 성공해요",
    )
