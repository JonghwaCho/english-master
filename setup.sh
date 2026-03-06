#!/bin/bash
# English Master - Setup Script
cd "$(dirname "$0")"

echo "╔══════════════════════════════════════╗"
echo "║   English Master 영어 마스터 Setup    ║"
echo "╚══════════════════════════════════════╝"

# Check Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "Python이 설치되어 있지 않습니다."
    echo "https://www.python.org/downloads/ 에서 설치해주세요."
    exit 1
fi

echo "Python: $($PYTHON --version)"

# Install dependencies
echo ""
echo "필요한 패키지를 설치합니다..."
$PYTHON -m pip install --quiet --upgrade pip
$PYTHON -m pip install --quiet -r requirements.txt

echo ""
echo "설치 완료! 다음 명령으로 실행하세요:"
echo "  $PYTHON server.py"
echo ""
echo "또는 run.command 파일을 더블클릭하세요."
