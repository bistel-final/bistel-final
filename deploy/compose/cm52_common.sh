#!/usr/bin/env bash
# CM-5.2 2단 실행에서 수동 step 0~2와 자동 step 3~10이 공유하는 함수만 둔다.

CM52_COMPOSE_DIR="${CM52_COMPOSE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CM52_REPO_ROOT="${CM52_REPO_ROOT:-$(cd "$CM52_COMPOSE_DIR/../.." && pwd)}"
CM52_ENV_FILE="${CM52_ENV_FILE:-$CM52_COMPOSE_DIR/.env.team}"

E2E_FILES=(
  -p bistel-team-e2e
  -f "$CM52_COMPOSE_DIR/docker-compose.team.yml"
  -f "$CM52_COMPOSE_DIR/docker-compose.e2e-backend.yml"
  --env-file "$CM52_ENV_FILE"
)
TEAM_FILES=(
  -p bistel-team
  -f "$CM52_COMPOSE_DIR/docker-compose.team.yml"
  --env-file "$CM52_ENV_FILE"
)

e2e() { docker compose "${E2E_FILES[@]}" "$@"; }
team() { docker compose "${TEAM_FILES[@]}" "$@"; }
runner() {
  e2e --profile e2e-runner run --rm --no-deps \
    --user "$(id -u):$(id -g)" e2e-runner "$@"
}

assert_owned_0600() {
  python3 "$CM52_COMPOSE_DIR/assert_owned_0600.py" "$@"
}

apply_artifact_paths() {
  python3 "$CM52_COMPOSE_DIR/set_artifact_paths.py" \
    "$CM52_ENV_FILE" "$1" "$2"
}

get_artifact_path() {
  python3 "$CM52_COMPOSE_DIR/set_artifact_paths.py" \
    --get "$CM52_ENV_FILE" "$1"
}

preflight_team_env() {
  python3 "$CM52_COMPOSE_DIR/preflight_team_env.py" --env-file "$CM52_ENV_FILE"
}

IDENTITY_SQL="from app.common.db import get_app_engine; from sqlalchemy import text; e=get_app_engine(); c=e.connect(); print(tuple(c.execute(text('SELECT current_database(), current_user')).one())); c.close()"
# shellcheck disable=SC2034 # sourced cm52_stage2.sh가 runner identity에 사용한다.
READONLY_IDENTITY="from app.analytics.db_pool import LogicalDb,PoolRole,pool_factory; from sqlalchemy import text; e=pool_factory.get_engine(LogicalDb.RUNTIME,PoolRole.QUERY); c=e.connect(); print(tuple(c.execute(text('SELECT current_database(), current_user')).one())); c.close()"
# shellcheck disable=SC2034 # sourced cm52_stage2.sh가 evaluation identity에 사용한다.
EVALUATION_IDENTITY="from app.common.db import get_evaluation_engine; from sqlalchemy import text; e=get_evaluation_engine(); c=e.connect(); print(tuple(c.execute(text('SELECT current_database(), current_user')).one())); c.close()"

production_verify() {
  curl -fsS http://127.0.0.1:8080/api/health >/dev/null \
    && team exec -T backend python -c "$IDENTITY_SQL" \
      | grep -q "('kosa_agent', 'kosa_app')" \
    && {
      if [[ $# -eq 0 ]]; then
        return 0
      fi
      team exec -T backend python \
        scripts/preflight_agent_evaluation_artifacts.py "$@" \
        && curl -fsS http://127.0.0.1:8080/api/agent/evaluations \
          | jq -e '
              .fault_5class != null
              and .golden_flow != null
              and .fault_5class_empty_reason == null
              and .golden_flow_empty_reason == null
            ' >/dev/null
    }
}

cm52_sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}
