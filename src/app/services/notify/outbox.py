"""알림 발송 대기열 (5.3절). 모든 알림은 notifications 테이블을 거친다.

- 이벤트가 생기면 pending 한 줄을 추가하고, 스케줄러가 30초마다 발송한다.
- 실패하면 최대 3회까지 재시도, 그래도 실패하면 failed (대시보드 '발송 실패').
- 야간(기본 21시~8시)에는 보내지 않고 아침으로 미룬다.
- 같은 학생·같은 유형·같은 받는 사람은 하루 1회. ref가 같은 알림은 한 번만.
"""

from datetime import date, datetime, timedelta

from sqlmodel import Session, col, func, select

from app.config import get_settings
from app.models import Channel, Notification, NotificationStatus, Role, User
from app.services.notify.channels import SendError, adapter_for, mode

MAX_ATTEMPTS = 3
GATEWAY_DOWN_AFTER = 3  # 한 번 처리할 때 문자 발송이 연속 이만큼 실패하면 원장에게 메일
ACTIVE = (NotificationStatus.pending, NotificationStatus.sent)


def next_allowed_time(at: datetime) -> datetime:
    """야간이면 다음 허용 시각(아침)으로 미룬다."""
    s = get_settings()
    if s.quiet_hours_end <= at.hour < s.quiet_hours_start:
        return at
    morning = at.replace(hour=s.quiet_hours_end, minute=0, second=0, microsecond=0)
    return morning if at.hour < s.quiet_hours_end else morning + timedelta(days=1)


def _duplicate(
    db: Session, *, to: str, template: str, student_id: int | None, ref: str | None, day: date
) -> bool:
    if ref:
        stmt = select(Notification.id).where(
            Notification.ref == ref, Notification.to == to, col(Notification.status).in_(ACTIVE)
        )
        if db.exec(stmt).first():
            return True
    if student_id is None:
        return False
    start = datetime.combine(day, datetime.min.time())
    return (
        db.exec(
            select(Notification.id).where(
                Notification.student_id == student_id,
                Notification.template == template,
                Notification.to == to,
                col(Notification.status).in_(ACTIVE),
                Notification.scheduled_at >= start,
                Notification.scheduled_at < start + timedelta(days=1),
            )
        ).first()
        is not None
    )


def enqueue(
    db: Session,
    *,
    to: str,
    template: str,
    body: str,
    channel: Channel = Channel.sms,
    student_id: int | None = None,
    scheduled_at: datetime | None = None,
    ref: str | None = None,
    dedupe: bool = True,
) -> Notification | None:
    """대기열에 넣는다. 중복이면 None. 커밋은 호출자가 한다."""
    when = next_allowed_time(scheduled_at or datetime.now())
    if dedupe and _duplicate(
        db, to=to, template=template, student_id=student_id, ref=ref, day=when.date()
    ):
        return None
    n = Notification(
        to=to,
        template=template,
        body=body,
        channel=channel,
        student_id=student_id,
        scheduled_at=when,
        ref=ref,
    )
    db.add(n)
    db.flush()
    return n


def cancel(db: Session, *, ref: str, student_id: int | None = None) -> int:
    """아직 안 나간 알림을 취소한다 (예: 결석 → 출석으로 바뀜)."""
    stmt = select(Notification).where(
        Notification.ref == ref, Notification.status == NotificationStatus.pending
    )
    if student_id is not None:
        stmt = stmt.where(Notification.student_id == student_id)
    rows = db.exec(stmt).all()
    for n in rows:
        n.status = NotificationStatus.cancelled
        db.add(n)
    return len(rows)


def _send(n: Notification) -> None:
    n.attempts += 1
    try:
        adapter = adapter_for(n.channel)
        n.provider = adapter.name
        adapter.send(n.to, n.body)
    except SendError as e:
        n.error = str(e)
        if n.attempts >= MAX_ATTEMPTS:
            n.status = NotificationStatus.failed
    else:
        n.status = NotificationStatus.sent
        n.sent_at = datetime.now()
        n.error = None


def process_pending(db: Session, now: datetime | None = None, *, test_flush: bool = False) -> int:
    """보낼 시각이 된 pending 알림을 보낸다. 처리 건수를 반환.
    test_flush=True: 테스트 화면의 '예약 무시하고 지금 보내기'. 테스트 모드 채널의 알림만,
    예약 시각·야간 제한을 무시하고 보낸다 (실제 발송 채널은 절대 앞당기지 않는다)."""
    now = now or datetime.now()
    stmt = select(Notification).where(Notification.status == NotificationStatus.pending)
    if test_flush:
        test_channels = [c for c in Channel if mode(c) == "test"]
        stmt = stmt.where(col(Notification.channel).in_(test_channels))
    else:
        if next_allowed_time(now) != now:
            return 0
        stmt = stmt.where(Notification.scheduled_at <= now)
    due = db.exec(stmt.order_by(Notification.scheduled_at)).all()
    sms_fail_streak = 0
    for n in due:
        _send(n)
        db.add(n)
        db.commit()
        if n.channel == Channel.sms:
            sms_fail_streak = sms_fail_streak + 1 if n.status != NotificationStatus.sent else 0
            if sms_fail_streak == GATEWAY_DOWN_AFTER:
                _alert_gateway_down(db, n.error or "")
    return len(due)


def _alert_gateway_down(db: Session, error: str) -> None:
    """문자 발송이 연속으로 실패하면 원장에게 메일 (하루 한 번)."""
    today = date.today()
    admins = db.exec(select(User).where(User.role == Role.admin, User.active)).all()
    for admin in admins:
        enqueue(
            db,
            to=admin.email,
            template="gateway_down",
            channel=Channel.email,
            body=(
                f"[{get_settings().academy_name}] 문자 발송이 연속으로 실패하고 있어요.\n"
                f"게이트웨이 폰의 전원·와이파이·앱 실행 상태를 확인해 주세요.\n마지막 오류: {error}"
            ),
            ref=f"gateway_down:{today}",
        )
    db.commit()


def send_now(db: Session, n: Notification) -> None:
    """테스트 발송: 대기열을 거치되 예약·야간 제한 없이 바로 보낸다."""
    _send(n)
    if n.status == NotificationStatus.pending:  # 첫 시도 실패 → 테스트는 재시도 없이 실패로
        n.status = NotificationStatus.failed
    db.add(n)


def retry(db: Session, n: Notification) -> None:
    n.status = NotificationStatus.pending
    n.attempts = 0
    n.error = None
    n.scheduled_at = next_allowed_time(datetime.now())
    db.add(n)


def counts(db: Session) -> dict[str, int]:
    return dict(
        db.exec(select(Notification.status, func.count()).group_by(Notification.status)).all()
    )
