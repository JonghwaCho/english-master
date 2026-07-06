#!/usr/bin/env bash
#
# sync-check.sh — 회사↔집 두 환경의 Git 동기화 상태를 한눈에 확인
#
# 사용법:
#   ./sync-check.sh          상태만 확인 (내 파일은 절대 건드리지 않음)
#   ./sync-check.sh --pull   뒤처져 있으면 자동으로 git pull 까지 수행
#
set -euo pipefail

# ── 색상 ─────────────────────────────────────────────
if [ -t 1 ]; then
  R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[34m'; BOLD=$'\e[1m'; N=$'\e[0m'
else
  R=""; G=""; Y=""; B=""; BOLD=""; N=""
fi

# ── 저장소 위치로 이동 ───────────────────────────────
cd "$(dirname "$0")"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "${R}✗ 여기는 Git 저장소가 아닙니다.${N}"
  exit 1
fi

# ── 커밋 작성자 자동 통일 ────────────────────────────
# 회사/집 어느 PC에서든 이 스크립트를 실행하면 이 저장소의 커밋 작성자를
# 아래 값으로 자동 설정한다. (한 번 설정하는 것을 잊어도 자동으로 맞춰짐)
WANT_NAME="조종화"
WANT_EMAIL="joe@nablecomm.com"
if [ "$(git config user.email 2>/dev/null)" != "$WANT_EMAIL" ] || \
   [ "$(git config user.name 2>/dev/null)" != "$WANT_NAME" ]; then
  git config user.name  "$WANT_NAME"
  git config user.email "$WANT_EMAIL"
  echo "${G}✓ 커밋 작성자를 이 저장소용으로 설정했습니다: ${WANT_NAME} <${WANT_EMAIL}>${N}"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "${BOLD}${B}▶ 저장소:${N} $(basename "$(git rev-parse --show-toplevel)")   ${BOLD}${B}브랜치:${N} ${BRANCH}"
echo "──────────────────────────────────────────────"

# ── 원격 최신 정보만 가져옴 (작업 파일은 안 건드림) ──
echo "  원격 정보 확인 중… (git fetch)"
git fetch --quiet

# 이 브랜치에 연결된 업스트림(origin/…)이 있는지 확인
if ! UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
  echo "${Y}⚠ 이 브랜치에 원격(upstream)이 설정돼 있지 않습니다.${N}"
  echo "  최초 1회:  git push -u origin ${BRANCH}"
  exit 0
fi

# ── ahead / behind 계산 ──────────────────────────────
read -r AHEAD BEHIND < <(git rev-list --left-right --count "HEAD...@{u}" | awk '{print $1, $2}')

# ── 로컬 미커밋 변경 여부 ────────────────────────────
DIRTY_TRACKED="$(git status --porcelain --untracked-files=no)"
UNTRACKED="$(git ls-files --others --exclude-standard)"

echo ""
echo "${BOLD}상태 요약${N}"
echo "  로컬이 앞선 커밋(push 필요) : ${AHEAD}"
echo "  원격이 앞선 커밋(pull 필요) : ${BEHIND}"
echo "──────────────────────────────────────────────"

# ── 판정 ─────────────────────────────────────────────
STATUS_OK=1

if [ -n "$DIRTY_TRACKED" ]; then
  STATUS_OK=0
  echo "${Y}● 저장 안 된(커밋 안 된) 변경이 있습니다:${N}"
  git status --short --untracked-files=no | sed 's/^/    /'
  echo "    → 마무리하려면: ${BOLD}git add -A && git commit -m \"메모\"${N}"
  echo ""
fi

if [ "$AHEAD" -gt 0 ] && [ "$BEHIND" -gt 0 ]; then
  STATUS_OK=0
  echo "${R}⚠ 양쪽에서 각각 커밋했습니다 (diverged).${N}"
  echo "    회사와 집에서 서로 다른 작업을 push/commit 한 상태입니다."
  echo "    → 합치기:  ${BOLD}git pull --rebase${N}  (충돌 시 도움이 필요하면 알려주세요)"
elif [ "$BEHIND" -gt 0 ]; then
  STATUS_OK=0
  echo "${Y}↓ 원격(GitHub)이 ${BEHIND}커밋 더 최신입니다. (다른 PC에서 push함)${N}"
  if [ "${1:-}" = "--pull" ]; then
    if [ -n "$DIRTY_TRACKED" ]; then
      echo "${R}  미커밋 변경이 있어 자동 pull을 건너뜁니다. 먼저 커밋하세요.${N}"
    else
      echo "  → git pull 실행 중…"
      git pull --ff-only && echo "${G}  ✓ 최신으로 업데이트 완료${N}"
    fi
  else
    echo "    → 내려받기: ${BOLD}git pull${N}   (또는 ./sync-check.sh --pull)"
  fi
elif [ "$AHEAD" -gt 0 ]; then
  STATUS_OK=0
  echo "${Y}↑ 로컬이 ${AHEAD}커밋 앞섭니다. 아직 GitHub에 안 올렸습니다.${N}"
  echo "    → 올리기: ${BOLD}git push${N}"
fi

if [ -n "$UNTRACKED" ]; then
  echo ""
  echo "${B}ℹ 참고: Git이 추적하지 않는 파일이 있습니다 (버전 비교와 무관):${N}"
  echo "$UNTRACKED" | sed 's/^/    /'
fi

echo "──────────────────────────────────────────────"
if [ "$STATUS_OK" -eq 1 ]; then
  echo "${G}${BOLD}✓ 완전 일치 — 회사와 집 버전이 동일합니다. 바로 작업하세요.${N}"
else
  echo "${Y}${BOLD}→ 위 안내대로 정리하면 두 환경이 일치합니다.${N}"
fi
