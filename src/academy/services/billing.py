"""월 청구서 생성. enrollments 기준 월정액. (5주차)"""

from sqlmodel import Session


def generate_invoices(db: Session, year_month: str) -> int:
    """해당 월에 수강 중인 학생별 청구서를 만든다. 이미 있으면 건너뛴다. 생성 개수를 반환."""
    raise NotImplementedError
