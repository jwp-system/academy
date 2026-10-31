# 데모판 배포 가이드 — 네이버클라우드(NCP)

누구나 둘러볼 수 있는 **데모판**을 네이버클라우드 서버 한 대에 올리는 절차입니다. 실제 운영(구축판)은 학원 PC에 설치합니다 → [구축판 설치 가이드](install-academy-windows.md).

> 요금·무료 혜택·서버 사양은 자주 바뀝니다. 아래 내용은 2026년 10월 기준으로 찾은 정보이니, 가입·생성 전에 [ncloud.com](https://www.ncloud.com) 콘솔과 요금 안내에서 다시 확인하세요.

## 0. 데모판이 구축판과 다른 점

| | 데모판 (`demo`) | 구축판 (`academy`) |
|---|---|---|
| 설치 위치 | 네이버클라우드 서버 (Ubuntu) | 학원 PC (Windows) |
| 데이터 | 가짜 데이터로 자동 채움, **매일 새벽 4시 초기화** | 실제 데이터 |
| 문자·메일 | **절대 실제로 보내지 않음** (코드에서 막음, 테스트 수신함에만) | 게이트웨이·Gmail로 실제 발송 |
| 입금 문자 웹훅 | 없음 (입금 매칭 화면의 '입금 문자 테스트'로 체험) | 있음 |
| 로그인 | "원장으로/강사로 둘러보기" 버튼 | 원장이 만든 계정 |
| 백업 | 필요 없음 (매일 초기화) | 매일 자동 백업 |
| DB | `DEMO_DATABASE_URL` (기본 `data/demo.db`) | `ACADEMY_DATABASE_URL` (기본 `data/academy.db`) |

데모 DB는 `DEMO_DATABASE_URL`로만 정하고, 파일 이름이 `demo`로 시작할 때만 초기화합니다. 같은 PC에서 구축판과 데모판을 함께 띄워도 실제 DB가 지워지지 않습니다.

```
브라우저 ─https─▶ Caddy(443) ─▶ demo 앱(127.0.0.1:8000) ─▶ data/demo.db
                     NCP 서버 (Ubuntu 24.04)
```

## 1. 준비물

- [ ] NCP 계정 (결제 수단 등록 필요)
- [ ] 도메인 (예: `demo.학원도메인.kr`) — HTTPS 인증서를 받으려면 필요합니다
- [ ] GitHub 저장소 `jwp-system/academy` (배포할 내용이 커밋·푸시돼 있어야 함)

## 2. NCP 서버 만들기

콘솔 → **Services → Compute → Server**.

1. **VPC / Subnet**: 처음이면 VPC 하나와 **Public Subnet** 하나를 만듭니다.
2. **서버 생성**
   - 이미지: **Ubuntu 24.04** (Python 3.12 기본 포함)
   - 사양: 데모는 작은 사양으로 충분합니다. 신규 고객 대상 **Micro 서버 1년 무료** 프로모션이 있는데([micro.ncloud.com](https://micro.ncloud.com/promotion/)), 1인 1대·결제 수단 등록 시점부터 1년·국내 거주자 조건이고 **1년이 지나면 반납하지 않은 서버는 자동 과금**됩니다. 공인 IP·트래픽은 무료 대상이 아닐 수 있습니다.
   - 인증키(.pem)를 새로 만들어 안전한 곳에 보관합니다.
3. **공인 IP**: Server → Public IP에서 하나 받아 서버에 연결합니다.
4. **ACG(방화벽)** 인바운드 규칙:

   | 프로토콜 | 포트 | 접근 소스 | 용도 |
   |---|---|---|---|
   | TCP | 22 | **내 사무실/집 IP**만 | SSH |
   | TCP | 80 | 0.0.0.0/0 | 인증서 발급(Let's Encrypt) |
   | TCP | 443 | 0.0.0.0/0 | 서비스 |

   8000 포트는 열지 않습니다 (앱은 서버 안에서만 Caddy가 접속).
5. **DNS**: 도메인 관리 화면에서 `A` 레코드 → 공인 IP.

## 3. 서버 기본 설정

콘솔의 "관리자 비밀번호 확인"에서 .pem으로 root 비밀번호를 확인하고 접속합니다.

```bash
ssh root@<공인IP>
timedatectl set-timezone Asia/Seoul
apt update && apt upgrade -y
apt install -y git sqlite3 unattended-upgrades

# 메모리가 작은 서버면 스왑 1GB (패키지 설치 중 메모리 부족 방지)
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 앱 전용 계정과 폴더
useradd --system --create-home --shell /bin/bash academy
mkdir -p /srv/academy && chown academy:academy /srv/academy
```

SSH 보안: 내 PC의 공개키를 `/root/.ssh/authorized_keys`에 넣고 접속되는 것을 확인한 뒤, `/etc/ssh/sshd_config`에서 `PasswordAuthentication no`로 바꾸고 `systemctl restart ssh`.

## 4. 앱 설치

```bash
su - academy
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv 설치
source ~/.local/bin/env

# 비공개 저장소면: 배포 키(읽기 전용) 만들고 GitHub 저장소 Settings → Deploy keys에 공개키 등록
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub

git clone git@github.com:jwp-system/academy.git /srv/academy
cd /srv/academy
uv sync --frozen --no-dev
mkdir -p data
cp deploy/demo-ncp/env.example .env
chmod 600 .env
nano .env
```

| 항목 | 값 |
|---|---|
| `ACADEMY_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` 결과. **비워 두면 앱이 시작하지 않습니다** |
| `ACADEMY_ACADEMY_NAME` | 화면에 보일 학원 이름 |
| `DEMO_DATABASE_URL` | 그대로 두면 `/srv/academy/data/demo.db` |

게이트웨이·메일·웹훅 설정은 필요 없습니다. DB도 따로 준비하지 않습니다 — 앱이 처음 뜰 때 표를 만들고 가짜 데이터로 채웁니다.

## 5. 앱 서비스(systemd)

```bash
exit   # root로 돌아오기
cp /srv/academy/deploy/demo-ncp/demo.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now demo
systemctl status demo             # active (running) 확인
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok","db":true,"scheduler":"running"}
```

## 6. HTTPS (Caddy)

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

cp /srv/academy/deploy/demo-ncp/Caddyfile /etc/caddy/Caddyfile
nano /etc/caddy/Caddyfile        # demo.example.com → 내 도메인
systemctl reload caddy
```

브라우저로 `https://내도메인` → 로그인 화면 아래에 "원장으로 둘러보기"가 보이면 성공. 인증서 발급이 실패하면 DNS가 공인 IP를 가리키는지, ACG에서 80·443이 열려 있는지 확인합니다 (`journalctl -u caddy -n 50`).

## 7. 데모 운영

- 매일 새벽 4시에 자동으로 처음 상태로 돌아갑니다. 바로 되돌리려면 로그인 화면의 "데모 처음 상태로 되돌리기", 또는 서버에서:
  ```bash
  su - academy -c "cd /srv/academy && .venv/bin/demo reset"
  ```
- 가짜 데이터는 [src/demo/seed.py](../src/demo/seed.py)에서 만듭니다 (학생 24명, 반 5개, 6주치 출결, 지난달·이번 달 수납, 확인 필요 입금, 발송 실패 예시). 보호자 번호는 모두 `010-0000-xxxx` 형태의 가짜 번호입니다.
- 실제 학생 데이터를 데모 서버에 올리지 마세요.

## 8. 업데이트

```bash
su - academy
cd /srv/academy && git pull && uv sync --frozen --no-dev
exit
systemctl restart demo
```

DB 구조가 바뀐 업데이트 뒤에는 `demo reset`으로 새로 만듭니다 (데모판은 마이그레이션 대신 매번 새로 만듦).

## 9. 운영 점검·보안

- `https://내도메인/healthz` 를 외부 모니터링(NCP Cloud Insight, UptimeRobot 등)에 등록
- 로그: `journalctl -u demo -f`
- `.env` 권한 600, `ACADEMY_COOKIE_SECURE=true`, 비밀키 설정
- SSH는 키로만, ACG의 22번은 내 IP만

| 증상 | 확인할 것 |
|---|---|
| 502 Bad Gateway | `systemctl status demo`, `journalctl -u demo -n 50` |
| 앱이 바로 꺼짐 + "ACADEMY_SECRET_KEY" | `.env`의 비밀키 |
| "데모 초기화는 demo*.db 파일에서만" | `DEMO_DATABASE_URL`의 파일 이름이 demo로 시작하는지 |
| 화면 시각이 9시간 어긋남 | 서비스 파일의 `Environment=TZ=Asia/Seoul` |
