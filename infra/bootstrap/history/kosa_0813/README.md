# kosa_0813 epoch 격리 이력

> [!CAUTION]
> 이 디렉터리는 폐기한 epoch의 조사 이력이다. 현재 bootstrap·검증·readiness·복구 입력으로
> 사용하거나 파일을 원위치에 복원하지 않는다. 현행 기준은
> [`infra/bootstrap/README.md`](../../README.md)와
> [최종 패키지 검증 기준표](../../../../docs/reference/mentor-final-20260818/README.md)다.

> 격리 일자: 2026-08-20 · 수행 Task: `V5-CM-1.2` (epoch 발급)
> 근거: 요구사항 v2.1 FR-I-04 · WBS v5 §9 · 팀 결정(2026-08-19: 이전 데이터 전부 폐기 가능)

`kosa_0813` epoch(2026-08-13 수령 `kosa_0813.zip`)의 등록·검증 artifact를 이 디렉터리로
격리했다. 현행 epoch는 `fdc_final_20260818`(`infra/bootstrap/dataset-epoch.json` v2)이다.

**복원 금지.** 이 디렉터리의 파일을 원위치로 되돌리면 구 파이프라인이 폐기된 epoch를
다시 소비하게 되어 "동시 참조 금지"(WBS v5 `V5-CM-1.2` 완료 기준)가 깨진다.

> [!NOTE]
> `V5-CM-1.2` 시점에는 구 loader가 epoch v2를 키 집합 검사에서 fail-fast하는 것이
> 차단 수단이었다. **`V5-CM-1.8`이 그 방향을 뒤집었다** — 이제
> `manifest_v3.load_dataset_epoch()`가 v2를 정본으로 읽고 구 epoch을 담은 payload를
> 거부한다. 폐기 계보 manifest는 `validate_historical_bootstrap_manifest()` 하나로만
> 검증되며, 그 함수는 active registry를 읽지 않는다.

## 격리 21파일

- 등록 3: `dataset-epoch.json`(v1 구본) · `source-data-manifest.json` ·
  `corrected-data-manifest.json`
- `manifests/` 7: `evaluation.base_schema` · `runtime.base_schema` ·
  `evaluation.corrected_base` · `neo4j.graph` ·
  `runtime.corrected_base`(`V5-CM-1.6`에서 이동) ·
  **`runtime.runtime_clean`·`evaluation.evaluation_mock`**(`V5-CM-1.8` 발급 시 이동)
- `markers/` 11: `base_schema.kosa_agent_e2e` · `corrected.v1` ·
  `corrected_base.{kosa_agent, kosa_agent_e2e}` · `evaluation_mock.kosa_text2sql` ·
  `neo4j_graph.neo4j` · `runtime_clean.{kosa_agent, kosa_agent_e2e}` ·
  `reference_extensions.{kosa_agent, kosa_agent_e2e, kosa_text2sql}`

## 잔류 0파일 — `V5-CM-1.8`이 모두 해제했다

`V5-CM-1.2` 당시에는 "부재가 오류인" 구 등록본 3개를 `infra/bootstrap/manifests/`에
남겨 뒀다. 셋 다 해제됐다.

| 파일 | 해제 Task | 해제 방식 |
|---|---|---|
| `runtime.corrected_base.json` | `V5-CM-1.6` | 유일한 소비자(corrected producer) 제거 |
| `runtime.runtime_clean.json` | `V5-CM-1.8` | 같은 이름의 **최종 epoch manifest로 원자 교체**, 구본은 여기로 |
| `evaluation.evaluation_mock.json` | `V5-CM-1.8` | `evaluation_reference` 발급 후 active에서 제거 |

현재 active 등록본은 최종 epoch 2종(`runtime.runtime_clean` 22 table ·
`evaluation.evaluation_reference` 13 table)뿐이며, 폐기 계보는 전부 이 디렉터리에 있다.

### `V5-CM-1.8` 이전 이력 (과거형)

`V5-CM-1.2`~`V5-CM-1.6` 구간에는 위 3파일이 `manifests/`에 남아 `dataset_epoch:
kosa_0813`을 담고 있었고, 그만큼의 동시 참조 폭이 관리 대상으로 계획 §6에 올라 있었다.
각 파일을 단독 격리한 뒤 전체 unit을 돌린 2026-08-20 실측 보호 수치는
`runtime.runtime_clean` 51 · `evaluation.evaluation_mock` 5 · `runtime.corrected_base` 3
이었다. 계획 §1.3의 +50/+4/+2와 각각 +1 차이가 난 것은 `V5-CM-1.2`가 신설한
"잔류 3파일 고정" 테스트(`test_dataset_epoch.py`)가 함께 실패했기 때문이다.

**이 구간은 끝났다.** 그 고정 테스트는 `V5-CM-1.8`에서 최종 2종 발급을 고정하는 회귀로
대체됐고, 동시 참조 폭은 0이다.
