# English Master - Claude Code 작업 규칙

## 문서 자동 업데이트 규칙 (필수)

**이 프로젝트는 3개의 핵심 문서를 관리합니다:**

1. `docs/USER_MANUAL.md` - 사용자 매뉴얼
2. `docs/ARCHITECTURE.md` - 시스템 설계 문서
3. `docs/FEATURE_SPEC.md` - 기능 명세서

### 필수 워크플로

코드를 수정(`.py`, `.html`, `.js`, `.css`)할 때마다 **반드시** 다음을 수행합니다:

1. **기능 변경 분석**: 수정한 코드가 어떤 기능/동작에 영향을 주는지 파악
2. **해당 문서 찾기**: 3개 문서 중 관련 섹션 식별
   - 사용자 관점 변경 → `USER_MANUAL.md`
   - 아키텍처/DB/API 구조 변경 → `ARCHITECTURE.md`
   - 기능 동작/알고리즘/API 명세 변경 → `FEATURE_SPEC.md`
3. **문서 업데이트**: 관련 섹션에 변경사항 반영
4. **버전 이력 추가**: 큰 변경의 경우 `FEATURE_SPEC.md`의 "버전 이력" 섹션 업데이트
5. **최종 업데이트 날짜 갱신**: 각 문서 하단의 날짜 수정

### 예외 상황

다음 경우에만 문서 업데이트를 생략할 수 있습니다:
- 오타/포매팅 수정 (기능 변경 없음)
- 주석만 수정
- 사용자가 명시적으로 "문서 업데이트 불필요"라고 요청

### 자동 검증

- **Git pre-commit hook**: 코드 수정 시 문서 미업데이트면 커밋 거부
- **Claude Stop hook**: 세션 종료 시 코드만 수정되고 문서 미업데이트면 경고

### 업데이트 예시

```
사용자: "학습하기에 새 버튼 추가해줘"

작업 순서:
1. templates/index.html 수정
2. docs/USER_MANUAL.md - "4. 학습하기" 섹션에 새 버튼 설명 추가
3. docs/FEATURE_SPEC.md - "F-2.x" 기능 명세 추가
4. (필요시) docs/ARCHITECTURE.md - UI 구조 업데이트
5. 각 문서 최종 업데이트 날짜 갱신
```

---

## 프로젝트 정보

- **서버**: Flask on `http://127.0.0.1:5294`
- **DB**: SQLite (`data/english_master.db`)
- **프론트엔드**: 단일 HTML 파일 (`templates/index.html`)
- **실행**: `python3 server.py` 또는 `preview_start`로 launch.json 사용

## 주요 파일

| 파일 | 역할 |
|------|------|
| server.py | Flask API 서버 (50+ endpoints) |
| database.py | SQLite 스키마 + CRUD |
| srs.py | 에빙하우스 망각곡선 알고리즘 |
| text_utils.py | 텍스트 추출/정제 |
| youtube_service.py | YouTube 자막/플레이리스트 |
| templates/index.html | SPA 프론트엔드 (HTML+CSS+JS 통합) |
