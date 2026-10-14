#!/usr/bin/env bash
#
# save.sh — 작업을 마칠 때 한 번에: 저장(커밋) + 원격 반영(pull) + 올리기(push)
#
# 회사↔집 어디서든 작업 끝에 ./save.sh 만 실행하면, 변경을 커밋하고
# 원격의 새 커밋을 합친 뒤 GitHub에 올려 두 환경을 일치시킨다.
# (시작할 때는 ./sync-check.sh — 짝으로 쓴다.)
#
# 사용법:
#   ./save.sh                 기본 메시지로 저장+동기화
#   ./save.sh "메모 내용"      커밋 메시지를 직접 지정
#
set -euo pipefail

if [ -t 1 ]; then
  R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[34m'; BOLD=$'\e[1m'; N=$'\e[0m'
else
  R=""; G=""; Y=""; B=""; BOLD=""; N=""
fi

cd "$(dirname "$0")"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "${R}✗ 여기는 Git 저장소가 아닙니다.${N}"; exit 1
fi

# ── 커밋 작성자 자동 통일(회사/집 동일하게) ──
WANT_NAME="조종화"; WANT_EMAIL="joe@nablecomm.com"
if [ "$(git config user.email 2>/dev/null)" != "$WANT_EMAIL" ] || \
   [ "$(git config user.name 2>/dev/null)" != "$WANT_NAME" ]; then
  git config user.name "$WANT_NAME"; git config user.email "$WANT_EMAIL"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "${BOLD}${B}▶ 저장+동기화 — 브랜치 ${BRANCH}${N}"
echo "──────────────────────────────────────────────"

# ── 1) 변경이 있으면 커밋 ──
if [ -n "$(git status --porcelain)" ]; then
  MSG="${1:-작업 저장 $(date '+%Y-%m-%d %H:%M') ($(hostname -s))}"
  git add -A
  git commit -q -m "$MSG"
  echo "${G}✓ 커밋 완료:${N} $MSG"
else
  echo "  저장할 변경 없음(working tree 깨끗)."
fi

# ── 2) 원격의 새 커밋을 먼저 합친다(rebase) ──
echo "  원격 확인 중… (git fetch)"
git fetch --quiet
UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [ -z "$UPSTREAM" ]; then
  echo "${Y}⚠ 이 브랜치에 원격이 없습니다. 최초 1회:${N} git push -u origin ${BRANCH}"
  exit 0
fi

BEHIND="$(git rev-list --count HEAD..@{u})"
if [ "$BEHIND" -gt 0 ]; then
  echo "  원격이 ${BEHIND}커밋 앞섭니다 → 합치는 중(git pull --rebase)…"
  if ! git pull --rebase --quiet; then
    echo "${R}✗ 자동 병합 실패(충돌).${N} 충돌을 해결한 뒤 다시 ./save.sh 하세요."
    echo "   (중단하려면: git rebase --abort)"
    exit 1
  fi
  echo "${G}✓ 원격 변경을 합쳤습니다.${N}"
fi

# ── 3) 올리기 ──
AHEAD="$(git rev-list --count @{u}..HEAD)"
if [ "$AHEAD" -gt 0 ]; then
  git push --quiet
  echo "${G}✓ GitHub에 올렸습니다(${AHEAD}커밋).${N}"
else
  echo "  올릴 커밋 없음."
fi

echo "──────────────────────────────────────────────"
echo "${G}${BOLD}✓ 완료 — 이제 다른 PC에서 ./sync-check.sh 하면 그대로 이어서 작업할 수 있습니다.${N}"
