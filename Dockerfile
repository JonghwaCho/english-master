FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스 복사
COPY . .

# 학습 데이터는 영구 볼륨(/data)에 저장
ENV DATA_DIR=/data
ENV PORT=8080
ENV OPEN_BROWSER=0
EXPOSE 8080

# gunicorn: 단일 워커 + 멀티스레드 (SQLite 다중 프로세스 잠금 회피,
# 백그라운드 동기화 스레드도 한 번만 실행)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "120", "server:app"]
