# U10 비교 결과 오프라인 계약 — V5-C-7.1

담당 방대혁(C). 계획 v59의 **부분 구현**이다. 32 attempt의 구조와 판정 재계산 및
고정 정책/공통 읽기와 32건 메모리 배치 실행 코어를 제공한다.
실제 CF 데이터·provider 연결·운영 전환 Gate는 아니다.
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

## ReAct 정책 연결

`u10_react_execution.execute_react_policy()`는 기존 `react.guard_selection()`·
`resolve_call()`·trace 생성 함수를 재사용하고, 허용된 읽기만 `ReadSession`으로 실행한다.
실제 selector와 read adapter, 관찰에서 문맥을 재구성하는 `build_context`는 필수 주입 포트다.
모듈 import는 config/provider를 불러오지 않으며 실행 진입 시 기존 ReAct 모듈만 지연 import한다.

- selector는 후보 토큰만 선택하며, history의 incident-derived `internal_context`는 request와
  분리해 전달한다. 재시도마다 이 문맥도 복사하여 adapter 변경으로 다음 호출이 달라지지 않는다.
- 실제 호출 이력으로 성공한 대상/동일 문서 query 재조회를 거부한다. 가드 거부 2회·selector
  호출 10회(구조 보정 재시도 포함)·읽기 8회/동일 도구 4회를 각각 제한한다.
- build_context의 남은 예산은 신뢰하지 않고 재계산한다. run/lot/chamber/alarm identity 변경이나
  이미 발급된 후보 token의 대상 변경을 거부한다. 새 후보의 사실상 정당성은 후속 snapshot adapter가
  검증해야 한다. 인접 FDC의 방향·형제 chamber·현재 계측 scope도 inventory와 대조한다.
- selector model/prompt를 검사하고 호출별 token·latency를 독립 집계한다. trace는 화면 호환
  이벤트이므로 token을 trace 이벤트에서 중복 합산하지 않는다. provider usage가 없는 오류는
  `SelectorMeasurement.usage=None`으로 보존하며 `measured_selector_calls()`가
  `METRIC_PRECONDITION_INVALID`로 거부한다(0 token 대체 금지).
- 반환값은 조사 calls/selector measurement/trace/stop reason뿐이다. 가설·조치·completion을
  만들지 않는다. 특히 동일 도구 cap 때문에 마지막 실패의 retry를 수행하지 못한 조사나
  불완전한 측정 결과를 caller가 임의로 성공 artifact로 바꾸면 안 된다.

관찰 DTO 기반 context builder와 읽기 어댑터는 아래 모듈로 연결한다. 실제 CF snapshot과
provider 실행은 아직 미연결이며 회귀는 실 LLM 관측·DB snapshot 재조회가 아니다.

## Tool DTO 기반 관찰 문맥

`u10_observations.ObservationContext`는 검증된 `ResolvedIncidentRoute`와 현재 FDC 대상 ID,
문서 model code를 받는다. 기존 `build_initial_candidates`·`refresh_history_candidates`·
`build_context`를 재사용하며 임의의 관찰 문자열을 입력받지 않는다.

1. 읽기 어댑터는 외부 조회 **전에** `authorize(tool, request, internal)`를 호출한다.
   FDC/설비/현재 계측의 대상, 관찰된 parameter의 history 후보·cutoff·n_lots=3·내부 lot/scope,
   문서 model/query 경계를 검사한다. history는 canonical JSON을 대조해 `True == 1` 우회도 막는다.
2. 실제 Tool DTO를 받으면 `record(...)`로 형식·요청 대상과의 일치 여부를 검사한다.
   성공만 관찰에 추가하고 실패는 제외한다. FDC lot/lot_hist/chamber/step, 설비의 graph revision·
   model·형제 집합, history의 현재 lot/scope, 계측 lot/step, 문서 model을 대조한다.
3. `build_context()`가 관찰 요약과 후보를 재계산한다. FDC 성공 후에만 현재 history 후보가,
   설비 성공으로 형제가 확인된 뒤에만 sibling history 후보가 생긴다. 완전히 같은 성공 DTO는
   중복 추가하지 않고, 입력/반환을 복사하여 외부 변경이 내부 문맥을 바꾸지 않게 한다.

`execute_react_policy(..., state.build_context, ..., invoke, ...)`에 연결하는 회귀를 제공한다.
예산은 여전히 정책 실행기가 덮어쓰며 이 builder가 집행하지 않는다. 이 모듈은 DTO의 실제
DB 출처/수치의 진위를 증명하지 않고, 조회·timeout·evidence ID 발급·가설 생성도 하지 않는다.
아래 읽기 어댑터가 `authorize → 조회 → 검증 → record` 순서를 집행한다.

## 읽기 어댑터 연결

`u10_read_adapter.ReadAdapter`는 `ObservationContext`, 읽기 포트 5개, caller 소유 deadline
runner를 필수로 받는다. 기본 projector는 아래 `project_read_evidence()`이며
테스트용 projector 주입은 유지한다. 전송 포트는 없다.
`ReadPorts.production()`은 기존 `ToolBoundary.production()`을 인자 없이 호출해 읽기 포트만
복사하며 send-action factory를 구성하지 않는다. module import 자체는 factory를 실행하지 않는다.

- 외부 호출 전에 scope를 확인하고 history 내부 문맥만 `_context` 키로 도구 입력에 결합한다.
  고정 정책과 ReAct 모두 문서 `model_code`를 명시해야 한다. Fixed query 입력과 policy spec에도
  snapshot model filter를 추가했으므로 `fixed_policy_sha256`은 새 코드에서 다시 고정해야 한다.
  historical v1/v2와 아직 발급하지 않은 U10 artifact를 혼동하지 않는다.
- `DeadlineRunner.call(..., seconds=8)`의 worker에는 읽기 함수만 전달한다. DTO 검증·projection·
  관찰 저장은 반환 후 caller에서 수행하므로 timeout 뒤의 늦은 worker 결과가 관찰을 갱신하지 않는다.
  server-side hard timeout은 기존 읽기 도구가 소유하며 이 어댑터가 worker를 강제 종료하지 않는다.
- 포화는 ERROR, timeout은 TIMEOUT, 의존성 예외는 ERROR로 기록하며 원문을 노출하지 않는다.
  실패 DTO도 TIMEOUT prefix를 구분하고 빈 evidence만 반환한다. 재시도/예산은 `ReadSession`이 소유한다.
- `validate_result()`는 관찰을 저장하지 않고 DTO/identity를 검사한다. 그 뒤 projector의 ID
  canonical 정렬·SHA를 검증하고 **마지막에만** `record()`한다. DTO/scope/projection 오류 시
  성공 관찰은 0건이다. 입력·내부 문맥·projector/반환 값의 mutation과 어댑터 재진입을 격리한다.

이 어댑터는 주입 projector의 ID가 실제 oracle 의미에 맞는지 증명하지 않는다. 현재 테스트는
fake read 포트/실 Tool DTO/로컬 ThreadDeadlineRunner로 연결 순서를 검증하며 공용 DB에는 접속하지 않는다.

## 실제 DTO의 근거 ID 투영

`u10_evidence.py`는 U10 전용 `u10-evidence-v1` 규칙이다. ID는 `namespace:exact_id`로
표현하고 정렬·중복 제거 후 canonical SHA를 계산한다. 예를 들어 `CHUNK:X`와 `PARAMETER:X`는
다른 근거다. 실제 ID를 hash로 대체하거나 문자열 내용에서 ID를 추측하지 않는다.
기존 발급 v1/v2의 raw ID 계약과 historical artifact는 바꾸지 않는다.

| 입력 | 발급되는 근거 |
|---|---|
| 검증된 초기 route | member alarm의 `ALARM:to_token()` 및 graph의 실제 `RELATION:relation_id` |
| 성공 FDC DTO | `LOT_HIST:wafer.lot_hist_id`, `PARAMETER:parameter_id` |
| 성공 문서 검색 DTO | `CHUNK:chunk_id` — 문서 제목·본문·document_id는 제외 |
| compact 설비·이력·계측 DTO | 빈 ID 목록 — 현 가설 v3에 대응하는 독립 인용 ID가 없음 |
| 최종 Hypothesis | supporting 5종 + origin basis + parameter findings의 ID 합집합 |

설비 parameter metadata는 FDC 측정값이 아니며 compact DTO에는 relation ID가 없다.
후보 route의 lot_hist를 조회 성공으로 간주하지 않는다. 이력·계측은 **읽기 성공·compared에는
집계되지만 독립 citation recall에는 집계되지 않는 한계**가 있다. 이를 가상의 이력/계측 ID로
메우지 않는다. CF-7/8 oracle의 실제 근거 적합성은 후속 fixture 작업에서 별도로 검증해야 한다.

`ObservationContext.initial_evidence_ids()`는 저장된 초기 route만 투영한다. 성공 읽기는
`ReadAdapter` 기본 projector가 같은 규칙으로 변환한다. `project_hypothesis_citations()`는
허용 근거와 교집합을 취하지 않아 미지원 인용이 평가 전에 사라지지 않는다. 이 함수는 DTO 형식
검사이지 가설 생성·진위 검증기가 아니다. production 가설 검증과 최종 evaluator는 여전히 필요하다.

`projection_spec()`/`projection_sha256()`은 새 규칙의 canonical 결속 입력을 제공한다.
향후 실제 benchmark runner는 이 규칙을 tool contract SHA에 포함하고 초기·읽기·인용·oracle
모두 같은 인코딩을 써야 한다. **현재 오프라인 validator가 이 source SHA 결속까지 증명하는 것은
아니다.** 아직 발급하지 않은 U10 runner의 잔여 사항이며 단위 테스트의 fake raw-ID 계약과 구분한다.

## 관찰에서 가설 v3 생성까지

`ObservationContext.hypothesis_inputs()`는 성공 DTO와 성공 입력에서만 기존 가설 생성 함수의
인자를 만든다. 현재 FDC·설비·문서·이력·계측·초기 route를 deep copy하고 oracle/label을
별도 인자로 받지 않는다. 실패 조회는 성공 목록에 넣지 않는다. 이 목록은 조사 상태 계산용이며
중복/재시도를 포함한 호출 횟수의 정본은 계속 `ReadSession.calls`다.

문서 병합은 production graph의 `_merge_document_results()`를 그대로 재사용한다
(동일 chunk 최고 점수·점수 내림차순·최대 10개). 이력/계측 DTO와 성공 호출은 기존
`InvestigationEvidence`로 전달한다. 실제 DB reservation을 저장했다고 주장하지 않는다.

`u10_hypothesis.execute_hypothesis()`는 읽기 루프 종료 후 호출한다. generator를 필수로 받으며
`production_hypothesis_port()`가 기존 `hypothesis.production_port()`를 반환한다. import나
factory 선택 자체가 provider를 호출하지는 않는다. 양쪽 정책이 같은 generator/model/seed를
사용하도록 실제 runner가 결속해야 한다.

- 성공 FDC가 하나도 없으면 generator 호출 없이 `HYPOTHESIS_EVIDENCE_INSUFFICIENT`를 반환한다.
- 수정 재시도와 사용량 합산은 기존 가설 v3가 소유한다. 어댑터는 재시도를 추가하지 않는다.
- 성공·실패 모두 실제 usage의 model 및 `agent-hypothesis-v3-ko1`을 대조한다.
  usage 미관측은 `None`으로 보존하며 `measured_tokens()`는 이를 0으로 바꾸지 않고
  `METRIC_PRECONDITION_INVALID`로 거부한다. timeout 전 관측된 부분 usage는 보존한다.
- 안전한 오류 코드와 generator 구간 monotonic latency만 반환한다. 예외 원문은 저장하지 않는다.
  seed는 음수/boolean을 거부한다. 어댑터 자체에 별도 provider timeout/강제 종료 기능은 없다.
- 성공 결과는 실제 `HypothesisOutcome`을 재검증하고 v3 `origin_assessment` 및 코드 계산
  `compared`와 대조한 뒤 namespace citation을 투영한다. generator 입력/결과 변조는 내부
  관찰 문맥으로 전파되지 않는다.

이때의 `compared`는 **production 가설의 route 기반 matrix**다. U10 artifact의
`candidate_inventory` 기반 matrix·attempt completion·action·available/required 근거 집합
검증을 대체하지 않는다. 어댑터에 주입 가능한 테스트 generator가 실제 LLM이라는 보증도 아니다.
model/config/seed/SHA 대조는 아래 단일 attempt/배치 코어에서 수행한다.
실제 provider·승인 검증기·출처 SHA 결속은 실실행 runner의 잔여다.

## ReAct 단일 attempt 실행 연결

`u10_attempt.execute_react_attempt()`는 새 `ObservationContext`에서 다음 순서로 한 건을
실행한다: snapshot/초기 근거 대조 → ReAct 읽기 → 가설 v3 → 순수 규칙 조치 → 외부 효과 관측
→ `Attempt` 조립 → 기존 `_check_attempt()` 검증. 전송·DB 저장·artifact 발급은 하지 않는다.

- fixture와 LLM 설정을 재검증/복사하고 CF-1~8 및 attempt 1/2만 허용한다. 이미 성공 관찰이
  있는 context는 재사용하지 않는다. fixture의 oracle은 selector/generator에 전달하지 않는다.
- selector와 hypothesis에 같은 seed를 전달한다. 모델·프롬프트는 각 기존 실행 어댑터가 검사한다.
  검증된 snapshot SHA 인자는 fixture와 대조하지만 **실 DB snapshot의 진위는 caller 책임**이다.
- 성공 읽기+초기 근거로 available을 계산하고 actual 호출/usage/latency에서 집계를 만든다.
  가설 생성 전 selector usage 누락, 실제 가설 호출 뒤 usage 누락은 발급 가능한 record로
  바꾸지 않는다. 성공 FDC가 없어 가설 호출 자체를 하지 않은 경우만 hypothesis 목록이 빈다.
- `derive_compared()`를 builder와 verifier가 공유한다. production 가설 matrix를 복사하지 않고
  factual inventory의 가용성과 성공 read slot으로 재계산한다.
- completion은 가설 성공과 정상 조사 종료(`LLM_STOP`, 예산/guard/step 상한), 미완료 retry 없음으로
  계산한다. 동일 도구 4번째 실패 뒤 retry를 못 한 경우(<8 read)는 가설 성공이어도 false다.
  timeout/실패의 부분 usage는 남긴다. 조치는 기존 `decision.decide_action(route)`이며 send가 아니다.
- `observe_effects()`는 필수이며 실행 **뒤에** 받은 safety/외부 효과 값을 그대로 보존한다.
  nonzero는 숨기지 않고 단일 attempt에 남긴다. offline evaluator는 부정 판정하며,
  아래 배치 코어는 격리 위반으로 즉시 중단한다. 관측 함수의 진위를 자체 보증하지 않으며
  실제 observer/격리 sandbox 연결은 실실행 runner 책임이다.
- ReAct의 교차 순서 번호는 CF별 첫 attempt=2, 둘째=3(다음 CF는 +4)으로 고정한다.
  이는 번호 결속일 뿐, fixed 쪽 실행이나 실제 교차 순서를 실행했다는 증명이 아니다.

반환물은 메모리의 `ReactAttemptResult(attempt, reads, hypothesis)`다. 아래 배치 코어가
32건 interleave를 조립한다. CF inventory 재조회·source/tool contract SHA·실제 승인/receipt/
이미지 결속은 남아 있다. 테스트 read/selector/generator/observer를 쓰는 검증은 실험 성과가 아니다.

## 고정 정책 단일 attempt 실행 연결

`u10_attempt.execute_fixed_attempt()`는 같은 snapshot·초기 근거·fresh context 검사와
가설/조치/외부 효과 관측/지표 조립 함수를 ReAct와 공유한다. 읽기만 기존
`execute_fixed_policy()`로 수행하고 selector 인자·호출 경로는 없다. selector 목록·횟수·
token·latency는 0이며 가설은 ReAct와 같은 seed/model을 사용한다.

- 비문서 `bound_inputs`와 문서 `DocumentContext`는 실제 snapshot에 결속된 입력을 caller가
  제공해야 한다. 새 코드는 그 데이터의 DB 진위를 증명하지 않는다.
- 첫 읽기 전에 slot 집합과 현재/인접 FDC 관계, 현재/형제 history chamber, 설비·계측 대상을
  검사한다. 같은 Tool을 쓰는 slot의 target 교환으로 `compared`를 잘못 계산하지 못하게 한다.
- 이력 내부 `_context`는 `ObservationContext.resolve_history_context()`가 **관측된** 후보와
  exact canonical request를 대조해 production `react.resolve_call()`로 생성한다.
  호출자 `_context`, 미관측 parameter, boolean/int 우회는 수용하지 않는다. FDC가 실패하여
  관측 후보가 없으면 history port를 호출하지 않고 공통 read runner가 ERROR/재시도로 기록한다.
- 문서 query 2개와 model filter는 기존 코드 소유 함수로 만든다. snapshot model과 다른
  문서 context는 첫 읽기 전에 거부한다. 문서 query를 bound_inputs로 주입할 수 없다.
- candidate가 없는 slot만 NO_CANDIDATE로 skip한다. ERROR/TIMEOUT 공통 1회 재시도와
  read 8회 예산을 그대로 쓰며 예산 소진 후 미실행 문서를 추가 호출하지 않는다.
- fixed 교차 번호는 CF별 첫 attempt=1, 둘째=4(+4씩)다. 두 policy 번호가 맞아도 실제
  32건 interleave를 수행했다는 증명은 아니며, 아래 배치 코어가 실제 호출 순서를 소유한다.

반환물은 메모리 `FixedAttemptResult(attempt, calls, skipped_slots, hypothesis)`다.
공유 조립 경계의 ReAct 회귀와 고정 정책의 문서/현재·형제 history/예산 경계를 함께 검증한다.
실 DB·LLM·observer 연결, 데이터 반출 승인, revision/image/receipt·immutable artifact 발급은
여전히 후속 실행기에서 처리해야 한다.

## 32건 메모리 배치 실행 코어

`u10_batch.execute_batch()`는 CF-1~8 순서로 fixture마다 fixed→ReAct, ReAct→fixed를
직렬 실행한다. 기존 두 단일 attempt 경로를 호출하며 전체 attempt 재시도·선별·대체는 없다.

- 입력 benchmark/LLM 설정을 재검증하고 복사한다. 40자리 revision 형식, caller가 별도로
  제공한 benchmark canonical SHA와 tool contract SHA, 실제 코드의 fixed-policy SHA를
  첫 승인 확인 전에 대조한다. revision의 clean main 여부와 tool/projection 소스 진위를
  확인하는 기능은 아니며 실제 출처 검증기는 별도로 필요하다.
- 매 attempt의 자원 준비 **전에** 필수 `authorize(BatchBinding)`를 호출한다. 정확한
  `True`만 허용하며, 만료·철회·데이터 반출 범위를 확인하는 실제 검증기는 caller 책임이다.
  기본 승인 구현은 없고 구현 공수 승인을 데이터 반출 승인으로 사용하지 않는다.
- `prepare(AttemptKey)`는 fixture ID·attempt 번호·policy·실행 순서만 받는다.
  oracle/inventory/required ID를 포트 팩토리에 전달하지 않는다. 자원 생성·폐기 및 예외를
  삼키지 않는 context manager는 caller가 제공한다. 문맥 객체의 동일성 재사용을 거부하고,
  각 snapshot/초기 근거와 fixed 입력·ReAct selector 요구사항은 기존 단일 경계에서 검사한다.
- 한 scope의 정리가 완료되어야 다음 승인/준비를 시작한다. 준비·실행·정리 오류는 전파하고
  완전한 artifact를 반환하지 않는다. 실제 외부 효과 또는 safety 비제로도 즉시 중단한다.
  이는 실행 격리 위반 시 후속 호출을 막는 정책이며 기존 offline 부정 판정 규칙 변경은 아니다.
- usage가 측정된 가설 실패/미완료는 그대로 32건에 포함하여 부정 판정한다. 누락 usage를
  0으로 채우거나 실패 attempt를 새 것으로 교체하지 않는다. 각 반환 행의 키·순서·설정 SHA와
  기존 `_check_attempt()`를 대조하고, 32건 조립 뒤 별도의 `validate_artifact()`로
  population·교차 순서·지표·판정을 재계산한다. 판정 함수를 위조한 회귀도 최종 단계에서 거부한다.

반환물은 **메모리 `Artifact` DTO**이며 파일 발급/게시 CLI가 아니다. 신규 배치 테스트는
한 DTO 시나리오를 CF ID 8개에 복사한 합성 입력과 테스트 승인/read/selector/generator/observer를
사용한다. 32건 호출 순서·격리·재검증의 검증이지 CF 8종 실제 시나리오나 LLM 관측 결과가 아니다.

14차 리뷰 권장 회귀는 반환 행의 LLM SHA 변조 시 첫 scope 정리 후 중단(1건), 두 모델의
65자/앞뒤 공백을 첫 승인 전에 거부(6건), dict/None 환경을 정리 후 거부(2건)를 추가했다.
배치 실행 코드는 변경하지 않았으며 M8/M9/M10 검사 제거 변이를 탐지한다.

## Inventory 읽기 재계산 경계

`u10_inventory.read_inventory()`는 caller가 제공한 연결에서 **단일 읽기 SQL**을 실행한다.
연결 생성·commit·close·DDL·외부 provider 호출은 하지 않는다. 사전 검증용 별도 문맥으로
production 후보 생성 규칙을 재사용하며, 조사 중인 Agent의 성공 관찰이나 읽기 예산을 채우지 않는다.

- route의 현재/상류/하류 후보 ID를 실제 `lot_history`의 lot·wafer·chamber·step과 대조한다.
  누락/교환은 거부하고 distinct wafer 수로 current/adjacent를 계산한다. 양쪽 인접 방향이
  있으면 fixed 정책처럼 상류를 우선한다. 이 조회는 **제공된 route 후보 집합**을 검증하며
  route 생성 자체나 후보 모집단의 완전성을 증명하지는 않는다.
- 과거 LOT은 현재 chamber·step에서 LOT별 최신 track-in이 현재 LOT의 최초 track-in보다
  **엄격히 이전**인 것만, 현재 LOT 제외·최신순·최대 3건으로 집계한다. cutoff는 후보 WAFER의
  시각을 믿지 않고 DB의 현재 LOT 전체에서 다시 구한다. prior 0도 history AVAILABLE이다.
- 계측은 현재 lot·step의 실제 `metrology_id` 행 수다. alarm_result·측정값·fault_code는
  SELECT하지 않는다. 실패/timeout은 예외로 전파하고 0건으로 바꾸지 않는다.
- sibling은 제공된 현재 chamber의 graph 근거에서 얻는다. 자기 자신이나 2개 이상 후보는
  거부하며 임의 tie-break를 추가하지 않는다(최종 graph의 chamber당 형제 1개 계약).
  문서는 같은 모델 범위의 성공 검색 DTO를 요구한다. 성공한 빈 결과는 조회 가능한 도구이며,
  실패 DTO·다른 모델 hit·잘못된 타입은 SQL 전에 거부한다. 실제 검색/graph 조회는 하지 않는다.
- `verify_fixture_inventory()`는 선언된 inventory/expected_compared와 재계산 결과를 대조하며
  불일치를 보정하지 않고 `U10_INVENTORY_MISMATCH`로 거부한다. oracle을 SQL에 전달하거나
  변경하지 않는다. snapshot SHA·초기 evidence·oracle 적합성 검사기를 대체하지 않는다.

이 경계는 **아직 `execute_batch.prepare`나 live CLI에 자동 연결되지 않았다.** 후속 runner가
격리/read-only DB 신원·route/graph/document probe의 같은 snapshot 출처를 검증하고 배치의
실제 준비 경로에 연결해야 한다. 실제 CF-1~8 snapshot 파일과 oracle도 미발급이다.
회귀는 최소 스키마의 메모리 SQLite에서 SQL을 실행하고 PostgreSQL dialect 컴파일을 확인한다.
실 PostgreSQL 타입·권한·격리/동시성 검증이나 실제 CF 8종의 관측 증거로 주장하지 않는다.

15차 권장 회귀는 CURRENT step과 graph step 불일치, 두 CURRENT wafer의 step 불일치가
각각 SQL 전에 `U10_INVENTORY_SCOPE_INVALID`로 차단됨을 확인한다. 두 번째 회귀는 첫
wafer의 step을 graph와 일치시켜 step 유일성 가드만 제거해도 탐지되도록 구성했다.

## 명시적 revision과 로컬 실행 기준 검사

`u10_revision.read_revision_identity(repository, revision)`는 명시한 저장소 **루트**와 전체
commit ID만 사용한다. 하위 디렉터리를 루트로 간주하지 않으며 `HEAD`·branch·짧은 SHA를
revision 입력으로 허용하지 않는다. 출력은 canonical repository root, evaluated revision,
실제 Git object format, 그 commit의 `backend`·`frontend`·`deploy` tree OID다. 경로가
없거나 blob/gitlink이면 정상 tree로 반환하지 않는다. 파일 SHA-256과 Git object ID를 구별한다.

- `verify_execution_revision(repository, expected_revision)`는 현재 artifact 계약의 40자리
  lowercase revision R을 받는다. **local main · HEAD=R · Git clean 상태**를 tree 조회 전후로
  확인하고 다르면 거부한다. tree 조회 중 branch/HEAD/dirty 변경을 주입한 회귀도 거부한다.
- Git 명령은 모두 읽기이며 pull/fetch/checkout/commit을 하지 않는다. 외부 `GIT_DIR`·
  `GIT_WORK_TREE`·index/config/trace 환경 재지정을 제외하고, optional lock·replace object·
  lazy fetch·fsmonitor를 억제한다. 명령 실패/timeout은 stderr·환경값 없이 고정 코드로 반환한다.
- 저수준 identity reader는 실제 SHA-1/256 object format에 맞는 전체 OID를 읽는다.
  실행 guard는 **현행 U10 Artifact의 R=40자리 계약**을 유지하므로 SHA-256 저장소의 64자리
  revision을 실험 실행 revision으로 수용하지 않는다. 기존 artifact schema를 확장하지 않았다.

이는 **시점 관측**이며 workspace lock, 원격 merge/CI PASS, main pull 완료를 증명하지 않는다.
Git status의 통상 ignored-file 규칙을 따르므로 ignored cache의 바이트나 실행 중 import된
모듈까지 증명하지 않는다. caller가 실실행 동안 저장소를 고정하고 사용 시 재검증해야 한다.
이미지 label/tree는 아래 검사기가 담당하며 receipt·`execute_batch`/live CLI 연결은 남아 있다. 현재 feature
브랜치에서 이 검사기를 구현한 것이 최종 R을 고정했거나 U10 실행을 승인했다는 뜻은 아니다.
회귀의 commit/checkout/replace는 pytest 임시 저장소에서만 수행하며 프로젝트 Git은 읽기만 한다.

16차 권장 회귀는 SHA-1 저장소에 64자 revision, SHA-256 저장소에 40자 revision을 넣으면
Git 객체 조회 실패로 뭉개지 않고 `U10_GIT_OBJECT_FORMAT_INVALID`로 거부함을 확인한다.

## Profile별 이미지·컨테이너 결속 경계

`u10_images.verify_image_bindings()`는 계획의 preflight 검사 **4(revision)·5(image label)·
6(running container)** 경계다. 전체 preflight PASS나 `allowed_actions`를 발급하지 않는다.

- profile 기대값은 코드 소유다. `production_level2`/`production_level3`는 `bistel-team`의
  backend·frontend, `e2e_level3`는 `bistel-team-e2e`의 backend·frontend·runner가 exact 집합이다.
  runner의 실제 Compose service label은 `e2e-runner`다. 이름은 `cm52_common.sh`와 맞춘다.
- caller는 독립 검증된 receipt의 R/tree OID와 빌드에서 고정한 역할별 image ID, 준비 단계에서
  얻은 역할별 container ID를 제공해야 한다. 현재 container가 말하는 image ID를 기대값으로
  재사용하지 않는다. image `sha256:<64hex>`·container `<64hex>`만 허용하고 tag/name은 거부한다.
- receipt R의 backend/frontend/deploy tree를 명시 root에서 다시 읽어 먼저 대조한다.
  각 image의 ID와 `org.opencontainers.image.revision == R`을 검사하고 image label의 revision으로
  tree를 다시 조회·대조한다. HEAD를 대신 쓰지 않으며 같은 tree여도 다른 label R은 허용하지 않는다.
- 각 container의 ID·`.Image`·Compose project/service를 기대값과 대조한다. 상태는 정확한
  `Running=true`, `Status=running`, `Paused=false`, `Restarting=false`여야 한다. E2E runner가
  backend image를 공유해도 **container ID는 달라야 하며 역할별로 별도 검사**한다.
- 모든 역할 검사 뒤 container를 한 번 더 조회하여 binding/state와 StartedAt을 대조한다.
  조회 사이 재시작은 거부하지만 이는 atomic snapshot이나 이후 변경을 막는 lock이 아니다.
- 기본 Docker 포트는 ID 기반 `docker image/container inspect --format`만 실행하며, 필요한
  ID·revision·Compose label 두 개·상태·StartedAt만 JSON으로 투영한다. Config.Env·mount·전체
  label은 읽기 출력에 넣지 않는다. 중복 JSON 키·잘못된 응답·16KiB 초과·명령 실패·timeout은
  고정 오류 코드로 거부하며 stdout/stderr 원문을 보고서에 남기지 않는다.

반환물은 기존 `RuntimeImage`/`RuntimeContainer`를 재사용하는 메모리 DTO다. 이미지 빌드·pull·
컨테이너 create/start/stop·provider 호출·파일 발급을 하지 않는다. 테스트는 합성 inspect 응답과
Git 포트를 사용하며, Docker subprocess도 대체하여 명령의 읽기 전용/최소 투영을 확인한다.
**실제 Docker daemon·Go template 실실행·이미지 빌드 증거는 아니다.** 기존 Stage2에는 아직
지속 실행 runner를 이 검사기에 넘기는 연결이 없으며 후속 prepare/lifecycle 구현이 필요하다.
receipt 출처/무결성·DB identity/effective-env·readiness·robustness/delivery·live preflight 연결은
각 소유 검증기를 통해 추가해야 한다. 이 검사기 통과만으로 production을 활성화할 수 없다.

17차 권장 회귀는 **유효 JSON 16KiB 초과**를 넣어 cap 자체를 검증하고, 공개
`docker_inspect()`에 image tag/container name/잘못된 kind/non-string ID를 직접 전달하여
subprocess 이전 거부를 확인한다. 외부 검사기의 선행 가드에 의존하지 않는다.

## Readiness 관측 경계

`u10_readiness.verify_readiness()`는 계획의 preflight 검사 **7**만 담당한다.
기본 포트는 순차 Compose 배포가 공유하는 `http://127.0.0.1:8080`의 코드 소유 경로를 사용한다.

- `/api/health/ready` HTTP 200 응답을 기존 `ReadinessResponse`로 strict 검증한다.
  epoch는 `fdc_final_20260818`, 상태는 READY, check는 postgresql_runtime·reference_migration·
  neo4j·rag·n8n·kafka **정확히 6종 PASS**여야 한다. 누락/추가 필드·상태 불일치·
  부적절한 reason·latency 타입 강제 변환을 허용하지 않는다. HTTP 200이어도 NOT_READY는 실패다.
- 이후 frontend `/`와 `/api/health` 각각 HTTP 200을 확인한다. 첫 실패에서 중단하며 재시도하지 않는다.
  production backend는 host port가 없으므로 nginx의 `/api/` prefix 제거 경로를 사용한다.
  `api_status`는 `/api/health`의 상태다. bare `/api`는 backend `/`로 전달되어 404이므로
  검사 경로로 허용하지 않는다(18차 필수 1·계획 v59). 기존 Stage2 경로와 통일하며
  backend에 새 root API를 추가하거나 nginx를 변경하지 않는다.
- 기본 HTTP 포트는 GET만 사용하고 임의 URL/path·환경 proxy·redirect를 허용하지 않는다.
  HTTP I/O timeout은 15초(전체 검사 wall-clock deadline이 아님)이며 readiness body는
  streaming 16KiB 제한을 적용한다. 주입 포트 응답도 타입·크기를 다시 검증한다.
  frontend HTML·liveness·오류 응답 body는 읽거나 보존하지 않고 response/client를 닫는다.
  비정상 JSON·중복 키·비유한 수·전송 오류는 원문/URL 없는 고정 코드로 거부한다.
- 반환물은 gateway origin·readiness DTO·frontend/API status의 메모리 관측값뿐이다.
  profile·container ID·revision·checked_at은 이 단위에서 결속하거나 주장하지 않는다.
  후속 orchestrator가 image/DB identity 검사와 시각을 묶어야 한다. 관측 간 atomic snapshot이나
  배포 교체 방지 lock은 아니며 이 결과만으로 overall integrity/allowed_actions를 발급하지 않는다.

회귀는 합성 JSON과 `httpx.MockTransport`를 사용한다. 실제 localhost HTTP·Docker·DB·n8n·
Kafka·LLM 호출을 수행하지 않는다. import 시 provider/config/DB/orchestrator를 로드하지 않는다.
검사 8은 아래 runtime readback 단위가 담당한다. 1~8 통합 preflight CLI, Stage2 및 실제
runner/lifecycle 연결은 남아 있다. 실환경 6-check PASS 증거는 아직 아니다.

## Effective-env·DB identity 관측 경계

`u10_runtime.verify_runtime_readbacks()`는 계획 v59의 preflight 검사 **8** 내부 단위다.
기존 `runtime_readback.PROFILES`와 `validate_readback()`을 재사용하며 기대 설정을 CLI로 받지 않는다.

- production_level2/3는 backend 1종, e2e_level3는 backend·runner 2종의 정확한 container ID map을
  받는다. frontend에는 Python runtime/DB 연결이 없으므로 검사 8에서 exec하지 않는다. frontend
  image/running/readiness 검사는 앞의 별도 단위가 계속 담당한다. ID는 64자리 hex이며 name/tag,
  누락·추가 role, 같은 container의 역할 중복을 읽기 전에 거부한다.
- 기본 포트는 `docker exec <ID> python -B /workspace/backend/scripts/read_agent_runtime.py
  --profile <profile>`로 기존 스크립트를 실행한다. shell·env override·Compose service 이름 재탐색을
  사용하지 않는다. 스크립트는 해당 container 환경의 config와 DB `current_database(), current_user`를
  조회한다. HTTP 공개 응답/업무 API는 변경하지 않는다. 30초 subprocess timeout, stdout 16KiB
  사후 cap, JSON object·중복 키 검증을 적용하고 stdout/stderr 원문은 오류에 포함하지 않는다.
- host는 `PASS` 선언만 믿지 않고 schema/profile/필수 필드/extra key/type을 엄격히 검사한 뒤
  database·user·level·enabled·budget을 코드 기대값과 다시 대조한다. 공통 validator도
  `3.0 == 3`, `1 == True`, float budget의 Python 동등성 우회를 거부한다.
- production Level 3는 별도 검증된 `expected_attempt_id`를 필수로 받아 `demo_ack`와 exact
  대조하고 `ack_matches_receipt is True`도 요구한다. 다른 profile은 expected attempt 입력을
  거부한다. **legacy receipt_matches는 attempt.json의 attempt 필드 일치만 확인**하므로 이 값은
  full receipt/robustness/delivery 무결성 증명이 아니다. 그 검증기와 3-Gate 조립은 별도로 필요하다.
- config/DB/React import는 `collect_readback()` 호출 때로 지연한다. host에서 검사 함수를
  import하거나 payload를 검증하는 것만으로 provider/DB를 초기화하지 않는다.

caller는 **검증된 image bindings와 동일한 container ID**, 독립 검증한 attempt를 제공해야 한다.
이 단위는 ID와 관측 payload를 메모리 DTO로 돌려주며 image/project/revision·시각·PID·DB server
system_identifier를 증명하지 않는다. exec는 새 Python process이므로 기존 장기 실행 process의
메모리를 읽는 것도 아니다. readback 전후 배포 drift 확인은 아래 조립기가 담당하고,
1~8 최종 wrapper와 실운영 연결은 남아 있다. `integrity`·`allowed_actions`·SMTP/LLM 승인·
파일 발급·workload 실행은 하지 않는다.
회귀는 합성 readback/subprocess와 실제 코드 validator를 사용하며 Docker/DB에는 접속하지 않는다.

## 검사 4~8 관측 구간 조립

`u10_deployment.observe_deployment()`는 별도 검사기를 **image/revision 결속 → runtime readback →
readiness → image/revision 재검사** 순으로 연결한다. 19차 리뷰의 동일 container ID 전달과
readback 전후 drift 확인 요구를 구현한다. **전체 `u10_preflight` 또는 4축 판정기는 아니다.**

- caller가 독립 고정한 image/container ID map을 최초 IO 전에 복사한다. runtime에는 이 map의
  backend(및 E2E runner)만 전달하고, frontend는 image/readiness 검사에서 유지한다. 주입 callback이
  caller의 원래 map을 바꿔도 뒤의 조회 대상이 바뀌지 않는다.
- 두 image 검사 모두 같은 repository·R·tree·image ID·container ID를 사용한다. 기존 검사기의
  각 회차 내부 재조회도 유지한다. 회차 사이 시작 시각/결속이 바뀌면 `U10_DEPLOYMENT_DRIFT`,
  중지/ID/label 변경은 기존 검사기의 고정 오류로 거부한다. 첫 실패 뒤 다음 단계는 실행하지 않는다.
- profile과 `phase=pre_u9|post_start_pre_enable`, 관측 시작/완료 UTC 시각을 결과 DTO에 묶는다.
  naive/잘못된 시각·완료 시각 역행을 거부한다. phase는 관측 라벨이며 여기서 lifecycle 단계의
  허용 여부나 실행 권한을 판정하지 않는다. 기본 clock은 UTC wall clock이며 deadline/lock은 아니다.
- 테스트는 상위 검사기 자체를 대체하지 않는다. 실제 image/runtime/readiness 검증을 연결하고
  Git 조회·inspect·readback·HTTP만 합성 포트로 대체한다. 세 profile·두 phase의 exact 순서,
  backend/frontend/runner의 runtime·HTTP 중 재시작, 중지, 실패 시 후속 호출 0건을 검증한다.

이는 관측 구간의 **시점 대조**다. 검사 사이 변경 뒤 원상 복구, 종료 후 변경, gateway의 실제
published-port 소유 container, 장기 실행 process 메모리의 동일성까지 증명하지 않는다.
현재 반환값은 image/runtime/readiness DTO·profile/phase/시각이며 `head`·overall integrity·
agent_verdict·robustness·delivery_integrity·allowed_actions를 발급하지 않는다.
검사 1~3의 artifact/evaluation receipt 연결, 최종 4축 CLI, Stage2 lifecycle 연결이 남아 있다.
공용 배포·실서비스 조회·artifact 발급은 수행하지 않았다.

## 아직 증명하지 않는 것

13차 리뷰 보완 회귀는 비정상 selector 종료 뒤 가설 성공이어도 completion false(구조 오류·
timeout·dependency 3건), graph 목록에 현재 chamber가 잘못 포함돼도 SIBLING 거부(1건),
정상 binding 양성 대조 후 CURRENT/ADJACENT target 교환 거부(상류·하류 2건)를 검증한다.
각각 F5/O1/O3 가드 무력화 변이를 탐지하며, 이 테스트 fixture를 실제 DB snapshot으로 주장하지 않는다.

- inventory 재계산기의 실제 격리 PostgreSQL 연결, CF-6 상류/CF-7 정상 형제/CF-8 이력 drift를 포함한 실제
  CF 8종 fixture/oracle의 적합성 및 각 입력 파일 SHA 검증.
- provider 호출·selector 결정별 실행 trace의 진위, 32건의 실제 실행, latency/token 실측.
  단위 테스트의 CF ID와 model 이름은 계약 검사용 가짜 입력이며 실험 결과가 아니다.
- 최종 실실행 revision의 clean main·이미지 결속 검사기 live 연결, 데이터 반출 승인, robustness·delivery,
  receipt/seal·immutable 게시·production 전환의 4축 검증.

다음 단위에서 실제 runner/기록·CF fixture를 연결하고 독립 검증을 추가한다.
실행 코드가 완성되어도 최종 merged clean main R과 **별도 LLM 데이터 반출 승인** 전에는
32회 실실행을 하지 않는다. SMTP 7통 승인 역시 별개다.
