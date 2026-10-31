# academy

소규모 학원 관리 시스템 (FastAPI + Jinja2 + HTMX + SQLite). 기획안은 [academy.md](academy.md).

같은 코드로 두 가지 판을 띄웁니다.

| | 구축판 `academy` | 데모판 `demo` |
|---|---|---|
| 용도 | 학원에서 실제 운영 | 누구나 둘러보기 |
| 설치 위치 | 학원 PC (Windows) | 네이버클라우드 (Ubuntu) |
| 문자·메일 | 실제 발송 (게이트웨이 로컬 모드, Gmail) | **절대 실제로 안 보냄** (테스트 수신함) |
| 입금 문자 웹훅 | 받음 (Cloudflare Tunnel https) | 없음 (화면에서 흉내) |
| 데이터 | 실제, 매일 자동 백업 | 가짜, 매일 새벽 4시 초기화 |
| 가이드 | [구축판 설치 (Windows)](docs/install-academy-windows.md) | [데모판 배포 (NCP)](docs/deploy-demo-ncp.md) |

## 시작하기 (개발)

```bash
uv sync
cp .env.example .env               # ACADEMY_SECRET_KEY 등

# 구축판: data/academy.db
uv run academy migrate
uv run academy create-admin admin@example.com --name 원장
uv run academy serve --reload      # http://127.0.0.1:8000

# 데모판: data/demo.db (가짜 데이터 자동 생성, 구축판 DB와 따로)
uv run demo serve --reload --port 8001
uv run demo reset                  # 처음 상태로
```

## 구조

```
src/
├─ app/                  공통 (두 판이 함께 씀)
│  ├─ main.py            create_app(edition): 미들웨어·라우터·스케줄러
│  ├─ edition.py         판이 바꿀 수 있는 것: 실제 발송 허용, 웹훅, 템플릿 덮어쓰기, 전용 화면·작업
│  ├─ config.py          설정 (.env, ACADEMY_ 접두사)
│  ├─ db.py, models.py   SQLite, SQLModel 모델
│  ├─ auth.py            로그인, CSRF, 권한
│  ├─ templating.py      render(): HX-Request면 조각, 아니면 전체 페이지
│  ├─ scheduler.py       알림 발송(30초), 회차 생성, 매일 10시 수납 알림
│  ├─ cli.py             공통 명령 (serve, create-admin, migrate)
│  ├─ routers/           화면별 라우터
│  ├─ services/          회차·출결·수납·입금 매칭·알림·백업·엑셀
│  ├─ templates/         화면 (_edition_banner.html, _login_extra.html은 판이 덮어쓰는 자리)
│  └─ static/            htmx, alpine, fullcalendar, app.css, app.js
├─ academy/              구축판: 자동 백업(매일 02:30)·백업 화면, academy 명령
└─ demo/                 데모판: 가짜 데이터(seed.py)·매일 초기화·바로 둘러보기, demo 명령
alembic/                 마이그레이션 (구축판)
deploy/academy-windows/  작업 스케줄러 등록 스크립트, .env 예시
deploy/demo-ncp/         systemd·Caddy 설정, .env 예시
docs/                    설치·배포 가이드
data/                    SQLite DB, 백업 (커밋 안 함)
```

## 개발

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
uv run alembic revision --autogenerate -m "..."   # 모델 변경 후 (구축판 DB)
```

테스트는 항상 테스트 모드로 돌아 `.env`에 게이트웨이 정보가 있어도 실제 문자가 나가지 않습니다.
