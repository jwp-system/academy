import logging
from collections.abc import Callable
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.config import get_settings
from app.db import engine
from app.services.notify import process_pending
from app.services.notify.events import run_daily
from app.services.sessions import generate_sessions

log = logging.getLogger(__name__)


def _run_outbox() -> None:
    with Session(engine) as db:
        process_pending(db)


def _run_generate_sessions() -> None:
    with Session(engine) as db:
        n = generate_sessions(db, date.today())
        db.commit()
    log.info("회차 %d개 생성", n)


def _run_daily_notices() -> None:
    with Session(engine) as db:
        out = run_daily(db)
    log.info("매일 10시 작업: %s", out)


def create_scheduler(
    add_jobs: Callable[[BackgroundScheduler], None] | None = None,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=get_settings().timezone)
    scheduler.add_job(_run_outbox, "interval", seconds=30, id="outbox", max_instances=1)
    # 매주 월요일 새벽에 앞으로 4주치 회차를 채운다 (앱 시작 시에도 한 번)
    scheduler.add_job(
        _run_generate_sessions, "cron", day_of_week="mon", hour=3, id="sessions", max_instances=1
    )
    scheduler.add_job(_run_generate_sessions, id="sessions_startup")
    # 매일 10시: (1일이면) 청구 생성 → 청구 안내 → 납부 리마인드·미납 안내
    scheduler.add_job(_run_daily_notices, "cron", hour=10, id="daily_notices", max_instances=1)
    if add_jobs:  # 판 전용 작업 (구축판: 자동 백업, 데모판: 매일 초기화)
        add_jobs(scheduler)
    return scheduler
