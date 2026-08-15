#!/usr/bin/env bash
#
# PR 생성 스크립트 — docs/development-guide.md 3~8장을 따른다.
#
# 흐름: 브랜치 검증 -> 스테이징 확인 -> 로컬 검증 -> 커밋 -> push -> PR 생성 -> 자체 검사
#
# 사용 예:
#   .github/scripts/pr.sh \
#     -t "feat: SQL 검증기 구현" \
#     -f "backend/app/analytics backend/tests/unit" \
#     -w "sqlglot AST 기반 5종 검사를 추가했다." \
#     -y "읽기 전용 정책을 코드로 강제하기 위해 필요하다." \
#     -c 12 -l "D - Analytics"
#
# 옵션:
#   -t  PR 제목            <type>: <한 줄 요약>   (필수)
#   -f  스테이징 경로       공백 구분. 생략 시 이미 staged 된 것만 사용
#   -w  ## 변경 내용 본문   (필수)
#   -y  ## 변경 이유 본문   (필수)
#   -m  커밋 본문           생략 시 -y 값을 사용
#   -c  Closes 이슈 번호
#   -l  라벨 (담당 영역)
#   -r  리뷰어 (쉼표 구분)  자기 PR 은 자기가 승인할 수 없다
#   --skip-verify   로컬 검증 생략 (권장하지 않음)
#   --draft         Draft PR
#   --dry-run       커밋/push/PR 없이 검증과 미리보기만

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

TITLE=""; PATHS=""; WHAT=""; WHY=""; COMMIT_BODY=""
CLOSES=""; LABEL=""; REVIEWERS=""
SKIP_VERIFY=0; DRAFT=0; DRY_RUN=0

die() { printf '\033[31m[x] %s\033[0m\n' "$1" >&2; exit 1; }
info() { printf '\033[36m[>] %s\033[0m\n' "$1"; }
ok() { printf '\033[32m[v] %s\033[0m\n' "$1"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$1" >&2; }
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

usage() { sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--title)    TITLE="$2"; shift 2 ;;
    -f|--files)    PATHS="$2"; shift 2 ;;
    -w|--what)     WHAT="$2"; shift 2 ;;
    -y|--why)      WHY="$2"; shift 2 ;;
    -m|--message)  COMMIT_BODY="$2"; shift 2 ;;
    -c|--closes)   CLOSES="${2#\#}"; shift 2 ;;
    -l|--label)    LABEL="$2"; shift 2 ;;
    -r|--reviewer) REVIEWERS="$2"; shift 2 ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --draft)       DRAFT=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage ;;
    *)             die "알 수 없는 옵션: $1  (--help 참고)" ;;
  esac
done

[[ -n "$TITLE" ]] || die "-t 로 PR 제목을 지정하세요."
[[ -n "$WHAT" ]]  || die "-w 로 '## 변경 내용' 을 지정하세요. (PR Policy 필수 항목)"
[[ -n "$WHY" ]]   || die "-y 로 '## 변경 이유' 를 지정하세요. (PR Policy 필수 항목)"
[[ -n "$COMMIT_BODY" ]] || COMMIT_BODY="$WHY"

command -v gh >/dev/null 2>&1 || die "gh CLI 가 필요합니다: brew install gh"
gh auth status >/dev/null 2>&1 || die "gh 인증이 필요합니다: gh auth login"

step "1/7  브랜치 검증"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

[[ "$BRANCH" != "main" ]] || die "main 에서 직접 작업할 수 없습니다. 작업 브랜치를 만드세요.
  git switch -c feat/analytics-<설명>"

BRANCH_RE='^(feat|fix|refactor|test|docs|chore)/(common|detection|knowledge|agent|analytics|integration)-[a-z0-9]+(-[a-z0-9]+)*$'
[[ "$BRANCH" =~ $BRANCH_RE ]] || die "브랜치명이 규칙에 맞지 않습니다: $BRANCH
  형식: <type>/<area>-<설명>
  type: feat | fix | refactor | test | docs | chore
  area: common | detection | knowledge | agent | analytics | integration
  예시: feat/analytics-text2sql"
ok "브랜치: $BRANCH"

TITLE_RE='^(feat|fix|refactor|test|docs|chore): .+$'
[[ "$TITLE" =~ $TITLE_RE ]] || die "PR 제목이 규칙에 맞지 않습니다: $TITLE
  형식: <type>: <한 줄 요약>
  담당 영역은 제목이 아니라 라벨로 표시합니다."
ok "제목: $TITLE"

step "2/7  스테이징"
if [[ -n "$PATHS" ]]; then
  # shellcheck disable=SC2086
  git add -- $PATHS
  ok "지정 경로를 스테이징했습니다: $PATHS"
else
  info "경로 미지정 - 이미 staged 된 변경만 사용합니다."
fi

git diff --cached --quiet && die "스테이징된 변경이 없습니다. -f 로 경로를 지정하세요.
  (git add -A 는 의도치 않은 파일을 담을 수 있어 사용하지 않습니다)"

git diff --cached --check || die "공백/줄바꿈 오류가 있습니다. 위 내용을 수정하세요."
echo
git diff --cached --stat
echo

if git diff --cached --name-only | grep -Eq '(^|/)\.env$|\.joblib$|(^|/)model-cache/'; then
  die "커밋 금지 파일이 포함되어 있습니다 (.env / *.joblib / model-cache/). 스테이징에서 제외하세요."
fi

step "3/7  로컬 검증"
CHANGED="$(git diff --cached --name-only)"
HAS_BACKEND=0; HAS_FRONTEND=0
grep -q '^backend/' <<<"$CHANGED" && HAS_BACKEND=1
grep -q '^frontend/' <<<"$CHANGED" && HAS_FRONTEND=1

BACKEND_MARK=" "; FRONTEND_MARK=" "

if [[ $SKIP_VERIFY -eq 1 ]]; then
  warn "--skip-verify 로 검증을 건너뜁니다. PR 본문에 미검증으로 남습니다."
else
  if [[ $HAS_BACKEND -eq 1 ]]; then
    info "backend 변경 감지 - ruff format / ruff check / pytest"
    ( cd backend && ruff format . && ruff check . && pytest ) \
      || die "backend 검증 실패. 수정 후 다시 실행하세요."
    ok "backend 검증 통과 (pytest 는 e2e 마커 제외)"
    BACKEND_MARK="x"
  fi
  if [[ $HAS_FRONTEND -eq 1 ]]; then
    info "frontend 변경 감지 - npm run lint / npm run build"
    ( cd frontend && npm run lint && npm run build ) \
      || die "frontend 검증 실패. 수정 후 다시 실행하세요."
    ok "frontend 검증 통과"
    FRONTEND_MARK="x"
  fi
  if [[ $HAS_BACKEND -eq 0 && $HAS_FRONTEND -eq 0 ]]; then
    info "backend/frontend 변경 없음 - 검증 생략"
  fi
fi

step "4/7  PR 본문"
BODY="## 변경 내용

${WHAT}

## 변경 이유

${WHY}
"

if [[ -n "$CLOSES" ]]; then
  BODY+="
## 관련 이슈

Closes #${CLOSES}
"
fi

BODY+="
## 확인 사항

- [${BACKEND_MARK}] backend: \`ruff format .\` / \`ruff check .\` / \`pytest\`
- [${FRONTEND_MARK}] frontend: \`npm run lint\` / \`npm run build\`
- [ ] 실제 FastAPI · React 연동을 확인했다
- [ ] AI 문서나 원본 사양을 변경했다면 \`CLAUDE.md\`·\`AGENTS.md\` 일치와 \`specifications/\`·\`ai-context/\` 버전 동기화를 확인했다
- [ ] E2E 를 실행했다면 공용 서버가 아닌 격리 DB(\`kosa_agent_e2e\`)에서 수행했다

<!-- E2E 는 파괴적이므로 이 스크립트가 자동 실행하지 않습니다. -->
"

for heading in "## 변경 내용" "## 변경 이유"; do
  printf '%s\n' "$BODY" | grep -Fq "$heading" || die "본문 자체 검사 실패: $heading 누락"
done
ok "필수 항목 자체 검사 통과 (## 변경 내용 / ## 변경 이유)"

echo
echo "----------------------------------------"
echo "$BODY"
echo "----------------------------------------"

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  ok "dry-run 이므로 커밋·push·PR 을 수행하지 않았습니다."
  exit 0
fi

echo
read -r -p "커밋하고 PR 을 생성할까요? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || die "취소했습니다. (스테이징은 유지됩니다)"

step "5/7  커밋"
git commit -m "$TITLE" -m "$COMMIT_BODY"
ok "커밋 완료"

step "6/7  push"
if git rev-parse --abbrev-ref "@{upstream}" >/dev/null 2>&1; then
  git push
else
  git push -u origin "$BRANCH"
fi
ok "push 완료"

step "7/7  PR 생성"
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

if EXISTING="$(gh pr view --json url -q .url 2>/dev/null)"; then
  ok "이미 PR 이 있어 push 만 반영했습니다: $EXISTING"
  exit 0
fi

CREATE_ARGS=(--base main --head "$BRANCH" --title "$TITLE" --body "$BODY")
[[ $DRAFT -eq 1 ]] && CREATE_ARGS+=(--draft)
[[ -n "$REVIEWERS" ]] && CREATE_ARGS+=(--reviewer "$REVIEWERS")

if [[ -n "$LABEL" ]]; then
  if gh label list -R "$REPO" --json name -q '.[].name' | grep -Fxq "$LABEL"; then
    CREATE_ARGS+=(--label "$LABEL")
  else
    warn "라벨 \"$LABEL\" 이 저장소에 없어 건너뜁니다."
    warn "  gh label create \"$LABEL\" -c 0E8A16 -R $REPO"
  fi
fi

URL="$(gh pr create "${CREATE_ARGS[@]}")"
ok "PR 생성 완료: $URL"

echo
info "다음 단계"
cat <<EOF
  gh pr checks --watch     PR Policy 결과 확인
  팀원 1명 이상 승인 필요 (자기 PR 은 자기가 승인할 수 없음)
  병합은 Squash and merge 고정
EOF
