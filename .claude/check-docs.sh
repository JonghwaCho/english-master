#!/bin/bash
# Auto-reminder: 코드 파일 수정 시 문서 업데이트 확인
# 경로는 스크립트 위치(.claude/) 기준으로 자동 계산 → 회사/집 어느 PC에서든 동작

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR" || exit 0

# 최근 수정된 코드 파일 확인 (git diff)
CODE_CHANGED=$(git diff --name-only HEAD 2>/dev/null | grep -E "\.(py|html|js|css)$" | grep -v "^docs/")
DOCS_CHANGED=$(git diff --name-only HEAD 2>/dev/null | grep -E "^docs/.*\.md$")

# 코드 변경 O, 문서 변경 X → 알림
if [ -n "$CODE_CHANGED" ] && [ -z "$DOCS_CHANGED" ]; then
  cat <<EOF >&2
{"decision": "block", "reason": "📝 코드가 수정되었는데 문서가 업데이트되지 않았습니다.\n\n수정된 파일:\n$CODE_CHANGED\n\n다음 문서를 확인하고 변경사항을 반영해주세요:\n- docs/DECISION_LOG.md (의사결정·정책·철학 — 중요한 결정 시 필수)\n- docs/FEATURE_SPEC.md (기능 명세서)\n- docs/ARCHITECTURE.md (설계 문서)\n- docs/USER_MANUAL.md (사용자 매뉴얼)\n- docs/NEXT_ACTIONS.md (작업 종료 시 다음 할 일 갱신)\n\n사용자가 명시적으로 '문서 업데이트 불필요'라고 요청한 경우에만 이 경고를 무시하세요."}
EOF
  exit 2
fi

exit 0
