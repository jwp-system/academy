"""폼 입력 검증 (Pydantic). HTML 폼과 엑셀 가져오기가 같은 스키마를 쓴다."""

import datetime as dt
import re
from datetime import time
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from academy.models import PaymentMethod, Role, StudentStatus

_MESSAGES = {
    "missing": "꼭 입력해 주세요.",
    "string_too_short": "꼭 입력해 주세요.",
    "string_too_long": "너무 길어요.",
    "enum": "목록에서 골라 주세요.",
    "int_parsing": "숫자만 입력해 주세요.",
    "greater_than_equal": "더 큰 값을 입력해 주세요.",
    "less_than_equal": "더 작은 값을 입력해 주세요.",
    "time_parsing": "시각을 확인해 주세요.",
    "time_type": "시각을 확인해 주세요.",
    "date_parsing": "날짜를 확인해 주세요.",
    "date_from_datetime_parsing": "날짜를 확인해 주세요.",
}


def form_errors(exc: ValidationError) -> dict[str, str]:
    """ValidationError를 {필드명: 한글 메시지}로 바꾼다."""
    errors: dict[str, str] = {}
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "__all__"
        if err["type"] == "value_error":
            msg = str(err["ctx"]["error"])
        else:
            msg = _MESSAGES.get(err["type"], err["msg"])
        errors.setdefault(field, msg)
    return errors


def normalize_phone(value: Any) -> str | None:
    """숫자만 남긴다. 엑셀이 숫자로 읽어 앞자리 0이 빠진 경우(1012345678)도 복원한다."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    if not digits.startswith("0"):
        digits = "0" + digits
    if not 9 <= len(digits) <= 11:
        raise ValueError("전화번호 형식을 확인해 주세요.")
    return digits


class FormModel(BaseModel):
    """앞뒤 공백 제거. 빈 값은 입력하지 않은 것으로 본다(필수면 missing, 아니면 기본값)."""

    @model_validator(mode="before")
    @classmethod
    def _drop_blank(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
            data = {k: v for k, v in data.items() if v not in ("", None)}
        return data


class StudentIn(FormModel):
    name: str
    school: str | None = None
    grade: str | None = None
    phone: str | None = None
    status: StudentStatus = StudentStatus.active
    memo: str | None = None
    payer_code: str | None = None  # 수정 시에만. 비우면 자동 부여

    _phone = field_validator("phone", mode="before")(normalize_phone)

    @field_validator("grade", mode="before")
    @classmethod
    def _grade_to_str(cls, v: Any) -> Any:
        return str(int(v)) if isinstance(v, float) and v.is_integer() else v


class GuardianIn(FormModel):
    name: str
    relation: str | None = None
    phone: str | None = None
    email: str | None = None
    notify_sms: bool = False
    notify_email: bool = False

    _phone = field_validator("phone", mode="before")(normalize_phone)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("이메일 형식을 확인해 주세요.")
        return v.lower() if v else v


class ConsultationIn(FormModel):
    content: str


class TeacherIn(FormModel):
    name: str
    email: str
    subject: str | None = None
    role: Role = Role.teacher
    active: bool = False
    password: str | None = None  # 등록 시 필수, 수정 시 입력하면 변경

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("이메일 형식을 확인해 주세요.")
        return v.lower()

    @field_validator("password")
    @classmethod
    def _password(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 해요.")
        return v


def _parse_won(v: Any) -> Any:
    """'280,000' / '280000원' → 280000"""
    if isinstance(v, str):
        digits = re.sub(r"[,\s원]", "", v)
        if not digits.isdigit():
            raise ValueError("숫자만 입력해 주세요.")
        return int(digits)
    return v


class ClassIn(FormModel):
    name: str
    subject: str | None = None
    teacher_id: int | None = None
    room: str | None = None
    capacity: int | None = Field(default=None, ge=1)
    monthly_fee: int = Field(default=0, ge=0)
    active: bool = False

    _fee = field_validator("monthly_fee", mode="before")(_parse_won)


class TimeRange(FormModel):
    start_time: time
    end_time: time

    @field_validator("end_time")
    @classmethod
    def _order(cls, v: time, info: ValidationInfo) -> time:
        start = info.data.get("start_time")
        if start and v <= start:
            raise ValueError("끝나는 시각이 시작 시각보다 늦어야 해요.")
        return v


class ScheduleIn(TimeRange):
    weekday: int = Field(ge=0, le=6)


class SessionIn(TimeRange):
    date: dt.date
    room: str | None = None
    teacher_id: int | None = None
    note: str | None = None


class EnrollIn(FormModel):
    student_id: int
    start_date: dt.date = Field(default_factory=dt.date.today)


class InvoiceIn(FormModel):
    amount: int = Field(ge=0)
    discount: int = Field(default=0, ge=0)
    due_date: dt.date

    _won = field_validator("amount", "discount", mode="before")(_parse_won)

    @field_validator("discount")
    @classmethod
    def _discount(cls, v: int, info: ValidationInfo) -> int:
        amount = info.data.get("amount")
        if amount is not None and v > amount:
            raise ValueError("할인이 수강료보다 클 수 없어요.")
        return v


class PaymentIn(FormModel):
    amount: int = Field(ge=1)
    method: PaymentMethod
    paid_on: dt.date = Field(default_factory=dt.date.today)
    memo: str | None = None

    _won = field_validator("amount", mode="before")(_parse_won)

    @field_validator("paid_on")
    @classmethod
    def _not_future(cls, v: dt.date) -> dt.date:
        if v > dt.date.today():
            raise ValueError("오늘 이후 날짜는 고를 수 없어요.")
        return v
