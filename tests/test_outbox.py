from datetime import datetime

from academy.models import NotificationStatus
from academy.services.notify import channels, enqueue, process_pending
from academy.services.notify.outbox import next_allowed_time


def test_quiet_hours_deferred_to_morning():
    assert next_allowed_time(datetime(2026, 11, 2, 22, 30)) == datetime(2026, 11, 3, 8, 0)
    assert next_allowed_time(datetime(2026, 11, 2, 6, 0)) == datetime(2026, 11, 2, 8, 0)
    noon = datetime(2026, 11, 2, 12, 0)
    assert next_allowed_time(noon) == noon


class _Failing:
    def send(self, to, body):
        raise channels.SendError("offline")


def test_failed_after_three_attempts(db, monkeypatch):
    monkeypatch.setitem(channels.ADAPTERS, channels.Channel.sms, _Failing())
    at = datetime(2026, 11, 2, 12, 0)
    n = enqueue(db, to="01000000000", template="absence", body="test", scheduled_at=at)
    for _ in range(3):
        process_pending(db, now=at)
    db.refresh(n)
    assert n.status == NotificationStatus.failed
    assert n.attempts == 3
