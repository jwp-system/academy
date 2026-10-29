"""채널 어댑터. 새 채널(kakao 등)은 ChannelAdapter를 구현해 ADAPTERS에 등록하면 된다."""

from typing import Protocol

import httpx

from academy.config import get_settings
from academy.models import Channel


class SendError(Exception):
    pass


class ChannelAdapter(Protocol):
    def send(self, to: str, body: str) -> None: ...


class SmsGatewayAdapter:
    """SMS Gateway for Android (capcom6/android-sms-gateway) 로컬 서버 모드."""

    def send(self, to: str, body: str) -> None:
        s = get_settings()
        try:
            resp = httpx.post(
                f"{s.sms_gateway_url.rstrip('/')}/message",
                json={"textMessage": {"text": body}, "phoneNumbers": [to]},
                auth=(s.sms_gateway_user, s.sms_gateway_password),
                timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise SendError(str(e)) from e


class EmailAdapter:
    def send(self, to: str, body: str) -> None:
        raise SendError("email 채널 미구현")


ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.sms: SmsGatewayAdapter(),
    Channel.email: EmailAdapter(),
}
