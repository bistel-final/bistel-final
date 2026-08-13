# Source data preflight

CM-0.4는 멘토 원본으로 적재한 PostgreSQL base table 16종이 runtime·evaluation 환경에서 동일한지 **읽기 전용으로** 검증한다. 원본 SQL·CSV를 재실행하거나 DB 데이터를 수정하지 않는다.

## 기준 manifest

`source-data-manifest.json` v2는 다음 메타데이터와 단일 `source.tables` 기준값을 저장한다.

- `format_version`
- `hash_algorithm`
- 테이블별 원본 컬럼 목록
- 테이블별 행 수
- canonical content SHA-256

runtime(`kosa_agent`, 과도기 공용 DB `kosa`)과 evaluation(`kosa_text2sql`)은 fresh bootstrap 시 같은 원본 01→02→03을 적재하므로 이 기준값 하나를 공유한다. `nl_query_log`는 누적 이력이라 제외한다. runtime migration이 `action_history`에 추가한 컬럼은 source hash에서 제외하며 migration 검증에서 따로 확인한다.

## 검증

저장소 루트 `.env`에 프로파일별 읽기 전용 DSN을 설정하고 다음을 실행한다.

```bash
python backend/scripts/verify_source_data.py --profile runtime
python backend/scripts/verify_source_data.py --profile evaluation
```

스크립트는 DB명과 manifest 형식부터 확인하고, read-only transaction과 30초 statement timeout 안에서 public allowlist 16종만 조회한다. 출력에는 host 별칭·port·DB명과 비밀 없는 diff만 포함한다.

## 생성·변경 제한

manifest는 migration 적용 전의 승인된 원본 적재 DB에서만 생성한다. 최초 생성과 변경 모두 `--confirm`이 필요하다.

```bash
# 미리보기: 파일을 쓰지 않음
python backend/scripts/verify_source_data.py --profile runtime --generate

# 승인된 원본 DB의 값을 실제로 기록
python backend/scripts/verify_source_data.py --profile runtime --generate --confirm
```

기존 manifest와 값 또는 메타데이터가 다르면 `--confirm` 없는 실행은 diff만 출력하고 종료한다. 공용 DB가 오염됐을 가능성이 있으면 manifest를 덮어쓰지 말고 원본 패키지와 먼저 대조한다.

## 완료 증빙

- Unit: `pytest backend/tests/unit/test_verify_source_data.py`
- 전체 Backend: `cd backend && pytest`
- 정적 검사: `cd backend && ruff check . && ruff format --check .`
- 실제 DB: 프로파일, 비밀 없는 대상 별칭, 종료 코드, 일치한 테이블 수를 PR 또는 Notion Task에 기록

비밀번호·사용자명·전체 DSN은 로그·artifact·PR에 기록하지 않는다.
