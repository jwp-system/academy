from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ACADEMY_", extra="ignore")

    academy_name: str = "OO학원"
    database_url: str = f"sqlite:///{PROJECT_DIR / 'data' / 'academy.db'}"
    secret_key: str = "change-me"
    # Cloudflare Tunnel 뒤에서는 HTTPS이므로 True. 로컬 개발 시 False.
    cookie_secure: bool = False
    scheduler_enabled: bool = True
    timezone: str = "Asia/Seoul"

    # SMS Gateway for Android (5.2절)
    sms_gateway_url: str = "http://192.168.0.10:8080"
    sms_gateway_user: str = ""
    sms_gateway_password: str = ""
    # 수신 웹훅 공유 비밀값과 파싱 대상 은행 발신번호(쉼표 구분)
    sms_webhook_token: str = ""
    bank_sender_numbers: str = ""

    # Gmail SMTP (앱 비밀번호)
    smtp_user: str = ""
    smtp_password: str = ""

    # 수납: 매월 납부 기한(일). 짧은 달은 말일로 맞춘다.
    billing_due_day: int = 10

    # 야간 발송 금지 시간대 (5.3절)
    quiet_hours_start: int = 21
    quiet_hours_end: int = 8

    @property
    def bank_senders(self) -> set[str]:
        return {n.strip() for n in self.bank_sender_numbers.split(",") if n.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
