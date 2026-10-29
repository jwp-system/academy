"""SMS 게이트웨이 수신 웹훅 (5.4절). 공유 비밀값으로 검증하고 등록된 은행 발신번호만 처리한다."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from academy.config import get_settings
from academy.db import get_session

router = APIRouter(prefix="/webhooks")


class InboundSms(BaseModel):
    sender: str
    message: str
    received_at: str | None = None


@router.post("/sms-inbound", status_code=202)
def sms_inbound(
    payload: InboundSms,
    db: Annotated[Session, Depends(get_session)],
    x_webhook_token: Annotated[str, Header()] = "",
):
    s = get_settings()
    if not s.sms_webhook_token or not secrets.compare_digest(x_webhook_token, s.sms_webhook_token):
        raise HTTPException(status_code=401)
    if payload.sender not in s.bank_senders:
        return {"status": "ignored"}
    # TODO(7주차) matching.parse_bank_sms → BankTransaction 저장 → matching.match_transaction
    return {"status": "accepted"}
