# English Master v1.1 - 시스템 설계 문서

## 1. 시스템 개요

English Master는 YouTube 영상 기반 영어 학습 웹 애플리케이션으로, 에빙하우스 망각곡선(Spaced Repetition System)을 핵심으로 한 문장/단어 학습 플랫폼입니다.

### 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────┐
│                  Browser (Client)                │
│  ┌───────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ HTML/CSS  │ │ Chart.js │ │ YouTube IFrame │  │
│  │ (SPA UI)  │ │ (통계)   │ │ API + TTS API  │  │
│  └─────┬─────┘ └────┬─────┘ └───────┬────────┘  │
│        └─────────────┴───────────────┘           │
│                      │ HTTP (JSON)               │
└──────────────────────┼───────────────────────────┘
                       │
┌──────────────────────┼───────────────────────────┐
│              Flask Server (:5294)                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ API      │ │ Background│ │ External APIs    │  │
│  │ Routes   │ │ Workers   │ │ - YouTube Trans  │  │
│  │ (50+)    │ │ (4 threads)│ │ - AI (Gemini/   │  │
│  │          │ │           │ │   Claude/GPT)    │  │
│  │          │ │           │ │ - Dictionary API │  │
│  │          │ │           │ │ - Lyrics API     │  │
│  └────┬─────┘ └─────┬────┘ └──────────────────┘  │
│       └─────────────┬┘                            │
│              ┌──────┴──────┐                      │
│              │   SQLite    │                      │
│              │ (11 tables) │                      │
│              └─────────────┘                      │
└───────────────────────────────────────────────────┘
```

## 2. 기술 스택

| 계층 | 기술 | 용도 |
|------|------|------|
| Frontend | HTML/CSS/JavaScript (Vanilla) | SPA 단일 페이지 앱 |
| 차트 | Chart.js 4.4.1 | 통계 시각화 |
| 영상 | YouTube IFrame API | 영상 재생/제어 |
| 음성 | Web Speech API (SpeechSynthesis) | TTS 음성 재생 |
| Backend | Flask (Python 3.8+) | REST API 서버 |
| DB | SQLite3 | 로컬 데이터 저장 |
| AI | Gemini / Claude / ChatGPT API | 학습 보조 |
| 자막 | youtube-transcript-api | YouTube 자막 추출 |

## 3. 디렉토리 구조

```
english-master/
├── server.py              # Flask 서버 + API 라우트 (50+ endpoints)
├── database.py            # SQLite DB 스키마 + CRUD 함수
├── srs.py                 # 에빙하우스 망각곡선 알고리즘
├── text_utils.py          # 텍스트 추출/정제 유틸리티
├── youtube_service.py     # YouTube 영상/자막/플레이리스트 처리
├── requirements.txt       # Python 의존성 (flask, youtube-transcript-api)
├── setup.sh               # 초기 설치 스크립트
├── run.command            # macOS 실행 스크립트
├── data/                  # SQLite DB 파일 저장 디렉토리
│   └── english_master.db
├── templates/
│   └── index.html         # SPA 프론트엔드 (HTML + CSS + JS 통합)
└── docs/
    ├── USER_MANUAL.md     # 사용자 매뉴얼
    ├── ARCHITECTURE.md    # 설계 문서 (본 문서)
    └── FEATURE_SPEC.md    # 기능 명세서
```

## 4. 데이터베이스 설계

### 4.1 ERD (Entity Relationship)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  categories │     │   videos    │     │  sentences  │
│─────────────│     │─────────────│     │─────────────│
│ id (PK)     │◄────│ category_id │     │ id (PK)     │
│ name        │     │ id (PK)     │◄────│ video_id    │
│ created_at  │     │ url (UNIQUE)│     │ text        │
└─────────────┘     │ video_id    │     │ paragraph_idx│
                    │ title       │     │ sentence_idx│
                    │ content_type│     │ status      │
                    │ source_text │     │ start_time  │
                    │ created_at  │     │ end_time    │
                    └──────┬──────┘     │ translation │
                           │            └──────┬──────┘
                           │                   │
                    ┌──────┴──────┐     ┌──────┴──────┐
                    │  playlists  │     │   reviews   │
                    │─────────────│     │─────────────│
                    │ id (PK)     │     │ id (PK)     │
                    │ playlist_id │     │ item_id     │
                    │ category_id │     │ item_type   │
                    │ enabled     │     │ level (0-7) │
                    └─────────────┘     │ next_review │
                                        │ last_review │
┌─────────────┐     ┌─────────────┐     │ streak      │
│   words     │     │word_video_  │     └─────────────┘
│─────────────│     │   link      │
│ id (PK)     │◄────│ word_id     │     ┌─────────────┐
│ word(UNIQUE)│     │ video_id    │     │  study_log  │
│ status      │     └─────────────┘     │─────────────│
│ created_at  │                         │ item_id     │
└─────────────┘     ┌─────────────┐     │ item_type   │
                    │word_meanings│     │ action      │
                    │─────────────│     │ correct     │
                    │ word (PK)   │     │ created_at  │
                    │ meaning     │     └─────────────┘
                    │ source      │
                    └─────────────┘     ┌─────────────┐
                                        │  ai_cache   │
                                        │─────────────│
                                        │ sentence_text│
                                        │ action      │
                                        │ result      │
                                        └─────────────┘
```

### 4.2 테이블 상세

| 테이블 | 역할 | 주요 컬럼 |
|--------|------|----------|
| videos | 콘텐츠(영상/텍스트) 메타데이터 | url, video_id, title, content_type |
| sentences | 개별 문장 데이터 | text, status, start_time, end_time, translation, unknown_count |
| words | 학습 단어 | word, status |
| reviews | SRS 복습 스케줄 | item_id, item_type, level, next_review |
| categories | 콘텐츠 분류 | name |
| playlists | YouTube 플레이리스트 | playlist_id, enabled |
| playlist_videos | 플레이리스트-영상 연결 | playlist_id, video_db_id |
| study_log | 학습 활동 로그 | item_id, action, correct |
| word_meanings | 단어 뜻 캐시 | word, meaning, source |
| word_video_link | 단어-영상 출처 연결 | word_id, video_id |
| ai_cache | AI 결과 캐시 | sentence_text, action, result |
| plans | 요금제 정의(관리자 편집) | code, price, content_limit, **ai_daily_limit, ai_monthly_limit** |
| content_registrations | 콘텐츠 등록 원장(할당량) | user_id, video_id, registered_at |
| ai_usage_log | 서버 공용 키 AI 호출 원장(일/월 한도) | user_id, used_at(UTC) |
| payments | 결제 주문 원장(단건 이용권) | order_id(UNIQUE), user_id, plan_code, amount, status, payment_key |

### 4.3 콘텐츠 유형 (content_type)

| 유형 | 설명 | 재생 방식 |
|------|------|----------|
| youtube | 일반 YouTube 영상 | 임베딩 플레이어 |
| youtube_lyrics | 임베딩 차단 YouTube | TTS 또는 팝업 |
| text | 텍스트 직접 입력/URL/파일 | TTS |

## 5. SRS (Spaced Repetition System) 설계

### 5.1 에빙하우스 망각곡선 구현

```
Level 0 ──[1h]──> Level 1 ──[24h]──> Level 2 ──[48h]──> Level 3
                                                            │
Level 7 <──[30d]── Level 6 <──[15d]── Level 5 <──[7d]── Level 4
 (MASTERED)                                        [96h]
```

### 5.2 복습 처리 로직

```
정답(correct=true):
  level += 1
  streak += 1
  if level >= 7: status = 'mastered', 복습 큐 제거
  else: next_review = 현재시각 + intervals[level]

오답(correct=false):
  level = 0
  streak = 0
  status = 'unknown'
  next_review = 현재시각 (즉시 복습 대상)
```

## 6. 백그라운드 워커

| 워커 | 큐 | 역할 |
|------|-----|------|
| meaning_worker | word_meaning_queue | 모르는 단어 뜻 자동 조회 (AI → 사전 API) |
| sentence_translation_worker | sentence_trans_queue | 모르는 문장 한국어 번역 자동 생성 |
| ai_precache_worker | ai_precache_queue | AI 분석 사전 캐싱 (직독직해, 문법, 단어) |
| sync_thread | (timer) | 플레이리스트 30분 주기 자동 동기화 |
| backup_loop | (timer) | 매일 1회 DB 자동 백업(1시간마다 '오늘 미백업 시' 생성, `ENABLE_BACKUP`) |
| billing_loop | (timer) | 자동결제 구독의 예정일 지난 건을 매일 청구·갱신(`ENABLE_BILLING`) |

## 7. 프론트엔드 설계

### 7.1 SPA 라우팅

```
showPage(page) → 모든 .page 숨김 → 해당 page-{name} 활성화 → loadPage(page)
```

### 7.2 페이지 구조

| 페이지 ID | 경로 | 기능 |
|-----------|------|------|
| page-dashboard | dashboard | 대시보드 |
| page-videos | videos | 콘텐츠 관리 |
| page-study | study | 학습하기 |
| page-review | review-sentences, review-words | 복습하기 |
| page-unknown-sentences | unknown-sentences | 모르는 문장 |
| page-unknown-words | unknown-words | 모르는 단어 |
| page-stats | stats | 통계 |
| page-settings | settings | 설정 |

### 7.3 UI 디자인 시스템

#### 색상 변수
```css
--bg: #0f0f1a          /* 메인 배경 */
--bg2: #1a1a2e         /* 카드 배경 */
--primary: #6366f1     /* 인디고 (주 강조) */
--green: #22c55e       /* 성공/아는 항목 */
--red: #ef4444         /* 오류/모르는 항목 */
--amber: #f59e0b       /* 경고/복습 대기 */
--purple: #a78bfa      /* 완전 습득 */
--cyan: #22d3ee        /* 보조 (단어) */
```

#### 글래스모피즘 효과
```css
--glass-bg: rgba(255, 255, 255, 0.04)
--glass-border: rgba(255, 255, 255, 0.08)
--glass-blur: 12px
backdrop-filter: blur(12px)
```

### 7.4 반응형 브레이크포인트

| 너비 | 변경 사항 |
|------|----------|
| > 1200px | 전체 레이아웃 (사이드바 240px) |
| ≤ 1200px | 사이드바 200px |
| ≤ 900px | 사이드바 상단 가로 네비, 차트 1열 |
| ≤ 600px | 스탯 2열, 폰트/패딩 축소 |

## 8. 외부 API 연동

| API | 용도 | 폴백 |
|-----|------|------|
| YouTube IFrame API | 영상 재생/제어 | TTS |
| youtube-transcript-api | 자막 추출 | 수동 입력/가사 검색 |
| YouTube oEmbed | 영상 제목 조회 | - |
| Gemini/Claude/ChatGPT | AI 학습 보조 | 사전 API |
| dictionaryapi.dev | 무료 영영 사전 | - |
| lyrics.ovh | 가사 자동 검색 | 수동 입력 |
| YouTube RSS | 플레이리스트 동기화 | - |

## 9. 보안 고려사항

- **사용자 인증**: 이메일/비밀번호 (werkzeug `pbkdf2:sha256` 해싱), Flask 서명 세션 쿠키
  - `SECRET_KEY` 환경변수로 세션 서명 (프로덕션 필수 — 미지정 시 재시작마다 세션 무효화)
  - `@app.before_request` 전역 게이트로 모든 페이지/API 보호 (`/login`, `/api/auth/*`, 법적 고지 페이지 `/terms`·`/privacy`·`/refund`·`/api/legal/*` 제외)
- AI API 키는 서버 측 저장 (클라이언트 노출 없음)
- **데이터 격리 완료(Step 2)**: 7개 테이블에 `user_id` 부여 + 복합 UNIQUE 재구축.
  요청마다 `db.set_current_user()`로 컨텍스트 설정 → 모든 DB 함수가 `_uid()`로 자동 필터.
  교차 사용자 접근 차단 검증됨. `word_meanings`/`ai_cache`만 전역 공유(내용 기반).
- **세션 쿠키 보안**: `HttpOnly` + `SameSite=Lax`, 프로덕션은 `Secure`(`COOKIE_SECURE=1`).
  `Lax`는 구글 OAuth 콜백 복귀와 호환(최상위 GET에 쿠키 전송).
- **로그인 brute-force 방지**: IP 기준 인메모리 레이트리밋(15분 창 8회 초과 시 429).
- **계정 정지/삭제(관리자)**: `users.suspended` → `before_request`에서 세션 클리어+차단(API 403 `suspended`). 삭제는 `delete_user()`가 사용자 범위 10개 테이블+링크 테이블을 정리(전역 캐시 보존). **관리자·본인 계정은 정지·삭제 불가**(`is_admin` 검증, 락아웃 방지).
  `--workers 1` 전제 — 확장 시 공유 저장소(Redis) 필요.
- HTTPS 강제 (Fly.io `force_https`)

## 10. 성능 최적화

- AI 결과 캐싱 (ai_cache 테이블)
- 단어 뜻 캐싱 (word_meanings 테이블)
- 번역 캐싱 (sentences.translation 컬럼)
- 백그라운드 워커로 비동기 처리
- localStorage로 UI 설정 클라이언트 캐싱
- Chart.js 인스턴스 재사용 (destroyChart 패턴)

## 11. 배포 (Deployment)

상용화를 위해 로컬 전용 앱을 클라우드 배포 가능한 구조로 전환했다.

### 11.1 환경변수 기반 설정

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `DATA_DIR` | `<프로젝트>/data` | SQLite DB + ai_settings.json 저장 경로. 클라우드에서는 영구 볼륨(`/data`) 지정 |
| `PORT` | `5294` | 서버 포트 (배포 시 8080) |
| `OPEN_BROWSER` | `1` | 로컬 실행 시 브라우저 자동 오픈 (배포 시 0) |
| `FLASK_DEBUG` | `1` | 디버그 모드 (배포 시 0) |
| `ENABLE_SYNC` | `1` | 플레이리스트 자동 동기화 스레드 (다중 워커 시 0) |
| `COOKIE_SECURE` | `0` | 세션 쿠키 Secure 플래그 (프로덕션 HTTPS에서 1) |
| `SECRET_KEY` | (dev값) | Flask 세션 서명 키 (프로덕션 필수) |
| `SERVER_AI_PROVIDER`/`SERVER_AI_KEY` | (없음) | 서버 공용 AI 키 (사용자 개인 키 미설정 시 폴백) |
| `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` | (없음) | 구글 OAuth 자격증명 (미설정 시 구글 로그인 비활성) |
| `GOOGLE_REDIRECT_URI` | (요청 호스트에서 유도) | 구글 콜백 URI 명시 지정 |

### 11.2 실행 방식

- **로컬 개발**: `python3 server.py` — `__main__` 블록에서 dev 서버 구동 + 브라우저 오픈
- **프로덕션**: `gunicorn server:app` — `init_app()`이 **import 시점**에 `db.init_db()`와
  백그라운드 스레드를 시작하므로 WSGI 서버에서도 정상 동작
- **동시성**: gunicorn `--workers 1 --threads 8` (SQLite 다중 프로세스 잠금 회피 +
  백그라운드 동기화 스레드 단일 실행 보장)

### 11.3 배포 대상 (Fly.io)

- `Dockerfile`: python:3.12-slim + gunicorn
- `fly.toml`: 도쿄(nrt) 리전, `/data` 영구 볼륨 마운트, HTTPS 강제, 유휴 시 자동 절전
- **주의**: SQLite는 단일 볼륨/단일 머신 전제 — 스케일아웃 시 Postgres 등으로 전환 필요

### 11.4 멀티유저 전환 (완료)

- ✅ 사용자별 데이터 분리(`user_id`) — Step 2
- ✅ 인증(이메일/비밀번호 + 구글 OAuth) — Step 1, 3
- ✅ AI 키: 사용자별 키 + 서버 공용 키 폴백(둘 다 지원) — Step 3
- ✅ 요금제·콘텐츠 등록 할당량 — 아래 11.5 참조
- ✅ 이메일 인증 — 아래 11.6 참조
- ✅ 플레이리스트 동기화 영상 할당량 정책 — 미적용(무제한)으로 **결정**(11.5 참조)
- ✅ AI 사용량 제한(서버 공용 키 비용 보호) — 아래 11.7 참조
- ✅ DB 백업(자동 일일 + 관리자 수동/다운로드) — 아래 11.8 참조
- ✅ 실결제(토스페이먼츠 단건 1개월 이용권) — 아래 11.9 참조
- 남은 과제: **자동 정기결제(빌링)**, 환불 처리(현재 토스 대시보드 수동)

### 11.5 요금제 · 콘텐츠 등록 할당량 (Billing / Quota)

- **`plans` 테이블**: 요금제 정의(code/name/price/content_limit/period_type/sort_order). **관리자 도구에서 가격·한도 편집 가능** — 코드 하드코딩이 아니라 DB 값. 최초 1회 기본 4단계(free/basic/premium/max) 시드
- **`content_registrations` 원장**: 콘텐츠가 '학습 가능 상태'로 등록된 이벤트를 `(user_id, video_id)` UNIQUE로 기록. **영상 행을 세지 않고 원장을 세므로** 영상을 삭제해도 카운트가 복구되지 않는다("월 신규 등록 수" 정책). 마이그레이션/데이터 인수 시 기존 영상을 원장에 백필
- **users 컬럼 추가**: `plan_code`(기본 free), `plan_started_at`(구독 시작일)
- **주기 계산**: `_period_start()` — lifetime(free)은 전 기간 누적, monthly는 구독 시작일 기준 '가장 최근 매월 기념일'(짧은 달 말일 보정). 시각은 SQLite `datetime('now')`(UTC)와 일치하도록 UTC로 계산
- **강제 지점**: `server._finalize_registration()`을 콘텐츠 등록 5개 경로(YouTube/가사/텍스트/URL/파일)에서 호출. 한도 초과 시 방금 생성한 콘텐츠를 롤백하고 402 응답. 재등록(멱등)은 소비하지 않음
- **정책(결정됨)**: 플레이리스트 자동 동기화(백그라운드/수동)로 추가되는 영상은 **의도적으로 할당량에 포함하지 않는다**(무제한). 수동 등록 5경로에만 한도 적용. 무료 우회 리스크를 알고도 허용 — 남용 시 재검토. (DECISION_LOG 2026-07-07 참조)

### 11.6 이메일 인증 (Email Verification)

- **`app_settings` 테이블**(범용 키-값): 이메일(SMTP) 설정을 저장하며 **관리자 도구에서 편집**한다. 발송 주체는 우리 회사가 아니라 서비스 운영자 — env 고정이 아니라 DB 값으로 두어 재배포 없이 운영자가 자기 메일 계정으로 바꾼다. 최초 시드는 환경변수(SMTP_HOST 등)가 있으면 반영
- **users 컬럼 추가**: `email_verified`, `verify_token`, `verify_sent_at`
- **발송 모듈** `email_service.py`: 표준 라이브러리 `smtplib`만 사용(TLS/SSL/none). 외부 의존성 없음
- **인증 흐름**: 가입 → 미인증 생성 + 토큰 발급 + 인증 메일 → 로그인 보류. 링크 클릭(`/api/auth/verify`)으로 인증 완료 후 즉시 로그인. 미인증 로그인은 403(`email_unverified`) + 재발송
- **자동 인증**: 구글 OAuth 계정, 최초 운영자(첫 가입자=관리자, 닭-달걀 방지), 마이그레이션 시점 기존 사용자
- **dev 폴백**: SMTP 미설정/실패 시 인증 링크를 서버 로그에 출력(로컬은 응답에도 노출)해 설정 전에도 테스트 가능
- **비밀번호 노출 금지**: 관리자 GET 응답은 `has_password` 불리언만 반환. PUT은 비밀번호가 비어 있지 않을 때만 갱신

### 11.7 AI 사용량 제한 (서버 공용 키 비용 보호)

- **문제**: 개인 키 없는 사용자는 **서버 공용 키**(`SERVER_AI_KEY`)로 AI를 쓰고 그 비용은 운영자가 전액 부담 → 상한 없으면 비용 폭탄.
- **대상(과금 호출만 집계)**: `서버키 + 캐시 미스 + 호출 성공`만 `ai_usage_log`에 1 기록. **캐시 히트·무료 사전(dictionaryapi) 폴백·개인 키 호출·배경 워커(전역 캐시 생성)는 제외.**
- **한도(`plans` 컬럼, 관리자 편집)**: `ai_daily_limit`/`ai_monthly_limit`. **-1=무제한, 0=사용 불가, 양수=허용 횟수**. 주기는 **캘린더 일/월(UTC)** — SQLite `datetime('now')`(UTC)과 `used_at`을 일치시켜 계산. 비용 안전 우선: 0을 '무제한' 아닌 '차단'으로 정의.
- **마이그레이션**: 기존 `plans`에 AI 컬럼 ALTER 추가 후, ALTER 기본값(20/200)을 상위 요금제 권장값으로 backfill(관리자 커스텀 `content_limit` 등은 보존). `ai_usage_log`는 `(user_id, used_at)`·`(used_at)` 인덱스. `record_ai_usage()`가 ≈5% 확률로 40일 초과 기록 정리.
- **enforcement**: `server._ai_guard()`가 호출 직전 게이트 — 개인키→무제한·미기록, 서버키 초과→**429 `ai_quota_exceeded`**, 통과→성공 시 기록. `/api/ai/action`(초과 429), `/api/ai/word-meaning`(초과 시 사전 폴백·은혜로운 저하), `/api/ai/test`(429)에 적용. `GET /api/ai/usage`로 잔여 조회, 설정 패널에 표시.

### 11.8 DB 백업 (Backup)

- **방식**: SQLite **온라인 백업 API**(`sqlite3.Connection.backup`)로 실행 중에도 **일관된** 스냅샷을 `DATA_DIR/backups/english_master-YYYYMMDD-HHMMSS.db`에 생성. 파일 복사(`cp`)는 쓰기 중 비일관 위험 때문에 기각.
- **스케줄**: `_backup_loop` 스레드가 1시간마다 깨어나 "오늘(UTC) 백업 없으면 1회" 생성 → **매일 1회**. `init_app`에서 `ENABLE_BACKUP`(기본 1) 가드. 단일 워커(Dockerfile `--workers 1`)라 중복 없음.
- **보관**: `prune_backups(keep=14)`로 최신 14개만 유지(롤링). `/data` 볼륨 용량 보호.
- **관리자 API**: `GET/POST /api/admin/backups`, `GET /api/admin/backups/<name>/download`(`send_file`), `DELETE /api/admin/backups/<name>`. 파일명은 `database.backup_path_if_valid()`가 정규식으로 검증 → **경로탈출/임의 파일 접근 차단**.
- **다층 방어**: 같은 볼륨 백업 = 앱 버그·실수 삭제 보호. **볼륨 유실** 대비는 (a) 관리자 다운로드(오프사이트) + (b) **Fly 볼륨 스냅샷**(`fly volumes snapshots`, 보존기간 `--snapshot-retention`). "백업=안전" 과신 금지.

### 11.10 법적 고지 (약관/개인정보/환불 + 사업자 정보)

- **공개 페이지**: `/terms`·`/privacy`·`/refund` → `legal.html`(page 분기, 표준 약관 템플릿 + `get_legal_settings()` 주입, "법률검토 권장" 배너). `_PUBLIC_PREFIXES`로 비로그인 접근 허용.
- **사업자 정보**: `LEGAL_SETTING_KEYS`를 app_settings에 저장(운영자 `/admin` 편집). **실제 법적 값(사업자등록번호 등)은 코드가 생성하지 않고 운영자 입력** — 미설정은 "(미설정)".
- **동의**: 가입 시 `agree` 필수(서버 검증) → `users.agreed_terms_at` 기록. 결제 전 고지는 업그레이드 모달에서 `/api/legal/info`로 사업자정보·정책 링크 표시.

### 11.9 실결제 (Billing / Toss Payments)

- **모델**: **단건 1개월 이용권**. 승인 성공 → `set_user_plan()`으로 요금제 1개월 부여(월주기 = 구매 시점). 자동 갱신(빌링)은 백로그.
- **플로우**: `POST /api/billing/checkout`(서버가 요금제 가격으로 `payments` 주문 READY 생성) → 토스 v2 SDK `requestPayment` → successUrl `GET /api/billing/confirm`.
- **보안 핵심**:
  - **금액 변조 차단**: 콜백 `amount`를 서버 저장 주문 금액과 대조. 소유자·상태(READY) 검증. 불일치 → 승인 호출 없이 FAILED.
  - **서버 승인**: `secret_key`로 `POST https://api.tosspayments.com/v1/payments/confirm`(Basic `base64(secretKey:)`). `client_key`만 프론트 노출.
  - **멱등**: `mark_payment_done()`이 `READY→DONE` 단일 전이 → 중복 콜백에도 이중 부여/과금 없음.
- **설정**: 토스 키는 `app_settings`에 저장(`/admin` 편집, secret 마스킹), env(`TOSS_CLIENT_KEY`/`TOSS_SECRET_KEY`) 시드. `_toss_settings()`가 DB→env 폴백.
- **환불/취소**: 관리자가 `/admin` 결제 목록의 DONE 건을 취소하면 `_toss_cancel`(`POST /v1/payments/{paymentKey}/cancel`, 전액) 호출 → `payments.status=CANCELED` + `set_user_plan(free)`로 요금제 회수. 멱등(DONE일 때만 전이).
- **자동 정기결제(빌링)**: 카드 등록(`requestBillingAuth`) → `auth-confirm`에서 빌링키 발급(`/v1/billing/authorizations/issue`)·첫 청구(`/v1/billing/{billingKey}`)·구독 active. `_billing_loop`가 매일 예정일 지난 구독을 청구(성공=요금제 연장+다음 청구 +1개월, 실패=past_due +1일 재시도, 3회→canceled). 구독 상태는 users 컬럼(billing_key 등), **빌링키는 비노출**(카드 청구 권한). customerKey 사용자 고정·검증.
- **남은 것**: 운영 키 교체(사업자 심사)·토스 자동결제 사용 신청, 부분환불·사용자 셀프환불, 결제수단 변경, 영수증.

---

*이 문서는 English Master v1.1 기준으로 작성되었습니다.*
*최종 업데이트: 2026-10-16 (결제 법적 고지: 약관/개인정보/환불 공개 페이지·사업자정보 운영자 편집·가입 동의 — 실결제 전환 요건)*
