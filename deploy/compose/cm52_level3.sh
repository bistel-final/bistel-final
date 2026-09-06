# Sourced only by cm52_stage2.sh. No second controller/PID or free-form command.
HOST_PYTHON="${CM52_HOST_PYTHON:-$CM52_REPO_ROOT/.venv/bin/python}"
[[ -x "$HOST_PYTHON" ]] || { printf '%s\n' LEVEL3_HOST_PYTHON_UNAVAILABLE >&2; exit 1; }
ENTRY_REPORT_ROOT="${CM52_REPORT_ROOT:-$CM52_REPO_ROOT/infra/bootstrap/reports}"
L3_PHASE_MODE="$MODE"
[[ "$MODE" != resume ]] || L3_PHASE_MODE=publish
CANONICAL_PREPARED="$ENTRY_REPORT_ROOT/cm-5.2/$ATTEMPT/robustness/prepared-attempt.json"
[[ -z "$PREPARED_PATH" || "$PREPARED_PATH" == "$CANONICAL_PREPARED" ]] || {
  printf '%s\n' STAGE2_PREPARED_PATH_MISMATCH >&2; exit 1;
}
PREPARED_PATH="$CANONICAL_PREPARED"
if [[ -z "${CM52_STAGE2_LOCK_FD+x}" ]]; then
  lock_options=()
  [[ -z "$APPROVAL_PATH" ]] || lock_options+=(--approval-record "$APPROVAL_PATH")
  exec "$HOST_PYTHON" "$CM52_REPO_ROOT/backend/scripts/lock_stage2.py" \
    --report-root "$ENTRY_REPORT_ROOT" --repository "$CM52_REPO_ROOT" \
    --attempt-id "$ATTEMPT" --mode "$L3_PHASE_MODE" --prepared-attempt "$PREPARED_PATH" ${lock_options[@]+"${lock_options[@]}"}
fi
[[ "$CM52_STAGE2_LOCK_FD" =~ ^[0-9]+$ ]] || { printf '%s\n' LIFECYCLE_LOCK_INVALID >&2; exit 1; }
if [[ "$MODE" != prepare ]]; then
  [[ "${CM52_STAGE2_PREPARED_SHA:-}" =~ ^[0-9a-f]{64}$ ]] || { printf '%s\n' LIFECYCLE_LOCK_INVALID >&2; exit 1; }
fi
level3_phase() {
  # Called in command substitution; exec preserves direct Bash owner ancestry.
  exec "$HOST_PYTHON" "$CM52_REPO_ROOT/backend/scripts/stage2_level3_phase.py" "$@" \
    --report-root "$ENTRY_REPORT_ROOT" --repository "$CM52_REPO_ROOT" \
    --env-file "$CM52_ENV_FILE" --attempt-id "$ATTEMPT" --mode "$L3_PHASE_MODE" \
    --lifecycle-lock-fd "$CM52_STAGE2_LOCK_FD" --owner-pid "$$" \
    --prepared-sha256 "${CM52_STAGE2_PREPARED_SHA:-}"
}
L3_CLAIMED=0
L3_DONE=0
L3_CLEANUP_STARTED=0
L3_PUBLISHED=0
PHASE_RECORD=""
level3_cleanup() {
  local original_rc=$1 result terminal cleanup_result=FAILED restore_result=FAILED primary="" code
  ((L3_CLEANUP_STARTED == 0)) || return
  L3_CLEANUP_STARTED=1
  trap - EXIT
  trap '' INT TERM
  set +e
  ((L3_DONE == 0)) || exit "$original_rc"
  if [[ "$MODE" == prepare ]]; then
    # No intent => read-only admission failure. Issued PREPARED => explicit abort
    # needed if the successful response was lost; the leaf refuses implicit cleanup.
    if result=$(level3_phase prepare-cleanup); then
      cleanup_result=$(jq -er '.result | select(. == "OK" or . == "NOT_ATTEMPTED")' <<<"$result") || cleanup_result=FAILED
    else printf '%s\n' "$result" >&2; fi
    if result=$(level3_phase prepare-restore); then
      [[ "$(jq -r '.result' <<<"$result")" != OK ]] || restore_result=OK
    else printf '%s\n' "$result" >&2; fi
    terminal=$(level3_phase prepare-finish --cleanup-result "$cleanup_result" --restore-result "$restore_result")
    printf '%s\n' "$terminal"
    exit 1
  fi
  # Denied/ambiguous admission must not remove another invocation's resources.
  ((L3_CLAIMED)) || exit "$original_rc"
  if result=$(level3_phase cleanup --phase-record "$PHASE_RECORD"); then
    cleanup_result=$(jq -er '.result | select(. == "OK" or . == "NOT_ATTEMPTED")' <<<"$result") || cleanup_result=FAILED
  else printf '%s\n' "$result" >&2; fi
  restore_options=()
  ((L3_PUBLISHED == 0)) || restore_options+=(--published)
  if result=$(level3_phase restore --phase-record "$PHASE_RECORD" ${restore_options[@]+"${restore_options[@]}"}); then
    [[ "$(jq -r '.result' <<<"$result")" != OK ]] || restore_result=OK
  else printf '%s\n' "$result" >&2; fi
  case "$(jq -r '.phase' <<<"$PHASE_RECORD")" in
    RESUME_WORKLOAD) primary=STAGE2_STEP_FAILED; ((original_rc != 130 && original_rc != 143)) || primary=WORKLOAD_ABORTED ;;
    PUBLISH) ((L3_PUBLISHED)) || primary=ARTIFACT_PUBLISH_FAILED ;;
  esac
  finish_options=()
  [[ -z "$primary" ]] || finish_options+=(--primary "$primary")
  if ! terminal=$(level3_phase finish --phase-record "$PHASE_RECORD" \
      --cleanup-result "$cleanup_result" --restore-result "$restore_result" ${finish_options[@]+"${finish_options[@]}"}); then
    printf '%s\n' "$terminal" LIFECYCLE_TERMINAL_WRITE_FAILED >&2
    exit 2
  fi
  printf '%s\n' "$terminal"
  code=$(jq -er '.failure_code // "NONE"' <<<"$terminal") || exit 2
  case "$code" in NONE) exit "$original_rc" ;; RESTORE_FAILED) exit 2 ;; *) exit 1 ;; esac
}
trap 'level3_cleanup "$?"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$MODE" == prepare ]]; then
  result=$(level3_phase prepare) || { printf '%s\n' "$result" >&2; exit 1; }
  L3_DONE=1
  printf '%s\n' "$result"
  exit 0
fi
begin_options=()
[[ -z "$APPROVAL_PATH" ]] || begin_options+=(--approval-record "$APPROVAL_PATH")
PHASE_RECORD=$(level3_phase begin ${begin_options[@]+"${begin_options[@]}"}) || { printf '%s\n' "$PHASE_RECORD" >&2; exit 1; }
L3_CLAIMED=1
if [[ "$MODE" == resume_workload ]]; then
  [[ "$(jq -r '.workload_authorized' <<<"$PHASE_RECORD")" == true ]] || exit 1
  result=$(level3_phase execute --phase-record "$PHASE_RECORD") || { printf '%s\n' "$result" >&2; exit 1; }
  result=$(level3_phase collect --phase-record "$PHASE_RECORD") || { printf '%s\n' "$result" >&2; exit 1; }
  result=$(level3_phase held --phase-record "$PHASE_RECORD") || { printf '%s\n' "$result" >&2; exit 1; }
  L3_DONE=1
  printf '%s\n' "$result"
else
  [[ "$(jq -r '.publish_authorized' <<<"$PHASE_RECORD")" == true ]] || exit 1
  result=$(level3_phase publish --phase-record "$PHASE_RECORD") || { printf '%s\n' "$result" >&2; exit 1; }
  L3_PUBLISHED=1
fi
exit 0
