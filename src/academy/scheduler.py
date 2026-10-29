import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from academy.config import get_settings
from academy.db import engine
from academy.services.notify import process_pending
from academy.services.sessions import generate_sessions

log = logging.getLogger(__name__)


def _run_outbox() -> None:
    with Session(engine) as db:
        process_pending(db)


def _run_generate_sessions() -> None:
    with Session(engine) as db:
        n = generate_sessions(db, date.today())
        db.commit()
    log.info("회차 %d개 생성", n)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=get_settings().timezone)
    scheduler.add_job(_run_outbox, "interval", seconds=30, id="outbox", max_instances=1)
    # 매주 월요일 새벽에 앞으로 4주치 회차를 채운다 (앱 시작 시에도 한 번)
    scheduler.add_job(
        _run_generate_sessions, "cron", day_of_week="mon", hour=3, id="sessions", max_instances=1
    )
    scheduler.add_job(_run_generate_sessions, id="sessions_startup")
    # TODO(5주차) 월 청구 생성: 매월 1일 10시 + 청구 안내 enqueue
    # TODO(6주차) 결석 알림(수업 시작 10분 후), 납부 리마인드, 미납 안내
    return scheduler
