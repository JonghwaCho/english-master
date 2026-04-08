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
| sentences | 개별 문장 데이터 | text, status, start_time, end_time, translation |
| words | 학습 단어 | word, status |
| reviews | SRS 복습 스케줄 | item_id, item_type, level, next_review |
| categories | 콘텐츠 분류 | name |
| playlists | YouTube 플레이리스트 | playlist_id, enabled |
| playlist_videos | 플레이리스트-영상 연결 | playlist_id, video_db_id |
| study_log | 학습 활동 로그 | item_id, action, correct |
| word_meanings | 단어 뜻 캐시 | word, meaning, source |
| word_video_link | 단어-영상 출처 연결 | word_id, video_id |
| ai_cache | AI 결과 캐시 | sentence_text, action, result |

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

- AI API 키는 서버 측 저장 (클라이언트 노출 없음)
- SQLite 로컬 DB (네트워크 접근 없음)
- 로컬호스트(127.0.0.1)만 바인딩
- 사용자 인증 없음 (개인 학습 도구)

## 10. 성능 최적화

- AI 결과 캐싱 (ai_cache 테이블)
- 단어 뜻 캐싱 (word_meanings 테이블)
- 번역 캐싱 (sentences.translation 컬럼)
- 백그라운드 워커로 비동기 처리
- localStorage로 UI 설정 클라이언트 캐싱
- Chart.js 인스턴스 재사용 (destroyChart 패턴)

---

*이 문서는 English Master v1.1 기준으로 작성되었습니다.*
*최종 업데이트: 2026-04-07*
