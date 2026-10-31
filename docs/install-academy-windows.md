# 구축판 설치 가이드 — 학원 PC (Windows 10/11)

학원 안의 PC 한 대에 실제 운영용 시스템을 설치합니다. 처음 한 번은 위에서부터 순서대로 하고, 이후 업데이트는 [8. 업데이트](#8-업데이트)만 보면 됩니다.

## 0. 구성

```
학부모 휴대폰 ◀─SMS─ 게이트웨이 폰 ◀─(학원 와이파이, http)─┐
은행 ─입금 문자─▶ 게이트웨이 폰 ─웹훅(https)─▶ Cloudflare ─┐ │
원장·강사 (학원 안·밖 모두) ─https─────────────▶ Cloudflare ─┤ │
                                                            ▼ ▼
                              학원 PC: Cloudflare Tunnel → 앱(127.0.0.1:8000) → SQLite
                                                                         └─ 매일 백업 → OneDrive 등
```

| 구성 요소 | 하는 일 |
|---|---|
| 학원 PC (Windows) | 앱이 작업 스케줄러로 **부팅하면 자동 실행**, 꺼지면 1분 뒤 다시 실행. 항상 켜 둔다 |
| Cloudflare Tunnel | 공유기 포트를 열지 않고 `https://academy.내도메인` 주소를 만들어 준다 (무료). 학원 안에서도 이 주소로 접속 |
| 게이트웨이 폰 | 학원 와이파이에 연결, **로컬 서버 모드**. 앱이 같은 와이파이로 직접 문자 발송 |
| 자동 백업 | 매일 02:30 DB 사본을 백업 폴더에. **OneDrive·구글 드라이브 동기화 폴더**로 지정하면 PC가 고장 나도 클라우드에 남는다 |

> Litestream(기획안의 실시간 백업)은 Windows를 공식 지원하지 않아서, 구축판은 앱이 직접 매일 백업하는 방식으로 바꿨습니다.

## 1. 준비물

- [ ] 항상 켜 둘 Windows 10/11 PC (인터넷 연결)
- [ ] 게이트웨이용 안드로이드 폰 + 문자 무제한 요금제, 충전기
- [ ] **도메인** + **Cloudflare 계정** (무료 플랜). 도메인을 Cloudflare에 연결해 두어야 터널 주소를 만들 수 있습니다
- [ ] GitHub 저장소 `jwp-system/academy` 접근 권한
- [ ] (권장) OneDrive 또는 구글 드라이브 데스크톱 앱

## 2. PC 설정

1. **절전 끄기**: 설정 → 시스템 → 전원 → 화면·절전 → 전원 연결 시 **절전 모드: 안 함**
   (관리자 PowerShell: `powercfg /change standby-timeout-ac 0`)
2. **Windows 업데이트 사용 시간**: 설정 → Windows 업데이트 → 고급 옵션 → 사용 시간을 학원 운영 시간으로 (수업 중 재부팅 방지). 재부팅돼도 앱은 자동으로 다시 뜹니다.
3. **시간대**: 설정 → 시간 및 언어 → (UTC+09:00) 서울. 앱은 시각을 한국 시간 그대로 저장합니다.

## 3. 앱 설치

**관리자 PowerShell**을 열고:

```powershell
winget install --id Git.Git -e                                   # Git
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv (Python 포함 관리)
# PowerShell 창을 닫았다가 다시 연다 (경로 반영)

git clone https://github.com/jwp-system/academy.git C:\Academy
cd C:\Academy
uv sync --frozen --no-dev
copy deploy\academy-windows\env.example .env
notepad .env
```

`.env`에서 먼저 채울 것:

| 항목 | 값 |
|---|---|
| `ACADEMY_SECRET_KEY` | `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"` 결과. **비워 두면 앱이 시작하지 않습니다** |
| `ACADEMY_ACADEMY_NAME`, `ACADEMY_BANK_ACCOUNT` | 학원 이름, 청구 안내에 넣을 계좌 |
| `ACADEMY_BACKUP_DIR` | 백업 폴더. 예: `C:\Users\원장\OneDrive\학원백업` |
| 문자·웹훅 항목 | [6. 게이트웨이](#6-문자-게이트웨이-로컬-서버-모드)에서. 비워 두면 **테스트 모드**(실제로 안 보냄) |

DB 만들기와 원장 계정:

```powershell
uv run academy migrate
uv run academy create-admin 원장이메일@example.com --name 원장
```

이미 쓰던 DB가 있으면 `data\academy.db`로 복사한 뒤 `uv run academy migrate`만 하면 됩니다.

## 4. 자동 실행 등록

```powershell
cd C:\Academy
powershell -ExecutionPolicy Bypass -File deploy\academy-windows\install-task.ps1
```

"실행 중이에요"가 나오면 성공입니다. 브라우저에서 `http://127.0.0.1:8000` 이 열리는지 확인하세요.

- 작업 이름: `AcademyApp` (작업 스케줄러에서 볼 수 있음). PC를 켜면 로그인 전에도 실행됩니다.
- 로그: `C:\Academy\data\app.log`
- 다시 시작: `deploy\academy-windows\restart.ps1`, 해제: `uninstall-task.ps1` (둘 다 관리자 PowerShell)

> `ACADEMY_COOKIE_SECURE=true`라서 로그인은 다음 단계의 **https 주소**로 해야 합니다. 터널을 만들기 전에 PC에서 바로 확인하려면 잠시 `false`로 두고 restart.ps1을 실행하세요.

## 5. 외부 접속 주소 (Cloudflare Tunnel)

1. Cloudflare 대시보드 → **Zero Trust → Networks → Tunnels → Create a tunnel** (Cloudflared)
2. 운영체제로 **Windows**를 고르면 설치 방법과 `cloudflared.exe service install <토큰>` 명령이 나옵니다. 안내대로 cloudflared를 설치하고 **관리자 PowerShell**에서 그 명령을 실행합니다 (Windows 서비스로 등록돼 부팅 시 자동 실행).
3. 터널의 **Routes → Add route → Published application**: 서브도메인 `academy`, 도메인 선택, Service URL `http://localhost:8000`
4. `https://academy.내도메인` 에서 로그인 화면이 뜨면 성공. 원장·강사 모두 이 주소를 즐겨찾기에 추가합니다 (학원 안에서도).

## 6. 문자 게이트웨이 (로컬 서버 모드)

1. 게이트웨이 폰을 학원 와이파이에 연결하고, **공유기 관리 화면에서 폰 IP를 고정**(DHCP 고정 할당)합니다. 예: `192.168.0.50`
2. 폰에 **SMS Gateway for Android** 설치 → **Local Server** 켜기 → 화면의 주소·아이디·비밀번호를 `.env`에:
   ```
   ACADEMY_SMS_GATEWAY_URL=http://192.168.0.50:8080
   ACADEMY_SMS_GATEWAY_USER=…
   ACADEMY_SMS_GATEWAY_PASSWORD=…
   ```
3. `restart.ps1` 실행 → 앱의 **알림 → 테스트 → 연결 확인**이 "연결됨"인지 보고, 내 번호로 테스트 발송
4. **입금 문자 받기**
   - 은행 앱에서 입금 알림 문자가 게이트웨이 폰으로 오게 설정
   - 폰 앱 **Settings → Webhooks → Signing Key** 값을 `ACADEMY_SMS_WEBHOOK_SIGNING_KEY`에, 은행 문자 발신번호를 `ACADEMY_BANK_SENDER_NUMBERS`에 넣고 `restart.ps1`
   - 웹훅 등록 (학원 PC PowerShell, 폰과 같은 와이파이):
     ```powershell
     curl.exe -X POST -u 아이디:비밀번호 -H "Content-Type: application/json" `
       -d '{\"url\": \"https://academy.내도메인/webhooks/sms-inbound\", \"event\": \"sms:received\"}' `
       http://192.168.0.50:8080/webhooks
     ```
     웹훅은 폰이 인터넷을 거쳐 https 주소로 보냅니다 (게이트웨이 앱은 사설 IP·http 주소를 받지 않음).
   - 학원 계좌로 1,000원을 넣어 보고 **수납 → 입금 매칭**에 들어오는지 확인

## 7. 백업과 복구

- 자동: 매일 02:30 `ACADEMY_BACKUP_DIR`에 `academy-날짜-시각.db`, 30일 보관. **백업** 메뉴에서 목록 확인·지금 백업·내려받기. 이틀 넘게 백업이 없으면 경고가 뜹니다.
- 수동: 오른쪽 위 **DB 백업** 버튼
- **복구**:
  ```powershell
  cd C:\Academy
  Stop-ScheduledTask -TaskName AcademyApp; Get-Process academy -EA SilentlyContinue | Stop-Process -Force
  Rename-Item data\academy.db academy-broken.db
  Remove-Item data\academy.db-wal, data\academy.db-shm -ErrorAction SilentlyContinue
  Copy-Item "백업폴더\academy-20261101-0230.db" data\academy.db
  uv run academy migrate
  Start-ScheduledTask -TaskName AcademyApp
  ```
- PC를 바꿀 때: 새 PC에 3~6단계를 하고 최근 백업을 `data\academy.db`로 복사

## 8. 업데이트

```powershell
cd C:\Academy
uv run academy backup        # 업데이트 전 사본
git pull
uv sync --frozen --no-dev
uv run academy migrate
powershell -ExecutionPolicy Bypass -File deploy\academy-windows\restart.ps1
```

## 9. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 접속이 안 됨 | PC가 켜져 있는지, `http://127.0.0.1:8000/healthz`, 작업 스케줄러의 `AcademyApp` 상태, `data\app.log` |
| 앱이 바로 꺼짐 | `data\app.log`에 "ACADEMY_SECRET_KEY" → `.env`의 비밀키 |
| 로그인해도 다시 로그인 화면 | http 주소로 접속 중. `https://academy.내도메인`으로 (`COOKIE_SECURE=true`) |
| 외부에서만 안 됨 | Cloudflare 터널 상태(대시보드에서 Healthy), Windows 서비스 `Cloudflared` 실행 여부 |
| 문자가 테스트 수신함에만 쌓임 | 알림 → 테스트의 발송 경로가 "테스트 모드" → `.env` 게이트웨이 아이디·비밀번호 |
| "게이트웨이에 연결할 수 없어요" | 폰이 같은 와이파이인지, 폰 IP가 바뀌지 않았는지, 게이트웨이 앱 Local Server가 켜져 있는지 |
| 입금 문자가 안 들어옴 | 입금 매칭의 "마지막 수신", 웹훅 등록, 서명 키, 은행 발신번호, 폰의 인터넷 |
| 같은 문자가 두 번 감 | 앱이 두 개 떠 있지 않은지 (`Get-Process academy`) |
