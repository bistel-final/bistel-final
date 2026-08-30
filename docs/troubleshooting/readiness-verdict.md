# Readiness 판정·복구 Runbook

`GET /health`는 API process 생존만 확인하며 외부 의존성을 조회하지 않는다. 배포·시연 전에는
반드시 `GET /health/ready`도 확인한다. 응답은 `fdc_final_20260818` epoch와 6개 check를 항상
포함하고, 하나라도 실패하면 HTTP 503·`NOT_READY`다. 원문 예외·DSN·credential은 응답에
노출하지 않는다.

## 판정표

| check | 확인 대상 | 실패 시 1차 확인 |
|---|---|---|
| `postgresql_runtime` | `kosa_agent`·`kosa_app`, final epoch/schema/role | Backend DB 설정, packaged marker, `kosa_app` 권한 |
| `reference_migration` | R03·통합 Alarm View final successor | `v5_001_reference_extensions_final`, R03 constraint와 View 정의 |
| `neo4j` | `neo4j`, 44 nodes·85 relationships·fingerprint | final graph marker와 live graph drift |
| `rag` | 3문서·chunk·1024차원 vector·검색 smoke 3건 | model-cache mount, RAG marker, document/chunk 상태 |
| `n8n` | `${N8N_BASE_URL}/healthz/readiness` exact 200 | origin 설정, n8n 2.32.7 process와 network |
| `kafka` | 두 topic, WF4 committed/earliest/latest offset | broker/SASL secret, topic, WF4 consumer group과 lag |

## reason_code별 조치

| reason_code | 의미 | 조치 |
|---|---|---|
| `NOT_CONFIGURED` | 필수 env·secret file·local artifact 없음 | `.env.team`과 read-only mount를 보완하고 Backend만 재시작 |
| `CONTRACT_MISMATCH` | marker·schema·identity·응답 형식 불일치 | 해당 owner verifier로 drift를 확인하고 승인된 migration/bootstrap 절차만 수행 |
| `DEPENDENCY_UNAVAILABLE` | 연결·인증·worker·조회 실패 | 의존 서비스 상태와 Backend network/credential을 확인한 뒤 재조회 |
| `TIMEOUT` | provider native timeout 또는 10초 aggregate deadline 초과 | 서비스 부하·network를 확인하고 자동 timeout 상향 없이 원인을 해소 |
| `RAG_MODEL_NOT_READY` | single-flight model warmup 진행 중 | 60초 내 재조회. 계속되면 model-cache와 Backend 로그를 확인 |
| `KAFKA_LAG_STALE` | lag가 0보다 큰 상태로 5분 지속 | 아래 WF4 순서대로 복구하고 lag 0 확인 전 영구 활성 금지 |

## Kafka WF4 복구

자동 offset reset은 하지 않는다. 먼저 WF4 Backend 소비자를 복구하고 동일 record가
멱등하게 재처리되도록 한 뒤 lag가 0인지 확인한다. 오진행을 확인한 경우에만 WF4를
비활성화하고 `deploy/compose/kafka/manage_wf4_offsets.sh`의 dry-run 결과를 검토한 후 exact
offset reset 절차를 사용한다. `--to-earliest` 일괄 reset은 금지한다.

Kafka Admin 조회는 60초 주기 sampler만 수행하고 `/health/ready` 요청 경로에서는 저장된
snapshot만 읽는다. snapshot이 2주기보다 오래되거나 worker가 종료되면 이전 PASS를 재사용하지
않는다. 재시작 직후 lag 지속 판정의 최대 관측 지연은 5분과 다음 sampling 1회다.

동시에 들어온 readiness 요청은 하나의 6-check 실행을 공유하며, 완료 결과는 3초 동안만
재사용한다. 따라서 probe burst가 executor를 고갈시키거나 Kafka Admin 조회를 증폭시키지 않는다.
PostgreSQL check는 연결 5초와 statement 3초로 10초 aggregate deadline 안에서 종료한다.

## 적용일 확인

팀 compose 기동 후 proxy 기준 `/api/health`가 HTTP 200·`UP`인지 확인하고,
`/api/health/ready`를 한 번 호출해 HTTP 200·`READY`와 6개 `PASS`를 기록한다. 503이면 위 표의
실패 check만 복구한다. readiness 실패 자체를 이유로 process를 종료하거나 공용 DB·Neo4j·n8n을
재생성하지 않는다.
