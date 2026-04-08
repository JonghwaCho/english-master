# English Master - Commercial SaaS 설정 가이드

이 문서는 `commercial-saas` 브랜치에서 구현된 Phase 1의 설정 및 실행 방법을 설명합니다.

## 완료된 Phase 1 작업

- [x] Flask Blueprint 기반 프로젝트 구조 (`app/` 패키지)
- [x] 환경 변수 기반 설정 (`app/config.py`, `pydantic-settings`)
- [x] SQLAlchemy 모델 - 모든 테이블 `user_id` FK 포함 (`app/db/models.py`)
- [x] `users`, `user_sessions`, `oauth_accounts`, `user_settings` 테이블 추가
- [x] 구독/결제 테이블 미리 정의 (`plans`, `subscriptions`, `payments`, `usage_counters`, `ai_usage_log`)
- [x] JWT 기반 인증 (Access 15분 + Refresh 30일, httpOnly cookie)
- [x] bcrypt 패스워드 해싱
- [x] `@require_auth`, `@require_tier` 데코레이터
- [x] 이메일 회원가입/로그인/로그아웃/리프레시
- [x] 이메일 인증 + 비밀번호 재설정 (SMTP 미설정 시 stdout)
- [x] Google OAuth 로그인 플로우
- [x] Kakao OAuth 로그인 플로우
- [x] PIPA 준수 동의 필드 (이용약관, 개인정보, 마케팅)
- [x] 사용자 설정 API (`GET/PUT/PATCH /api/users/settings`)
- [x] 모든 콘텐츠 API에 `user_id` 필터 (videos, categories, sentences)
- [x] 모든 학습/복습/단어 API에 `user_id` 필터
- [x] 통계 API (`/api/stats`, `/api/usage`) - 사용자별 격리
- [x] AI 모듈 - 서버 사이드 키 + 쿼터 강제 (`enforce_ai_quota`)
- [x] AI 제공자 추상화 (Gemini, Claude, OpenAI) + 재시도
- [x] 공유 캐시 보존 (`word_meanings`, `ai_cache` - 비용 절감)
- [x] Celery 워커 설정 + 3개 태스크 (AI 프리캐시, 번역, 단어 뜻)
- [x] 쿼터 시스템 (videos/월, AI calls/월) - 402 에러 반환
- [x] 티어별 기능 플래그 (`plans.features_json`)
- [x] Alembic 마이그레이션 설정
- [x] SQLite → 새 DB 마이그레이션 스크립트 (`scripts/migrate_sqlite_to_postgres.py`)
- [x] 플랜 시드 스크립트 (`scripts/seed_plans.py`)
- [x] DB 초기화 스크립트 (`scripts/init_db.py`)
- [x] 테스트 스위트 (pytest + auth + ownership + quota)
- [x] 프론트엔드 로그인/회원가입 페이지 (`templates/auth.html`)
- [x] JWT 자동 처리 부트스트랩 (`app/static/auth-bootstrap.js`)
- [x] Rate limiting (flask-limiter)
- [x] Security headers (HSTS, X-Frame-Options 등)
- [x] 헬스체크 엔드포인트 (`/healthz`)

## 빠른 시작

### 1. 의존성 설치
```bash
pip3 install -r requirements.txt
```

### 2. 환경 변수 설정
```bash
cp .env.example .env
# .env 파일 열어서 최소한 SECRET_KEY, JWT_SECRET 변경
```

개발용 최소 설정:
```env
ENV=development
DEBUG=true
SECRET_KEY=randomly-generated-32+-character-string-for-dev
JWT_SECRET=another-32+-character-random-string-for-jwt
DATABASE_URL=sqlite:///data/english_master_v2.db
```

> 💡 `DATABASE_URL`이 상대 경로(`sqlite:///data/...`)여도 `config.py`의 validator가
> 자동으로 프로젝트 루트 기준 절대 경로로 변환합니다. Flask 디버그 모드에서 CWD가
> 바뀌어도 안전하게 동작합니다.

### 3. 데이터베이스 초기화
```bash
# 스키마 생성
python3 scripts/init_db.py

# 플랜 시드 (free/basic/heavy/vip)
python3 scripts/seed_plans.py
```

### 4. 기존 v1.3 데이터 이관 (선택)
```bash
python3 scripts/migrate_sqlite_to_postgres.py \
  --source data/english_master.db \
  --admin-email your-email@example.com \
  --admin-password temp-password-change-me
```

### 5. 서버 실행
```bash
# 개발 모드
python3 wsgi.py

# 프로덕션 모드 (gunicorn)
gunicorn --bind 0.0.0.0:5294 --workers 3 wsgi:app
```

### 6. Celery 워커 (선택, AI 프리캐싱용)
```bash
# Redis가 실행 중이어야 함
redis-server  # 별도 터미널

# 워커 시작
celery -A app.workers.celery_app.celery worker --loglevel=info
```

## 테스트 실행
```bash
pytest
pytest tests/test_auth.py -v
pytest tests/test_ownership.py -v  # 중요: 사용자 격리 검증
```

## API 엔드포인트

### 인증 (`/api/auth/*`)
- `POST /api/auth/signup` - 이메일 회원가입
- `POST /api/auth/login` - 이메일 로그인
- `POST /api/auth/logout` - 로그아웃
- `POST /api/auth/refresh` - 액세스 토큰 갱신
- `POST /api/auth/verify-email` - 이메일 인증
- `POST /api/auth/forgot-password` - 비밀번호 재설정 요청
- `POST /api/auth/reset-password` - 비밀번호 재설정
- `GET /api/auth/me` - 현재 사용자 정보
- `DELETE /api/auth/me` - 계정 삭제
- `GET /api/auth/oauth/google` - Google OAuth 시작
- `GET /api/auth/oauth/google/callback` - Google 콜백
- `GET /api/auth/oauth/kakao` - Kakao OAuth 시작
- `GET /api/auth/oauth/kakao/callback` - Kakao 콜백

### 사용자 (`/api/users/*`)
- `GET /api/users/profile` - 프로필 조회
- `PATCH /api/users/profile` - 프로필 수정
- `GET /api/users/settings` - 설정 조회 (localStorage 대체)
- `PUT /api/users/settings` - 설정 전체 교체
- `PATCH /api/users/settings` - 설정 부분 업데이트

### 콘텐츠 (`/api/*`)
- `GET /api/categories` - 카테고리 목록
- `POST /api/categories` - 카테고리 생성
- `PUT /api/categories/{id}` - 카테고리 수정
- `DELETE /api/categories/{id}` - 카테고리 삭제
- `GET /api/videos` - 비디오 목록 (사용자 소유만)
- `GET /api/videos/{id}/info` - 비디오 정보
- `GET /api/videos/{id}/sentences` - 비디오 문장 목록
- `POST /api/videos` - YouTube URL 추가 (쿼터 소비)
- `DELETE /api/videos/{id}` - 비디오 삭제
- `PUT /api/videos/{id}/category` - 비디오 카테고리 변경
- `POST /api/content/text` - 텍스트 콘텐츠 추가 (쿼터 소비)
- `DELETE /api/sentences/{id}` - 문장 삭제
- `GET /api/sentences/unknown` - 모르는 문장 목록
- `GET /api/sentences/known` - 아는 문장 목록

### 학습/복습 (`/api/*`)
- `GET /api/study/sentences?video_id=X` - 학습할 문장 목록
- `POST /api/study/mark` - 문장 상태 표시 (known/unknown)
- `GET /api/study/paragraphs/{video_id}` - 단락 목록
- `GET /api/reviews?type=sentence|word` - 복습 대기 목록
- `GET /api/reviews/counts` - 복습 대기 개수
- `GET /api/reviews/remaining` - 남은 복습 수
- `GET /api/reviews/all` - 전체 복습 항목
- `POST /api/reviews/process` - 복습 결과 처리
- `GET /api/reviews/videos` - 복습 있는 비디오 목록

### 단어 (`/api/words/*`)
- `GET /api/words/unknown` - 모르는 단어 목록
- `GET /api/words/known` - 아는 단어 목록
- `POST /api/words/add` - 단어 추가
- `POST /api/words/mark` - 단어 상태 변경
- `DELETE /api/words/{id}` - 단어 삭제

### AI (`/api/ai/*`, 쿼터 강제)
- `POST /api/ai/action` - AI 액션 실행 (literal/similar/grammar/words/quiz)
- `GET /api/ai/word-meaning?word=X` - 단어 뜻 조회
- `POST /api/ai/word-meanings-batch` - 단어 뜻 일괄 조회 (캐시만)
- `GET /api/ai/usage` - 현재 사용량

### 통계 (`/api/*`)
- `GET /api/stats` - 사용자 통계
- `GET /api/usage` - 쿼터 사용량

### 헬스 (`/healthz`)
- `GET /healthz` - 상태 체크 (로드 밸런서용)

## Phase 0: 사용자가 직접 처리해야 할 외부 작업

제가 코드로 도와드릴 수 없는 실세계 작업입니다. 가능한 빨리 시작해주세요:

### 필수 작업 (2-3주)
1. **도메인 구입**: 가비아/후이즈에서 `englishmaster.kr` 등 구입
2. **사업자등록**: 국세청 홈택스에서 간이과세자 또는 일반과세자
3. **NCP 가입**: https://www.ncloud.com → Sub Account 활성화
4. **Toss Payments 가맹점 신청**: https://www.tosspayments.com/ (2-3주 소요)
5. **Google Cloud Console OAuth**: https://console.cloud.google.com
   - Credentials → Create OAuth 2.0 Client ID
   - Authorized redirect URI: `http://127.0.0.1:5294/api/auth/oauth/google/callback`
   - Client ID/Secret을 `.env`의 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`에 입력
6. **Kakao Developers OAuth**: https://developers.kakao.com
   - My Application → Product Settings → Kakao Login 활성화
   - Redirect URI: `http://127.0.0.1:5294/api/auth/oauth/kakao/callback`
   - REST API 키를 `.env`의 `KAKAO_CLIENT_ID`에 입력
7. **AI 제공자 상용 계정**: OpenAI/Anthropic/Google Cloud에 결제 수단 등록

### 법적 작업
- 이용약관, 개인정보처리방침, 환불정책, 정기결제 약관 작성 (변호사 검토 권장)
- KISA 호스팅 컴플라이언스 체크
- 14세 미만 연령 게이트 (정보통신망법)

## 다음 단계: Phase 2-5

Phase 1 코드가 모두 완성되었습니다. 다음 단계:

### Phase 2: 구독 & 티어 (8-11주차)
- Toss Payments 빌링키 통합 (정기결제)
- 웹훅 처리 (`/api/webhooks/toss`)
- 결제 UI (`/billing` 페이지)
- Celery beat 정기결제 스케줄러

### Phase 3: 프로덕션 인프라 (12-15주차)
- Docker 이미지 빌드
- NCP 인프라 프로비저닝
- Cloudflare 프록시
- CI/CD (GitHub Actions)
- 모니터링 (Sentry, Grafana)
- 백업 & DR

### Phase 4: PWA 프론트엔드 (16-20주차)
- `manifest.json`, Service Worker
- 모바일 우선 UI 개편
- localStorage → 서버 설정 마이그레이션

### Phase 5: HA / 리던던시 (7-9개월차)
- 자동 스케일링
- DB Read Replica
- Redis 클러스터

## 주요 파일

| 경로 | 역할 |
|------|------|
| `app/__init__.py` | Flask 앱 팩토리 |
| `app/config.py` | 환경 변수 기반 설정 |
| `app/db/models.py` | SQLAlchemy 모델 전체 |
| `app/auth/routes.py` | 인증 엔드포인트 |
| `app/auth/oauth.py` | Google/Kakao OAuth |
| `app/auth/decorators.py` | `@require_auth`, `@require_tier` |
| `app/quota.py` | 티어 쿼터 강제 |
| `app/content/routes.py` | 콘텐츠 API |
| `app/study/routes.py` | 학습/복습/단어 API |
| `app/ai/routes.py` | AI 엔드포인트 |
| `app/ai/providers.py` | AI 제공자 추상화 |
| `app/workers/celery_app.py` | Celery 설정 |
| `wsgi.py` | 앱 진입점 |
| `templates/auth.html` | 로그인/회원가입 페이지 |
| `app/static/auth-bootstrap.js` | JWT 자동 처리 |
| `scripts/init_db.py` | DB 초기화 |
| `scripts/seed_plans.py` | 플랜 시드 |
| `scripts/migrate_sqlite_to_postgres.py` | v1.3 데이터 이관 |
| `tests/` | 테스트 (auth, ownership, quota) |

## 보안 체크리스트 (Phase 1 완료 시점)

- [x] 비밀번호 bcrypt 해싱 (cost 12)
- [x] JWT 서명 검증
- [x] httpOnly + SameSite=Lax refresh 쿠키
- [x] Rate limiting (flask-limiter)
- [x] 소유권 검증 (모든 리소스 접근 시 user_id 체크)
- [x] CSRF 방지 (SameSite 쿠키)
- [x] 입력 검증 (이메일/비밀번호 형식)
- [x] Security 헤더 (HSTS, X-Frame-Options 등)
- [x] PIPA 동의 기록
- [x] 계정 삭제 (이메일 익명화)
- [ ] 프로덕션: HTTPS 강제 (Cloudflare + NCP LB)
- [ ] 프로덕션: Secrets Manager (NCP KMS)
- [ ] 프로덕션: 로그 감사
- [ ] 프로덕션: 침해 테스트

## 테스트 커버리지

| 카테고리 | 테스트 파일 |
|---------|------------|
| 인증 플로우 | `tests/test_auth.py` |
| 사용자 격리 | `tests/test_ownership.py` ⭐ 가장 중요 |
| 쿼터 강제 | `tests/test_quota.py` |

실행:
```bash
pytest -v
```
