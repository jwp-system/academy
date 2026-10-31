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

    # 발송 모드: auto(설정이 있으면 실제 발송, 없으면 테스트) | live | test
    sms_mode: str = "auto"
    email_mode: str = "auto"

    # SMS Gateway for Android (5.2절)
    sms_gateway_url: str = "http://192.168.0.10:8080"
    sms_gateway_user: str = ""
    sms_gateway_password: str = ""
    # 수신 웹훅 검증. 게이트웨이 앱의 Settings → Webhooks → Signing Key 값(권장).
    # 서명 키 대신 URL에 ?token=... 을 붙이는 방식도 받는다.
    sms_webhook_signing_key: str = ""
    sms_webhook_token: str = ""
    # 파싱할 은행 입금 알림 발신번호(쉼표 구분, 숫자만 비교)
    bank_sender_numbers: str = ""

    # Gmail SMTP (앱 비밀번호)
    smtp_user: str = ""
    smtp_password: str = ""

    # 청구 안내 문자에 넣을 입금 계좌 (예: "OO은행 123-456-789 홍길동")
    bank_account: str = ""

    # 자동 백업 (구축판): 매일 새벽 DB 사본을 이 폴더에 남긴다.
    # OneDrive·구글 드라이브 동기화 폴더로 지정하면 PC가 고장 나도 클라우드에 사본이 남는다.
    backup_dir: str = str(PROJECT_DIR / "data" / "backups")
    backup_keep_days: int = 30

    # 수납: 매월 납부 기한(일). 짧은 달은 말일로 맞춘다.
    billing_due_day: int = 10

    # 야간 발송 금지 시간대 (5.3절)
    quiet_hours_start: int = 21
    quiet_hours_end: int = 8

    @property
    def bank_senders(self) -> set[str]:
        return {
            "".join(c for c in n if c.isdigit())
            for n in self.bank_sender_numbers.split(",")
            if n.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
