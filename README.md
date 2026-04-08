# English Master

AI 기반 영어 학습 플랫폼 - YouTube 영상 기반 에빙하우스 망각곡선 간격 반복 학습

## 버전

- **v1.3** (main 브랜치): 단일 사용자 로컬 앱
- **commercial-saas 브랜치**: 다중 사용자 SaaS (Phase 1 완료)

## 문서

- [사용자 매뉴얼](docs/USER_MANUAL.md)
- [시스템 설계](docs/ARCHITECTURE.md)
- [기능 명세서](docs/FEATURE_SPEC.md)
- [SaaS 설정 가이드 (commercial-saas 브랜치)](docs/SAAS_SETUP.md)

## v1.3 실행 (main 브랜치)

```bash
git checkout main
pip3 install -r requirements.txt
python3 server.py
# http://127.0.0.1:5294
```

## v2 SaaS 실행 (commercial-saas 브랜치)

```bash
git checkout commercial-saas
pip3 install -r requirements.txt
cp .env.example .env
# .env 편집: SECRET_KEY, JWT_SECRET 설정

python3 scripts/init_db.py
python3 scripts/seed_plans.py
python3 wsgi.py
# http://127.0.0.1:5294
```

자세한 내용은 [SAAS_SETUP.md](docs/SAAS_SETUP.md) 참고.

## 주요 기능

### 학습
- YouTube 영상 / 텍스트 / URL / 파일에서 콘텐츠 자동 추출
- 문장별 점진적 단어 노출 (0%→20%→50%→75%→100%)
- 카라오케 단어 하이라이트 (영상 타이밍 동기화)
- 전체 재생 모드 (영상 + 문장 자동 추적)
- 모르는 문장 누적 카운트

### 복습
- 에빙하우스 망각곡선 SRS (Lv0~Lv7, 30일 후 완전 습득)
- 4버튼 리스닝 모드 (다시듣기/잘안들림/내용확인/완벽숙지)
- 잘안들림 힌트 (단어 O/X 선별)
- 개별학습 + 전체학습

### AI
- 직독직해, 유사문장, 문법설명, 단어설명, 퀴즈
- Gemini / Claude / ChatGPT 지원
- 캐싱으로 비용 절감

### SaaS 기능 (commercial-saas 브랜치)
- 4단계 구독: 무료 / ₩4,900 / ₩9,900 / ₩19,900
- 사용자별 데이터 격리
- 이메일 + Google + Kakao OAuth
- JWT 기반 인증
- 월별 쿼터 (콘텐츠/AI)

## 아키텍처

```
Frontend (PWA) ←→ Flask API ←→ PostgreSQL
                        ↓
                   Celery Workers
                        ↓
                   AI Providers (Gemini/Claude/GPT)
```

## 라이선스

Private. 상용화 준비 중.
