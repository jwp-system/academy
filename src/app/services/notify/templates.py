"""메시지 템플릿. 기본 문구는 코드에 두고, 알림 화면에서 고치면 message_templates에 저장한다.

자리표시자는 {학생명}처럼 한글로 쓴다. 템플릿마다 쓸 수 있는 자리표시자가 정해져 있다.
"""

import re
from dataclasses import dataclass

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Channel, MessageTemplate


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    label: str
    when: str
    default: str
    fields: tuple[str, ...]


_COMMON = ("학원명", "학생명")
SPECS: dict[str, TemplateSpec] = {
    s.key: s
    for s in [
        TemplateSpec(
            "absence",
            "결석 알림",
            "수업 시작 10분 뒤 (그 전에 출석으로 바뀌면 취소)",
            "[{학원명}] {학생명} 학생이 오늘 {시작시각} {반명} 수업에 결석했어요.",
            (*_COMMON, "반명", "날짜", "시작시각"),
        ),
        TemplateSpec(
            "invoice",
            "청구 안내",
            "매월 1일 오전 10시 (청구를 만든 뒤)",
            "[{학원명}] {학생명} 학생 {월}월 수강료는 {금액}원이에요. {기한}까지 {계좌}로 입금자명 '{입금자명}'으로 보내 주세요.",
            (*_COMMON, "월", "금액", "기한", "입금자명", "계좌"),
        ),
        TemplateSpec(
            "reminder",
            "납부 리마인드",
            "납부 기한 3일 전",
            "[{학원명}] {학생명} 학생 {월}월 수강료 납부 기한은 {기한}이에요. 남은 금액 {남은금액}원, 입금자명 '{입금자명}'",
            (*_COMMON, "월", "남은금액", "기한", "입금자명", "계좌"),
        ),
        TemplateSpec(
            "overdue",
            "미납 안내",
            "납부 기한 다음 날, 이후 7일 간격 최대 2회",
            "[{학원명}] {학생명} 학생 {월}월 수강료 {남은금액}원이 아직 확인되지 않았어요. 이미 보내셨다면 연락 부탁드려요.",
            (*_COMMON, "월", "남은금액", "기한", "입금자명", "계좌"),
        ),
        TemplateSpec(
            "payment_confirmed",
            "납부 확인",
            "입금이 자동 확인된 직후 (7주차)",
            "[{학원명}] {학생명} 학생 {월}월 수강료 {금액}원 납부가 확인됐어요. 감사합니다.",
            (*_COMMON, "월", "금액"),
        ),
    ]
}
# 원장에게 보내는 시스템 알림 (편집 대상 아님)
SYSTEM_LABELS = {"gateway_down": "게이트웨이 장애", "test": "테스트 발송"}

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def label(key: str) -> str:
    return SPECS[key].label if key in SPECS else SYSTEM_LABELS.get(key, key)


def get_body(db: Session, key: str, channel: Channel = Channel.sms) -> str:
    row = db.exec(
        select(MessageTemplate).where(
            MessageTemplate.key == key, MessageTemplate.channel == channel
        )
    ).first()
    return row.body if row else SPECS[key].default


def is_custom(db: Session, key: str) -> bool:
    return db.exec(select(MessageTemplate.id).where(MessageTemplate.key == key)).first() is not None


def validate(key: str, body: str) -> str | None:
    """문제가 있으면 한글 메시지, 없으면 None."""
    if not body.strip():
        return "내용을 입력해 주세요."
    unknown = sorted(set(_PLACEHOLDER.findall(body)) - set(SPECS[key].fields))
    if unknown:
        return f"쓸 수 없는 자리표시자예요: {', '.join('{' + u + '}' for u in unknown)}"
    return None


def save(db: Session, key: str, body: str, channel: Channel = Channel.sms) -> None:
    row = db.exec(
        select(MessageTemplate).where(
            MessageTemplate.key == key, MessageTemplate.channel == channel
        )
    ).first()
    if body.strip() == SPECS[key].default:  # 기본 문구와 같으면 저장하지 않는다
        if row:
            db.delete(row)
        return
    row = row or MessageTemplate(key=key, channel=channel, body=body)
    row.body = body.strip()
    db.add(row)


def reset(db: Session, key: str) -> None:
    for row in db.exec(select(MessageTemplate).where(MessageTemplate.key == key)).all():
        db.delete(row)


def render_body(body: str, values: dict[str, object]) -> str:
    values = {"학원명": get_settings().academy_name, **values}
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), body)


def render(db: Session, key: str, values: dict[str, object], channel: Channel = Channel.sms) -> str:
    return render_body(get_body(db, key, channel), values)


SAMPLE = {
    "학생명": "홍길동",
    "반명": "중2 수학 A",
    "날짜": "11월 3일",
    "시작시각": "16:00",
    "월": "11",
    "금액": "280,000",
    "남은금액": "180,000",
    "기한": "11월 10일",
    "입금자명": "홍길동1",
    "계좌": "OO은행 123-456-789",
}


def sms_bytes(text: str) -> int:
    """통신사 기준 바이트 수 (한글 2바이트). 90바이트를 넘으면 장문(LMS)으로 나뉘어 간다."""
    return sum(2 if ord(c) > 127 else 1 for c in text)
