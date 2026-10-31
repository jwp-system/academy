"""SQLite 백업. 쓰는 중에도 일관된 사본을 만든다 (sqlite3 backup API, WAL 모드에서도 안전)."""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)


class NotSqlite(Exception):
    pass


def db_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise NotSqlite
    return Path(url.removeprefix("sqlite:///"))


def snapshot(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path()) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    return dest


def run_scheduled(now: datetime | None = None) -> Path:
    """backup_dir에 academy-YYYYMMDD-HHMM.db를 만들고, 보관 기간이 지난 사본을 지운다."""
    s = get_settings()
    now = now or datetime.now()
    folder = Path(s.backup_dir)
    made = snapshot(folder / f"academy-{now:%Y%m%d-%H%M}.db")
    cutoff = now - timedelta(days=s.backup_keep_days)
    for old in folder.glob("academy-*.db"):
        try:
            when = datetime.strptime(old.stem.removeprefix("academy-"), "%Y%m%d-%H%M")
        except ValueError:
            continue
        if when < cutoff:
            old.unlink(missing_ok=True)
    log.info("백업: %s", made)
    return made


def latest(folder: Path | None = None) -> Path | None:
    folder = folder or Path(get_settings().backup_dir)
    files = sorted(folder.glob("academy-*.db"))
    return files[-1] if files else None
