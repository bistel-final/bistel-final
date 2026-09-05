# U10 비교 결과 오프라인 계약 — V5-C-7.1

담당 방대혁(C). 계획 v58의 **부분 구현**이다. 32 attempt의 구조와 판정 재계산 및
고정 정책/공통 읽기 실행 코어를 제공한다. 실제 CF 데이터·provider 연결·운영 전환 Gate는 아니다.
기존 `comparison.py`의 historical v1/v2 발급물은 변경하지 않는다.

## 입력과 결속

- 모델 정본: `backend/app/agent/u10_comparison.py`의 `Benchmark`, `Artifact`.
- `u10-benchmark-v1`: CF-1~8, fixture/tool/fixed-policy SHA, 초기 snapshot과 evidence ID,
  candidate inventory, oracle 및 각 canonical SHA를 고정한다. oracle은 최소 2개 ID다.
- inventory의 가용성(`AVAILABLE`/`NOT_AVAILABLE`)과 oracle의 필수 조사 축
  (`oracle_required_dimensions`, 목록에 없는 축은 NOT_REQUIRED)을 분리한다.
  history 이전 lot이 0이어도 현재 chamber 이력 조회 자체를 불가능하다고 처리하지 않는다.
- `u10-comparison-v1`: 40자리 revision, benchmark canonical SHA, 코드 소유 판정 규칙 SHA,
  hypothesis v3 / selector v2 prompt와 model revision·temperature 0·seed,
  세 가지 synthetic/experiment-only 표시를 필수로 둔다.
- 8 fixture × 2 attempt × 2 policy = 32건/16쌍, fixture별 첫 쌍은 fixed→ReAct,
  둘째 쌍은 ReAct→fixed다. 각 attempt의 snapshot·LLM 설정 SHA도 결속한다.

## 재계산 범위

- 실제 read attempt는 ERROR/TIMEOUT과 재시도까지 센다. 최대 8회, 동일 도구 최대 4회,
  동일 선택 재시도 1회다. Fixed는 selector 0회이며 지정된 8 slot 순서와
  후보 부재의 `NO_CANDIDATE` skip을 확인한다.
- 성공 읽기의 evidence와 초기 evidence 합집합, 인용 oracle recall, 미지원 인용,
  compared, selector/hypothesis token 합, tool/selector latency를 재계산한다.
- 부작용·안전 위반·미지원 인용·미완료·조치 불일치는 연구 판정의 hard gate 실패다.
- 효율 분기: recall 비회귀, 읽기 감소 중앙값 ≥1, token 증가 중앙값 ≤10%,
  latency 증가 중앙값 ≤25%.
- 품질 분기: recall 증가 평균 ≥0.125, token/latency 증가 중앙값 각각 ≤50%.
- 두 분기 모두 fixture별 recall 중앙값 비회귀 및 전체/fixture별 ERROR+TIMEOUT
  **합계** 비회귀를 요구한다. 선언된 breakdown 전체를 재계산 결과와 대조한다.
- reason 우선순위는 HARD_GATE_FAIL → COST_CAP_EXCEEDED → NO_GAIN이다.

## 읽기 전용 CLI

```sh
python backend/scripts/verify_u10_comparison.py \
  --artifact /absolute/private/comparison.json \
  --benchmark /absolute/private/benchmark.json \
  --benchmark-sha256 <사전별도고정한-benchmark-파일의-raw-SHA256>
```

디렉터리는 0700, 입력 파일은 소유자가 일치하는 0600 regular/single-link 파일이어야 한다.
symlink·중복 JSON key·비유한 수·boolean/number coercion을 거부한다.
CLI의 benchmark pin은 **파일 byte SHA**이며 artifact 내부 benchmark SHA는
**모델 canonical JSON SHA**이므로 구분한다. 검증 대상 artifact에서 pin을 가져오지 않는다.

구조·재계산 불일치는 exit 1이다. 일관된 부정 연구 결과는 exit 0이며
`agent_verdict=AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21`로 출력한다.
`integrity=PASS`는 이 오프라인 계약의 일관성만 뜻한다. `inspection_only=true`이고
`allowed_actions`·승인 파일·receipt·env 수정·production enable 기능은 없다.

## 고정 정책 및 공통 읽기 실행 코어

`backend/app/agent/u10_read_execution.py`는 provider/config/DB를 import하지 않는다.
`execute_fixed_policy()`는 후보가 존재하는 slot만 정해진 순서로 실행하며, selector나
hypothesis를 호출하지 않는다. 미래 ReAct 어댑터도 같은 `ReadSession.execute()`를 사용한다.

- `fixed_policy_document_query()`는 snapshot의 model/parameter ID만 받아 중복 제거·정렬한
  식별자와 두 code-owned suffix로 검색어를 만든다. 최대 200자이며 oracle/가설 입력은 없다.
  `fixed_policy_sha256()`는 query 규칙·slot·budget의 canonical spec SHA를 제공한다.
  실제 benchmark에 이 SHA를 결속하는 runner는 아직 미연결이다.
- 시작 전 전체 fixed 입력의 slot 집합/JSON 크기를 검사한다. 문서 검색어 override는 거부한다.
  snapshot별 식별자 allowlist와 사실상 inventory 검증은 후속 adapter의 책임이다.
- ERROR/TIMEOUT은 동일 선택·canonical 입력으로 1회 재시도한다. 매 호출 직전 예산을 소비하며
  read 8회·동일 도구 4회 상한에 도달하면 추가 adapter 호출 없이 차단한다.
- callback 예외 원문은 보존하지 않고 ERROR/TIMEOUT만 기록한다. monotonic clock으로 지연을
  측정하고 input digest·selection·retry·status·evidence ID를 기존 `ReadCall` 계약으로 남긴다.
- 반환 기록과 입력은 복사하여 외부 mutation을 격리한다. 동시/재진입은 BUSY로 거부하고,
  비정상 관찰·중단·선택 중 상한 도달로 끝난 session은 다시 실행할 수 없다.
  남은 호출 기록을 보존하되 runner가 임의로 completion=true를 만들면 안 된다.

이는 **인메모리 조사 실행 코어**다. production의 DB 예약/복구/감사 경계를 대체하지 않는다.
주입 adapter가 read-only이고 실제 hard timeout을 집행한다는 검증도 실제 연결 단계에 남는다.
이 모듈 자체는 callback의 강제 종료, 파일/artifact 발급, DB 또는 외부 서비스 호출을 하지 않는다.

## 아직 증명하지 않는 것

- inventory의 실제 DB 재조회, CF-6 상류/CF-7 정상 형제/CF-8 이력 drift를 포함한 실제
  CF 8종 fixture/oracle의 적합성 및 각 입력 파일 SHA 검증.
- provider 호출·selector 결정별 실행 trace의 진위, 32건의 실제 실행, latency/token 실측.
  단위 테스트의 CF ID와 model 이름은 계약 검사용 가짜 입력이며 실험 결과가 아니다.
- revision의 clean main 여부·이미지 label, 데이터 반출 승인, robustness·delivery,
  receipt/seal·immutable 게시·production 전환의 4축 검증.

다음 단위에서 실제 runner/기록·CF fixture를 연결하고 독립 검증을 추가한다.
실행 코드가 완성되어도 최종 merged clean main R과 **별도 LLM 데이터 반출 승인** 전에는
32회 실실행을 하지 않는다. SMTP 7통 승인 역시 별개다.
