# Bootstrap artifacts

> [!CAUTION]
> 현재 기준은 `kosa_0813`이다. 구 epoch의 16-table hash, 알람 51건,
> `ACT-0001~0010`, 공개 Fault 정답을 신규 구현의 기대값으로 사용하지 않는다.

## 1. 등록 상태

| artifact | 파일 | 현재 상태 | 등록 소유 Task |
|---|---|---|---|
| dataset epoch | `dataset-epoch.json` | 등록됨 | V4-CM-0.1 |
| source files | `source-data-manifest.json` | **v3 등록됨** | V4-CM-1.1 |
| corrected files | `corrected-data-manifest.json` + `markers/corrected.v1.json` | **v3 등록됨** | V4-CM-1.7 |
| DB base schema stage | `manifests/{profile}.base_schema.json` | **v3 등록됨** | V4-CM-1.5 |
| DB corrected base stage | `manifests/{profile}.corrected_base.json` | **v3 등록됨** | V4-CM-2.2 |
| 후속 DB bootstrap stage | `manifests/{profile}.{stage}.json` | 일부 미등록 | V4-CM-2.3 이후 |
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
| `7` | 외부 연결·권한·미적재로 현재 검증 불가 |

## 4. Corrected copy 빌드

`V4-CM-1.2`는 원본 ZIP을 수정하거나 압축 해제하지 않고 PostgreSQL CSV 8개를
결정론적인 corrected build로 복사하는 기반을 만들었다. CSV를 UTF-8(BOM 없음)·LF·
`QUOTE_MINIMAL` 형식으로 다시 직렬화하므로 source 파일과 byte-identical임을 보장하지 않는다.

현재 registry에는 다음 correction stage가 등록돼 있다.

| 순서 | stage | version | reads | writes | 보정 |
|---:|---|---:|---|---|---|
| 1 | `trace_seq_no` | 1 | `fdc_trace`, `trace_alarm_history` | `fdc_trace` | Step 1은 0~2, Step 2는 3~5가 되도록 `seq_no=ordv` 적용 |
| 2 | `dim_parameter_seed` | 1 | `fdc_trace` | `dim_parameter` | Generator SPEC·RAG 단위 자료로 고정한 8행 seed 생성 및 Trace FK 양방향 확인 |
| 3 | `summary_alarm_time` | 1 | `summary_alarm_history`, `lot_history` | `summary_alarm_history` | `(lot, wafer, chamber)` 유일 매칭의 `track_in_at`으로 빈 시각만 채움 |

`trace_seq_no`는 원본 Step-local 상태만 변환하고, 이미 전역 0~5이면 빈 patch로 통과한다.
두 상태가 섞였거나 Step·point 계약이 다르면 추측하지 않고 실패한다. 현재 Trace 알람은 전부
Step 1이라 값은 바꾸지 않으며, Step 2 Trace 알람이 발견되면 알람 보정 정책이 없으므로 빌드를
중단한다.

`dim_parameter_seed`는 `PH_DOSE`부터 `ET_ESC`까지 photo 4종·etch 4종을 schema 순서의
10개 column으로 만든다. 음수는 ASCII `-`로 저장하고, `upper_only=true`는 `ET_REFL`
하나만 허용한다. 8개 parameter가 Trace에서 모두 사용되며 Trace가 다른 parameter를
참조하지 않는지 함께 검사한다. 기존 seed가 있으면 column·행·순서·값이 모두 같을 때만
빈 patch로 통과한다.

`summary_alarm_time`은 Summary의 `lot/wafer/chamber`를 lot history의
`lot_id/wafer_no/chamber_id`에 연결하고 area·equipment까지 교차 확인한다. 매칭 누락·중복,
빈 `track_in_at`, 기존 비어 있지 않은 `occurred_at` 충돌은 덮어쓰지 않고 빌드를 중단한다.
`metrology.measured_at`은 공식 생성 규칙이 없으므로 이번 corrected copy에서도 NULL을
그대로 보존한다.

파일 잠금은 macOS·Linux에서 POSIX `fcntl`, native Windows에서 `msvcrt`를 사용한다.
따라서 팀의 macOS·Windows 환경에서 같은 CLI를 실행할 수 있다. Linux CI와 플랫폼별
잠금 adapter 계약 테스트로 동시 실행 직렬화를 검증한다.

```bash
cd backend

# 최초 build 생성 및 active pointer 등록
python scripts/build_corrected_dataset.py \
  --archive /path/to/kosa_0813.zip

# 현재 active build의 입력·generator·stage identity와 파일 무결성 확인
python scripts/build_corrected_dataset.py \
  --archive /path/to/kosa_0813.zip \
  --check

# generator revision이나 stage가 바뀐 build로 active pointer를 교체할 때만 사용
python scripts/build_corrected_dataset.py \
  --archive /path/to/kosa_0813.zip \
  --confirm
```

출력은 `.gitignore` 대상인 `data/corrected/v1/` 아래에 둔다.

```text
data/corrected/v1/
├── .staging/<uuid>/
├── builds/<build_id>/
│   ├── postgres/*.csv
│   ├── correction-report.json
│   └── build-receipt.json
└── active.json
```

- build는 생성 후 수정하지 않는다. 새 입력·generator·stage는 새 `build_id`를 만든다.
- `active.json`만 원자 교체하며, 기존 build는 rollback 근거로 보존한다.
- 최초 등록과 동일 build 재실행은 `--confirm`이 필요 없다. 기존 active와 다른 build로
  바꾸는 경우에만 `--confirm`이 필요하다.
- `build-receipt.json`은 source ZIP, generator component, stage 순서, table별 의미 hash와
  byte hash를 기록한다. `registration_status=UNREGISTERED`는 공식 corrected manifest가
  `V4-CM-1.7` 소유임을 뜻한다.
- `correction-report.json`은 table별 전후 행 수·column·변경량·적용 stage를 기록한다.
- `--check`는 active pointer, receipt hash, CSV 의미/byte hash와 현재 build identity를 모두
  확인하며 어떤 파일도 만들거나 교체하지 않는다.
- 빌더는 PostgreSQL·Neo4j·n8n에 접속하지 않는다.

### Corrected manifest 등록과 통합 검증

`V4-CM-1.7`은 active receipt·실제 CSV·source manifest를 다시 계산해 corrected manifest와
등록 marker를 만든다. 등록은 marker-last 방식이라 manifest 교체 직후 중단돼 marker가 없으면
등록 완료로 인정하지 않는다. no-op도 manifest·marker·active receipt가 모두 일치할 때만 허용한다.
파일 역할은 source와 동일해야 하는 6종, 내용 hash가 반드시 달라야 하는 보정 2종
(`fdc_trace`, `summary_alarm_history`), source에 없어야 하는 신규 1종(`dim_parameter`)으로
고정한다. 세 집합은 겹치지 않고 corrected table 9종을 정확히 구성해야 한다.

```bash
cd backend

# 신규·변경·marker 복구 미리보기. 파일을 쓰지 않는다.
python scripts/verify_bootstrap_state.py --register-corrected

# corrected manifest를 먼저, commit marker를 마지막에 원자 교체한다.
python scripts/verify_bootstrap_state.py --register-corrected --confirm

# source ZIP·active corrected·PK/FK·reference output 검증
python scripts/verify_bootstrap_state.py \
  --files-only --archive /path/to/kosa_0813.zip

# 명시한 등록 stage에 대한 단일 PostgreSQL acceptance. read-only transaction이다.
python scripts/verify_bootstrap_state.py \
  --database kosa_agent_e2e --stage base_schema

# Neo4j live graph·manifest·success marker fingerprint 3자 대조. READ session이다.
python scripts/verify_bootstrap_state.py \
  --neo4j --archive /path/to/kosa_0813.zip

# files → PostgreSQL 3개 → Neo4j. reports/에 Git 비추적 report 1건을 남긴다.
python scripts/verify_bootstrap_state.py \
  --all --archive /path/to/kosa_0813.zip
```

개별 검증은 stdout만 사용하고 `--report <path>`를 명시했을 때만 report를 쓴다. `--all`은
version-controlled `EXPECTED_STAGES`를 사용하며 inventory 결과로 합격 stage를 자동 선택하지
않는다. 현재 기대 stage는 세 PostgreSQL DB 모두 `corrected_base`다. DB profile끼리 content hash를
서로 비교하지 않고, 각 DB를 자신에게 해당하는 등록 manifest와만 대조한다.

PostgreSQL은 `SET TRANSACTION READ ONLY`, public schema `USAGE`, table별 `SELECT` 권한을
확인한다. `CREATE` 권한은 요구하지 않는다. Neo4j는 read access mode에서 38 node·81 relation,
relation ID 81개·중복 0, label/type 분포, graph/schema fingerprint와 success marker를 확인한다.
리포트에는 host·port·사용자·URI·DSN·원시 예외를 기록하지 않는다.
종합 상태는 종료 코드와 동일하게 `PASS(0)`, `FAIL(1)`, `NOT_REGISTERED(6)`,
`UNVERIFIABLE(7)`로 기록한다. 접속·권한·설정 실패는 원시 메시지 대신
`MISSING_CONFIGURATION`, `TARGET_IDENTITY_MISMATCH`, `NO_SCHEMA_USAGE`,
`NO_SELECT_GRANT`, `CONNECT_OR_QUERY_FAILED` 같은 고정 reason code만 남긴다.

### 로컬 산출물 복구

- active build가 손상됐으면 자동으로 덮어쓰거나 삭제하지 않는다. 실행 중인 corrected
  builder가 없는지 확인한 뒤 `data/corrected/v1` 전체를 이름이 겹치지 않는 별도
  quarantine 디렉터리로 **이동해 보존**하고, 등록 ZIP으로 다시 생성한다.
- 강제 종료로 `.staging/<uuid>`가 남았으면 다른 builder가 사용 중이지 않은 UUID인지
  확인한 뒤 개별 디렉터리만 quarantine으로 이동한다. `.staging`, `v1`, `corrected`,
  `data` 자체를 재귀 삭제하지 않는다.
- `builds/<build_id>`는 rollback·충돌 조사 근거이므로 자동 또는 수동으로 덮어쓰지 않는다.
- active가 다른 candidate로 바뀌어야 할 때 출력되는 기존/후보 build ID, 변경 identity,
  변경 table을 확인한 후에만 `--confirm`을 사용한다.

종료 코드는 source manifest 검증기와 같은 공통 계약을 사용한다.

| code | 의미 |
|---:|---|
| `0` | 생성·교체·동일 build no-op 또는 `--check` 일치 |
| `1` | `--check`에서 현재 build identity 불일치 |
| `2` | 잘못된 CLI 또는 안전 경계 위반 |
| `3` | active 변경에 `--confirm` 필요 |
| `4` | artifact metadata 불일치 |
| `5` | manifest·receipt·report·CSV schema/hash 오류 |
| `6` | active pointer, build 또는 receipt가 없거나 무결성 오류 |

Corrected dataset build 집중 검증:

```bash
cd backend
pytest tests/unit/test_build_corrected_dataset.py \
  tests/unit/test_correction_trace_seq_no.py \
  tests/unit/test_correction_dim_parameter_seed.py \
  tests/unit/test_correction_summary_alarm_time.py
ruff check scripts/build_corrected_dataset.py scripts/manifest_v3.py \
  scripts/corrections tests/unit/test_build_corrected_dataset.py \
  tests/unit/test_correction_trace_seq_no.py \
  tests/unit/test_correction_dim_parameter_seed.py \
  tests/unit/test_correction_summary_alarm_time.py
```

## 5. DB bootstrap profile 계약

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

위 여섯 조합 외에는 DB 접근 전에 거부한다. `base_schema` 두 manifest는 V4-CM-1.5,
`corrected_base` 두 manifest는 V4-CM-2.2에서 등록했으며 나머지는 각 후속 bootstrap Task가
등록한다. 모든 DB manifest는 `value_normalization_version=db-value-v1`을 기록한다.

### Base schema 생성·검증

`001_base_schema.sql`은 등록된 `03_schema_clean.sql`의 구조를 보존한 corrected overlay다.
아래 설명 주석만 v2 기준으로 보정했다.

- WAFER 번호 `1~25`
- `fault_code=NRM`은 공개 Fault 정답이 아닌 placeholder
- `metrology.alarm_result`는 제품 CD PASS/FAIL이며 Fault Mode 정답이 아님
- `dim_parameter`는 raw Trace/evaluation 고정 5선이고 Summary 동적 CL±3σ는 별도 계산
- source 경로 `클린데이터셋`

SQL에는 data·role·database·transaction·destructive 문장이 없고, 9개 base table과 명시적
index 4개, COMMENT 11개만 있다. PK가 만드는 constraint index 9개를 포함한 적용 후 전체
index 기대값은 13개다. 세 DB는 이 단계에서 모든 table이 0행이어야 한다.

bootstrap runner는 앱 DSN을 읽지 않고 루트 `.env`의 다음 전용 키만 읽는다.

```text
POSTGRES_BOOTSTRAP_HOST
POSTGRES_BOOTSTRAP_PORT
POSTGRES_BOOTSTRAP_USER
POSTGRES_BOOTSTRAP_PASSWORD
POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256
```

host allowlist 값은 `sha256(lowercase(host) + ":" + decimal port)`다. 실제 host·user·password·
fingerprint는 Git에 기록하지 않는다. 실행은 한 번에 한 DB만 허용한다.

```bash
cd backend

# SQL·manifest·target 설정만 확인, DB 접속 없음
python scripts/bootstrap_base_schema.py --dry-run --database kosa_agent_e2e

# read-only transaction으로 identity·public schema·상태·lock 확인
python scripts/bootstrap_base_schema.py --preflight --database kosa_agent_e2e

# 대상 이름을 한 번 더 확인해야 실제 DDL 적용
python scripts/bootstrap_base_schema.py \
  --database kosa_agent_e2e \
  --confirm-target kosa_agent_e2e

# exact empty schema가 이미 있고 marker만 없을 때 DDL 없이 복구
python scripts/bootstrap_base_schema.py \
  --database kosa_agent_e2e \
  --recover-marker \
  --confirm-target kosa_agent_e2e

# 성공 marker는 남아 있지만 공용 객체가 전부 유실된 E2E DB만 명시적으로 재생성
python scripts/bootstrap_base_schema.py \
  --database kosa_agent_e2e \
  --repair-lost-schema \
  --approval-ref GH-45 \
  --confirm-target kosa_agent_e2e

# 객체가 전혀 없는 E2E DB에서만 DDL rollback을 주입 검증
python scripts/bootstrap_base_schema.py \
  --database kosa_agent_e2e \
  --verify-rollback \
  --confirm-target kosa_agent_e2e
```

mutation은 DB identity와 `public` search path를 다시 검사하고 고정 advisory transaction lock을
먼저 획득한다. exact empty·marker 일치 상태는 no-op이고, partial schema·추가 객체·1행 이상
데이터·marker 불일치는 변경하지 않고 실패한다. rollback 주입은 `kosa_agent_e2e` 외 DB에서
거부된다.

`--repair-lost-schema`는 marker 파일을 수동 삭제하거나 공용 DB 전체를 초기화하지 않고
marker/live schema drift만 복구하는 제한 모드다. 기존 success marker가 있고 transaction 안의
실제 상태가 `ABSENT`일 때만 `kosa_agent_e2e`에 9개 base table을 다시 만든다. partial schema,
추가 객체, 데이터가 있는 DB, `kosa_agent`·`kosa_text2sql`에서는 거부한다. 적용 후
`EXACT_EMPTY` 검증과 DB commit이 모두 끝난 뒤에만 기존 marker를 원자 교체한다. 팀이 추적할
수 있는 `--approval-ref`가 필수이며, marker는 `REPAIRED` 상태와 이전 marker의 status·기록
시각·SHA-256을 보존한다.

현재 공용 E2E DB는 이미 `EXACT_EMPTY`라 repair DDL 경로를 실환경에서 실행하지 않았다. 다음에
marker/live drift가 발생하면 일반 `--preflight` 실패를 먼저 팀 이슈에 기록하고, 공용 객체 유실
여부를 read-only inventory로 확인한 뒤 승인 이슈 번호를 `--approval-ref`로 전달해 이 명령을
첫 복구 수단으로 사용한다. 실행 결과와 새 marker diff도 같은 이슈에 남긴다.

성공 marker는 `markers/base_schema.<database>.json`에 원자적으로 저장한다. 실제 신규 적용은
`APPLIED`, 기존 exact empty schema 확인 후 marker만 복구하면 `VERIFIED_EXISTING`, 유실 schema를
명시적으로 복구하면 `REPAIRED`다. marker는 DB명·profile·source/SQL/signature hash·검증 건수와
복구 감사 근거만 담고 host·user·password·DSN은 담지 않는다.
동시 저장은 macOS/Linux의 `fcntl`, native Windows의 `msvcrt` lock으로 직렬화한다.

집중 검증:

```bash
cd backend
pytest tests/unit/test_base_schema_sql.py tests/unit/test_bootstrap_base_schema.py
ruff check scripts/db_target.py scripts/bootstrap_base_schema.py \
  tests/unit/test_base_schema_sql.py tests/unit/test_bootstrap_base_schema.py
ruff format --check scripts/db_target.py scripts/bootstrap_base_schema.py \
  tests/unit/test_base_schema_sql.py tests/unit/test_bootstrap_base_schema.py
```

검증 시 `--mode bootstrap|steady-state`는 manifest가 아닌 CLI 인자다. bootstrap은
승인된 fresh/reset 직후의 빈 Runtime 상태를 확인하고, steady-state는 누적 Runtime row를
0건으로 요구하지 않는다. `nl_query_log`는 모든 profile·stage에서 `schema_only`로만
검증하며 immutable content hash나 bootstrap empty 기준을 둘 수 없다.

### Reference extension migration 적용·검증

`V4-CM-2.1`의 `001_reference_extensions.sql`은 세 PostgreSQL DB에 공통으로 필요한
reference schema만 추가한다. `vector` extension과 다음 객체만 만들며 base table row,
`action_history`, Runtime Agent 상태를 변경하지 않는다.

- `r03_alarm_history`
- `document_corpus`, `document`, `document_chunk(vector(1024))`
- `nl_query_log`
- `ux_document_corpus_active` 부분 고유 index
- TRACE·SUMMARY·R03를 정규화한 `v_alarm_event` view

runner는 Base schema와 같은 bootstrap 전용 PostgreSQL 설정과 advisory lock namespace를
공유한다. 세 DB 중 한 번에 하나만 적용하며, 공용 DB DDL은 코드 리뷰·최종 검증·팀 승인 후
`kosa_agent_e2e` → `kosa_agent` → `kosa_text2sql` 순서로 수행한다.

```bash
cd backend

# SQL·target 설정만 검사한다. DB에 연결하지 않는다.
python scripts/apply_reference_extensions.py \
  --dry-run --database kosa_agent_e2e

# read-only transaction으로 base schema·현재 migration 상태·lock을 검사한다.
python scripts/apply_reference_extensions.py \
  --preflight --database kosa_agent_e2e

# E2E 전용: 실제 apply와 같은 DDL·postcheck를 실행하고 무조건 rollback한다.
# receipt·marker를 만들지 않으며 다른 DB와 change-ref 사용을 거부한다.
python scripts/apply_reference_extensions.py \
  --rehearse \
  --database kosa_agent_e2e \
  --confirm-target kosa_agent_e2e

# 승인 후에만 실제 transaction을 실행한다.
python scripts/apply_reference_extensions.py \
  --database kosa_agent_e2e \
  --confirm-target kosa_agent_e2e \
  --change-ref GH-<issue-or-pr-number>

# DB commit 뒤 marker 저장이 중단된 경우에만 기존 receipt로 marker를 복구한다.
python scripts/apply_reference_extensions.py \
  --recover-marker \
  --database kosa_agent_e2e \
  --confirm-target kosa_agent_e2e \
  --change-ref GH-<issue-or-pr-number>

# 단일 DB 또는 전체 DB를 read-only로 검증한다.
python scripts/verify_migrations.py --database kosa_agent_e2e
python scripts/verify_migrations.py --all
```

정상 적용은 `infra/bootstrap/reports/reference_extensions.<database>.<uuid>.json`에 시도별
receipt를 먼저 기록하고, DB commit 후
`infra/bootstrap/markers/reference_extensions.<database>.json` marker를 마지막에 원자 저장한다.
receipt는 `STARTED`·`COMMITTED`·`ABORTED` 상태를 보존한다. DB가 이미 exact schema인데 marker나
복구 가능한 receipt가 전혀 없는 receipt-less adoption은 거부한다. marker가 남았는데 schema가
전부 유실된 `LOST_SCHEMA`도 이 migration에서 자동 복구하지 않는다. partial schema, column·
constraint·index·view·vector typmod drift, PUBLIC 권한 부여는 변경하지 않고 중단한다.

공용 DB 적용 순서는 `preflight → rehearse → apply → verify`로 고정한다. `rehearse`는
`kosa_agent_e2e`의 marker·001 객체·reference sequence가 없는 상태에서만 실행하며, 실제 apply와
같은 `postcheck_database()`를 통과한 뒤 extension·table·view·sequence·action 상태를 transaction
이전으로 되돌렸는지 새 read-only transaction에서 다시 확인한다. rehearsal 성공은
`REHEARSAL_OK ... rolled_back=true`로만 출력하고 Git artifact를 만들지 않는다.

검증기는 다음을 모두 확인한다.

- `vector`가 `public` schema에 있고 `document_chunk.embedding`이 `vector(1024)`인지
- 5개 table·1개 index·1개 view의 catalog signature가 marker와 같은지
- `v_alarm_event` branch 합계·source/type·AlarmRef·lot key가 유효한지
- `action_history` row count가 migration 전후 동일한지
- PUBLIC에 table/view `SELECT`·DML 권한이 없는지
- Runtime role matrix가 아직 미완성이라면 `NOT_READY(V4-CM-2.6)`로 명시되는지

실패 출력은 원시 예외 대신 `MISSING_CONFIGURATION`, `TARGET_IDENTITY_MISMATCH`,
`MIGRATION_NOT_APPLIED`, `MISSING_SUCCESS_MARKER`, `SCHEMA_DRIFT`,
`SCHEMA_SIGNATURE_MISMATCH`, `VECTOR_VERSION_MISMATCH`, `PUBLIC_PRIVILEGE_DETECTED`,
`CONNECT_OR_QUERY_FAILED` 같은 고정 reason code를 사용한다.

`001` 적용 직후 `verify_bootstrap_state.py --all`이 즉시 PASS할 필요는 없다. 이 migration이
schema stage를 `reference_extensions`로 올리는 반면 `corrected_base`·`evaluation_mock`
profile manifest 등록은 `V4-CM-2.2`·`V4-CM-2.3` 소관이므로, 그 전까지 stage mismatch는 예상된
Gate 상태다.

### Corrected base data 채택·적재

`V4-CM-2.2`는 등록된 active corrected build만 입력으로 허용한다. source archive,
corrected manifest·registration marker·build receipt와 generator identity를 mutation 전에 하나의
SHA-256 identity로 묶으며 임의 data directory 우회는 제공하지 않는다.

DB와 CSV의 numeric·boolean·timestamp·JSON 값을 `db-value-v1` 규칙으로 정규화해 비교한다.
NULL은 JSON `null`, 빈 문자열은 `""`로 서로 다르게 유지한다. CSV의 빈 cell은 load contract상
NULL이며, 이 규칙은 다음 `evaluation_mock` action 48건 적재에도 그대로 적용한다.

| database | 경로 | action_history |
|---|---|---:|
| `kosa_agent_e2e` | corrected base 8 table을 FK 순서로 신규 적재 | 0 유지 |
| `kosa_agent` | 기존 내용 채택, `dim_parameter.parameter_name` 3건만 보정 | 0 유지 |
| `kosa_text2sql` | 기존 내용 채택, 같은 3건만 보정 | 48 유지 |

runner는 `action_history`, R03·corpus 4 table, `nl_query_log`에 DML을 실행하지 않는다. mutation은
공통 advisory lock을 얻은 `REPEATABLE READ` 단일 transaction이며 timeout·FK 오류·postcheck
실패 시 전체 rollback한다. `nl_query_log`는 누적 table이므로 schema만 검증한다.

```bash
cd backend

# DB 접속 없이 corrected_base manifest 2종의 변경만 미리 본다.
python scripts/load_corrected_base.py --register-manifests

# 코드 리뷰로 확정된 manifest만 원자 등록한다.
python scripts/load_corrected_base.py --register-manifests --confirm

# 각 DB의 001 marker/signature와 EMPTY·ADOPTED·NEEDS_FIXUP·DRIFT 상태만 읽는다.
python scripts/load_corrected_base.py \
  --database kosa_agent_e2e --preflight

# E2E 전용 실제 bulk 적재 경로를 실행하고 무조건 rollback한다.
python scripts/load_corrected_base.py \
  --database kosa_agent_e2e --rehearse --confirm-target kosa_agent_e2e

# 승인 후 실제 적용. 세 DB를 한 명령으로 묶지 않는다.
python scripts/load_corrected_base.py \
  --database kosa_agent_e2e --confirm-target kosa_agent_e2e --change-ref GH-<number>
python scripts/load_corrected_base.py \
  --database kosa_agent --confirm-target kosa_agent --change-ref GH-<number>
python scripts/load_corrected_base.py \
  --database kosa_text2sql --confirm-target kosa_text2sql --change-ref GH-<number>

# DB commit 뒤 완료 artifact 저장이 중단됐을 때만 사용한다.
python scripts/load_corrected_base.py \
  --recover-artifact --database kosa_agent \
  --confirm-target kosa_agent --change-ref GH-<number>
```

runtime 두 DB는 성공 marker를 마지막에 기록한다. evaluation DB의 corrected-base alignment
report는 action 48건을 보존한 채 다음 stage가 `V4-CM-2.3`임을 기록한다. 통합 검증기는 더 이상
이를 PASS 가능한 중간 상태로 승격하지 않으며, 아래 Evaluation Mock marker가 생긴 뒤에만
`evaluation_mock` stage를 PASS한다.

현재 PR의 로컬 구현·단위 테스트 단계에서는 공용 DB에 mutation을 실행하지 않는다. 독립 코드
리뷰와 최종 검증 후 사용자 승인으로 E2E → runtime → evaluation 순서로 적용한다.

Corrected base 집중 검증:

```bash
cd backend
pytest tests/unit/test_load_corrected_base.py \
  tests/unit/test_value_normalization.py \
  tests/unit/test_verify_bootstrap_state.py
ruff check scripts/load_corrected_base.py scripts/value_normalization.py \
  scripts/mutation_runtime.py tests/unit/test_load_corrected_base.py
ruff format --check scripts/load_corrected_base.py scripts/value_normalization.py \
  scripts/mutation_runtime.py tests/unit/test_load_corrected_base.py
```

### Evaluation Mock fixture 채택·등록

`V4-CM-2.3`은 `kosa_text2sql` 하나만 대상으로 한다. active corrected bundle의
`action_history.csv` 48건을 `db-value-v1`로 정규화해 expected hash를 만들며, live DB는 이 값을
생성하는 입력이 아니라 비교 대상이다. 기존 48건이 일치하면 DML 없이 채택하고, table이 비어
있을 때만 동일 48건을 단일 transaction으로 INSERT한다. 한 행이라도 다르면 부분 보정 없이
`DRIFT`로 중단한다.

`fixture_type=MOCK`은 `evaluation.evaluation_mock.json`의 `action_history` metadata이며 DB
컬럼이 아니다. 이 fixture는 Text2SQL·화면 계약 회귀에만 사용하고 Agent/Fault 학습·정답·
runtime seed로 사용하지 않는다.

```bash
cd backend

# DB 접속 없이 evaluation_mock manifest 변경만 확인·등록한다.
python scripts/load_evaluation_mock.py --register-manifests
python scripts/load_evaluation_mock.py --register-manifests --confirm

# 공용 DB 적용 전 read-only 상태 확인과 transaction rollback rehearsal.
python scripts/load_evaluation_mock.py \
  --database kosa_text2sql --preflight
python scripts/load_evaluation_mock.py \
  --database kosa_text2sql --rehearse --confirm-target kosa_text2sql

# 독립 코드리뷰·최종검증 후 승인된 change reference로만 실제 채택/적재한다.
python scripts/load_evaluation_mock.py \
  --database kosa_text2sql --confirm-target kosa_text2sql --change-ref GH-<number>

# DB commit 뒤 marker 기록만 중단된 경우, 일치하는 receipt가 정확히 1건일 때만 복구한다.
python scripts/load_evaluation_mock.py --recover-artifact \
  --database kosa_text2sql --confirm-target kosa_text2sql --change-ref GH-<number>

# 완료 marker와 48건 immutable hash까지 포함한 전체 Gate.
python scripts/verify_bootstrap_state.py --all
```

성공 marker `markers/evaluation_mock.kosa_text2sql.json`은 마지막에 원자 기록한다. 영구 no-op
판정은 action entry·dataset/correction/value-normalization·001 schema signature로 구성한
durable fixture identity를 사용한다. 적용 당시 전체 manifest·corrected build·generator는
ignored receipt와 marker의 감사 provenance로 남긴다. 따라서 이후 R03/corpus reference 내용만
변해도 Mock 유실로 오판하지 않지만, 현재 reference 정합성은 통합 검증기가 별도로 검사한다.

Evaluation Mock 집중 검증:

```bash
cd backend
pytest tests/unit/test_load_evaluation_mock.py \
  tests/unit/test_verify_bootstrap_state.py
ruff check scripts/load_evaluation_mock.py scripts/verify_bootstrap_state.py \
  tests/unit/test_load_evaluation_mock.py tests/unit/test_verify_bootstrap_state.py
ruff format --check scripts/load_evaluation_mock.py scripts/verify_bootstrap_state.py \
  tests/unit/test_load_evaluation_mock.py tests/unit/test_verify_bootstrap_state.py
```

Reference extension 집중 검증:

```bash
cd backend
pytest tests/unit/test_reference_extensions.py \
  tests/unit/test_verify_migrations.py \
  tests/unit/test_schema_lock.py \
  tests/unit/test_alarm_event_contract.py \
  tests/unit/test_document_contract.py \
  tests/unit/test_nl_query_log_contract.py
ruff check scripts/apply_reference_extensions.py scripts/verify_migrations.py \
  scripts/schema_lock.py tests/unit/test_reference_extensions.py \
  tests/unit/test_verify_migrations.py
ruff format --check scripts/apply_reference_extensions.py scripts/verify_migrations.py \
  scripts/schema_lock.py tests/unit/test_reference_extensions.py \
  tests/unit/test_verify_migrations.py
```

## 6. Neo4j destructive-safe loader

`master_graph.cypher`는 등록된 `kosa_0813.zip`의 `master.cypher`에서 destructive 문을
분리하고 relationship 81건에 stable `relation_id`를 주입해 만든 결정적 생성물이다.
원본의 `MATCH (n) DETACH DELETE n`은 Neo4j로 전송하지 않는다.

`manifests/neo4j.graph.json`은 다음 불변 기준을 고정한다.

- node 38건, relationship 81건과 label/type 분포
- corrected Cypher SHA-256
- stable relationship ID 규칙 `rel-id-v1`
- expected graph fingerprint와 legacy relation-id 미부여 graph fingerprint
- source archive/member SHA-256과 business-key 계약 `bk-v1`

loader는 앱의 `NEO4J_*` 설정을 읽지 않고 다음 전용 키만 읽는다.

```text
NEO4J_BOOTSTRAP_URI
NEO4J_BOOTSTRAP_USER
NEO4J_BOOTSTRAP_PASSWORD
NEO4J_BOOTSTRAP_ALLOWED_TARGET_SHA256
NEO4J_BOOTSTRAP_BACKUP_ROOT
```

target allowlist 값은
`sha256(lowercase(scheme) + "://" + lowercase(host) + ":" + decimal port + "/" + database)`다.
URI userinfo·path·query·fragment를 금지하며, 연결 후 `db.info()`의 database도 다시 확인한다.
로그와 marker에는 database와 되돌릴 수 없는 target fingerprint만 남긴다.

### 안전한 실행 순서

```bash
cd backend

# ZIP·생성물·manifest·target 설정만 확인. Neo4j 접속 없음
python scripts/bootstrap_neo4j_graph.py \
  --dry-run --database neo4j \
  --archive /path/to/kosa_0813.zip

# graph·schema 상태를 읽고 24시간 유효 preflight receipt를 저장
python scripts/bootstrap_neo4j_graph.py \
  --preflight --database neo4j --confirm-target neo4j \
  --archive /path/to/kosa_0813.zip \
  --receipt-out /external/backup-root/preflight/neo4j.json

# graph가 완전히 비어 있고 marker가 없을 때만 seed 적용
python scripts/bootstrap_neo4j_graph.py \
  --apply-empty --database neo4j --confirm-target neo4j \
  --archive /path/to/kosa_0813.zip

# source와 정확히 같은 legacy graph에 stable relation_id만 원자적으로 backfill
python scripts/bootstrap_neo4j_graph.py \
  --adopt-existing --database neo4j --confirm-target neo4j \
  --archive /path/to/kosa_0813.zip --approval-ref KOSA-123
```

populated graph는 기본 교체하지 않는다. `--replace`는 다음 증거가 모두 같은 target과 기존
graph fingerprint를 가리킬 때만 허용한다.

1. 24시간 이내 preflight receipt
2. 저장소 밖 backup root의 logical backup과 manifest
3. backup을 실제로 읽어 fingerprint를 재계산한 restore verification receipt
4. 팀이 추적 가능한 `approval_ref`
5. 실행 직전 transaction 안에서 다시 확인한 기존 graph fingerprint

논리 백업 v2는 graph data와 함께 **현재 schema fingerprint**를 증빙에 묶는다. Neo4j 기본
LOOKUP index와 NODE UNIQUENESS constraint가 소유한 RANGE backing index만 허용하며, 독립
사용자 index·다른 constraint는 공식 dump 없이는 거부한다. 교체는 schema DDL을 변경하지
않고 기존 constraint/index를 그대로 유지한다. preflight·backup·restore receipt의 schema
fingerprint가 모두 같고 실행 transaction 직전에도 동일할 때만 삭제·seed를 시작한다. 구
epoch 복구를 위해 `Sensor.sensor_id`와 finite float property도 backup/restore 범위에 포함한다.

backup 생성과 오프라인 restore 검증은 다음처럼 분리한다.

```bash
# 현재 graph의 logical backup과 manifest 생성
python scripts/bootstrap_neo4j_graph.py \
  --backup --database neo4j --confirm-target neo4j \
  --archive /path/to/kosa_0813.zip

# DB 접속 없이 backup을 읽고 restore verification receipt 생성
python scripts/bootstrap_neo4j_graph.py \
  --verify-backup \
  --backup-manifest /external/backup-root/backups/<name>.manifest.json \
  --receipt-out /external/backup-root/receipts/<name>.restore.json
```

교체·복구 명령은 receipt에 기록된 SHA-256과 CLI의 expected fingerprint를 모두 다시
대조한다. `--replace`는 transaction 안에서 삭제·seed·38/81 fingerprint 검증을 한 번에
수행하며, 하나라도 실패하면 rollback한다. `--restore-backup`도 현재 fingerprint를 다시
확인한 뒤 backup snapshot을 원자 복원하고 `RESTORED` marker를 남긴다. 이 marker는 readiness
성공이 아니므로, 복원 후 graph가 새 expected 기준과 정확히 같을 때만 `--recover-marker`로
`VERIFIED_EXISTING` marker를 다시 만든다.

성공 marker는 `markers/neo4j_graph.<database>.json`에 원자적으로 저장한다. 앱 readiness는
marker가 `APPLIED`, `REPLACED`, `ADOPTED_EXISTING`, `VERIFIED_EXISTING` 중 하나이고 실제
fingerprint가 marker의 expected 값과 일치할 때만 통과한다. `RESTORED`는 운영자 확인이 필요한
복구 상태다. 파일 잠금은 macOS·Linux의 `fcntl`, native Windows의 `msvcrt`를 사용한다.

집중 검증:

```bash
cd backend
pytest tests/unit/test_master_cypher.py tests/unit/test_bootstrap_neo4j_graph.py
ruff check scripts/master_cypher.py scripts/neo4j_target.py \
  scripts/bootstrap_neo4j_graph.py tests/unit/test_master_cypher.py \
  tests/unit/test_bootstrap_neo4j_graph.py
ruff format --check scripts/master_cypher.py scripts/neo4j_target.py \
  scripts/bootstrap_neo4j_graph.py tests/unit/test_master_cypher.py \
  tests/unit/test_bootstrap_neo4j_graph.py
```

V4-CM-1.6의 최초 구현·단위 테스트에서는 공용 Neo4j에 적용하지 않았다. 이후 운영 단계에서
GitHub 이슈 `#41`을 적용 근거로 preflight, 논리 백업 v2, 오프라인 restore 검증을 순서대로 통과한
뒤 구 24 nodes·26 relationships를 신규 38 nodes·81 relationships로 원자 교체했다. 교체 전후
schema fingerprint는 같고, 관계 81건의 `relation_id`는 전부 존재하며 중복 0건이다. 복구용
backup·manifest·restore receipt는 저장소 밖 backup root에 보존하고, 저장소에는 비밀정보가
없는 `REPLACED` success marker만 등록한다.

## 7. Synthetic evaluation 격리

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

## 8. 보안·운영 원칙

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
