<!-- level3-lifecycle-generated:start -->

### 코드·fixture 파생 lifecycle 계약

생성: `backend/scripts/render_level3_lifecycle_contract.py`. 구조는 JSON Schema, nullable·시각·SHA 결속은 Python validator로 함께 검사한다.
기본 생성·CI 검사 범위는 docs/deliverables/agent의 두 산출물이다. 로컬 Plan·Task는 --sync-planning을 명시한 경우에만 동기화·검사한다.

SMTP 설정 digest는 SmtpConfigSnapshot의 workflow ID→version, SMTP host/port/from, recipient allowlist, WF2 callback endpoint를 키 정렬 canonical JSON으로 SHA-256 계산한다. recipient는 v2로 정규화하며 credential 등 미정의 필드는 거부한다. validate_runtime은 실제 관측 digest 한 개를 받아 prepared의 approved_config_digest_allowlist에 포함되는지 검사한다. 실측 수집·Stage2 연결은 별도 후속 구현이다.

| source state | 허용 mode |
|---|---|
| `PREPARED` | `RESUME_WORKLOAD` · `ABORT` |
| `UNRESOLVED` | `RECOVER` |
| `HELD` | `PUBLISH` |
| `TERMINAL` | 없음 |

| phase | issued_by | primary_failure_code |
|---|---|---|
| `RESUME_WORKLOAD` | `RESUME_WORKLOAD` | `STAGE2_STEP_FAILED` |
| `RESUME_WORKLOAD` | `RESUME_WORKLOAD` | `WORKLOAD_ABORTED` |
| `ABORT` | `ABORT` | `null` |
| `RESUME_WORKLOAD` | `RECOVER` | `STALE_CLAIM_RECOVERED` |
| `ABORT` | `RECOVER` | `STALE_CLAIM_RECOVERED` |
| `PUBLISH` | `PUBLISH` | `ARTIFACT_PUBLISH_FAILED` |
| `PUBLISH` | `PUBLISH` | `PUBLISH_PRECONDITION_FAILED` |
| `PUBLISH` | `RECOVER` | `PUBLISH_PHASE_RECOVERED` |

| cleanup_result | restore_result | failure_code |
|---|---|---|
| `OK` | `OK` | `primary_failure_code(null 포함)` |
| `FAILED` | `OK` | `CLEANUP_FAILED` |
| `OK` | `FAILED` | `RESTORE_FAILED` |
| `FAILED` | `FAILED` | `RESTORE_FAILED` |

`NOT_ATTEMPTED`는 FAILED가 아니며 primary를 유지한다. HELD를 제외하면 `basis`에 `CLEANUP_E2E_ABSENT`, `RESTORE_PREV_EMPTY` 또는 `RESTORE_ALREADY_TARGET` 근거가 필요하다.

<!-- level3-lifecycle-generated:end -->
