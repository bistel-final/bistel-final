# 최종 비기능·증적 Gate

> Task: `V5-CM-5.3` · 정본: `final-nonfunctional-gate.json`
> Stage: **SKELETON** · Verdict: **INCOMPLETE**
> JSON SHA-256: `b9bb280c90d6cb2fd19eee6b61c7d79eaa69578f179f846cb5aa5a220de260f0`

## 규칙

| Rule | Required | Status | Evidence | Residual |
|---|---:|---|---:|---:|
| `NFR-02` | true | **PASS** | 1 | 0 |
| `NFR-03` | true | **RESIDUAL** | 3 | 3 |
| `NFR-12` | true | **PASS** | 2 | 0 |
| `NFR-13` | true | **RESIDUAL** | 7 | 1 |
| `NFR-14` | true | **EVIDENCE_MISSING** | 4 | 1 |
| `NFR-15` | true | **PASS** | 6 | 0 |
| `NFR-16` | true | **PASS** | 11 | 0 |

### NFR-02

- `file` `backend/scripts/final_nonfunctional_gate.py` — tracked 전수 secret scan과 exact binary baseline (`3b1539391c490ec015a325fa3f261de69c8860cb9777c4cb1c6491e7a7ea3d7b`)

### NFR-03

- `file` `docs/troubleshooting/tool-timeout-verdict.md` — CM-4.8 Tool hard/soft timeout 판정 정본 (`9adca4ab2f79c6e8f2b6a775ad8ff31358fadcbd7ed985318803648178c3caa8`)
- `test` `tests/unit/test_agent_tool_budget.py::test_budget_policy_uses_fixed_precedence_and_limits` — HITL 전후 누적 Tool budget 상한 (`test reference`)
- `test` `tests/unit/test_tool_timeouts.py::test_reserved_tool_call_sentinel_has_no_automatic_recovery_writer` — 미완료 sentinel 자동 회수 금지 (`test reference`)
- 잔여: embedding·anomaly model은 process hard cancellation이 없다.
- 잔여: /agent/ask에는 caller soft deadline이 없다.
- 잔여: 예약 sentinel은 실행 identity가 없어 자동 회수하지 않는다.

### NFR-12

- `test` `tests/test_health.py::test_cors_preflight_allows_configured_origin` — 명시 Origin 허용 (`test reference`)
- `test` `tests/test_health.py::test_cors_preflight_rejects_unconfigured_origin` — 비허용 Origin 거부 (`test reference`)

### NFR-13

- `file` `infra/bootstrap/source-manifest-v4.json` — 6테이블·10 timestamp 컬럼 logical type 정본 (`888409de2d935eeccf47a80030c65f6cf49c541621a283907849e7332297f843`)
- `file` `infra/bootstrap/001_base_schema.sql` — timestamp without time zone 물리 DDL 정본 (`ce8bc9b38fe4f4b915eb2bd76f8f3617977b282f8ef7112025a7930198df0fa2`)
- `test` `tests/unit/test_rehearsal_profile_verifier.py::test_naive_timestamp_projects_to_kst` — source wall time의 Asia/Seoul +09:00 투영 (`test reference`)
- `test` `tests/unit/test_rehearsal_profile_verifier.py::test_row_count_and_typed_hash_match` — source row count와 typed content hash 보존 (`test reference`)
- `test` `tests/unit/test_detection_public_api.py::test_alarm_projection_is_canonical_offset_aware_and_stably_queried` — public API date-time +09:00 serialization (`test reference`)
- `test` `tests/unit/test_agent_public_api.py::test_public_run_json_has_exact_allowlist_and_canonical_aliases` — Agent public API date-time +09:00 serialization (`test reference`)
- `file` `backend/scripts/run_analytics_eval.py` — 평가 artifact executed_at 생성 지점(UTC 기록 — 잔여 근거) (`8f837140e11a3df19b4c9db74913fb9ecd1e5ec7304f60399e67920828a6a265`)
- 잔여: V5-D-2.6 GET /analytics/evaluations의 EvaluationResponse.executed_at은 평가 artifact가 UTC로 기록한 값을 그대로 직렬화해 +09:00이 아니다. D 소유 경계이므로 이 Task에서 수정하지 않고 owner 확인 뒤 최종화 전 해소한다.

### NFR-14

- `file` `infra/bootstrap/markers/postgres_profile.kosa_agent_e2e.json` — CM-2.6 GH-108 tracked marker (`47e2ff1282f584951d1d3c04a18a3e2784e73176c2237a83643d69c01fa69fef`)
- `file` `infra/bootstrap/markers/postgres_profile.kosa_agent.json` — CM-2.6 GH-108 tracked marker (`87dcbbeacd96bc4f509a92058c0ab4e4192246b74994c00ba7a14ff55db8455a`)
- `file` `infra/bootstrap/markers/postgres_profile.kosa_text2sql.json` — CM-2.6 GH-108 tracked marker (`4b718b42cd0b75883974d6d67e1d5ad938cf169b079f3aa987443af011e297b4`)
- `file` `infra/bootstrap/markers/neo4j_graph.neo4j.json` — CM-2.7 GH-128 ADOPTED_EXISTING tracked marker (`484f42916cdd4153069c7721fe055585f8688da92ca4780b237ddabb7ee95f23`)
- 잔여: 저장소 밖 CM-2.6·2.7 backup/restore receipt는 FINAL 단계에서 read-only 대조한다.

### NFR-15

- `file` `backend/requirements.txt` — Python exact pins (`55992e5ebd8dc1fb4343a5fb2c395c8793afd732768a03654f244c43929f6136`)
- `file` `frontend/package-lock.json` — npm lockfile v3 (`355d576360563236f0f166e14b7ee8676b16f47e8d887a50bed11e729cdabaee`)
- `file` `backend/Dockerfile` — Python base image pin (`2105e1630e857287000fce61ec5b0536668226dfb1b24e190c7e3b2b62054ece`)
- `file` `frontend/Dockerfile` — Node/nginx base image pins (`0660b7a3a005a2b3cbeb052761283ee44a881d7a74f1c943b887fe3962f4637a`)
- `file` `deploy/compose/docker-compose.team.yml` — team app/Kafka image pin contract (`00258582c42608373f6ce49cdf9bdf7a61a860667e10652b595b28bfef20aced`)
- `file` `deploy/compose/docker-compose.e2e-backend.yml` — E2E backend production override inventory (`a5c21df70f6ec9533284af060b0e301daca0f9430557b99af74292bd70218993`)

### NFR-16

- `test` `tests/unit/test_final_gate_fault_isolation.py::test_postgres_fault_keeps_process_alive_and_recovers` — 두 PostgreSQL readiness check 격리·복구 (`test reference`)
- `test` `tests/unit/test_final_gate_fault_isolation.py::test_neo4j_fault_keeps_process_alive_and_recovers` — Neo4j readiness 격리·복구 (`test reference`)
- `test` `tests/unit/test_final_gate_fault_isolation.py::test_llm_fault_returns_sanitized_503_then_recovers` — LLM sanitized 503·동일 process 복구 (`test reference`)
- `test` `tests/unit/test_final_gate_fault_isolation.py::test_n8n_fault_keeps_process_alive_and_recovers` — n8n readiness 격리·복구 (`test reference`)
- `test` `tests/unit/test_final_gate_fault_isolation.py::test_kafka_fault_keeps_process_alive_and_recovers` — Kafka readiness 격리·복구 (`test reference`)
- `test` `tests/unit/test_tool_timeout_postgres_container.py::test_registration_query_is_canceled_and_pool_state_is_clean` — PostgreSQL server timeout·pool 후속 성공 보조 증적 (`test reference`)
- `test` `tests/unit/test_tool_timeout_neo4j_container.py::test_query_timeout_terminates_marker_transaction_and_driver_recovers` — Neo4j transaction timeout·driver 후속 성공 보조 증적 (`test reference`)
- `test` `tests/unit/test_common_llm.py::test_preflight_fails_closed_when_configured_model_is_absent` — LLM preflight fail-closed 보조 증적 (`test reference`)
- `test` `tests/unit/test_readiness.py::test_n8n_readiness_fails_closed_before_network_for_invalid_origin` — n8n invalid origin fail-closed 보조 증적 (`test reference`)
- `test` `tests/unit/test_readiness.py::test_kafka_lag_tracker_rejects_late_observation_and_tracks_stale_lag` — Kafka stale lag fail-closed 보조 증적 (`test reference`)
- `test` `tests/unit/test_mes_kafka_container.py::test_wrong_sasl_credential_cannot_publish` — Kafka 잘못된 credential 거부 보조 증적 (`test reference`)

## 외부 증적 index

- `.fdc_final_20260818.GH-108.closure.json` · target `kosa_agent_e2e,kosa_agent,kosa_text2sql` · result **EVIDENCE_MISSING** — 저장소 밖 PostgreSQL 3-target closure bundle; stage 1에서는 탐색하지 않음
- `neo4j_graph.neo4j.<timestamp>.manifest.json` · target `neo4j` · result **EVIDENCE_MISSING** — 저장소 밖 Neo4j backup/restore bundle; stage 1에서는 탐색하지 않음

> SKELETON은 공용 전환을 재수행하지 않은 1단 산출물이다. 외부 증적과 CM-5.2 2단 실행보고를 대조하기 전에는 완료·PASS로 해석하지 않는다.
