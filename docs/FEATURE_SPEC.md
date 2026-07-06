# English Master v1.1 - 기능 명세서

## 0. 사용자 인증 (멀티유저)

> 상용 서비스 전환을 위한 인증 시스템. **Step 1 완료**: 이메일/비밀번호 인증.
> **Step 2 완료**: 데이터 `user_id` 완전 격리. **Step 3 완료**: 구글 로그인 + 사용자별 AI 키.

### F-0.5 구글 OAuth 로그인 (Step 3)
- **흐름**: `/api/auth/google`(state CSRF 토큰 + 구글 인증 URL 리다이렉트) → `/api/auth/google/callback`(code→token 교환→userinfo 조회→계정 연결/생성→세션)
- **계정 매칭**: `google_id`로 조회 → 없으면 동일 이메일 계정에 `google_id` 연결 → 둘 다 없으면 신규 생성(비밀번호 NULL)
- **설정**: 환경변수 `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`(+선택 `GOOGLE_REDIRECT_URI`). 미설정 시 로그인 화면 구글 버튼 비활성("준비 중")
- **구현**: 표준 라이브러리(urllib)만 사용, 외부 의존성 없음

### F-0.7 관리자 운영 도구
- **접근**: `/admin` 페이지 + `/api/admin/users`·`/api/admin/stats`. 관리자만(그 외 403/리다이렉트)
- **관리자 판별**: `ADMIN_EMAILS` 환경변수(콤마 구분) 또는 미설정 시 첫 사용자(id=1)
- **표시**: 전체 사용자 목록(이메일·이름·가입일·로그인수단·개인AI키 여부·영상/문장/단어/복습 수·최근 활동), 요약 통계(전체/7일 신규·활성 사용자, 총 콘텐츠)
- **주의**: 비밀번호 해시·AI 키 값은 노출하지 않음. 관리자 조회는 `_uid()` 필터를 우회하므로 반드시 서버 측 is_admin 검증 후 호출

### F-0.9 이메일 인증 (Email Verification)
- **가입 흐름**: 이메일/비밀번호 회원가입 시 계정을 **미인증(email_verified=0)** 상태로 생성하고 인증 메일을 발송, **로그인은 보류**. 인증 링크(`GET /api/auth/verify?token=...`) 클릭 시 인증 완료 + 즉시 로그인 → `/?verified=1`
- **로그인 차단**: 미인증 계정 로그인 시 `403 {error:"email_unverified"}`. 로그인 화면에 **재발송 버튼**(`POST /api/auth/resend-verification`) 노출
- **자동 인증 예외**: (1) **구글 OAuth** 계정 — 구글이 이메일을 검증했으므로 자동 인증. (2) **최초 운영자(첫 가입자=관리자)** — SMTP 설정을 위해 로그인해야 하는 닭-달걀 문제를 풀기 위해 인증 없이 통과. (3) 마이그레이션 시점의 **기존 사용자** — 락아웃 방지로 자동 인증
- **발송 설정(운영자가 편집)**: SMTP 설정을 `app_settings` 테이블에 저장하고 **관리자 도구에서 편집**한다. 발송 주체는 우리 회사가 아니라 서비스 운영자다. `GET/PUT /api/admin/email-settings`(비밀번호는 `has_password` 불리언으로만 반환·마스킹), `POST /api/admin/email-settings/test`(관리자 본인에게 테스트 메일)
- **발송 구현**: 표준 라이브러리 `smtplib`(`email_service.py`), TLS/SSL/none 지원. **dev 폴백**: SMTP 미설정/실패 시 인증 링크를 서버 로그에 출력(로컬에선 응답에도 `dev_verify_url` 노출)
- **users 추가 컬럼**: `email_verified`, `verify_token`, `verify_sent_at`

### F-0.8 요금제 · 콘텐츠 등록 할당량 (Billing / Quota)
- **요금제(4단계, `plans` 테이블)**: 기본값 — free(무료, 평생 1개), basic(3,000원/월, 5개), premium(5,000원/월, 10개), max(7,000원/월, 20개). **가격·콘텐츠 한도는 관리자 도구에서 변경 가능**하며 DB에 저장(하드코딩 아님)
- **카운트 기준(중요)**: "월 신규 등록 수". 콘텐츠를 받아와 **전체학습/개별학습이 가능한 상태로 등록되는 시점**(`_finalize_registration`)에 1개 소비. `content_registrations` 원장에 `(user_id, video_id)` 멱등 기록 → **영상을 삭제해도 카운트는 복구되지 않음**. 같은 콘텐츠 재등록은 소비 없음
- **주기**: free는 `period_type='lifetime'`(평생 누적), 유료는 `monthly` — **구독 시작일(plan_started_at) 기준 매월 리셋**. 관리자가 요금제를 변경하면 구독 시작일이 그 시점으로 갱신됨
- **한도 강제**: 등록 시 한도 초과분은 방금 만든 콘텐츠를 롤백(삭제)하고 `402 {error:"quota_exceeded", message, usage}` 반환. 프론트는 안내 문구 토스트 + 사이드바 사용량 배지 갱신
- **적용 경로**: YouTube 추가, 가사 추가, 텍스트/URL/파일 콘텐츠 등 사용자 등록 경로 5종. *(현재 범위 제외: 플레이리스트 자동 동기화로 추가되는 영상 — 추후 정책 결정)*
- **API**: `GET /api/me/plan`(내 사용량+요금제), 관리자 `GET /api/admin/plans`·`PUT /api/admin/plans/<code>`(가격·한도)·`PUT /api/admin/users/<id>/plan`(요금제 부여). PG(실결제) 연동은 다음 단계 — 현재는 관리자 수동 부여
- **users 추가 컬럼**: `plan_code`(기본 'free'), `plan_started_at`(구독 시작일)

### F-0.6 사용자별 AI 키 (Step 3)
- **정책(둘 다 지원)**: 개인 키 설정 시 개인 키 사용, 미설정 시 **서버 공용 키로 폴백**
- **저장**: `users.ai_provider`/`ai_key` (사용자별). `/api/ai/settings` GET/POST가 현재 사용자 기준으로 동작
- **서버 공용 키**: 환경변수 `SERVER_AI_PROVIDER`/`SERVER_AI_KEY`(우선) → `ai_settings.json`(폴백)
- **키 사용처 구분**: 배경 워커(전역 캐시 word_meanings/ai_cache 생성)는 **서버 키**, 상호작용 요청(직독직해·퀴즈·단어뜻 등)은 **사용자 키 우선**

### F-0.4 사용자별 데이터 격리 (Step 2)
- **대상 테이블**: `videos`, `categories`, `playlists`, `words`, `sentences`, `reviews`, `study_log`에 `user_id` 부여
- **UNIQUE 재정의**: `videos(user_id,url)`, `words(user_id,word)`, `categories(user_id,name)`, `playlists(user_id,playlist_id)` — 같은 콘텐츠도 사용자별 독립 보관
- **전역 공유 유지**: `word_meanings`, `ai_cache` (내용 기반 캐시)
- **격리 방식**: `database.set_current_user()`를 요청마다(`before_request`) 설정 → 모든 DB 함수가 `_uid()`로 자동 필터. 백그라운드 동기화 워커는 플레이리스트 소유자로 컨텍스트 설정
- **기존 데이터 이관**: 첫 가입자가 `claim_orphan_data()`로 소유자 없는 기존 데이터를 인수
- **검증**: 마이그레이션 데이터 보존, 첫 사용자 인수, 신규 사용자 격리, 양방향 격리, 교차 접근 차단 모두 확인

### F-0.1 회원가입 / 로그인
- **엔드포인트**: `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- **비밀번호**: werkzeug `pbkdf2:sha256` 해싱 저장 (평문 저장 안 함)
- **세션**: Flask 서명 쿠키 (`SECRET_KEY` 환경변수로 서명)
- **검증**: 이메일 형식, 비밀번호 8자 이상, 이메일 중복(409)
- **UI**: `templates/login.html` — 로그인/회원가입 탭 전환, 구글 버튼(준비 중 비활성)

### F-0.2 인증 게이트
- `@app.before_request`로 전역 적용
- 예외 경로: `/login`, `/api/auth/*`, `/static/*`, `/health`
- 미로그인 시: 페이지 요청 → `/login` 리다이렉트, API 요청 → `401 unauthorized`

### F-0.3 users 테이블
`id, email(unique), password_hash, name, google_id(unique), ai_provider, ai_key, created_at`
- `password_hash`는 OAuth 전용 계정에서 NULL 허용
- `ai_provider`/`ai_key`: 사용자별 AI 설정 (미설정 시 서버 공용 키로 폴백 — Step 3)

---

## 1. 콘텐츠 관리 시스템

### F-1.1 YouTube 영상 추가
- **입력**: YouTube URL
- **처리**: youtube-transcript-api로 자막 추출 → 문장 분리 → 타이밍 매핑
- **출력**: 비디오 메타 + 문장 목록 (paragraph_idx, sentence_idx, text, start_time, end_time)
- **예외**: 자막 없는 영상 → 가사 검색(lyrics.ovh) 또는 수동 입력 제공

### F-1.2 텍스트 콘텐츠 추가
- **입력**: 직접 텍스트 / 웹 URL / 파일(.txt, .pdf, .html)
- **처리**: 텍스트 정제 → 광고/네비게이션 제거 → 문장 분리 → 단락 그룹핑
- **출력**: content_type='text', 문장 목록 (타이밍 없음, TTS 재생)

### F-1.3 카테고리 관리
- **기능**: CRUD (생성/조회/수정/삭제)
- **연결**: 비디오 ↔ 카테고리 (N:1)
- **UI**: 사이드바 카테고리 목록, 필터링

### F-1.4 플레이리스트 관리
- **입력**: YouTube 플레이리스트 URL
- **기능**: 등록, 활성화/비활성화, 자동 동기화 (30분)
- **동기화**: RSS 피드 → 새 영상 자동 추가

---

## 2. 학습 시스템

### F-2.1 문장별 학습 (개별)
- **흐름**: 영상 선택 → new 상태 문장 순차 표시
- **입력**: 알아요(known) / 모르겠어요(unknown) 선택
- **처리**:
  - known → status='known', SRS level=1 스케줄 등록
  - unknown → status='unknown', SRS level=0, 번역/AI 캐시 백그라운드 생성

#### F-2.1.1 점진적 단어 노출
- **적용 조건**: 문장별 학습 화면
- **동작**:
  - 처음: 0% 단어 노출 (전체 마스킹 `·····`)
  - 다시듣기 1회: 20% 노출
  - 다시듣기 2회: 50% 노출
  - 다시듣기 3회: 75% 노출
  - 다시듣기 4회+: 100% 노출
- **노출 알고리즘**: 단어 인덱스를 균등 분배 (0, 중간, 끝부터)

#### F-2.1.2 카라오케 하이라이트
- **적용 조건**: YouTube 영상 문장 (start_time, end_time 존재)
- **동작**: 재생 타이밍에 맞춰 단어별 파란색 하이라이트 + 이전 단어 회색 처리
- **마스킹 연동**: 설정된 회차부터 마스킹된 단어도 하이라이트 시 잠깐 보였다가 다시 숨김
- **설정**: 카라오케 힌트 시작 회차 (1~4회, 기본 3회)

### F-2.2 전체학습 (일반 YouTube 영상)
- **흐름**: 영상 전체 연속 재생 + 문장 자동 추적
- **UI**: sticky 컨트롤 바 (일시정지/해석/단어/이모지/종료)
- **기능**:
  - 문장 클릭 → 모르는 문장 표시 (큐 방식)
  - Word Flash 카드
  - 카라오케 단어 하이라이트
  - 해석 ON/OFF
  - 이모지 필터

#### F-2.2.1 모르는 문장 큐 재생 (일반 영상)
- **트리거**: 문장 클릭 또는 R키
- **동작**:
  1. 현재 재생 중인 문장 끝까지 재생 (중단하지 않음)
  2. 큐에 모르는 문장 추가
  3. 현재 문장 종료 시 영상 일시정지
  4. 모르는 문장으로 스크롤 + 설정 속도로 재생
  5. 재생 완료 → 정상 속도로 다음 문장 자동 재개
- **복수 선택**: 여러 문장 클릭 시 큐에 순서대로 쌓임

### F-2.3 전체학습 (임베딩 차단 영상 / TTS)
- **적용 조건**: content_type='youtube_lyrics' 또는 런타임 임베딩 차단 감지
- **UI**: TTS 전용 화면 (일시정지/해석/이모지/종료)
- **기능**:
  - TTS 순차 재생 (rate 0.9, 선택된 음성)
  - 단어별 노란색 하이라이트 (onboundary 또는 타이머 폴백)
  - 해석 ON/OFF
  - YouTube 팝업 열기 옵션

#### F-2.3.1 모르는 문장 큐 재생 (TTS)
- **동작**:
  1. 현재 TTS 문장 끝까지 재생
  2. 큐에 모르는 문장 추가 (onend에서 확인)
  3. 모르는 문장으로 스크롤 + 설정 속도(replaySpeed)로 TTS 재생
  4. 재생 완료 → 다음 문장부터 TTS 흐름 자동 재개
- **동시성 보호**: `_ttsUnknownReplaying` 플래그로 중복 실행 방지

### F-2.4 전체학습 시각적 리셋 (Visual-Only Reset)
- **설정**: 전체학습 시 모르는 문장 리셋 ON/OFF (기본: ON)
- **동작**: ON일 때 `startFullVideoPlay()` 호출 시 `window._fullplayVisualReset = true` 플래그 설정
- **처리**:
  - **DB는 변경되지 않음** (`reset_unknown_sentences()`는 deprecated no-op)
  - 전체학습 화면 렌더링 시 `_fullplayVisualReset`가 true이면 `marked-unknown` 클래스를 추가하지 않음
  - 상태 점(dot)도 'new'로 표시
- **결과**: 전체학습 화면에서는 깨끗하게 시작하지만, "모르는 문장" 페이지에는 그대로 유지됨

### F-2.4.1 모르는 문장 누적 카운트 (unknown_count)
- **DB 컬럼**: `sentences.unknown_count INTEGER DEFAULT 0`
- **마이그레이션**: 기존 unknown 상태 문장은 1로 초기화
- **증가 시점**: `mark_sentence(id, 'unknown')` 호출 시 `unknown_count += 1`
- **세션 중복 방지**: `markFullplaySentence()` / `toggleTTSSentenceUnknown()`에서 `marked-unknown` 클래스로 세션 내 중복 클릭 차단 (DB는 세션당 1회만 증가)
- **표시**:
  - 토스트: "모르는 문장으로 저장됨 (N회)"
  - 모르는 문장 페이지: `❌ N회` 배지 (색상 강도: 1-2회/3-4회/5회+)
- **정렬**: `get_unknown_sentences()`에서 `unknown_count DESC`로 자주 틀린 문장 우선 정렬

### F-2.5 메뉴 이동 시 미디어 정리
- **트리거**: 사이드바 메뉴 클릭 (모든 페이지 전환)
- **동작**:
  - TTS 즉시 중단 (`speechSynthesis.cancel()`)
  - YouTube 일시정지
  - fullPlayActive 상태 완전 초기화
  - TTS 뷰 DOM 제거
  - fullplay 화면 숨김

---

## 3. 복습 시스템 (SRS)

### F-3.1 문장 개별 복습

#### F-3.1.1 시작 화면
- **흐름**: 개별학습 선택 → "시작하기" 화면 → 클릭 후 복습 시작
- **정보**: 총 N개 항목 표시

#### F-3.1.2 4버튼 리스닝 모드
- **적용 조건**: YouTube 문장 + 듣기 먼저 모드(reviewListenFirst=true)
- **UI**: "👂 듣고 있습니다..." + 4개 버튼
- **버튼**:
  | 버튼 | 동작 |
  |------|------|
  | 🔊 다시듣기 | forcePlayFromTo() 또는 speakText() |
  | 👂 잘안들림 | showListeningHint() - 힌트 단어 표시 |
  | 📖 내용확인 | revealFromListening() → revealReview() |
  | ✅ 완벽숙지 | reviewMarkPerfect() → API 호출 + 다음 문장 |

#### F-3.1.3 잘안들림 힌트
- **알고리즘**:
  1. 문장에서 모든 단어 추출
  2. 동일 수의 랜덤 일반 영단어 생성 (COMMON_HINT_WORDS 40개 풀)
  3. Fisher-Yates 셔플로 섞기
  4. opacity: 0.35로 희미하게 표시
- **클릭 동작**:
  - 문장 단어 → ⭕ 파란색 마킹 (hint-selected)
  - 랜덤 단어 → ❌ 빨간색 마킹 (hint-wrong)
  - 1회만 선택 가능 (이중 클릭 방지)

#### F-3.1.4 복습 팝업 위치
- **동작**: 잊어버렸어요/기억나요 클릭 시 마우스 위치 기반 팝업 표시
- **알고리즘**:
  1. 팝업을 (0,0)에 렌더링
  2. double-RAF 후 "확인" 버튼의 실제 위치 측정
  3. "확인" 버튼 중심이 클릭한 버튼 중심과 일치하도록 팝업 이동
  4. 뷰포트 밖으로 나가지 않도록 보정

### F-3.2 문장 전체 복습
- **UI**: 전체 목록에서 기억/잊음 체크
- **잊음 팝업**: 문장 표시 + 번역 + 모르는 단어 선택 + AI 도구

### F-3.3 단어 복습
- 문장 복습과 동일한 SRS 메커니즘
- AI 자동 뜻 조회 (word_meanings 캐시)

### F-3.4 복습 처리 로직
```
process_review(item_id, item_type, correct):
  if correct:
    level = min(level + 1, 7)
    streak += 1
    if level >= 7: mark 'mastered', delete from reviews
    else: schedule next_review
  else:
    level = 0
    streak = 0
    mark 'unknown'
    schedule next_review (즉시)
```

---

## 4. AI 통합

### F-4.1 AI 제공자
| 제공자 | 모델 | 엔드포인트 |
|--------|------|-----------|
| Gemini | gemini-2.5-flash | generativelanguage.googleapis.com |
| Claude | claude-sonnet-4-20250514 | api.anthropic.com |
| ChatGPT | gpt-4o-mini | api.openai.com |

### F-4.2 AI 액션
| 액션 | 설명 | 캐시 |
|------|------|------|
| literal | 직독직해 (단어/청크/전체/팁) | ✅ |
| similar | 유사 문장 패턴 생성 | ✅ |
| quiz | 3종 퀴즈 (선택/어순/번역) | ❌ (매번 새로 생성) |
| grammar | 문법 구조 분석 | ✅ |
| words | 단어별 뜻/품사/발음/예문 | ✅ |

### F-4.3 AI 폴백 체인
```
단어 뜻 조회:
  1. word_meanings 캐시 → 즉시 반환
  2. AI API 호출 → 캐시 저장 + 반환
  3. dictionaryapi.dev → 캐시 저장 + 반환
  4. 실패 → "(뜻을 불러올 수 없습니다)"
```

### F-4.4 백그라운드 프리캐싱
- 모르는 문장 마킹 시 큐에 추가
- 백그라운드에서 literal, grammar, words 3종 AI 결과 사전 생성
- 복습 시 즉시 표시 가능

---

## 5. 미디어 재생

### F-5.1 YouTube 임베딩
- YouTube IFrame API 사용
- `playFromTo(videoId, start, end)`: 구간 재생
- `forcePlayFromTo()`: 비디오 설정 무시하고 강제 재생
- 구간 종료 시 자동 정지 (sentenceEndTimer)

### F-5.2 임베딩 차단 감지
- **방법 1**: content_type='youtube_lyrics' (DB 기록)
- **방법 2**: `onYouTubeEmbedBlocked()` (런타임 감지)
- **폴백**: TTS 뷰 또는 YouTube 팝업

### F-5.3 TTS (Text-to-Speech)
- Web Speech API `SpeechSynthesisUtterance`
- 영어 음성 선택 (브라우저/OS별)
- 속도: 기본 0.9x, 모르는 문장 재생 시 설정값(replaySpeed)
- 단어별 하이라이트: `onboundary` 이벤트 또는 타이머 폴백

---

## 6. 통계 시스템

### F-6.1 통계 카드
- 8개 카드: 글래스모피즘 디자인
- `data-accent` 속성으로 카드별 컬러 악센트
- fadeInUp 순차 애니메이션
- 호버: 3px 부상 + 컬러 글로우

### F-6.2 차트 (Chart.js)
| 차트 | 유형 | 데이터 |
|------|------|--------|
| 문장 숙달 | 도넛 | mastered/known/learning/unknown/new |
| 단어 숙달 | 도넛 | mastered/known/unknown/frequently_wrong |
| 일별 학습 | 라인 | 30일간 문장/단어 학습 수 (모든 날짜 표시) |
| SRS 레벨 | 누적 막대 | Lv0~Lv7 문장/단어 분포 |

### F-6.3 콘텐츠 필터
- 드롭다운으로 특정 영상의 통계만 조회 가능

---

## 7. 설정 시스템

### F-7.1 학습 설정
| 설정 키 | 저장소 | 기본값 | 설명 |
|---------|--------|--------|------|
| videoEnabled | localStorage | true | 영상 표시 |
| translationEnabled | localStorage | false | 해석 표시 |
| ttsEnabled | localStorage | true | 음성 재생 |
| selectedVoiceName | localStorage | '' | TTS 음성 |
| konglishEnabled | localStorage | false | 콩글리시 표시 |
| studyKaraokeRevealFrom | localStorage | 3 | 카라오케 힌트 시작 회차 |
| fullplayResetUnknown | localStorage | true | 전체학습 리셋 |
| emojiFilterEnabled | localStorage | false | 이모지 필터 |
| replaySpeed | localStorage | 1 | 모르는 문장 재생 속도 |
| reviewListenFirst | localStorage | false | 듣기 먼저 모드 |
| reviewPopupOnCorrect | localStorage | true | 기억나요 팝업 |
| shortcutKeys | localStorage | 기본키 | 단축키 매핑 |

### F-7.2 AI 설정
| 설정 | 저장 | 설명 |
|------|------|------|
| AI 제공자 | 서버 DB | Gemini/Claude/ChatGPT |
| API 키 | 서버 DB | 암호화 없음 (로컬 전용) |

---

## 8. API 명세 요약

### 콘텐츠 API
| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | /api/videos | 영상 목록 |
| POST | /api/videos | YouTube 영상 추가 |
| DELETE | /api/videos/:id | 영상 삭제 |
| GET | /api/videos/:id/info | 영상 상세 |
| GET | /api/videos/:id/sentences | 영상 문장 목록 |

### 학습 API
| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | /api/study/sentences | 학습 대상 문장 |
| POST | /api/study/mark | 문장 상태 변경 |
| POST | /api/study/reset-unknown | 모르는 문장 리셋 |

### 복습 API
| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | /api/reviews | 복습 대기 항목 |
| POST | /api/reviews/process | 복습 결과 처리 |
| GET | /api/reviews/remaining | 남은 복습 수 |
| GET | /api/reviews/all | 전체 복습 항목 |
| GET | /api/reviews/counts | 복습 카운트 |

### 단어 API
| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | /api/words/unknown | 모르는 단어 |
| POST | /api/words/add | 단어 추가 |
| POST | /api/words/mark | 단어 상태 변경 |
| DELETE | /api/words/:id | 단어 삭제 |

### 통계 API
| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | /api/stats | 요약 통계 |
| GET | /api/analytics | 차트 데이터 |

### AI API
| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | /api/ai/action | AI 액션 실행 |
| GET | /api/ai/word-meaning | 단어 뜻 조회 |
| POST | /api/ai/word-meanings-batch | 단어 뜻 일괄 조회 |

---

## 9. 버전 이력

| 버전 | 날짜 | 주요 변경 |
|------|------|----------|
| v1.0 | 2026-04-06 | 초기 릴리즈 - 콘텐츠 필터, 간소화 UI, 사이드바 개편 |
| v1.1 | 2026-04-07 | 반응형 UI, 글래스모피즘, 리스닝 모드, 점진적 단어 노출, 전체학습 큐 재생 |
| v1.1.1 | 2026-04-08 | 모르는 문장 시각적 리셋 + unknown_count 누적 카운트 |
| v1.2 | 2026-07-06 | 클라우드 배포(Fly.io) + 이메일/비밀번호 인증(Step 1) — 회원가입/로그인/로그아웃, 인증 게이트, users 테이블 |
| v1.3 | 2026-07-06 | 멀티유저 데이터 완전 격리(Step 2) — 7개 테이블 user_id, 복합 UNIQUE 재구축, 요청별 사용자 컨텍스트, 첫 가입자 기존 데이터 인수 |
| v1.4 | 2026-07-06 | Step 3 — 구글 OAuth 로그인, 사용자별 AI 키(서버 공용 키 폴백) |
| v1.5 | 2026-07-06 | 관리자 운영 도구 — `/admin` 대시보드(전체 사용자·데이터량·활성/신규 통계), 관리자 접근 제어 |
| v1.6 | 2026-07-07 | 요금제·콘텐츠 등록 할당량 — free/basic/premium/max 4단계, 관리자 가격·한도 편집, 등록 원장 기반 카운트(삭제해도 유지), 구독 시작일 기준 월 리셋, 한도 초과 시 402 차단 |
| v1.7 | 2026-07-07 | 이메일 인증 — 가입 시 인증 메일, 미인증 로그인 차단, 재발송, 관리자 SMTP 설정 편집(운영자별), smtplib 발송, 구글/최초운영자/기존사용자 자동 인증 |

---

*이 문서는 English Master v1.1 기준으로 작성되었습니다.*
*최종 업데이트: 2026-07-07*
