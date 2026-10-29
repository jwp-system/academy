"""입금 알림 문자 파싱과 청구서 자동 매칭 (5.4절, 7주차)."""

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from academy.models import BankTransaction


@dataclass
class ParsedDeposit:
    received_at: datetime
    payer_name: str
    amount: int


def parse_bank_sms(sender: str, text: str) -> ParsedDeposit | None:
    """은행별 정규식으로 입금자명·금액·시각을 뽑는다. 입금 문자가 아니면 None."""
    raise NotImplementedError


def match_transaction(db: Session, tx: BankTransaction) -> None:
    """1) payer_code(또는 별칭)+금액 일치 → auto, 2) 이름만 일치/금액 다름 → review, 3) 없음 → unmatched."""
    raise NotImplementedError
