# 소규모 학원 관리 시스템 기획안 (FastAPI + HTMX)

**전제**: 강사 1~3명, 학생 약 100명, 동시 접속 5명 이하, 운영비 0원이 목표입니다. (알림·수납 자동화용 안드로이드 공기계와 알뜰폰 최저 요금제는 예외)

## 1. 설계 방향

- **단순하게**: SPA 없이 서버 렌더링(Jinja2)에 HTMX로 부분 갱신만 합니다. 빌드 도구도 쓰지 않습니다.
- **DB는 SQLite**: 100명 규모라면 충분히 처리하고, 파일 하나라서 백업과 이전이 쉽습니다.
- **역할은 둘**: 원장(관리자)과 강사. 학생과 학부모는 로그인 없이 **조회 전용 링크**로 접근합니다.
- **유료 연동 없이 자동화**: 문자는 안드로이드 폰 SMS 게이트웨이로, 수납 확인은 은행 입금 알림 자동 매칭으로 처리합니다. PG 결제와 알림톡은 쓰지 않고, 나중에 교체할 수 있도록 채널을 추상화합니다. (5절 참고)

## 2. 기술 스택 (모두 무료)

| 영역 | 선택 | 비고 |
|---|---|---|
| 웹 프레임워크 | **FastAPI** + Uvicorn | |
| 템플릿 | **Jinja2** | 페이지 전체와 부분 조각(partial)을 같은 템플릿으로 렌더링 |
| 프론트 상호작용 | **HTMX** 2.x | 폼 제출, 검색, 인라인 편집, 모달 |
| 클라이언트 보조 | **Alpine.js** | 드롭다운, 탭, 모달 열고 닫기 등 순수 UI 상태 |
| CSS | **Pico.css** 또는 **Bootstrap 5** | CDN 한 줄이면 됨, 빌드 불필요 |
| ORM | **SQLModel** (SQLAlchemy 기반) | Pydantic 스키마와 DB 모델을 하나로 |
| DB 마이그레이션 | **Alembic** | |
| DB | **SQLite** (WAL 모드) | |
| 인증 | 세션 쿠키(Starlette `SessionMiddleware`) + **argon2-cffi** | JWT 불필요 |
| 폼 검증 | Pydantic | |
| 백그라운드 작업 | **APScheduler** (앱 프로세스 내장) | 알림 발송, 월 청구 생성, 리마인드 |
| 캘린더 | **FullCalendar** (MIT 라이선스 기본판) | 주간 시간표 |
| 차트 | **Chart.js** | 성적 추이, 출석률 |
| 엑셀 입출력 | **openpyxl** | 기존 명단 가져오기, 거래내역 업로드, 수납 내역 내보내기 |
| PDF 리포트 | **WeasyPrint** | 월간 학습 리포트 |
| 문자 발송·수신 | **SMS Gateway for Android** (오픈소스, capcom6/android-sms-gateway) | 공기계 + 문자 무제한 요금제, HTTP API로 발송, 수신 문자 웹훅 |
| 이메일 | Gmail SMTP (앱 비밀번호) | 하루 약 500통 |
| 푸시 알림 | **Web Push** (pywebpush) + PWA | 무료, 학부모가 홈 화면에 추가해야 함 |
| 백업 | **Litestream** → Cloudflare R2(10GB 무료) | SQLite를 실시간으로 복제 |

## 3. 배포 옵션 (무료)

| 방식 | 장점 | 주의점 |
|---|---|---|
| **A. 학원 PC + Cloudflare Tunnel** (추천) | 완전 무료, 데이터가 학원 안에 있음, HTTPS 자동, SMS 게이트웨이 폰과 같은 와이파이에서 로컬 API로 통신 | PC를 켜 두어야 함 |
| B. Oracle Cloud Always Free VM | 24시간 가동, 성능이 넉넉함 | 가입 시 카드 인증, 서버 관리 필요, SMS 게이트웨이는 클라우드 중계 모드 사용 |
| C. Render 또는 Railway 무료 플랜 | 배포가 가장 쉬움 | 디스크가 휘발되어 **SQLite에 부적합**, 슬립 발생 |

A나 B 어느 쪽이든 Litestream으로 R2에 백업해 두면 PC 고장에도 복구할 수 있습니다.

## 4. 기능 범위

### MVP

1. **로그인과 권한**: 원장은 전체, 강사는 담당 반만 봅니다.
2. **학생 관리**: 등록·수정, 상태(재원/휴원/퇴원), 보호자 연락처, 상담 메모, 엑셀로 일괄 등록
3. **강사 관리**: 계정, 담당 과목, 담당 반
4. **반·시간표**: 반 생성, 요일과 시간 지정, 정규 시간표로 회차 자동 생성, 강의실과 강사 시간 겹침 경고
5. **출결**: 오늘 수업 목록에서 학생 이름을 탭하면 출석, 지각, 결석 순으로 바뀝니다(HTMX).
6. **수납**: 매월 1일 청구 자동 생성, 납부 체크(현금, 계좌이체, 카드 단말기), 미납 목록, 엑셀 내보내기
7. **알림 자동 발송**: 결석 알림, 청구·미납 안내를 SMS 게이트웨이로 자동 발송 (5절)
8. **입금 자동 매칭**: 은행 입금 알림 문자를 받아 청구서와 자동 대조, 납부 확인 문자 발송 (5절)
9. **대시보드**: 오늘 수업, 결석자, 이번 달 미납 현황, 매칭 확인 필요 건, 발송 실패 건

### 2차

- 수업 일지(진도, 과제), 시험 성적 입력과 추이 차트
- 보강 관리: 결석한 회차에 보강 회차를 연결
- 학부모 조회 링크(PWA): 출결, 성적, 공지, 수납 내역, 웹푸시 수신
- 월간 리포트 PDF 생성과 이메일 발송
- 강사 시수 집계(정산 참고용)

### 하지 않을 것

- PG 온라인 결제: 수수료(약 3%) 대비 효용이 낮습니다. 계좌이체 자동 매칭과 학원 카드 단말기로 대체합니다.
- 홈택스 현금영수증 자동 발급: 연동이 어렵습니다. "발급 필요" 목록만 보여주고 원장이 홈택스에서 처리합니다.
- 학생과 학부모 계정 회원가입: 토큰 링크로 대체합니다.

## 5. 알림·수납 자동화

### 5.1 추천 조합

| 용도 | 1순위 (무료) | 보조 | 나중에 업그레이드 |
|---|---|---|---|
| 결석·출결 알림 | 안드로이드 SMS 게이트웨이 | 웹푸시 | 카카오 알림톡 |
| 청구·미납 안내 | 안드로이드 SMS 게이트웨이 | 이메일 | 카카오 알림톡 |
| 월간 리포트 | 이메일(PDF 첨부) | 학부모 조회 링크 | |
| 납부 확인 | 입금 문자 자동 매칭 | 거래내역 엑셀 업로드 | |

### 5.2 안드로이드 SMS 게이트웨이

```
FastAPI ──HTTP POST──▶ 안드로이드 폰(게이트웨이 앱) ──SMS──▶ 학부모
은행 ──입금 알림 문자──▶ 안드로이드 폰 ──웹훅──▶ FastAPI
```

- 남는 안드로이드 폰에 게이트웨이 앱을 설치하고, 충전기와 와이파이에 상시 연결합니다.
- 문자 무제한 요금제(알뜰폰 최저 요금제 가능)를 사용합니다. 학부모는 아무것도 설치하지 않아도 됩니다.
- **제약**
  - 통신사가 하루 대량 발송을 제한합니다(보통 수백 건 수준). 학생 100명 규모에는 충분합니다.
  - 광고성 문자는 정보통신망법 규제 대상이므로 **출결·수납 같은 정보성 문자만** 보냅니다.
- **나중에 업그레이드**: 카카오 알림톡(솔라피 등 중계사, 건당 약 10원 안팎)은 카카오톡 채널 개설, 사업자 인증, 템플릿 심사가 필요합니다. 채널 어댑터만 추가하면 됩니다.

### 5.3 알림 발송 대기열(outbox)

모든 알림은 `notifications` 테이블에 쌓고, 백그라운드 작업이 발송합니다. 채널은 어댑터로 분리합니다(`sms`, `push`, `email`, 나중에 `kakao`).

- **흐름**: 이벤트가 생기면 `pending` 한 줄을 추가하고, APScheduler가 30초마다 발송합니다. 실패하면 최대 3회 재시도하고, 그래도 실패하면 대시보드에 표시합니다.
- **발송 정책**
  - 결석 알림은 수업 시작 10분 뒤 발송합니다. 그 전에 출석으로 바뀌면 취소합니다.
  - 야간(21시~8시)에는 보내지 않고 다음 날 아침으로 미룹니다.
  - 같은 학생·같은 유형은 하루 1회로 제한합니다.
- **템플릿**: 설정 화면에서 수정합니다. 예: `[OO학원] {학생명} 학생이 오늘 {반명} 수업에 결석했습니다.`

| 이벤트 | 시점 | 채널 |
|---|---|---|
| 결석 | 수업 시작 10분 후 | SMS (+ 웹푸시) |
| 월 청구 안내 | 매월 1일 오전 10시 | SMS (+ 이메일) |
| 납부 리마인드 | 납부일 3일 전 | SMS |
| 미납 안내 | 납부일 다음 날, 이후 7일 간격 최대 2회 | SMS |
| 납부 확인 | 입금 매칭 직후 | SMS |
| 월간 리포트 | 매월 말 | 이메일(PDF) |

### 5.4 입금 자동 매칭

```
입금 알림 문자 수신 ─▶ 웹훅(/webhooks/sms-inbound) ─▶ 은행별 정규식 파싱(입금자명, 금액, 시각)
─▶ bank_transactions 저장 ─▶ 미납 청구서와 매칭 ─▶ payments 생성, 청구서 상태 갱신 ─▶ 납부 확인 문자
```

- **입금자명 규칙**: 학생마다 `payer_code`를 부여합니다. 예: `홍길동3` (학생명 + 학생번호 끝자리). 청구 안내 문자에 함께 안내합니다.
- **매칭 순서**
  1. `payer_code`와 금액이 정확히 일치 → 자동 처리
  2. 이름만 일치하거나 금액이 다름(부분 납부, 형제 합산) → "확인 필요" 목록
  3. 일치 없음 → "미확인 입금" 목록, 원장이 클릭으로 학생에 연결
- 원장이 수동으로 연결하면 그 입금자명을 학생의 별칭으로 저장해 다음부터 자동 매칭합니다.
- **보완 수단**: 인터넷뱅킹 거래내역 엑셀을 업로드하면 같은 규칙으로 일괄 매칭합니다. 중복은 (시각, 금액, 입금자명)으로 걸러냅니다.
- **카드 결제**: 학원 카드 단말기로 받고, 시스템에는 "카드"로 기록만 합니다.
- **현금영수증**: 학원은 현금영수증 의무발행 업종입니다. 계좌이체 납부 건은 "현금영수증 발급 필요" 목록에 올리고, 원장이 홈택스에서 발급한 뒤 체크합니다.

## 6. 데이터 모델

```
users              (id, name, email, password_hash, role[admin|teacher], active)
students           (id, name, school, grade, phone, status, enrolled_at, memo,
                    view_token, payer_code)
payer_aliases      (id, student_id, name)                               -- 학습된 입금자명
guardians          (id, student_id, name, relation, phone, email, notify_sms, notify_email)
classes            (id, name, subject, teacher_id, room, capacity, monthly_fee, active)
class_schedules    (id, class_id, weekday, start_time, end_time)        -- 정규 시간표
enrollments        (id, student_id, class_id, start_date, end_date)      -- 수강 이력
sessions           (id, class_id, date, start_time, end_time, room,
                    status[normal|cancelled|makeup], teacher_id, note)   -- 실제 수업 회차
attendances        (id, session_id, student_id, status[present|late|absent|excused], memo)
invoices           (id, student_id, year_month, amount, discount, due_date,
                    status[unpaid|paid|partial])
payments           (id, invoice_id, paid_at, amount, method[cash|transfer|card],
                    bank_transaction_id, cash_receipt_issued, memo)
bank_transactions  (id, received_at, payer_name, amount, raw_text, source[sms|excel],
                    match_status[auto|review|unmatched|manual], student_id)
notifications      (id, student_id, channel[sms|push|email|kakao], to, template, body,
                    status[pending|sent|failed|cancelled], attempts, scheduled_at,
                    sent_at, error)
message_templates  (id, key, channel, body)
consultations      (id, student_id, user_id, date, content)
-- 2차
push_subscriptions (id, guardian_id, endpoint, keys)
lesson_logs        (id, session_id, progress, homework)
exams / scores     (id, class_id, name, date) / (exam_id, student_id, score)
```

**설계 포인트**

- `class_schedules`(정규)와 `sessions`(실제 회차)를 분리합니다. 휴강, 보강, 대강은 `sessions`만 수정하면 됩니다.
- 회차 생성은 "이번 달 회차 생성" 버튼이나 APScheduler 작업으로 4주치를 미리 만듭니다. Celery 같은 별도 작업 큐는 필요 없습니다.
- 수강료는 월정액을 기본으로 하고, `enrollments` 기준으로 `invoices`를 생성합니다.
- 알림은 반드시 `notifications`를 거칩니다. 발송 이력이 남고, 채널 교체가 쉽습니다.

## 7. 프로젝트 구조

```
academy/
├─ app/
│  ├─ main.py              # FastAPI 앱, 미들웨어, 라우터 등록, 스케줄러 시작
│  ├─ db.py                # 엔진, 세션, SQLite PRAGMA(WAL, foreign_keys)
│  ├─ models.py            # SQLModel 모델
│  ├─ auth.py              # 로그인, require_role 의존성
│  ├─ scheduler.py         # APScheduler 작업 등록
│  ├─ routers/
│  │  ├─ dashboard.py
│  │  ├─ students.py
│  │  ├─ teachers.py
│  │  ├─ classes.py
│  │  ├─ attendance.py
│  │  ├─ billing.py        # 청구, 납부, 입금 매칭 확인
│  │  ├─ notifications.py  # 발송 이력, 템플릿 설정
│  │  ├─ webhooks.py       # SMS 게이트웨이 수신 웹훅
│  │  └─ public.py         # 학부모 토큰 링크
│  ├─ services/
│  │  ├─ sessions.py       # 회차 생성
│  │  ├─ billing.py        # 청구 생성
│  │  ├─ matching.py       # 입금 파싱, 매칭
│  │  ├─ notify/           # outbox 처리, 채널 어댑터(sms, email, push)
│  │  └─ excel.py
│  └─ templates/
│     ├─ base.html
│     ├─ students/
│     │  ├─ list.html      # 전체 페이지
│     │  └─ _rows.html     # HTMX 부분 조각
│     └─ ...
├─ static/                 # htmx.min.js, alpine.min.js, css (로컬 보관)
├─ alembic/
├─ data/academy.db
└─ pyproject.toml
```

## 8. HTMX 활용 패턴

| 화면 | 패턴 |
|---|---|
| 학생 검색 | `hx-get="/students" hx-trigger="keyup changed delay:300ms" hx-target="#rows"`로 `_rows.html`만 교체 |
| 출결 체크 | 이름 버튼에 `hx-post="/attendance/{id}/toggle" hx-swap="outerHTML"`로 버튼 하나만 갱신 |
| 등록·수정 폼 | 모달 안에 폼을 두고, 검증 실패 시 폼 조각을 오류와 함께 다시 렌더링하며, 성공 시 `HX-Trigger: studentSaved` 헤더로 목록을 새로고침 |
| 인라인 편집 | 클릭하면 `hx-get`으로 입력칸 조각을 받고, 엔터를 치면 `hx-put`으로 표시 조각을 받음 |
| 삭제 | `hx-delete` + `hx-confirm="퇴원 처리할까요?"` |
| 납부 체크 | 체크박스에 `hx-patch`, 응답에 행 전체와 미납 합계를 함께 담아 `hx-swap-oob`로 갱신 |
| 입금 매칭 확인 | 미확인 입금 행에서 학생을 선택하면 `hx-post`로 연결하고 행을 제거 |
| 대시보드 갱신 | 미납·발송 실패 카드에 `hx-trigger="every 60s"`로 주기적 갱신 |

라우터 규칙은 하나만 둡니다. `HX-Request` 헤더가 있으면 부분 조각을, 없으면 전체 페이지를 반환합니다. 헬퍼 함수 하나로 처리할 수 있습니다.

## 9. 화면 목록

1. 로그인
2. 대시보드: 오늘 수업, 결석자, 미납 건수, 매칭 확인 필요, 발송 실패
3. 학생 목록과 상세(탭: 정보, 수강, 출결, 수납, 알림 이력, 상담)
4. 강사 목록 (원장만)
5. 반 목록과 상세(수강생, 정규 시간표)
6. 주간 시간표(FullCalendar, 강사별·강의실별 필터)
7. 출결 체크: 모바일 우선, 오늘 회차의 학생 버튼 그리드
8. 수납: 월 선택, 청구 생성, 납부 체크, 엑셀 내보내기, 현금영수증 발급 필요 목록
9. 입금 매칭: 자동 처리 내역, 확인 필요, 미확인 입금, 거래내역 엑셀 업로드
10. 알림: 발송 이력, 실패 재발송, 템플릿 편집, 테스트 발송
11. 설정: 강의실, 과목, 할인 규칙, SMS 게이트웨이 연결, 발송 시간대, 백업 다운로드
12. 학부모 조회 페이지 (`/p/{token}`, 2차)

## 10. 보안과 운영 체크리스트

- 비밀번호는 argon2로 해시하고, 세션 쿠키는 `HttpOnly`, `Secure`, `SameSite=Lax`로 설정합니다.
- HTMX의 POST 요청에는 CSRF 토큰을 넣습니다(`hx-headers`로 전역 설정).
- 강사 권한은 쿼리 단계에서 `teacher_id`로 필터링합니다.
- 학부모 링크 토큰은 `secrets.token_urlsafe(24)`로 만들고, 재발급할 수 있게 합니다.
- SMS 수신 웹훅은 공유 비밀값(서명 또는 토큰)으로 검증하고, 등록된 은행 발신번호의 문자만 파싱합니다.
- 게이트웨이 폰의 API 자격 증명은 `.env`에 두고 저장소에 커밋하지 않습니다.
- 게이트웨이 폰이 오프라인이면(발송 실패 연속 발생) 원장에게 이메일로 알립니다.
- 학생 개인정보를 다루므로 퇴원생 정보 보관 기간(예: 3년)을 정하고 파기 기능을 둡니다. 알림 이력도 같은 기준으로 정리합니다.
- 백업은 Litestream으로 실시간 복제하고, 설정 화면에 DB 파일 수동 다운로드 버튼을 둡니다.

## 11. 일정 (1인, 주 10~15시간 기준)

| 주차 | 산출물 |
|---|---|
| 1 | 프로젝트 골격, DB 모델, Alembic, 로그인과 권한, base 레이아웃 |
| 2 | 학생·강사 CRUD, 검색, 엑셀 가져오기 |
| 3 | 반, 정규 시간표, 회차 자동 생성, 주간 캘린더 |
| 4 | 출결 체크(모바일), 학생별 출결 이력 |
| 5 | 수납: 청구 생성, 납부 처리, 미납 목록, 내보내기 |
| 6 | 알림 outbox, APScheduler, SMS 게이트웨이 연동, 결석·청구·미납 자동 발송 |
| 7 | 입금 문자 웹훅, 매칭 로직, 매칭 확인 화면, 거래내역 엑셀 업로드 |
| 8 | 대시보드, Cloudflare Tunnel 배포, Litestream 백업, 실사용 시작 |
| 9~ | 2차 기능 (웹푸시, 월간 리포트, 성적, 보강) |

## 12. 확인할 사항

1. **수강료 방식**: 월정액 고정인가요, 아니면 회차 차감이나 패키지 방식도 있나요?
2. **반 구조**: 그룹 수업 위주인가요, 1:1 레슨도 섞여 있나요? (1:1이면 반 대신 개별 회차 예약 구조가 필요합니다)
3. **배포 위치**: 학원 PC를 상시 켜둘 수 있나요?
4. **게이트웨이 폰**: 남는 안드로이드 폰과 문자 무제한 요금제를 준비할 수 있나요? 학원 대표번호로 보내야 하나요?
5. **입금 계좌**: 어느 은행인가요? 입금 알림 문자(또는 알림 서비스)를 받을 수 있나요? (은행별 문자 형식에 맞춰 파싱 규칙을 만듭니다)
6. **이메일**: 이메일을 쓰는 학부모가 있나요?

## 13. 배포 구성 (2026-11 결정)

- **데모판**은 네이버클라우드에 올려 누구나 둘러보게 하고, **실제 구축**은 학원 내부 PC(Windows)에 설치한다.
- 코드는 `src/app`(공통), `src/academy`(구축판), `src/demo`(데모판)로 나눈다.
- 구축판 백업은 Litestream(Windows 미지원) 대신 앱이 매일 DB 사본을 백업 폴더(OneDrive 등 동기화 폴더)에 남긴다.
- 데모판은 문자·메일을 실제로 보내지 않고, 가짜 데이터로 매일 초기화한다.
- 자세한 절차: [구축판 설치](docs/install-academy-windows.md), [데모판 배포](docs/deploy-demo-ncp.md)
