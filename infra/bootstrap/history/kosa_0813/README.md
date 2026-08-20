# kosa_0813 epoch 격리 이력

> 격리 일자: 2026-08-20 · 수행 Task: `V5-CM-1.2` (epoch 발급)
> 근거: 요구사항 v2.1 FR-I-04 · WBS v5 §9 · 팀 결정(2026-08-19: 이전 데이터 전부 폐기 가능)

`kosa_0813` epoch(2026-08-13 수령 `kosa_0813.zip`)의 등록·검증 artifact를 이 디렉터리로
격리했다. 현행 epoch는 `fdc_final_20260818`(`infra/bootstrap/dataset-epoch.json` v2)이다.

**복원 금지.** 이 디렉터리의 파일을 원위치로 되돌리면 구 파이프라인이 폐기된 epoch를
다시 소비하게 되어 "동시 참조 금지"(WBS v5 `V5-CM-1.2` 완료 기준)가 깨진다. 구 파이프라인은
`manifest_v3.load_dataset_epoch()`의 키 집합 검사에서 fail-fast하도록 의도적으로 차단된 상태다.

## 격리 18파일

- 등록 3: `dataset-epoch.json`(v1 구본) · `source-data-manifest.json` ·
  `corrected-data-manifest.json`
- `manifests/` 4: `evaluation.base_schema` · `runtime.base_schema` ·
  `evaluation.corrected_base` · `neo4j.graph`
- `markers/` 11: `base_schema.kosa_agent_e2e` · `corrected.v1` ·
  `corrected_base.{kosa_agent, kosa_agent_e2e}` · `evaluation_mock.kosa_text2sql` ·
  `neo4j_graph.neo4j` · `runtime_clean.{kosa_agent, kosa_agent_e2e}` ·
  `reference_extensions.{kosa_agent, kosa_agent_e2e, kosa_text2sql}`

## 잔류 3파일 — 왜 남았는가 (작업계획 §1.3)

부재가 오류이고 읽는 코드가 현행 앱인 것만 `infra/bootstrap/manifests/`에 남겼다.
잔류 판정 기준은 "테스트가 깨지는가"가 아니라 "부재가 오류인가"다.

| 파일 | 읽는 코드 | unit 회귀가 보호 | 해제 시점 |
|---|---|---|---|
| `manifests/runtime.runtime_clean.json` | `app/analytics/sql_validator.py:54` · `app/analytics/preflight.py:91-99`(`_load_manifest`) · `scripts/apply_agent_runtime.py:769·796·1003` | 예 (51 failed) | `V5-CM-2.4` 재적재 후 새 manifest로 교체 |
| `manifests/evaluation.evaluation_mock.json` | `app/analytics/preflight.py:91-99` | 예 (5 failed) | `V5-CM-2.4` |
| `manifests/runtime.corrected_base.json` | `scripts/apply_agent_runtime.py:976` | 예 (3 failed) | `V5-CM-3.2` Runtime migration 재작성 |

보호 수치는 구현 시점 실측(2026-08-20, 각 파일을 단독 격리한 뒤 전체 unit 실행)이다.
계획 §1.3의 +50/+4/+2와의 +1 차이는 `V5-CM-1.2`가 신설한 "잔류 3파일 고정" 테스트
(`test_dataset_epoch.py`)가 각 케이스에서 함께 실패하기 때문이다.

이 3개는 전부 `dataset_epoch: kosa_0813`을 담고 있어 3개 파일 폭의 동시 참조가 남는다.
해제는 위 표의 후속 Task가 수행하며, 관리 대상으로 작업계획 §6에 명시돼 있다.
