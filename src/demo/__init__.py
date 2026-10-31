"""데모판: 네이버클라우드에 올려 누구나 둘러볼 수 있게 하는 버전.

- 가짜 데이터로 채워 두고 매일 새벽 4시에 초기화
- 문자·메일은 절대 실제로 보내지 않음 (테스트 수신함에만 쌓임)
- 입금 문자 웹훅 없음 (입금 매칭 화면의 '입금 문자 테스트'로 체험)
- 로그인 화면에서 원장·강사 계정으로 바로 둘러보기
"""

import os
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "demo.db"


def prepare_env() -> None:
    """app을 import하기 전에 부른다. 데모 DB는 DEMO_DATABASE_URL로만 정한다 —
    .env의 ACADEMY_DATABASE_URL(구축판 DB)을 실수로 쓰다가 초기화로 지워 버리는 일을 막기 위해."""
    os.environ["ACADEMY_DATABASE_URL"] = os.environ.get(
        "DEMO_DATABASE_URL", f"sqlite:///{DEFAULT_DB}"
    )
