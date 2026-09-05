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
실제 실행과 model/config/seed/SHA 결속·승인 검증은 32-attempt runner의 잔여다.

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
  nonzero는 숨기지 않고 최종 32건 evaluator가 부정 판정하도록 남긴다. 관측 함수의 진위를
  자체 보증하지 않으며 실제 observer/격리 sandbox 연결은 batch runner 책임이다.
- ReAct의 교차 순서 번호는 CF별 첫 attempt=2, 둘째=3(다음 CF는 +4)으로 고정한다.
  이는 번호 결속일 뿐, fixed 쪽 실행이나 실제 교차 순서를 실행했다는 증명이 아니다.

반환물은 메모리의 `ReactAttemptResult(attempt, reads, hypothesis)`다. 32건 interleave/배치·
CF inventory 재조회·source/tool contract SHA·승인/receipt/이미지 결속은
아직 남아 있다. 단위 테스트는 테스트 read/selector/generator/observer를 사용하며 실험 성과가 아니다.

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
  32건 interleave를 수행했다는 증명은 아니며, 배치 실행기는 후속 작업이다.

반환물은 메모리 `FixedAttemptResult(attempt, calls, skipped_slots, hypothesis)`다.
공유 조립 경계의 ReAct 회귀와 고정 정책의 문서/현재·형제 history/예산 경계를 함께 검증한다.
실 DB·LLM·observer 연결, 데이터 반출 승인, revision/image/receipt·immutable artifact 발급은
여전히 후속 실행기에서 처리해야 한다.

## 아직 증명하지 않는 것

13차 리뷰 보완 회귀는 비정상 selector 종료 뒤 가설 성공이어도 completion false(구조 오류·
timeout·dependency 3건), graph 목록에 현재 chamber가 잘못 포함돼도 SIBLING 거부(1건),
정상 binding 양성 대조 후 CURRENT/ADJACENT target 교환 거부(상류·하류 2건)를 검증한다.
각각 F5/O1/O3 가드 무력화 변이를 탐지하며, 이 테스트 fixture를 실제 DB snapshot으로 주장하지 않는다.

- inventory의 실제 DB 재조회, CF-6 상류/CF-7 정상 형제/CF-8 이력 drift를 포함한 실제
  CF 8종 fixture/oracle의 적합성 및 각 입력 파일 SHA 검증.
- provider 호출·selector 결정별 실행 trace의 진위, 32건의 실제 실행, latency/token 실측.
  단위 테스트의 CF ID와 model 이름은 계약 검사용 가짜 입력이며 실험 결과가 아니다.
- revision의 clean main 여부·이미지 label, 데이터 반출 승인, robustness·delivery,
  receipt/seal·immutable 게시·production 전환의 4축 검증.

다음 단위에서 실제 runner/기록·CF fixture를 연결하고 독립 검증을 추가한다.
실행 코드가 완성되어도 최종 merged clean main R과 **별도 LLM 데이터 반출 승인** 전에는
32회 실실행을 하지 않는다. SMTP 7통 승인 역시 별개다.
