"""채널 어댑터와 발송 모드.

- live: 실제 발송 (SMS Gateway for Android, Gmail SMTP)
- test: 실제로 보내지 않고 기록만 남긴다. 알림 화면의 '테스트 수신함'에서 본다.
- auto(기본): 접속 정보가 설정돼 있으면 live, 아니면 test.

새 채널(kakao 등)은 send(to, body)를 구현해 _LIVE에 등록하면 된다.
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx

from app import edition
from app.config import get_settings
from app.models import Channel


class SendError(Exception):
    pass


class ChannelAdapter(Protocol):
    name: str

    def send(self, to: str, body: str) -> None: ...


def is_cloud_gateway(url: str) -> bool:
    """클라우드 서버 모드(https://api.sms-gate.app/3rdparty/v1)인지. 서버가 학원 밖(네이버클라우드 등)이면 이 모드."""
    return "/3rdparty/" in url


class SmsGatewayAdapter:
    """SMS Gateway for Android (capcom6/android-sms-gateway). Basic 인증, {"textMessage": {"text"}, "phoneNumbers": [...]}
    - 로컬 서버 모드: http://폰IP:8080/message (서버와 폰이 같은 와이파이)
    - 클라우드 서버 모드: https://api.sms-gate.app/3rdparty/v1/messages"""

    name = "gateway"

    def send(self, to: str, body: str) -> None:
        s = get_settings()
        base = s.sms_gateway_url.rstrip("/")
        path = "messages" if is_cloud_gateway(base) else "message"
        try:
            resp = httpx.post(
                f"{base}/{path}",
                json={"textMessage": {"text": body}, "phoneNumbers": [to]},
                auth=(s.sms_gateway_user, s.sms_gateway_password),
                timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise SendError(f"게이트웨이 응답 {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise SendError(f"게이트웨이에 연결할 수 없어요 ({type(e).__name__})") from e


class SmtpAdapter:
    """Gmail SMTP (앱 비밀번호). 하루 약 500통."""

    name = "smtp"

    def send(self, to: str, body: str) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["From"] = s.smtp_user
        msg["To"] = to
        msg["Subject"] = body.splitlines()[0][:60] if body else s.academy_name
        msg.set_content(body)
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
                smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"메일 발송 실패 ({type(e).__name__})") from e


class TestAdapter:
    """테스트 모드: 실제로 보내지 않는다. fail_next를 켜면 발송 실패를 흉내 낸다(재시도·실패 화면 확인용)."""

    name = "test"

    def __init__(self) -> None:
        self.fail = False

    def send(self, to: str, body: str) -> None:
        if self.fail:
            raise SendError("테스트 모드: 발송 실패 흉내")


TEST = TestAdapter()
_LIVE: dict[Channel, ChannelAdapter] = {
    Channel.sms: SmsGatewayAdapter(),
    Channel.email: SmtpAdapter(),
}


def _configured(channel: Channel) -> bool:
    s = get_settings()
    if channel == Channel.sms:
        return bool(s.sms_gateway_url and s.sms_gateway_user and s.sms_gateway_password)
    if channel == Channel.email:
        return bool(s.smtp_user and s.smtp_password)
    return False


def mode(channel: Channel) -> str:
    """'live' 또는 'test'. 판이 실제 발송을 막으면(데모판) 설정과 상관없이 항상 test."""
    if not edition.current().allow_live_notifications:
        return "test"
    s = get_settings()
    setting = {Channel.sms: s.sms_mode, Channel.email: s.email_mode}.get(channel, "test")
    if setting in ("live", "test"):
        return setting
    return "live" if _configured(channel) else "test"


def adapter_for(channel: Channel) -> ChannelAdapter:
    if mode(channel) == "test":
        return TEST
    try:
        return _LIVE[channel]
    except KeyError:
        raise SendError(f"{channel} 채널은 아직 지원하지 않아요") from None


def any_test_mode() -> bool:
    return any(mode(c) == "test" for c in (Channel.sms, Channel.email))


@dataclass
class ChannelStatus:
    channel: Channel
    label: str
    mode: str  # live | test
    configured: bool
    ok: bool | None  # 연결 확인 결과 (확인 안 했으면 None)
    message: str


def check_sms_gateway(timeout: float = 3) -> tuple[bool | None, str]:
    """로컬 모드: GET /health. status가 pass/warn이면 정상. 클라우드 모드는 확인할 방법이 없어 None."""
    s = get_settings()
    if is_cloud_gateway(s.sms_gateway_url):
        return None, "클라우드 서버 모드예요. 연결 상태는 테스트 발송으로 확인해 주세요."
    try:
        resp = httpx.get(
            f"{s.sms_gateway_url.rstrip('/')}/health",
            auth=(s.sms_gateway_user, s.sms_gateway_password),
            timeout=timeout,
        )
    except httpx.HTTPError:
        return (
            False,
            "게이트웨이 폰에 연결할 수 없어요. 폰이 켜져 있고 같은 와이파이인지 확인해 주세요.",
        )
    if resp.status_code == 401:
        return False, "게이트웨이 아이디·비밀번호가 맞지 않아요."
    try:
        status = resp.json().get("status")
    except ValueError:
        status = None
    if resp.is_success and status in ("pass", "warn", None):
        return True, "게이트웨이 폰과 연결돼 있어요." + (" (경고 있음)" if status == "warn" else "")
    return False, f"게이트웨이 상태가 좋지 않아요 ({status or resp.status_code})."


def statuses(check: bool = False) -> list[ChannelStatus]:
    """알림 화면 상단에 보여줄 채널 상태. check=True면 게이트웨이에 실제로 연결해 본다."""
    out = []
    for channel, label in ((Channel.sms, "문자"), (Channel.email, "이메일")):
        m, conf = mode(channel), _configured(channel)
        if m == "test":
            if not edition.current().allow_live_notifications:
                why = f"{edition.current().label or '이 판'}에서는"
            else:
                why = "접속 정보가 없어서" if not conf else "설정에서 테스트 모드로 지정해서"
            out.append(
                ChannelStatus(
                    channel,
                    label,
                    m,
                    conf,
                    None,
                    f"{why} 실제로 보내지 않고 테스트 수신함에 쌓아요.",
                )
            )
            continue
        ok, msg = (None, "실제로 발송해요.")
        if check and channel == Channel.sms:
            ok, msg = check_sms_gateway()
        elif channel == Channel.email:
            msg = f"{get_settings().smtp_user} 계정으로 실제로 발송해요."
        out.append(ChannelStatus(channel, label, m, conf, ok, msg))
    return out
