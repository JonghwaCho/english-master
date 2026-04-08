# English Master v1.1 - 기능 명세서

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

---

*이 문서는 English Master v1.1 기준으로 작성되었습니다.*
*최종 업데이트: 2026-04-08*
