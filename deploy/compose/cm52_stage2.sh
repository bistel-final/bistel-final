#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=cm52_common.sh
source "$SCRIPT_DIR/cm52_common.sh"

usage() {
  printf '%s\n' \
    'usage: cm52_stage2.sh [--plan] [--hold-after 5d | --resume-from 6] --attempt-id <id>' >&2
  exit 2
}

print_plan() {
  printf '%s\n' \
    '3 team down → artifact env blank E2E up → identity/readiness fail-fast' \
    '4 UI Text2SQL 3건 ID digest → 7화면·36 operation 확인' \
    '5 pending 12건 → postcondition 12/12/12/0/0/5/4/3/0 → diagnostic targets' \
    '   (--hold-after 5d: 여기서 E2E를 올린 채 HELD_FOR_GOLDEN_FLOW 기록 후 종료 · 운영자가 live E2E에서 golden-flow phase 3~7 evidence 수집)' \
    '   (--resume-from 6: hold 기록·E2E identity/readiness 재확인 후 6~10 계속)' \
    '6 golden-flow 7 phase evidence 검증 → golden-flow.json' \
    '7 fault 5-class 평가 → fault-5class.json' \
    '8 E2E artifact preflight → /api/agent/evaluations Success' \
    '9 public DB observer 3축 → secret/raw-question scan' \
    '10 E2E down → PASS publish 또는 이전 production 상태 복원'
}

PLAN=0
MODE=full
ATTEMPT=""
while (($#)); do
  case "$1" in
    --plan) PLAN=1; shift ;;
    --attempt-id) (($# >= 2)) || usage; ATTEMPT=$2; shift 2 ;;
    --hold-after)
      (($# >= 2)) || usage
      [[ "$2" == 5d && "$MODE" == full ]] || usage
      MODE=hold; shift 2 ;;
    --resume-from)
      (($# >= 2)) || usage
      [[ "$2" == 6 && "$MODE" == full ]] || usage
      MODE=resume; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$ATTEMPT" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] || usage
if ((PLAN)); then
  print_plan
  exit 0
fi

# team env 파일의 키가 셸에 export돼 있으면 compose 보간에서 --env-file 값을 덮어쓴다(공용 PC 실측:
# 빈 AGENT_*_PATH export → production ENV_MISMATCH · 옛 TEAM_IMAGE_TAG export → 옛 태그 빌드).
for shell_override in SOURCE_REVISION TEAM_IMAGE_TAG AGENT_FAULT_EVAL_ARTIFACT_PATH \
  AGENT_GOLDEN_FLOW_SUMMARY_PATH; do
  if [[ -n "${!shell_override+x}" ]]; then
    printf '%s\n' "SHELL_ENV_OVERRIDE $shell_override" >&2
    exit 1
  fi
done

REV="${CM52_REVISION:-$(git -C "$CM52_REPO_ROOT" rev-parse HEAD)}"
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { printf '%s\n' REVISION_MISMATCH >&2; exit 1; }
[[ "${ATTEMPT##*-}" == "${REV:0:12}" ]] || {
  printf '%s\n' ATTEMPT_ID_MISMATCH >&2
  exit 1
}

REPORT_ROOT="${CM52_REPORT_ROOT:-$CM52_REPO_ROOT/infra/bootstrap/reports}"
A="${CM52_ATTEMPT_DIR:-$REPORT_ROOT/cm-5.2/$ATTEMPT}"
CA="/reports/cm-5.2/$ATTEMPT"
LOG="$A/stage2-log.jsonl"
[[ -d "$A" && ! -L "$A" ]] || { printf '%s\n' ATTEMPT_DIR_INVALID >&2; exit 1; }
assert_owned_0600 "$A/observer-baseline.json" >/dev/null
jq -e '
  .artifact_type == "cm52_public_database_observer"
  and .format_version == 1
' "$A/observer-baseline.json" >/dev/null

PREV_FAULT="$(get_artifact_path AGENT_FAULT_EVAL_ARTIFACT_PATH)"
PREV_GOLDEN="$(get_artifact_path AGENT_GOLDEN_FLOW_SUMMARY_PATH)"
PREV_STATE=""
PREV_ATTEMPT=""
PREV_FAULT_SHA=""
PREV_GOLDEN_SHA=""
PREV_REV=""
RUNNING_REV=""
ACTIVE_CONTAINER_REVISION=""

classify_prev_state() {
  if [[ -z "$PREV_FAULT" && -z "$PREV_GOLDEN" ]]; then
    PREV_STATE=empty
    return 0
  fi
  if [[ "$PREV_FAULT" =~ ^/reports/cm-5\.2/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12})/fault-5class\.json$ ]] \
    && [[ "$PREV_GOLDEN" == "/reports/cm-5.2/${BASH_REMATCH[1]}/golden-flow.json" ]]; then
    PREV_STATE=bound
    PREV_ATTEMPT=${BASH_REMATCH[1]}
    return 0
  fi
  return 1
}

classify_prev_state || { printf '%s\n' PREV_STATE_INVALID >&2; exit 1; }

host_report_path() {
  local container_path=$1
  printf '%s%s\n' "$REPORT_ROOT" "${container_path#/reports}"
}

test_gate() {
  local point=$1
  [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]] || return 0
  [[ ",${CM52_STAGE2_FAIL_AT:-}," != *",$point,"* ]]
}

if [[ "$PREV_STATE" == bound ]]; then
  PREV_FAULT_HOST="$(host_report_path "$PREV_FAULT")"
  PREV_GOLDEN_HOST="$(host_report_path "$PREV_GOLDEN")"
  if [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]]; then
    PREV_FAULT_SHA=${CM52_TEST_PREV_FAULT_SHA:-$(printf old-fault | shasum -a 256 | awk '{print $1}')}
    PREV_GOLDEN_SHA=${CM52_TEST_PREV_GOLDEN_SHA:-$(printf old-golden | shasum -a 256 | awk '{print $1}')}
    PREV_REV=${CM52_TEST_PREV_REV:-$REV}
  else
    assert_owned_0600 "$PREV_FAULT_HOST" "$PREV_GOLDEN_HOST" >/dev/null
    PREV_FAULT_SHA=$(cm52_sha256 "$PREV_FAULT_HOST")
    PREV_GOLDEN_SHA=$(cm52_sha256 "$PREV_GOLDEN_HOST")
    PREV_REV=$(jq -er '.code_revision' "$PREV_FAULT_HOST")
  fi
fi

verify_prev_state() {
  local expected_container_revision=${1:-}
  [[ "$expected_container_revision" =~ ^[0-9a-f]{40}$ ]] || return 1
  if ! test_gate restore_verify; then
    return 1
  fi
  if [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]]; then
    # trap handler 안에서 인자 없는 return은 직전 명령이 아니라 trap 진입 전 상태를
    # 돌려준다(bash 매뉴얼 return) — 비교 결과를 명시적으로 반환한다.
    local restored=1
    if [[ "$(get_artifact_path AGENT_FAULT_EVAL_ARTIFACT_PATH)" == "$PREV_FAULT" \
      && "$(get_artifact_path AGENT_GOLDEN_FLOW_SUMMARY_PATH)" == "$PREV_GOLDEN" \
      && "$ACTIVE_CONTAINER_REVISION" == "$expected_container_revision" ]]; then
      restored=0
    fi
    return "$restored"
  fi
  case "$PREV_STATE" in
    empty)
      cm52_wait_http http://127.0.0.1:8080/api/agent/evaluations \
        && curl -fsS http://127.0.0.1:8080/api/agent/evaluations \
        | jq -e '
            .fault_5class == null
            and .golden_flow == null
            and .fault_5class_empty_reason == "NOT_CONFIGURED"
            and .golden_flow_empty_reason == "NOT_CONFIGURED"
          ' >/dev/null
      ;;
    bound)
      production_verify \
        --fault "$PREV_FAULT" \
        --golden "$PREV_GOLDEN" \
        --expect-fault-sha "$PREV_FAULT_SHA" \
        --expect-golden-sha "$PREV_GOLDEN_SHA" \
        --expect-revision "$PREV_REV" \
        --expect-container-revision "$expected_container_revision" \
        --attempt-id "$PREV_ATTEMPT"
      ;;
  esac
}

restore_prev() {
  test_gate restore_apply \
    && apply_artifact_paths "$PREV_FAULT" "$PREV_GOLDEN" \
    && test_gate restore_preflight \
    && { [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]] || preflight_team_env; } \
    && test_gate restore_up \
    && {
      if [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]]; then
        ACTIVE_CONTAINER_REVISION=$REV
      else
        team up -d --force-recreate
      fi
    } \
    && { [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]] || production_verify; } \
    && verify_prev_state "$REV"
}

FAULT_SHA=""
GOLDEN_SHA=""
publish_new() {
  test_gate publish_apply \
    && apply_artifact_paths "$CA/fault-5class.json" "$CA/golden-flow.json" \
    && test_gate publish_preflight \
    && { [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]] || preflight_team_env; } \
    && test_gate publish_up \
    && { [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]] || team up -d; } \
    && test_gate publish_verify \
    && {
      if [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]]; then
        return 0
      fi
      production_verify \
        --fault "$CA/fault-5class.json" \
        --golden "$CA/golden-flow.json" \
        --expect-fault-sha "$FAULT_SHA" \
        --expect-golden-sha "$GOLDEN_SHA" \
        --expect-revision "$REV" \
        --attempt-id "$ATTEMPT"
    }
}

read_running_revision() {
  if [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]]; then
    printf '%s\n' "${CM52_TEST_RUNNING_REV:-${PREV_REV:-$REV}}"
    return 0
  fi
  team exec -T backend printenv BISTEL_SOURCE_REVISION
}

HOLD_RECORD=""
read_hold_record() {
  # --hold-after 5d 가 남긴 마지막 hold 기록. production은 step 3에서 내려간 상태라
  # production_verify·running revision 조회를 대신해 hold 시점 값을 재사용한다.
  [[ -f "$LOG" && ! -L "$LOG" ]] || return 1
  jq -e . "$LOG" >/dev/null || return 1
  HOLD_RECORD=$(jq -cs '[.[] | select(.step == "hold")] | last // empty' "$LOG")
  [[ -n "$HOLD_RECORD" ]] || return 1
  jq -e \
    --arg attempt "$ATTEMPT" \
    --arg fault "$PREV_FAULT" \
    --arg golden "$PREV_GOLDEN" \
    '.outcome == "HELD_FOR_GOLDEN_FLOW"
     and .attempt == $attempt
     and .last_ok_step == "5d"
     and (.running_rev | test("^[0-9a-f]{40}$"))
     and .prev_fault == $fault
     and .prev_golden == $golden' <<<"$HOLD_RECORD" >/dev/null
}

if [[ "$MODE" == resume ]]; then
  if ! read_hold_record; then
    printf '%s\n' HOLD_RECORD_REQUIRED >&2
    exit 1
  fi
  RUNNING_REV=$(jq -r '.running_rev' <<<"$HOLD_RECORD")
  ACTIVE_CONTAINER_REVISION=$RUNNING_REV
  [[ ! -s "$LOG" ]] || jq -e . "$LOG" >/dev/null
else
  if [[ "${CM52_STAGE2_TEST_MODE:-0}" != 1 ]]; then
    production_verify
  fi
  if ! RUNNING_REV=$(read_running_revision) \
    || [[ ! "$RUNNING_REV" =~ ^[0-9a-f]{40}$ ]]; then
    printf '%s\n' PRODUCTION_REVISION_UNREADABLE >&2
    exit 1
  fi
  ACTIVE_CONTAINER_REVISION=$RUNNING_REV
  verify_prev_state "$RUNNING_REV"

  set -o noclobber
  : >"$LOG"
  set +o noclobber
  chmod 0600 "$LOG"
fi

append_log() {
  local step=$1 status=$2 detail=${3:-}
  local record
  record=$(jq -cn --arg step "$step" --arg status "$status" --arg detail "$detail" \
    '{step:$step,status:$status,detail:$detail}') || return 1
  printf '%s\n' "$record" >>"$LOG" || return 1
  sync "$LOG" 2>/dev/null || sync
}

log_ok() {
  local phase=${1:-normal}
  test_gate "log_${phase}" || return 1
  [[ ! -s "$LOG" ]] || jq -e . "$LOG" >/dev/null
}

LAST_OK_STEP="2c"
[[ "$MODE" != resume ]] || LAST_OK_STEP="5d"
STAGE2_PASS=0
CLEANUP_RUNNING=0
FORCE_PUBLISH_FAILED=0
HOLD_REACHED=0

write_hold_record() {
  local record
  record=$(jq -cn \
    --arg attempt "$ATTEMPT" \
    --arg running_rev "$RUNNING_REV" \
    --arg fault "$PREV_FAULT" \
    --arg golden "$PREV_GOLDEN" \
    --arg last_ok "$LAST_OK_STEP" \
    '{step:"hold",outcome:"HELD_FOR_GOLDEN_FLOW",attempt:$attempt,running_rev:$running_rev,prev_fault:$fault,prev_golden:$golden,last_ok_step:$last_ok}') || return 1
  test_gate log_hold || return 1
  printf '%s\n' "$record" >>"$LOG" || return 1
  { sync "$LOG" 2>/dev/null || sync; } || return 1
  log_ok after
}

cleanup() {
  local rc outcome="" record="" record_ok=1
  local down_failed=0
  rc=$1
  ((CLEANUP_RUNNING == 0)) || return
  CLEANUP_RUNNING=1
  trap - EXIT
  set +e

  # --hold-after 5d 정상 도달: E2E는 올린 채, production은 내린 채 둔다. 기록에 실패하면
  # hold를 포기하고 아래 일반 복구 경로(E2E down → 이전 production 복원)로 내려간다.
  if ((HOLD_REACHED)) && [[ "$rc" == 0 ]]; then
    if write_hold_record; then
      exit 0
    fi
    printf '%s\n' HOLD_RECORD_WRITE_FAILED >&2
    rc=1
  fi

  if ! log_ok precheck || ! jq -cn '{probe:true}' >/dev/null; then
    printf '%s\n' LOG_INVALID_BEFORE_PUBLISH >&2
    STAGE2_PASS=0
    FORCE_PUBLISH_FAILED=1
    rc=1
  fi

  if ! test_gate e2e_down; then
    down_failed=1
  elif [[ "${CM52_STAGE2_TEST_MODE:-0}" != 1 ]] && ! e2e down; then
    down_failed=1
  fi
  if ((down_failed)); then
    if restore_prev; then outcome=PUBLISH_FAILED; else outcome=RESTORE_FAILED; fi
  elif [[ "$STAGE2_PASS" == 1 ]]; then
    if publish_new; then
      outcome=PUBLISHED
    elif restore_prev; then
      outcome=PUBLISH_FAILED
    else
      outcome=RESTORE_FAILED
    fi
  else
    if restore_prev; then
      if ((FORCE_PUBLISH_FAILED)); then outcome=PUBLISH_FAILED; else outcome=RESTORED; fi
    else
      outcome=RESTORE_FAILED
    fi
  fi

  if ! record=$(jq -cn \
    --arg outcome "$outcome" \
    --argjson rc "$rc" \
    --arg last_ok "$LAST_OK_STEP" \
    '{step:"cleanup",outcome:$outcome,original_rc:$rc,last_ok_step:$last_ok}'); then
    record_ok=0
  fi
  if ((record_ok == 0)) \
    || ! test_gate log_append \
    || ! printf '%s\n' "$record" >>"$LOG" \
    || ! { sync "$LOG" 2>/dev/null || sync; } \
    || ! log_ok after; then
    printf '%s\n' \
      "LOG_WRITE_FAILED outcome=$outcome last_ok_step=$LAST_OK_STEP · 수동 복구: set_artifact_paths.py <prev…> → preflight → team up -d --force-recreate → production_verify" \
      >&2
    if [[ "$outcome" == PUBLISHED ]]; then
      if restore_prev; then outcome=PUBLISH_FAILED; else outcome=RESTORE_FAILED; fi
    elif [[ "$outcome" == RESTORED ]]; then
      outcome=PUBLISH_FAILED
    fi
  fi

  case "$outcome" in
    PUBLISHED) exit "$rc" ;;
    RESTORED) exit "$rc" ;;
    PUBLISH_FAILED) exit 1 ;;
    RESTORE_FAILED)
      printf '%s\n' \
        "RESTORE_FAILED last_ok_step=$LAST_OK_STEP · 상태 미보장 · 수동 복구 필요" >&2
      exit 2
      ;;
    *) printf '%s\n' CLEANUP_STATE_INVALID >&2; exit 2 ;;
  esac
}
trap 'cleanup "$?"' EXIT

if [[ "${CM52_STAGE2_TEST_MODE:-0}" == 1 ]]; then
  if [[ "$MODE" == hold ]]; then
    LAST_OK_STEP=5d
    HOLD_REACHED=1
    exit "${CM52_TEST_ORIGINAL_RC:-0}"
  fi
  LAST_OK_STEP=${CM52_TEST_LAST_OK_STEP:-9}
  STAGE2_PASS=${CM52_TEST_STAGE2_PASS:-1}
  exit "${CM52_TEST_ORIGINAL_RC:-0}"
fi

step3_boot_e2e() {
  team ps --format json >"$A/team-before.json"
  team down
  install -d -m 0700 "$CM52_COMPOSE_DIR/trail"
  AGENT_FAULT_EVAL_ARTIFACT_PATH='' \
    AGENT_GOLDEN_FLOW_SUMMARY_PATH='' \
    e2e up -d --build
  e2e ps --format json >"$A/e2e-services.json"
  e2e_identity_readiness
}

e2e_identity_readiness() {
  e2e exec -T backend python -c "$IDENTITY_SQL" \
    | grep -q "('kosa_agent_e2e', 'kosa_app')"
  runner python -c "$READONLY_IDENTITY" \
    | grep -q "('kosa_agent_e2e', 'kosa_readonly')"
  runner python -c "$EVALUATION_IDENTITY" \
    | grep -q "('kosa_text2sql', 'kosa_evaluation')"
  runner python -c \
    "import os; assert os.environ['BISTEL_SOURCE_REVISION'] == os.environ['GITHUB_SHA'] == '$REV'"
  [[ "$(runner id -u)" == "$(id -u)" ]]
  curl -fsS http://127.0.0.1:8080/api/health >/dev/null
  curl -fsS http://127.0.0.1:8080/api/health/ready \
    | jq -e '
        .status == "READY"
        and ([.checks[] | .status] | length == 6 and all(. == "PASS"))
      ' >/dev/null
}

if [[ "$MODE" == resume ]]; then
  # hold 동안 E2E가 살아 있고 같은 revision인지, 전반 산출물이 그대로인지 재확인한다.
  e2e_identity_readiness
  assert_owned_0600 \
    "$A/analytics-digests.json" "$A/pending-run.jsonl" "$A/diagnostic-targets.json" >/dev/null
  append_log 5d-resume PASS e2e-still-live
else
step3_boot_e2e
LAST_OK_STEP=3b
append_log 3b PASS identity-readiness

IDS=${CM52_ANALYTICS_QUERY_IDS:-}
[[ "$IDS" =~ ^[0-9]+,[0-9]+,[0-9]+$ ]] || {
  printf '%s\n' ANALYTICS_QUERY_IDS_REQUIRED >&2
  exit 1
}
runner python scripts/e2e_analytics_questions.py \
  --ids "$IDS" --output "$CA/analytics-digests.json"
assert_owned_0600 "$A/analytics-digests.json" >/dev/null
curl -fsS http://127.0.0.1:8080/api/analytics/history >/dev/null
curl -fsS http://127.0.0.1:8080/api/agent/evaluations \
  | jq -e '
      .fault_5class == null
      and .golden_flow == null
      and .fault_5class_empty_reason == "NOT_CONFIGURED"
      and .golden_flow_empty_reason == "NOT_CONFIGURED"
    ' >/dev/null
LAST_OK_STEP=4
append_log 4 PASS real-api-before-artifact

runner python scripts/run_pending_incidents.py \
  --database kosa_agent_e2e >"$A/pending-plan.json"
jq -e '.selected | length == 12' "$A/pending-plan.json" >/dev/null
jq -e '.rejected == [] and .incomplete == []' "$A/pending-plan.json" >/dev/null
runner python scripts/run_pending_incidents.py \
  --database kosa_agent_e2e --once >"$A/pending-run.jsonl"
tail -n 1 "$A/pending-run.jsonl" \
  | jq -e '.attempted == 12 and .succeeded == 12 and .failed == 0 and .skipped == 0 and .new_runs_observed == 12' >/dev/null

# kosa_readonly(analytics QUERY pool)는 C-0.2 allowlist상 agent_run·approval_request를 읽지 못한다
# (공용 PC 실측 InsufficientPrivilege). postcondition은 read-only count라 kosa_app engine으로 읽는다.
POSTCONDITION_SQL="from app.common.db import get_app_engine; from sqlalchemy import text; e=get_app_engine(); c=e.connect(); q=text(\"SELECT (SELECT count(*) FROM agent_run), (SELECT count(*) FROM agent_run WHERE prompt_version='agent-hypothesis-v2-ko1'), (SELECT count(*) FROM agent_run_action), (SELECT count(*) FROM agent_run WHERE retry_of_run_id IS NOT NULL), (SELECT count(*) FROM agent_run WHERE status IN ('RUNNING','FAILED')), (SELECT count(*) FROM action_history WHERE action_code='MONITORING'), (SELECT count(*) FROM action_history WHERE action_code='WARNING'), (SELECT count(*) FROM action_history WHERE action_code='EQP_HOLD'), (SELECT count(*) FROM (SELECT agent_run_id, count(*) c FROM agent_run_action GROUP BY agent_run_id HAVING count(*)<>1) x)\"); print(tuple(c.execute(q).one())); c.close()"
runner python -c "$POSTCONDITION_SQL" | grep -q '(12, 12, 12, 0, 0, 5, 4, 3, 0)'
runner python scripts/emit_diagnostic_targets.py \
  --agent-database kosa_agent_e2e \
  --attempt-id "$ATTEMPT" \
  --output "$CA/diagnostic-targets.json"
assert_owned_0600 "$A/diagnostic-targets.json" >/dev/null
LAST_OK_STEP=5d
append_log 5d PASS agent-12-diagnostic-22
fi

if [[ "$MODE" == hold ]]; then
  HOLD_REACHED=1
  exit 0
fi

EVIDENCE_FILE=${CM52_GOLDEN_EVIDENCE_FILE:-}
[[ "$EVIDENCE_FILE" == "$CA/evidence/"* ]] || {
  printf '%s\n' GOLDEN_EVIDENCE_REQUIRED >&2
  exit 1
}
runner python scripts/verify_golden_flow.py \
  --database kosa_agent_e2e \
  --evidence-file "$EVIDENCE_FILE" \
  --summary-output "$CA/golden-flow.json"
assert_owned_0600 "$A/golden-flow.json" >/dev/null
GOLDEN_SHA=$(cm52_sha256 "$A/golden-flow.json")
LAST_OK_STEP=6
append_log 6 PASS golden-flow

runner python scripts/evaluate_fault_5class.py \
  --agent-database kosa_agent_e2e \
  --golden-evidence "$EVIDENCE_FILE" \
  --output "$CA/fault-5class.json"
jq -e \
  --arg revision "$REV" \
  '.hard_gate_passed and .prompt_version == "agent-hypothesis-v2-ko1" and .code_revision == $revision' \
  "$A/fault-5class.json" >/dev/null
assert_owned_0600 "$A/fault-5class.json" >/dev/null
FAULT_SHA=$(cm52_sha256 "$A/fault-5class.json")
LAST_OK_STEP=7
append_log 7 PASS fault-5class

# C-4.6 trail은 같은 RUN_ID의 기존 파일을 거부하므로(TRAIL_CONFIG_INVALID) 재생성 backend에는
# 파생 RUN_ID를 준다. 이 backend는 artifact preflight·API 확인 전용이라 callback을 받지 않는다.
AGENT_FAULT_EVAL_ARTIFACT_PATH="$CA/fault-5class.json" \
  AGENT_GOLDEN_FLOW_SUMMARY_PATH="$CA/golden-flow.json" \
  DELIVERY_CALLBACK_TRAIL_RUN_ID="${DELIVERY_CALLBACK_TRAIL_RUN_ID:-c46_e2e}_s8_$(date -u +%H%M%S)" \
  e2e up -d --force-recreate --wait backend
e2e exec -T backend python scripts/preflight_agent_evaluation_artifacts.py \
  --fault "$CA/fault-5class.json" \
  --golden "$CA/golden-flow.json" \
  --expect-fault-sha "$FAULT_SHA" \
  --expect-golden-sha "$GOLDEN_SHA" \
  --expect-revision "$REV" \
  --attempt-id "$ATTEMPT"
cm52_wait_http http://127.0.0.1:8080/api/agent/evaluations
curl -fsS http://127.0.0.1:8080/api/agent/evaluations \
  | jq -e '.fault_5class != null and .golden_flow != null' >/dev/null
LAST_OK_STEP=8
append_log 8 PASS artifact-e2e

python3 "$CM52_REPO_ROOT/backend/scripts/observe_public_databases.py" verify \
  --baseline "$A/observer-baseline.json" \
  --expected-digests "$A/analytics-digests.json" \
  --output "$A/observer-final.json"
assert_owned_0600 "$A/observer-final.json" >/dev/null
python3 "$CM52_COMPOSE_DIR/scan_cm52_artifacts.py" \
  --root "$A" --env-file "$CM52_ENV_FILE" >/dev/null
LAST_OK_STEP=9
append_log 9 PASS observer-and-scan
STAGE2_PASS=1
