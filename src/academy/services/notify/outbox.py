"""알림 발송 대기열 (5.3절). 모든 알림은 notifications 테이블을 거친다."""

from datetime import datetime, timedelta

from sqlmodel import Session, select

from academy.config import get_settings
from academy.models import Channel, Notification, NotificationStatus
from academy.services.notify.channels import ADAPTERS, SendError

MAX_ATTEMPTS = 3


def next_allowed_time(at: datetime) -> datetime:
    """야간(기본 21시~8시)이면 다음 허용 시각(아침 8시)으로 미룬다."""
    s = get_settings()
    if s.quiet_hours_end <= at.hour < s.quiet_hours_start:
        return at
    morning = at.replace(hour=s.quiet_hours_end, minute=0, second=0, microsecond=0)
    return morning if at.hour < s.quiet_hours_end else morning + timedelta(days=1)


def enqueue(
    db: Session,
    *,
    to: str,
    template: str,
    body: str,
    channel: Channel = Channel.sms,
    student_id: int | None = None,
    scheduled_at: datetime | None = None,
) -> Notification:
    n = Notification(
        to=to,
        template=template,
        body=body,
        channel=channel,
        student_id=student_id,
        scheduled_at=next_allowed_time(scheduled_at or datetime.now()),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def process_pending(db: Session, now: datetime | None = None) -> int:
    """발송 시각이 된 pending 알림을 보낸다. 실패 시 최대 3회까지 재시도. 처리 건수를 반환."""
    now = now or datetime.now()
    if next_allowed_time(now) != now:
        return 0
    due = db.exec(
        select(Notification)
        .where(Notification.status == NotificationStatus.pending)
        .where(Notification.scheduled_at <= now)
        .order_by(Notification.scheduled_at)
    ).all()
    for n in due:
        n.attempts += 1
        try:
            ADAPTERS[n.channel].send(n.to, n.body)
        except (SendError, KeyError) as e:
            n.error = str(e) or "no adapter"
            if n.attempts >= MAX_ATTEMPTS:
                n.status = NotificationStatus.failed
        else:
            n.status = NotificationStatus.sent
            n.sent_at = datetime.now()
            n.error = None
        db.add(n)
        db.commit()
    return len(due)
