# Final epoch Bootstrap 안내

> [!CAUTION]
> 현행 기준은 멘토님 제공 최종 `project.zip`의 `fdc_final_20260818` epoch다. 이 디렉터리의
> 구 `kosa_0813` 명령·수치·manifest를 현재 bootstrap, readiness 또는 복구 입력으로 사용하지
> 않는다. 이전 epoch 기록은 [`history/kosa_0813/`](history/kosa_0813/README.md)에서 이력으로만
> 조회하며 원위치에 복원하지 않는다.

## 현행 근거

1. [최종 패키지 검증 기준표](../../docs/reference/mentor-final-20260818/README.md)
2. [`final-zip-intake.json`](final-zip-intake.json) — 최종 ZIP·선택 artifact intake 증빙
3. [`dataset-epoch.json`](dataset-epoch.json) — 현행 epoch 식별자
4. [WBS v5](../../docs/planning/Task분해_WBS_v5_작업본.md)의 `V5-CM-1.*`~`V5-CM-3.*`

`source-manifest-v4.json`과 후속 marker·report는 해당 V5 Task의 생성기와 검증을 통과한 뒤에만
실행 근거가 된다. 파일이 존재한다는 사실만으로 생성 완료나 공용 적용 완료를 뜻하지 않는다.

## 안전 경계

- 최종 ZIP의 DDL·CSV·`master.cypher`를 공용 서비스에 직접 실행하지 않는다.
- 구 corrected pipeline, `kosa_0813` manifest·marker 및 이 README의 이전 명령을 재사용하지 않는다.
- target database·schema·role·epoch·source hash·fingerprint를 preflight하고, 변경 작업은
  backup/restore 검증·명시적 confirm·단일 transaction·재실행 no-op을 통과해야 한다.
- PostgreSQL은 profile별 fresh bootstrap, migration, runtime seed를 분리한다.
- Neo4j는 destructive 문장을 제거한 safe loader만 사용하며 검증 성공 뒤 marker를 마지막에 쓴다.
- 공용 PostgreSQL·Neo4j·n8n은 외부 canonical 서비스다. 팀 compose에는
  Backend·Frontend·Kafka·MES Mock만 포함하고 두 번째 DB·Neo4j·n8n을 만들지 않는다.
- 대응 `V5-*` 구현·리뷰가 끝나기 전에는 이 문서에서 임시 실행 명령을 만들어 사용하지 않는다.

## 최종 기대값 요약

| 영역 | 기대값 |
|---|---|
| base source | 9 tables |
| `action_history` | evaluation 12 / runtime 0 / E2E fresh 0 |
| Neo4j | 44 nodes / 85 relationships / duplicate business key 0 |
| RAG | canonical document 3종 / `BAAI/bge-m3` / vector 1024 |

세부 행 수·해시·profile 계약과 적용 순서는 최종 패키지 검증 기준표와 리뷰된 WBS v5 Task를
직접 확인한다. readiness는 실제 적재 marker·검증 artifact를 읽으며 문서의 수치만으로 성공을
판정하지 않는다.
