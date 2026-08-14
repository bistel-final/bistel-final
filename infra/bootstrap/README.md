# Bootstrap artifacts

> [!CAUTION]
> 현재 기준 데이터는 `kosa_0813` epoch다. 신규 구현에서 구 데이터의 고정 Fault 정답,
> 알람 51건, `ACT-0001~0010`을 기대값으로 사용하지 않는다.

## 현재 기준 — dataset epoch 등록

`dataset-epoch.json`은 V4-CM-0.1에서 확정한 **원본 ZIP 식별 등록부**다.

- dataset epoch와 수신일
- 원본 archive 파일명과 SHA-256
- ZIP의 디렉터리 항목을 제외한 전체 파일 22개의 정렬된 상대 경로·크기·SHA-256
- `public_fault_ground_truth_available=false`

이 파일은 원본 ZIP의 정체성과 공개 Fault 정답 부재만 고정한다. 원본·corrected 파일의
행 수·컬럼·보정 내역이나 runtime/evaluation DB 상태를 나타내는 manifest가 아니다.
그 항목들은 V4-CM-1.1에서 `format_version=3`의 source/corrected file manifest와
profile별 bootstrap manifest로 분리한다.

등록부에는 원본 ZIP을 둔 로컬 절대 경로, DSN, 계정, 비밀번호를 기록하지 않는다.
원본 ZIP과 압축 해제 파일은 직접 수정하지 않는다.

검증:

```bash
cd backend
pytest tests/unit/test_dataset_epoch.py
```

## Legacy — 구 epoch source preflight

> [!WARNING]
> `source-data-manifest.json` v2와 `backend/scripts/verify_source_data.py`는 구 epoch
> CM-0.4 구현 이력이다. `kosa_0813.zip`의 source·corrected·runtime·evaluation
> 기준값이 아니며 신규 DB bootstrap이나 평가의 expected manifest로 사용하지 않는다.
> 파일은 V4-CM-1.1에서 v3 manifest 구조로 교체하기 전까지 이력 보존 목적으로만 둔다.

구 CM-0.4는 멘토의 이전 원본으로 적재한 PostgreSQL base table 16종이
runtime·evaluation 환경에서 동일한지 **읽기 전용으로** 검증했다. 원본 SQL·CSV를
재실행하거나 DB 데이터를 수정하지 않았다.

### 구 v2 manifest 구조

`source-data-manifest.json` v2는 다음 메타데이터와 단일 `source.tables` 기준값을
저장한다.

- `format_version`
- `hash_algorithm`
- 테이블별 원본 컬럼 목록
- 테이블별 행 수
- canonical content SHA-256

구 runtime(`kosa_agent`, 과도기 공용 DB `kosa`)과 evaluation(`kosa_text2sql`)이
같은 원본 01→02→03을 적재한다는 가정을 사용했다. 이 단일 expected hash 가정은
신규 epoch에 적용하지 않는다.

### 구 검증 명령 기록

다음 명령은 구 epoch의 재현 기록이다. V4-CM-1.1 리팩터링 전에는 신규 epoch나 신규
DB를 대상으로 실행하지 않는다.

```bash
python backend/scripts/verify_source_data.py --profile runtime
python backend/scripts/verify_source_data.py --profile evaluation
```

구 스크립트는 DB명과 manifest 형식을 먼저 확인하고, read-only transaction과 30초
statement timeout 안에서 public allowlist 16종만 조회했다. 출력에는 host 별칭·port·
DB명과 비밀 없는 diff만 포함했다.

### 구 manifest 생성 제한

아래 `--generate` 명령도 구 epoch 기록이다. 신규 epoch 기준 manifest 생성에 사용하지
않는다.

```bash
# 미리보기: 파일을 쓰지 않음
python backend/scripts/verify_source_data.py --profile runtime --generate

# 구 epoch의 승인된 원본 DB 값을 기록했던 명령
python backend/scripts/verify_source_data.py --profile runtime --generate --confirm
```

비밀번호·사용자명·전체 DSN은 로그·artifact·PR에 기록하지 않는다.
