import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from academy.config import get_settings
from academy.db import engine
from academy.services.notify import process_pending

log = logging.getLogger(__name__)


def _run_outbox() -> None:
    with Session(engine) as db:
        process_pending(db)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=get_settings().timezone)
    scheduler.add_job(_run_outbox, "interval", seconds=30, id="outbox", max_instances=1)
    # TODO(3주차) 회차 자동 생성: 매주 월요일 새벽, 4주치 유지
    # TODO(5주차) 월 청구 생성: 매월 1일 10시 + 청구 안내 enqueue
    # TODO(6주차) 결석 알림(수업 시작 10분 후), 납부 리마인드, 미납 안내
    return scheduler
