#!/usr/bin/env bash
#
# Issue 생성 스크립트 — docs/development-guide.md 2장을 따른다.
#
# gh CLI 는 Issue Forms 를 거치지 않으므로 _issue_body.py 로 템플릿 yml 을 읽어
# 웹 UI 제출 결과와 같은 본문을 만든 뒤 이슈를 생성한다.
#
# 사용 예:
#   .github/scripts/issue.sh -k feature --show-fields
#   .github/scripts/issue.sh -k feature -t "generate_analysis_plan Tool 구현" \
#     -F area="D - Analytics" \
#     -F summary="자연어 질의를 분석 계획으로 변환한다" \
#     -F work="- [ ] 스키마 정의" \
#     -F done="정책 위반 SQL 에서 POLICY_REJECTED 반환"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BODY_BUILDER="$SCRIPT_DIR/_issue_body.py"

TEMPLATE=""
TITLE=""
LABEL=""
SHOW_FIELDS=0
DRY_RUN=0
FIELDS=()

die() { printf '\033[31m[x] %s\033[0m\n' "$1" >&2; exit 1; }
info() { printf '\033[36m[>] %s\033[0m\n' "$1"; }
ok() { printf '\033[32m[v] %s\033[0m\n' "$1"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$1" >&2; }

usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -k|--kind)     TEMPLATE="$2"; shift 2 ;;
    -t|--title)    TITLE="$2"; shift 2 ;;
    -l|--label)    LABEL="$2"; shift 2 ;;
    -F)            FIELDS+=("-F" "$2"); shift 2 ;;
    --show-fields) SHOW_FIELDS=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage ;;
    *)             die "알 수 없는 옵션: $1  (--help 참고)" ;;
  esac
done

[[ -n "$TEMPLATE" ]] || die "-k 로 템플릿을 지정하세요 (feature | bug | task)"

PYTHON="$(command -v python3 || true)"
[[ -n "$PYTHON" ]] || die "python3 를 찾을 수 없습니다."

if [[ $SHOW_FIELDS -eq 1 ]]; then
  "$PYTHON" "$BODY_BUILDER" --template "$TEMPLATE" --show-fields
  exit 0
fi

[[ -n "$TITLE" ]] || die "-t 로 제목을 지정하세요 (접두사는 자동으로 붙습니다)"

command -v gh >/dev/null 2>&1 || die "gh CLI 가 필요합니다: brew install gh"
gh auth status >/dev/null 2>&1 || die "gh 인증이 필요합니다: gh auth login"

info "템플릿 '$TEMPLATE' 으로 본문을 생성합니다"
if [[ ${#FIELDS[@]} -gt 0 ]]; then
  BODY="$("$PYTHON" "$BODY_BUILDER" --template "$TEMPLATE" "${FIELDS[@]}")"
else
  BODY="$("$PYTHON" "$BODY_BUILDER" --template "$TEMPLATE")"
fi

PREFIX="$("$PYTHON" "$BODY_BUILDER" --template "$TEMPLATE" --title-prefix)"
FULL_TITLE="${PREFIX}${TITLE}"
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

echo
echo "----------------------------------------"
echo "저장소 : $REPO"
echo "제목   : $FULL_TITLE"
[[ -n "$LABEL" ]] && echo "라벨   : $LABEL"
echo "----------------------------------------"
echo "$BODY"
echo "----------------------------------------"
echo

if [[ $DRY_RUN -eq 1 ]]; then
  ok "dry-run 이므로 생성하지 않았습니다."
  exit 0
fi

read -r -p "이 내용으로 Issue 를 생성할까요? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || die "취소했습니다."

CREATE_ARGS=(--title "$FULL_TITLE" --body "$BODY")

if [[ -n "$LABEL" ]]; then
  if gh label list -R "$REPO" --json name -q '.[].name' | grep -Fxq "$LABEL"; then
    CREATE_ARGS+=(--label "$LABEL")
  else
    warn "라벨 \"$LABEL\" 이 저장소에 없어 건너뜁니다."
    warn "  gh label create \"$LABEL\" -c 0E8A16 -R $REPO"
  fi
fi

URL="$(gh issue create "${CREATE_ARGS[@]}")"
ok "Issue 생성 완료: $URL"

SLUG="$(printf '%s' "$TITLE" \
  | tr '[:upper:]' '[:lower:]' \
  | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-//; s/-$//' \
  | cut -c1-40 | sed 's/-$//')"

echo
info "다음 단계 - 브랜치 생성"
cat <<EOF
  git switch main && git pull --ff-only origin main
  git switch -c feat/analytics-${SLUG}

  area: common | detection | knowledge | agent | analytics | integration
EOF
