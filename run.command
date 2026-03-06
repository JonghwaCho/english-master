#!/bin/bash
# English Master 영어 마스터 - 더블클릭으로 실행
cd "$(dirname "$0")"

# Check and install deps if needed
python3 -c "import flask; import youtube_transcript_api" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "패키지를 설치합니다..."
    python3 -m pip install --quiet -r requirements.txt
fi

python3 server.py
