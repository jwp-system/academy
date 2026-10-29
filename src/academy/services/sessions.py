"""정규 시간표(class_schedules)로 실제 회차(sessions)를 생성한다. (3주차)"""

from datetime import date

from sqlmodel import Session


def generate_sessions(db: Session, start: date, weeks: int = 4) -> int:
    """start부터 weeks주치 회차를 만든다. 이미 있는 회차는 건너뛴다. 생성 개수를 반환."""
    raise NotImplementedError
