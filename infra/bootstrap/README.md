# Bootstrap artifacts

> [!CAUTION]
> 현재 기준은 `kosa_0813`이다. 구 epoch의 16-table hash, 알람 51건,
> `ACT-0001~0010`, 공개 Fault 정답을 신규 구현의 기대값으로 사용하지 않는다.

## 1. 등록 상태

| artifact | 파일 | 현재 상태 | 등록 소유 Task |
|---|---|---|---|
| dataset epoch | `dataset-epoch.json` | 등록됨 | V4-CM-0.1 |
| source files | `source-data-manifest.json` | **v3 등록됨** | V4-CM-1.1 |
| corrected files | `corrected-data-manifest.json` | `NOT_REGISTERED` | V4-CM-1.7 |
| DB bootstrap stage | `manifests/{profile}.{stage}.json` | `NOT_REGISTERED` | V4-CM-1.5 이후 |
| synthetic evaluation | `synthetic-evaluation-manifest.json` | `NOT_REGISTERED` | 평가 소유 Task |

미등록 artifact를 빈 JSON placeholder로 만들지 않는다. 검증기는 파일이 없으면 DB 연결이나
입력 파일 조회 전에 `NOT_REGISTERED`로 종료한다.

## 2. Dataset epoch와 source manifest

`dataset-epoch.json`은 원본 ZIP의 정체성을 고정한다.

- archive SHA-256
- 디렉터리를 제외한 ZIP member 22개의 상대 경로·크기·SHA-256
- `public_fault_ground_truth_available=false`

`source-data-manifest.json`은 epoch 등록부를 통과한 ZIP에서 PostgreSQL CSV 8개만 읽어
다음 값을 기록한다.

- `format_version=3`, `artifact_type=source_files`
- `dataset_epoch=kosa_0813`, `correction_version=none`
- dataset epoch와 같은 `source_archive_sha256`
- canonical file ID, 원래 순서의 columns, header 제외 row count, canonical row SHA-256

원본 CSV의 모든 cell은 UTF-8 문자열로 읽고 NFC 정규화한다. 빈 값은 빈 문자열로
보존하며 숫자 변환을 하지 않는다. 시작 BOM만 제거하고 내부 BOM, 빈·중복 header,
열 수가 다른 row는 hash 계산 전에 거부한다.

현재 source CSV 기준값은 다음과 같다.

| table | rows | 성격 |
|---|---:|---|
| `lot_history` | 600 | 원본 이력, 공개 Fault 정답 없음 |
| `fdc_trace` | 14,400 | 원본 Trace |
| `summary_data` | 4,800 | 제공 reference |
| `evaluation` | 4,800 | 제공 reference |
| `trace_alarm_history` | 126 | 제공 reference |
| `summary_alarm_history` | 47 | 제공 reference |
| `metrology` | 48 | 제품 계측 결과 |
| `action_history` | 48 | `MOCK` reference, runtime seed 금지 |

## 3. 검증·생성 명령

아래 명령은 ZIP을 읽을 뿐 DB에 접속하거나 쓰지 않는다.

```bash
cd backend

# 등록된 source 기준값과 비교
python scripts/verify_source_data.py \
  --artifact source-files \
  --archive /path/to/kosa_0813.zip

# 변경 미리보기: manifest를 쓰지 않음
python scripts/verify_source_data.py \
  --artifact source-files \
  --archive /path/to/kosa_0813.zip \
  --generate

# ZIP 전체 inventory guard 통과 후 manifest만 원자 교체
python scripts/verify_source_data.py \
  --artifact source-files \
  --archive /path/to/kosa_0813.zip \
  --generate --confirm
```

`--confirm`은 마지막 파일 교체만 승인한다. archive·member hash, CSV schema,
dataset epoch guard를 우회하지 못한다. candidate가 현재 manifest와 같으면 confirm 없이
no-op 성공한다.

CLI 종료 코드는 자동화에서 다음 계약으로 사용한다.

| code | 의미 |
|---:|---|
| `0` | 검증 성공 또는 동일 manifest no-op |
| `1` | 등록 artifact와 입력 불일치 |
| `2` | 잘못된 CLI 입력·검증 입력 |
| `3` | 생성·변경은 필요하지만 `--confirm` 없음 |
| `4` | artifact metadata 불일치 |
| `5` | manifest schema 오류 |
| `6` | 후속 Task 소유 artifact 미등록 |

## 4. DB bootstrap profile 계약

DB manifest는 source/corrected file manifest와 전체 hash를 공유하지 않는다. Runtime은
`action_history=0`, evaluation은 최종 Mock fixture 48건이므로 profile마다 별도
expected row/hash를 사용한다.

| profile | `applies_to` |
|---|---|
| `runtime` | `kosa_agent`, `kosa_agent_e2e` |
| `evaluation` | `kosa_text2sql` |

manifest registry는 `(profile, bootstrap_stage)`를 key로 사용한다.

| profile | stage | schema stage | migrations |
|---|---|---|---|
| runtime | `base_schema` | `base` | 없음 |
| evaluation | `base_schema` | `base` | 없음 |
| runtime | `corrected_base` | `reference_extensions` | `001_reference_extensions` |
| evaluation | `corrected_base` | `reference_extensions` | `001_reference_extensions` |
| evaluation | `evaluation_mock` | `reference_extensions` | `001_reference_extensions` |
| runtime | `runtime_clean` | `runtime_clean` | `001_reference_extensions`, `002_agent_runtime_clean` |

위 여섯 조합 외에는 DB 접근 전에 거부한다. 실제 파일은 각 bootstrap Task가 성공
marker와 함께 등록한다. 이번 Task는 경로 registry와 정적 계약만 제공하며 DB에 연결하지
않는다.

검증 시 `--mode bootstrap|steady-state`는 manifest가 아닌 CLI 인자다. bootstrap은
승인된 fresh/reset 직후의 빈 Runtime 상태를 확인하고, steady-state는 누적 Runtime row를
0건으로 요구하지 않는다. `nl_query_log`는 모든 profile·stage에서 `schema_only`로만
검증하며 immutable content hash나 bootstrap empty 기준을 둘 수 없다.

## 5. Synthetic evaluation 격리

Synthetic label은 다음 envelope를 사용하고 DB bootstrap profile을 갖지 않는다.

```json
{
  "artifact_type": "synthetic_evaluation",
  "usage_scope": "EVALUATION_ONLY",
  "ground_truth_available": true,
  "label_source": "SYNTHETIC_GENERATOR",
  "production_ground_truth_available": false
}
```

실제 파일·generator revision·seed·hash 등록은 평가 소유 Task에서 수행한다. source,
corrected, Runtime, Text2SQL/RAG 입력으로 유입하지 않는다.
현재 v3는 위 격리 envelope만 고정한다. V4-A-3.5에서 synthetic artifact를 등록할 때
generator revision·seed·file hash의 정확한 schema와 회귀 테스트를 먼저 확장한 뒤 파일을
생성한다.

## 6. 보안·운영 원칙

- manifest에는 전체 DSN, 사용자명, 비밀번호, credential, secret을 기록하지 않는다.
- URI userinfo와 POSIX·Windows 로컬 절대 경로를 기록하지 않는다.
- 알 수 없는 key는 허용하지 않는다.
- 오류와 diff에는 값 원문을 출력하지 않고 달라진 field 위치만 표시한다.
- `verify_source_data.py`는 manifest만 쓸 수 있으며 PostgreSQL DDL/DML을 수행하지 않는다.
- 구 CM-0.4 코드와 테스트는 active backend 경로에서 제거했고 로컬 archive의 SHA-256
  inventory로만 보존한다.

집중 검증:

```bash
cd backend
pytest tests/unit/test_dataset_epoch.py tests/unit/test_manifest_v3.py \
  tests/unit/test_source_manifest_artifact.py
ruff check scripts/manifest_v3.py scripts/verify_source_data.py \
  tests/unit/test_manifest_v3.py tests/unit/test_source_manifest_artifact.py
```
