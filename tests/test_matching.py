import hashlib
import hmac
import json
import time
from datetime import date, datetime
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlmodel import select

from app.config import get_settings
from app.models import (
    BankTransaction,
    ClassGroup,
    Enrollment,
    Guardian,
    Invoice,
    MatchStatus,
    Notification,
    PayerAlias,
    Payment,
    TxSource,
)
from app.schemas import StudentIn
from app.services import billing, matching
from app.services.excel import parse_bank_statement
from app.services.students import create_student

NOW = datetime(2026, 11, 5, 15, 0)
KNOWN = {"홍길동1", "김하나2"}


# --- 문자 파싱 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,name,amount,when",
    [
        (
            "[KB]11/05 14:23\n123456**789\n홍길동1\n입금\n280,000\n잔액1,234,567",
            "홍길동1",
            280000,
            (11, 5, 14, 23),
        ),
        (
            "[Web발신]\n신한11/05 14:23\n110-***-123456\n입금 280,000\n잔액 1,234,567\n홍길동1",
            "홍길동1",
            280000,
            (11, 5, 14, 23),
        ),
        (
            "[Web발신]\n우리 11/05 14:23\n1002***123456\n입금 150,000원\n김모름\n잔액 3,456,789원",
            "김모름",
            150000,
            (11, 5, 14, 23),
        ),
        (
            "하나,11/05,14:23\n123-******-12345\n입금280,000원\n홍길동1\n잔액1,234,567원",
            "홍길동1",
            280000,
            (11, 5, 14, 23),
        ),
        (
            "농협 입금280,000원\n11/05 14:23 123-****-1234-56 박서준 잔액1,234,567원",
            "박서준",
            280000,
            (11, 5, 14, 23),
        ),
    ],
)
def test_parse_bank_formats(text, name, amount, when):
    p = matching.parse_bank_sms(text, KNOWN, now=NOW)
    assert p is not None, text
    assert (p.payer_name, p.amount) == (name, amount)
    assert (
        p.received_at.month,
        p.received_at.day,
        p.received_at.hour,
        p.received_at.minute,
    ) == when


@pytest.mark.parametrize(
    "text",
    [
        "[Web발신]\n하나 11/05 14:23\n출금 50,000원\n관리비\n잔액 1,000,000원",
        "[KB]11/05 입금취소 280,000 홍길동1",
        "안녕하세요 광고입니다",
        "[KB]11/05 14:23 입금 홍길동1",  # 금액 없음
    ],
)
def test_parse_rejects(text):
    assert matching.parse_bank_sms(text, KNOWN, now=NOW) is None


def test_parse_year_rollover():
    p = matching.parse_bank_sms(
        "[KB]12/31 23:50\n홍길동1\n입금\n1,000", KNOWN, now=datetime(2027, 1, 1, 9)
    )
    assert p.received_at == datetime(2026, 12, 31, 23, 50)


# --- 매칭 -------------------------------------------------------------------


@pytest.fixture
def setup(db):
    c = ClassGroup(name="수학", monthly_fee=280000)
    db.add(c)
    db.flush()
    students = []
    for n in ("홍길동", "홍동생"):
        s = create_student(db, StudentIn(name=n))
        db.flush()
        db.add(Enrollment(student_id=s.id, class_id=c.id, start_date=date(2026, 9, 1)))
        db.add(Guardian(student_id=s.id, name="엄마", phone="01011112222", notify_sms=True))
        students.append(s)
    db.commit()
    billing.generate_invoices(db, date(2026, 11, 1), 10)
    db.commit()
    return students


def _ingest(db, name, amount, minute=0):
    r = matching.ingest(
        db,
        received_at=datetime(2026, 11, 5, 14, minute),
        payer_name=name,
        amount=amount,
        source=TxSource.sms,
    )
    db.commit()
    return r


def _invoice(db, student):
    return db.exec(select(Invoice).where(Invoice.student_id == student.id)).one()


def test_auto_match_pays_invoice_and_confirms(db, setup):
    hong, _ = setup
    r = _ingest(db, hong.payer_code, 280000)
    assert r.tx.match_status == MatchStatus.auto and r.tx.student_id == hong.id
    assert _invoice(db, hong).status == "paid"
    p = db.exec(select(Payment)).one()
    assert (
        p.bank_transaction_id == r.tx.id
        and p.method == "transfer"
        and p.paid_at == datetime(2026, 11, 5, 14, 0)
    )
    note = db.exec(select(Notification).where(Notification.template == "payment_confirmed")).one()
    assert "280,000원" in note.body and note.ref == f"payment:{p.id}"


def test_wrong_amount_goes_to_review(db, setup):
    hong, _ = setup
    r = _ingest(db, hong.payer_code, 100000)
    assert r.tx.match_status == MatchStatus.review and r.tx.student_id == hong.id
    assert _invoice(db, hong).status == "unpaid"


def test_name_only_and_unknown(db, setup):
    hong, _ = setup
    assert _ingest(db, "홍길동엄마", 280000).tx.match_status == MatchStatus.review
    assert _ingest(db, "김모름", 280000, 1).tx.match_status == MatchStatus.unmatched


def test_duplicate_ignored(db, setup):
    hong, _ = setup
    _ingest(db, hong.payer_code, 280000)
    r = matching.ingest(
        db,
        received_at=datetime(2026, 11, 5, 14, 0, 37),
        payer_name=hong.payer_code,
        amount=280000,
        source=TxSource.excel,
    )
    assert not r.created and len(db.exec(select(BankTransaction)).all()) == 1


def test_sibling_split_unlink_and_alias(db, setup):
    hong, sib = setup
    r = _ingest(db, "홍엄마", 560000)
    assert r.tx.match_status == MatchStatus.unmatched
    assert [s.id for s in matching.siblings(db, hong.id)] == [sib.id]
    pays = matching.apply(db, r.tx, [_invoice(db, hong), _invoice(db, sib)], MatchStatus.manual)
    assert [p.amount for p in pays] == [280000, 280000]
    assert matching.remember_alias(db, "홍엄마", hong) is None
    db.commit()
    assert _invoice(db, sib).status == "paid"
    assert matching.unlink(db, r.tx) == 2
    db.commit()
    assert _invoice(db, hong).status == "unpaid"
    assert r.tx.match_status == MatchStatus.review  # 되돌려도 학생 후보는 남는다
    # 별칭을 배웠으니 같은 이름·맞는 금액은 자동
    assert _ingest(db, "홍엄마", 280000, 5).tx.match_status == MatchStatus.auto
    assert (
        matching.remember_alias(db, "홍엄마", sib)
        == "'홍엄마'은 다른 학생의 별칭이라 저장하지 않았어요"
    )


def test_overpay_goes_to_last_invoice(db, setup):
    hong, _ = setup
    tx = _ingest(db, "누군가", 300000).tx
    (p,) = matching.apply(db, tx, [_invoice(db, hong)], MatchStatus.manual)
    assert p.amount == 300000


# --- 웹훅 -------------------------------------------------------------------


def _body(message, sender="15881688", event="sms:received"):
    return json.dumps(
        {
            "event": event,
            "payload": {
                "message": message,
                "sender": sender,
                "receivedAt": "2026-11-05T14:23:00.000+09:00",
            },
        }
    ).encode()


@pytest.fixture
def hook(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "sms_webhook_signing_key", "secret")
    monkeypatch.setattr(s, "bank_sender_numbers", "1588-1688")


def _signed(body, key="secret", ts=None):
    ts = str(ts or int(time.time()))
    sig = hmac.new(key.encode(), body + ts.encode(), hashlib.sha256).hexdigest()
    return {"X-Signature": sig, "X-Timestamp": ts, "Content-Type": "application/json"}


def test_webhook_signature(client, db, setup, hook):
    hong, _ = setup
    body = _body(f"[KB]11/05 14:23\n{hong.payer_code}\n입금\n280,000\n잔액1")
    assert client.post("/webhooks/sms-inbound", content=body).status_code == 401
    assert (
        client.post(
            "/webhooks/sms-inbound", content=body, headers=_signed(body, "wrong")
        ).status_code
        == 401
    )
    old = int(time.time()) - 600
    assert (
        client.post(
            "/webhooks/sms-inbound", content=body, headers=_signed(body, ts=old)
        ).status_code
        == 401
    )
    resp = client.post("/webhooks/sms-inbound", content=body, headers=_signed(body))
    assert resp.json() == {"status": "accepted", "match": "auto"}
    assert _invoice(db, hong).status == "paid"
    # 앱이 재시도해도 한 번만
    assert (
        client.post("/webhooks/sms-inbound", content=body, headers=_signed(body)).json()["status"]
        == "duplicate"
    )


def test_webhook_ignores_other_senders_and_events(client, db, setup, hook):
    for body in (
        _body("입금 1,000 홍길동1", sender="01099998888"),
        _body("입금 1,000 홍길동1", event="sms:sent"),
    ):
        r = client.post("/webhooks/sms-inbound", content=body, headers=_signed(body))
        assert r.status_code == 200 and r.json()["status"] == "ignored"
    body = _body("광고 문자")
    assert (
        client.post("/webhooks/sms-inbound", content=body, headers=_signed(body)).json()["reason"]
        == "not a deposit"
    )
    assert db.exec(select(BankTransaction)).first() is None


def test_webhook_token_fallback_and_unconfigured(client, monkeypatch, setup):
    s = get_settings()
    monkeypatch.setattr(s, "bank_sender_numbers", "15881688")
    body = _body("광고")
    monkeypatch.setattr(s, "sms_webhook_token", "")
    assert client.post("/webhooks/sms-inbound", content=body).status_code == 401  # 설정 안 됨
    monkeypatch.setattr(s, "sms_webhook_token", "tok")
    assert client.post("/webhooks/sms-inbound?token=bad", content=body).status_code == 401
    assert client.post("/webhooks/sms-inbound?token=tok", content=body).status_code == 200


# --- 거래내역 파일 -------------------------------------------------------------


def _xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_statement_xlsx_with_title_rows():
    data = _xlsx(
        [
            ["거래내역 조회"],
            ["계좌번호", "123-456"],
            ["거래일시", "적요", "기재내용", "출금액", "입금액", "잔액"],
            [datetime(2026, 11, 5, 14, 23, 10), "타행이체", "홍길동1", 0, 280000, 1000000],
            [datetime(2026, 11, 5, 15, 0), "카드", "관리비", 50000, 0, 950000],
            ["2026.11.06 09:10:00", "타행이체", "김하나2", "", "250,000", ""],
        ]
    )
    r = parse_bank_statement(data, "내역.xlsx")
    assert r.error is None
    assert [(x.payer_name, x.amount) for x in r.rows] == [("홍길동1", 280000), ("김하나2", 250000)]
    assert r.rows[1].received_at == datetime(2026, 11, 6, 9, 10)


def test_statement_csv_cp949_separate_time():
    text = "거래일자,거래시간,입금금액,출금금액,보낸분\n20261105,142310,280000,0,홍길동1\n"
    r = parse_bank_statement(text.encode("cp949"), "x.csv")
    assert (
        r.rows[0].received_at == datetime(2026, 11, 5, 14, 23, 10)
        and r.rows[0].payer_name == "홍길동1"
    )


def test_statement_errors():
    assert "다른 이름으로 저장" in parse_bank_statement(b"x", "old.xls").error
    assert "열을 찾지 못했어요" in parse_bank_statement(_xlsx([["이름", "금액"]]), "a.xlsx").error
    assert "읽을 수 없어요" in parse_bank_statement(b"garbage", "a.xlsx").error


# --- 화면 --------------------------------------------------------------------


@pytest.fixture
def admin(client, login):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return client


def _toast(resp):
    return json.loads(resp.headers["HX-Trigger"]).get("toast", "")


def test_pages_and_preview(admin, setup):
    hong, _ = setup
    r = admin.get("/billing/deposits", headers={"HX-Request": ""})
    assert r.status_code == 200 and "입금 문자 테스트" in r.text and "설정 필요" in r.text
    html = admin.post(
        "/billing/deposits/preview",
        data={"text": f"[KB]11/05 14:23\n{hong.payer_code}\n입금\n280,000"},
    ).text
    assert hong.payer_code in html and "280,000원" in html


def test_simulate_link_unlink_ignore_restore(admin, db, setup):
    hong, sib = setup
    resp = admin.post(
        "/billing/deposits/simulate",
        data={"text": f"[KB]11/05 14:23\n{hong.payer_code}\n입금\n280,000"},
    )
    assert "자동 처리" in _toast(resp)
    resp = admin.post(
        "/billing/deposits/simulate", data={"text": "[우리]11/05 14:30 입금 280,000원 모르는분"}
    )
    assert "미확인" in _toast(resp)
    tx = db.exec(select(BankTransaction).where(BankTransaction.payer_name == "모르는분")).one()
    assert "확인할 입금" not in admin.get("/billing/deposits").text
    sheet = admin.get(f"/billing/deposits/{tx.id}/link").text
    assert "다른 학생 찾기" in sheet
    found = admin.get(f"/billing/deposits/{tx.id}/search?q=홍동").text
    assert "홍동생" in found
    inv = _invoice(db, sib)
    resp = admin.post(
        f"/billing/deposits/{tx.id}/link", data={"invoice_id": [str(inv.id)], "remember": "on"}
    )
    assert "연결했어요" in _toast(resp)
    db.refresh(inv)
    assert inv.status == "paid" and db.exec(select(PayerAlias)).one().name == "모르는분"
    assert (
        admin.post(
            f"/billing/deposits/{tx.id}/link", data={"invoice_id": [str(inv.id)]}
        ).status_code
        == 409
    )
    # 매칭으로 들어온 납부는 수납 화면에서 못 지운다 (5주차 잠금)
    assert admin.patch(f"/billing/invoices/{inv.id}/paid", data={}).status_code == 409
    admin.post(f"/billing/deposits/{tx.id}/unlink")
    db.refresh(inv)
    assert inv.status == "unpaid"
    admin.post(f"/billing/deposits/{tx.id}/ignore")
    db.refresh(tx)
    assert tx.match_status == "ignored"
    admin.post(f"/billing/deposits/{tx.id}/restore")
    db.refresh(tx)
    assert tx.match_status == "auto"  # 별칭을 배워서 이제 자동
    assert admin.post(f"/billing/deposits/{tx.id}/link", data={}).status_code == 409


def test_upload_endpoint(admin, db, setup):
    hong, _ = setup
    data = _xlsx(
        [
            ["거래일시", "기재내용", "입금액"],
            [datetime(2026, 11, 5, 9, 0), hong.payer_code, 280000],
            [datetime(2026, 11, 5, 9, 5), "누구", 1000],
        ]
    )
    files = {"file": ("x.xlsx", data, "application/octet-stream")}
    html = admin.post("/billing/deposits/upload", files=files).text
    assert "입금 2건" in html and "자동 처리 1" in html and "미확인 1" in html
    html = admin.post("/billing/deposits/upload", files=files).text
    assert "이미 받은 입금 2" in html
    assert (
        "열을 찾지 못했어요"
        in admin.post(
            "/billing/deposits/upload", files={"file": ("x.xlsx", _xlsx([["a"]]), "x")}
        ).text
    )
