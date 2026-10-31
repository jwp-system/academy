"""SMS 게이트웨이 수신 웹훅 (5.4절).

게이트웨이 앱(SMS Gateway for Android)의 sms:received 이벤트:
  {"event": "sms:received", "payload": {"message", "sender", "receivedAt", ...}, ...}

검증
- 서명 키(ACADEMY_SMS_WEBHOOK_SIGNING_KEY)가 있으면: X-Signature = hex(HMAC-SHA256(키, 원문 본문 + X-Timestamp)),
  X-Timestamp는 ±5분 이내.
- 없으면 URL의 ?token= 값(ACADEMY_SMS_WEBHOOK_TOKEN)으로 확인.
- 둘 다 없으면 받지 않는다.

앱은 2xx가 아니면 며칠 동안 재시도하므로, 입금 문자가 아니거나 이미 받은 문자도 200으로 답한다.
"""

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import get_settings
from app.db import DBSession
from app.services import matching

router = APIRouter(prefix="/webhooks")

MAX_SKEW_SECONDS = 300


def verify(body: bytes, headers, token: str | None, now: float | None = None) -> None:
    s = get_settings()
    if s.sms_webhook_signing_key:
        sig = headers.get("x-signature", "")
        ts = headers.get("x-timestamp", "")
        if not ts.isdigit() or abs((now or time.time()) - int(ts)) > MAX_SKEW_SECONDS:
            raise HTTPException(status_code=401, detail="timestamp")
        expected = hmac.new(
            s.sms_webhook_signing_key.encode(), body + ts.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig.lower(), expected):
            raise HTTPException(status_code=401, detail="signature")
    elif s.sms_webhook_token:
        if not token or not secrets.compare_digest(token, s.sms_webhook_token):
            raise HTTPException(status_code=401, detail="token")
    else:
        raise HTTPException(status_code=401, detail="webhook not configured")


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _received_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # 학원 현지 시각(naive)으로
    return t.astimezone(ZoneInfo(get_settings().timezone)).replace(tzinfo=None) if t.tzinfo else t


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/sms-inbound")
def sms_inbound(
    request: Request,
    db: DBSession,
    body: Annotated[bytes, Depends(_raw_body)],
    token: str | None = None,
):
    verify(body, request.headers, token)
    try:
        data = json.loads(body)
        payload = data["payload"]
        message, sender = payload["message"], payload.get("sender") or ""
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="bad payload") from None
    if data.get("event") != "sms:received":
        return {"status": "ignored", "reason": "event"}
    if _digits(sender) not in get_settings().bank_senders:
        return {"status": "ignored", "reason": "sender"}
    result = matching.ingest_sms(db, message, _received_at(payload.get("receivedAt")))
    db.commit()
    if result.tx is None:
        return {"status": "ignored", "reason": "not a deposit"}
    return {
        "status": "accepted" if result.created else "duplicate",
        "match": result.tx.match_status,
    }
