# U10 비교 결과 오프라인 계약 — V5-C-7.1

> 2026-09-06 사용자 승인 변경: 아래 release 증적은 기존 `ACTION-POLICY-V1` 전용이다.
> 새 `MOCK-NOTIFY-V1`은 확인 기록 없이 이메일 + 자동 MES Mock이며 9/3 대기·pre-HITL Kafka 0·승인
> 증적을 재사용할 수 없다. 기존 Stage2/readback/production Level 3는 새 정책을 거부한다.
> [새 정책 계약과 후속 증적 전환](mock-notify-contract.md). 이 변경은 기존 묶음 B 리뷰 통과 범위가 아니다.

담당 방대혁(C). 계획 v60. 묶음 B는 25차-1 리뷰 **필수·권장·편집 0** 후 `7e5ce22`에 반영됐다.
CF8·32 attempt 코어와 실 provider/관측 경계·private 발급 runner를 연결했다.
현재 **묶음 C 구현 진행 중**이며 delivery/completion/round 검증·aggregate offline 발급과
WF2 읽기 전용 API 수집·DB callback 조립부, 고정 이미지/지속 runner의 하위 실행 어댑터를
추가했다. private prepared 발급 helper와 A preflight/n8n probe·container context 수집
조립부를 연결했다. 2026-09-06 후속으로 실제 모델/게시 SHA readback, production preflight
세 Gate와 fence/rollback wrapper, 현재 SMTP observer, 재개 검증·phase helper 및 checkpoint/DB
12-run 읽기 수집부를 추가했다. **Stage2 7-mode의 실행·정리·발급 연결은 미완료**이며,
운영 전환이나 실 LLM/SMTP 배치를 수행한 것은 아니다.
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
  실제 benchmark/runner 결속은 아래 묶음 B 조립 절에서 수행한다.
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

관찰 DTO 기반 context builder와 읽기 어댑터는 아래 모듈로 연결한다. 실제 CF/provider 조립은
묶음 B 절을 따른다. 이 코어의 회귀 자체는 실 LLM 관측·DB snapshot 재조회가 아니다.

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
아니다.** 아래 source binding/runner가 이 경계를 담당하며 단위 테스트의 fake raw-ID 계약과 구분한다.

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
실제 provider·승인 검증기·출처 SHA 결속은 아래 묶음 B runner가 담당한다.

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
이미지 결속은 이 단일 attempt 모듈의 책임이 아니다. 아래 묶음 B에서 실행 조립하며,
테스트 read/selector/generator/observer를 쓰는 검증은 실험 성과가 아니다.

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

이 모듈 단독으로는 배치를 실행하지 않는다. 아래 묶음 B의 원천 고정 CF8·격리 DB loader와
`verified_preparer`가 `execute_batch.prepare`/실실행 CLI에 연결한다.
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
이미지 label/tree는 아래 검사기가 담당하며 receipt·`execute_batch`/live CLI는 묶음 B에서 연결한다. 현재 feature
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
  20차 권장 반영: 두 시각의 DTO 타입은 기존 `UtcTime`이다. 원래 마이크로초 정밀도로 역행을
  먼저 검사한 뒤 초 단위 `YYYY-MM-DDTHH:MM:SSZ`로 절삭·`utc()` 재검증해 직렬화한다.
- 테스트는 상위 검사기 자체를 대체하지 않는다. 실제 image/runtime/readiness 검증을 연결하고
  Git 조회·inspect·readback·HTTP만 합성 포트로 대체한다. 세 profile·두 phase의 exact 순서,
  backend/frontend/runner의 runtime·HTTP 중 재시작, 중지, 실패 시 후속 호출 0건을 검증한다.

이는 관측 구간의 **시점 대조**다. 검사 사이 변경 뒤 원상 복구, 종료 후 변경, gateway의 실제
published-port 소유 container, 장기 실행 process 메모리의 동일성까지 증명하지 않는다.
현재 반환값은 image/runtime/readiness DTO·profile/phase/시각이며 `head`·overall integrity·
agent_verdict·robustness·delivery_integrity·allowed_actions를 발급하지 않는다.
검사 1~3은 아래 평가 검증 단위가 담당한다. 최종 1~8 조립·4축 CLI·Stage2 lifecycle 연결은 남아 있다.
공용 배포·실서비스 조회·artifact 발급은 수행하지 않았다.

## 검사 1~3 artifact·evaluation receipt 검증

`u10_evaluation.verify_evaluation()`는 세 private 파일(artifact·evaluation receipt·benchmark)을
읽고 기존 `verify_u10_comparison.py`와 **동일한 `validate_artifact()`**를 직접 재실행한다.
파일 발급·subprocess·DB·LLM·배포 작업은 없다.

- 세 파일 모두 기존 FD 기반 private reader의 regular file·0600·현재 사용자·단일 hard link·
  symlink 거부·크기 제한을 적용한다. parent/root는 0700이다. benchmark **raw 파일 SHA**는
  실행 전에 별도로 고정한 `pinned_benchmark_sha256`와 비교한다. artifact의 `benchmark_sha256`은
  canonical Benchmark DTO SHA라 줄바꿈 포함 파일 SHA와 다르며 서로 대체하지 않는다.
- `EvaluationReceipt`는 계획 §431의 필드를 exact/strict 검증한다. `validator_command`는
  비어 있지 않은 argv 문자열 목록의 **이력 메타데이터**이며 실행하거나 exit 0 선언으로 검증을
  생략하지 않는다. 명령의 실행 이력/완전성 자체는 증명하지 않는다. `decided_at`은 초 단위 UTC와
  실제 달력값을 검증하고, tree OID 3종의 길이는 git_object_format sha1/sha256과 대조한다.
- receipt의 artifact SHA를 raw bytes와 대조한 뒤 실제 32행·지표·판정 재계산을 수행한다.
  artifact R, benchmark의 fixture/oracle/inventory/tool/fixed-policy SHA 5종, verdict rules SHA,
  코드 소유 U10 예산 `{total:10, read:8, send:2, same_tool:4}` 및 재계산한 verdict를 receipt와 비교한다.
  receipt `effective_budget_policy`는 이 U10 비교 예산의 exact map이며 검사 8의 runtime budget
  5키 출력과는 별도 형식이다. 둘을 묵시적으로 교환하지 않는다.
- 판정 값은 기존 비교 코드의 정본 문자열 `AGENT_JUSTIFICATION_ESTABLISHED_V21` /
  `AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21`을 보존한다. 계획의 축약 표기를 새 값으로 발급하지
  않는다. receipt의 historical 필드명 `agent_justification_verdict`와 반환 `agent_verdict`를
  exact 대조하되 부정 판정도 검증 성공이며 `HARD_GATE_FAIL:*` 등의 사유를 원문 코드로 유지한다.

반환 DTO는 receipt와 파일 SHA·재계산 verdict/reason뿐이다. 파일·receipt·선언값의 일치가
실제 LLM 실행, 원천 CF/tool source 내용의 진위, clean merged main, receipt 발급자/불변성을
증명하지 않는다. SHA 원천과 파일 발급 책임은 별도다. tree OID는 이 단위에서 Git을 조회하지
않으며 이후 검사 4에 전달해야 한다. 전체 preflight의 `integrity`나 allowed_actions는 발급하지
않는다. CLI subprocess exit 재실행 대신 같은 순수 검증 함수를 호출하며 예외가 실패를 나타낸다.
이 평가 결과와 검사 4~8의 동일 R/tree 연결은 아래 무결성 조립기가 담당한다.
CLI의 exit 0/1 및 4축 출력은 아래 묶음 A가 담당하며, robustness/delivery의 실제 검증은 묶음 C다.

## 검사 1~8 무결성 조립

`u10_integrity.verify_preflight_integrity()`는 평가 파일 검증 → 명시 repository HEAD 관측 →
배포 검사 4~8 → 평가 파일 재검증 → HEAD 재관측 순서로 실행한다. **R/tree를 별도 인자로 받지
않고 오직 검증된 evaluation receipt에서 배포 검사로 전달**한다(21차 리뷰 §5).

- `read_repository_head()`는 기존 Git 환경 격리/명시 root 규칙으로 HEAD commit과 object format을
  읽는다. receipt object format과 일치해야 하며 두 HEAD 관측도 같아야 한다. HEAD가 R과 달라도
  R/tree 검사는 receipt 기준으로 진행한다. main/clean 실행 revision 검증을 대체하지 않는다.
- 배포 관측 뒤 artifact·receipt·benchmark bytes와 의미를 다시 검증한다. 유효한 JSON에
  공백만 추가돼도 raw SHA 차이로 거부한다. 파일이 유효한 다른 receipt로 교체돼도 최초 관측과
  다르면 `U10_EVALUATION_DRIFT`다. 저장소 HEAD 변경은 `U10_REPOSITORY_HEAD_DRIFT`다.
- image/container pin은 caller map에서 복사한다. production ACK의 독립 attempt는 기존처럼
  runtime 검사에 전달한다. profile·phase·repository_root·실제 head·최종 checked_at과 평가/
  배포 관측을 묶고, 모든 검사가 통과했을 때만 내부 DTO의 `integrity=PASS`를 반환한다.
  실패는 고정 코드 예외다. 부정 연구 판정과 사유는 평가 DTO 안에 그대로 남긴다.
- 전체 clock 관측은 원래 마이크로초 정밀도로 단조 순서를 요구한다. 최종 파일/HEAD 재검증 뒤
  읽은 시각도 역행하면 거부하고, 출력은 기존 초 단위 UTC 형식으로 직렬화한다.
- 회귀는 실제 임시 Git 저장소·private 파일·상위 validator를 사용하고 Docker/HTTP만 대체한다.
  세 profile×정/부정 판정, HEAD≠R 양성, receipt tree/format 불일치, 실행 중 파일·HEAD 변경,
  시각 역행, 반복 호출 시 파일·Git 작업 트리 무변경을 검증한다.

이 모듈은 읽기 전용이며 저장된 preflight 상태를 만들지 않는다. `integrity=PASS`는 **검사 1~8의
관측 결과**이지 production 활성화 허가가 아니다. 4축 출력·allowed_actions·실패 JSON/exit는
아래 CLI로 연결했다. robustness/delivery 검증기·Stage2 연결은 남아 있다. caller는 내부 DTO 전체를
공개 stdout으로 덤프하지 말고 최종 계약의 비밀 제외 필드만 투영해야 한다.
재검사 뒤 변경, 변경 후 원상 복구, 실제 실행/발급자 진위와 gateway port 소유권은 이 시점
대조로 보장하지 않는다. 기존 준비 격리와 승인 조건은 유지된다.

## 묶음 A — preflight CLI와 evaluation receipt 발급

22차 리뷰 §6의 묶음 A다. 구현리뷰는 이 묶음의 연결 테스트를 포함해 한 번 받는다.

### `backend/scripts/u10_preflight.py`

필수 인자: `--repository`, `--profile`, `--phase`, `--artifact`, `--evaluation-receipt`,
`--benchmark`, `--benchmark-sha256`, 반복 가능한 `--image-id ROLE=ID`, `--container-id ROLE=ID`.
production Level 3만 기존 `--expected-attempt-id` 계약의 독립 검증된 attempt를 전달한다.
같은 role 중복·mutable ID·알 수 없는 role을 거부한다. role별 필수 집합은 기존 검사기가 소유한다.

- `verify_preflight_integrity()`를 실제 호출하고 내부 DTO에서 계획 §454의 15개 공개 필드만
  투영한다. `validator_command`, receipt 원문, config/budget, ACK·DSN은 stdout에 내보내지 않는다.
  정상/오류 모두 **한 줄 JSON**, 오류 parser도 원래 argv/path를 stderr에 복사하지 않는다.
  `--help`만 일반 argparse help/exit 0이며 preflight 실행 결과가 아니다.
- 성공은 `failed_checks=[]`, 실패는 첫 고정 오류 코드 1건을 배열로 반환한다. 알려지지 않은
  예외/원문 오류는 `U10_CLI_FAILED`로 바꾼다. 실패 시 확보하지 못한 root/head/R/verdict는 null,
  image_ids는 빈 map이다. 부분 관측을 성공 결과처럼 복구하거나 별도 IO로 채우지 않는다.
- **exit 0은 integrity PASS만 의미**한다. 연구 부정 판정의 `NO_GAIN`, `COST_CAP_EXCEEDED`,
  `HARD_GATE_FAIL:*`도 그대로 보존하면서 exit 0이다. integrity 실패만 exit 1이다.
- 묶음 A에는 robustness/delivery verifier가 없으므로 **두 축은 항상 `NOT_RUN`**이다.
  CLI에 PASS 값을 주입하는 옵션이나 `--robustness-artifact`는 아직 없다(묶음 C 구현 대상).
  `reset_attempt_id`도 reset lineage 검증 전이므로 null이며 production ACK로 대신 채우지 않는다.
  이는 22차 묶음 A의 NOT_RUN 허용 범위다. 묶음 C에서 receipt 재검증/필수 인자 계약을 완성한다.
- 순수 정책 함수는 `u9=e2e=(integrity==PASS)`, production은 integrity/robustness/delivery
  **모두 PASS**일 때만 true다. 연구 verdict는 이 함수의 입력이 아니다. 현재 CLI에서는
  production이 항상 false이므로 **exit 0만 보고 production을 켜면 안 된다**.
- 상태 파일·로그·승인·컨테이너 lifecycle을 생성하지 않으며 반복 호출 때 매번 재검증한다.
  실제 CLI는 기본 Docker/HTTP 포트를 사용한다. 테스트만 Python 호출의 leaf port를 주입한다.

### `backend/scripts/emit_u10_evaluation_receipt.py`

필수 인자: `--repository`, `--artifact`, `--benchmark`, `--benchmark-sha256`.
artifact 안의 R을 사용하며 output 경로나 R을 따로 덮어쓰는 인자는 없다.

- private artifact/benchmark SHA·순수 validator를 확인하고 **clean local main HEAD==R**을
  검증한다. 코드 소유 argv로 `verify_u10_comparison.py`를 실제 subprocess 실행(30초 timeout,
  stdout 16KiB 상한)하고 exit 0·artifact SHA·판정·사유·inspection_only 결과 전체를 대조한다.
  사용자 명령을 실행하지 않는다. 실행한 interpreter/script/인자 목록을 private receipt에 기록한다.
- 검증 명령 뒤 입력 bytes와 revision/clean 상태를 다시 대조하고, 발급 직전에도 clean R을
  확인한다. Git/파일 lock은 아니며 동시 변경 방지와 실제 LLM 실행 provenance는 runner 책임이다.
- 출력은 `<repository>/output/v5-c-7.1/<R>/u10-evaluation-receipt.json` 고정이다.
  필요한 디렉터리는 0700으로 만들고 기존 경로가 symlink/non-directory/다른 소유자면 거부한다.
  기존 R 디렉터리가 0700이 아니면 chmod하지 않고 실패한다. 상위 기존 디렉터리 권한은 보존한다.
- 기존 `write_private`의 **0600·O_EXCL·fsync**를 사용한다. 재발급은 `ARTIFACT_EXISTS`로
  거부하고 기존 바이트를 보존한다. 부분 파일도 삭제/덮어쓰지 않는다. immutable은 이 writer의
  no-clobber 계약이며 OS immutable flag/서명이나 사후 편집 불가능성을 주장하지 않는다.
- 성공 stdout은 status/receipt 경로·SHA/artifact SHA/R/verdict만 반환한다. 실패는 고정 code와
  exit 1이다. 명령/stdout/stderr 원문은 출력하지 않는다. 부모 디렉터리 생성 중 실패하면 생성된
  빈 디렉터리는 남을 수 있다. 공용 DB·LLM·SMTP·Docker는 호출하지 않는다.

테스트는 임시 Git/private 파일·**실제 offline verifier subprocess**로 receipt를 발급한 뒤
preflight CLI에 넘긴다. preflight의 상위 validator들은 실제 실행하며 Docker/HTTP만 합성이다.
세 profile×정/부정, 부정 사유 3종, 반복 무변경, 전체 18개 Gate 조합, CLI parser/exit/필드 투영,
발급 no-clobber·권한·symlink·dirty/feature·검증 실패·입력/HEAD drift를 함께 검증한다.
실제 프로젝트에 새 실험 artifact/receipt를 발급하거나 배포한 증거는 아니다.

## 묶음 B 진행 — 입력 결속·반출 승인·prepare 연결

23차 구현리뷰(묶음 A)는 필수 0·권장 1이었다. 권장 pin 정규식 회귀는
`backend=bistel-backend:latest`와 이름 기반 container ID를 각각
`U10_CLI_PIN_INVALID`로 IO 전에 거부하는 2건으로 보완했다.

`u10_source.py`는 실행 중인 패키지의 코드 소유 파일 목록에서 source bytes SHA를 구한다.
selector JSON schema, 실제 5 Tool input/result schema, evidence projection SHA,
fixed policy SHA와 그 구현 소스를 하나의 `u10-tool-source-v1` canonical SHA에 결속한다.
null matrix·candidate resolver·history trend는 수동 복제한 규칙 대신 해당 구현 파일 bytes로
결속된다. benchmark의 선언값을 expected 값으로 다시 사용하는 자기 대조는 하지 않는다.
이는 지정 파일의 로컬 결속이며 전체 배포/실행 증명이 아니다. 최종 runner의 clean main R 검사와
CF source·시나리오·도구·bundle·격리 DB·provider/observer·실행/dry-run CLI와
Common LLM/config·intake/parser/R03/route 의존 소스를 결속 목록에 추가했다.

`u10_export.py`의 `ExportAuthorization`은 별도로 승인받아 준비된 private JSON과 독립 raw SHA를
매번 재검증한다. `u10-data-export-grant-v1`의 필드는 정확히
`schema_version/purpose/approved_by/binding/issued_at/expires_at`이며 purpose는
`U10_DATA_EXPORT_GRANT`, 승인자는 담당자 방대혁이다. binding은 기존 `BatchBinding`의
R·benchmark/LLM config/tool/fixed policy SHA·attempt_count=32 여섯 필드다.
`issued_at <= now < expires_at`, UTC 달력값, 0700/0600, 원본 SHA와 exact binding을 요구한다.
삭제·교체·만료는 다음 검사에서 거부한다. 이 모듈은 승인 파일을 생성하지 않으며 공수/SMTP 승인을
받아들이지 않는다. 로컬 operator acknowledgement일 뿐 사람 신원을 암호학적으로 증명하지 않는다.

`u10_preparation.verified_preparer()`는 기존 `execute_batch.prepare`에 연결한다.

1. CF-1~8 exact 경로 집합, batch binding, 실제 source pin을 검사한다.
2. 매 준비마다 source/승인을 재검증하고 private snapshot bytes SHA를 fixture와 대조한다.
3. loader에는 attempt key와 bytes만 넘긴다. oracle/기대 inventory를 넘기지 않는다.
4. loader가 연 connection에서 `verify_fixture_inventory` SQL을 실제 실행하고,
   inventory용 route·current target과 실행용 `ObservationContext`가 같은지도 검사한다.
5. selector/가설 port 진입마다 승인 재검증 wrapper를 적용한다.
6. 실행 뒤 snapshot/source drift를 검사하고 loader 정리가 끝나야 다음 attempt로 진행한다.

**한계:** `SnapshotSession`의 DB 격리·snapshot 복원·read port·effect observer 진위는 loader의
책임이다. 이 seam이 임의 connection을 격리 DB로 인증하지 않는다. 승인 wrapper는 port 진입을
보호한다. 가설 내부 correction/HTTP retry의 재검증은 아래 `RealProvider`가 추가한다.
seam 단독 사용과 실제 CLI의 고정 조립을 구별한다.

39개 신규 준비 회귀는 private grant/snapshot, 실제 SQL, 기존 두 정책과 32건 coordinator를
연결한다. 이때 CF ID 8개에 복제한 DTO는 연결 검사용 합성 fixture이며 과학적 CF 8종이 아니다.
별도의 일회용 PostgreSQL에서도 inventory 준비 검사를 1회 실행했다(LLM 0회·cleanup 완료).
이 1건은 실제 CF snapshot/oracle의 PostgreSQL 실측을 완료했다는 뜻은 아니다.

### CF8 입력·격리 loader·no-LLM dry-run 추가

`u10_fixture_source.py`는 Common intake의 최종 ZIP/member SHA를 재사용하고, 추출이나
Cypher 실행 없이 bounded read/parse한다. lot_history의 `fault_code`는 allowlist projection에서
즉시 제외한다. 관측 전 실측한 label-free projection SHA
`178e72098f16978d208d7ee45db9f28ed828cb55735c91126d67169f126013c8`을 코드에 고정하므로
snapshot 내용과 자체 hash를 같이 바꿔도 복원이 거부된다.

`u10_counterfactual.py`는 실제 member alarm·R03 순수 규칙·두 공정 route·최종 graph를 구성한다.
`u10_fixture_tools.py`의 수치는 **의도적으로 합성한 CF 관측값**이며 원본 측정 성능을 주장하지 않는다.
CF-2는 두 번째 현재 wafer의 추가 파라미터, CF-6은 실제 PHOTO 파라미터를 가진 상류 이탈,
CF-7은 정상 형제 대조, CF-8은 이전 lot 2개 이상에서 계산되는 DRIFT를 검증한다.
문서의 첫 실패(CF-3)는 attempt마다 초기화하며 inventory probe가 그 상태를 소비하지 않는다.

`u10_fixture_bundle.py`는 snapshot과 분리해 oracle을 구성한다. 각 최소 2개 근거는 실제
FDC/문서 evidence projection으로 얻을 수 있는 ID다. CF-6은 상류 LOT_HIST도 요구한다.
CF-7/8의 집계 자체에는 인용 ID가 없으므로 sibling/history 필수 차원은 별도로 확인한다.
이는 해당 차원의 citation recall을 직접 측정한다는 뜻이 아니다.

`u10_fixture_database.py`는 caller DSN을 받지 않고 로컬 image ID로 일회용 PostgreSQL을
생성한다. pull을 금지하고, attempt별 새 연결의 TEMP inventory 2개 테이블만 복원한다.
실제 inventory SQL을 읽기 전용 transaction에서 실행한다. 이는 전체 production DB 복원이나
실제 T2/FDC 원본 측정 재실행이 아니라 **사실 재고 + 합성 관측**의 분리된 실험 입력이다.
`counterfactual_loader()`는 이를 `verified_preparer()`의 SnapshotSession에 연결한다.
live runtime의 deadline/provider/effect observer는 모두 필수 주입이며 가짜 기본값이 없다.

단일 명령(backend cwd, output은 존재하지 않는 새 디렉터리):

```sh
../.venv/bin/python scripts/prepare_u10_fixtures.py \
  --source-archive /absolute/path/project.zip \
  --output /absolute/private/parent/new-u10-inputs \
  --postgres-image-id sha256:<local-image-id-64hex> --dry-run
```

CF 8종·32개 독립 준비 상태·inventory·빈 관측 context를 검사한 뒤 cleanup까지 성공해야
0700 디렉터리/0600 입력 파일을 no-clobber 발급한다. 출력은 `DRY_RUN_INPUTS_ONLY`,
`attempts_executed=0`, `llm_calls=0`, artifact/receipt 발급 false다. dry runtime은 어떤
provider/effect 호출도 성공시키지 않고 즉시 거부한다. **데이터 반출 승인을 생성하거나 사용하지 않는다.**
실제 최종 ZIP과 로컬 PostgreSQL에서도 이 경로를 검증했다. 이 입력 bundle은 개발 중 source에
결속된 rehearsal 산출물이므로 최종 merged clean main R의 연구 artifact로 재사용하지 않는다.

검증: 신규 fixture/loader 회귀 36건, 최종 관련 선택 회귀 1120 passed, 추가 메모리 변이
8/8 탐지. 실제 원본 ZIP/일회용 PostgreSQL에서도 CF8·32 preparation PASS이며 LLM 0회다.
결과와 private 개발용 입력 SHA는 `output/V5-C-7.1_구현보고.md`의 묶음 B 절에 기록했다.

### 실 provider·관측 경계·32-run 발급 runner

`u10_provider.RealProvider`는 실제 `generate_hypothesis`/`select_next_step`의 parsing,
교정, usage 누적을 재사용한다. 두 함수에 선택적 `completion_port`, Common LLM에
선택적 `request_port`만 추가했고 미주입 production 동작은 그대로다. 실제 HTTP 요청마다
승인 파일·binding·시간, model/temperature/seed, endpoint/credential의 시작값 일치를 검사한다.
따라서 503 retry와 가설 correction도 각각 재검증한다. 성공/실패의 usage를 꾸며내지 않는다.

현재 조립은 Common의 단일 model 설정을 사용하므로 selector/hypothesis model revision이
같아야 한다. temperature=0을 실제 보내지 않는 reasoning model은 fail-closed 거부한다.
remote는 HTTPS, HTTP는 loopback만 허용하며 proxy env와 redirect는 비활성화한다.
이는 기본 production LLM 설정을 변경하거나 승인 record를 자동 생성하는 경로가 아니다.

`u10_observer.EffectObserver`는 전용 CLI process의 attempt 동안 Python audit hook으로
허용 provider 주소 외 connect/sendto, 파일 쓰기/삭제, 하위 프로세스를 차단·계수한다.
allowlist 주소라도 해당 attempt의 provider 요청 구간/호출 thread에서만 허용한다.
hook 설치 성공은 실제 audit probe로 검사한다. 원시 selector 응답의 `send_action` 선택도
구조 파싱 실패에 묻히지 않고 별도로 기록한다. 지연된 read worker를 같은 관측 범위 안에서
join하고, 차단된 IO나 send 선택이 있으면 clean 결과를 발급하지 않는다.

**관측 한계:** 이것은 OS/container 네트워크 sandbox가 아니다. 임의 native extension이나
사전에 열린 외부 descriptor까지 증명하지 않는다. CLI는 자체 소유 PG 연결과 5개 메모리
read port, 단일 provider만 조립하며 production DB/send/HITL/MES factory를 만들지 않는다.
따라서 다른 앱 process에 embedding하거나 이미 외부 연결이 있는 process에서 재사용하지 않는다.
관측 파일에도 `DEDICATED_PROCESS_PYTHON_AUDIT_NOT_OS_SANDBOX`를 명시한다.
실행 전 로깅 핸들러가 **stdout만 사용**하는지 확인한다. attempt 안의 파일 로깅도
쓰기 차단 대상이므로 파일 핸들러가 있으면 fail-closed될 수 있다.

`run_u10_comparison.py --execute`의 순서:

1. 명시 repository의 clean local main HEAD=R, 실행 패키지 위치 일치, private 입력 SHA,
   실제 source/tool pin, 별도 반출 grant, CF8 snapshot/source를 확인한다.
2. `<repo>/output/v5-c-7.1/<R>/u10-execution-claim.json`을 O_EXCL/0600으로 선점한다.
   경쟁/중복 실행은 Docker·DNS·provider 호출 전에 거부한다.
3. 로컬-only 일회용 PG에서 전체 benchmark/독립 oracle을 다시 구성해 선언값과 exact 대조한다.
4. fresh loader/provider scope를 32개 interleave한다. 매 attempt 전후 main/입력/source를 재검사하고,
   IO fence 안에서는 subprocess 없이 매 transport 승인을 재검사한다.
5. 32개 observer scope와 PG cleanup이 성공한 뒤 IO 관측과 `u10-comparison.json`을 private 발급한다.
6. 기존 **A receipt CLI를 실제 subprocess로 실행**하고 receipt bytes/SHA/identity를 대조한 뒤
   `u10-execution-complete.json`에 artifact/IO/receipt SHA를 결속한다. production은 항상 false다.

```sh
../.venv/bin/python scripts/run_u10_comparison.py --execute \
  --repository /absolute/repository --revision <clean-main-40hex> \
  --inputs /absolute/private/u10-inputs --benchmark-sha256 <raw-sha256> \
  --llm-config /absolute/private/llm.json --llm-config-sha256 <raw-sha256> \
  --export-grant /absolute/private/export.json --export-grant-sha256 <raw-sha256> \
  --postgres-image-id sha256:<local-image-id-64hex>
```

`llm.json`은 `LlmConfiguration`의 6필드(두 model revision, 두 prompt version, temperature,
seed)만 가진다. grant는 위의 별도 승인 스키마를 따른다. 파일/부모는 0600/0700이다.
실패 시 claim과 이미 발급한 파일을 **삭제/덮어쓰기/자동 재시도하지 않는다**. receipt만 실패한 경우
artifact는 보존되고 완료 파일은 없다. 승인·실행 기록 확인 후 기존 A receipt CLI로만 복구할 수 있다.
failed batch를 새 32-run으로 자동 치환하지 않는다.

묶음 B는 25차 리뷰 보완 후 25차-1 독립 재리뷰를 통과했다. 실 LLM 연구 실행과
최종 R 발급을 완료했다는 뜻은 아니다. 후속 묶음 C의 진행 범위는 아래 절과 구분한다.

25차 보완 전 관련 선택 회귀 1174 passed, provider/runner/CI 집중 회귀 45 passed, runtime 변경
메모리 변이 8/8 탐지. 원본 ZIP/실 PostgreSQL dry-run은 CF8/32 preparation PASS·LLM 0회다.

25차 회귀 보완은 production 코드 변경 없이 다음 검사의 호출 경로를 검증한다.

- R3: oracle 변조 후 DTO/canonical/raw SHA와 테스트 grant까지 재결속해 앞선 검사를 통과시킨다.
  DB 재계산에서 `U10_BENCHMARK_RECOUNT_MISMATCH`, provider 생성/HTTP 0, claim만 보존한다.
  재계산에는 DB 진입이 필요하므로 단위 검증은 SQLite open/close 1회이며 실제 Docker는 0이다.
- R4: 정상 32회 종료 뒤 observer의 행 누락·key·요청 수 0·차단·send 계수 5종을 변조한다.
  `U10_OBSERVER_POPULATION_INVALID`로 IO/연구 artifact/receipt/completion 모두 미발급이다.
- R5: 첫 실제 provider scope의 IO fence 종료 직후 benchmark bytes를 바꾼다.
  `U10_EXECUTION_INPUT_DRIFT`로 다음 attempt에 진입하지 않는다.
- R6: 실제 receipt CLI subprocess가 성공한 뒤 stdout의 receipt SHA/artifact SHA/path/revision
  4필드를 각각 변조한다. `U10_RECEIPT_CLI_FAILED`, 원 artifact/receipt bytes 보존, completion 0이다.
- V4: 원격 HTTP·userinfo·query·fragment endpoint 4종은 `U10_PROVIDER_ENDPOINT_INVALID`, DNS 0이다.
- F2: 원천/SQL은 유지하고 CF-8 history treatment만 깨뜨려 `build_benchmark()` 자체가
  `U10_CF_TREATMENT_INVALID`를 내는지 확인한다. 직접 treatment 검사만 호출하는 테스트와 별개다.

보완 후 관련 선택 회귀 **1190 passed**, fixtures/provider/runner **84 passed**(신규 16건),
위 검사 제거 메모리 변이 **9/9 탐지**. 전체 저장소 테스트·원격 CI 결과는 아니다.
R6의 네 필드는 개별 제거했다. pytest exit 1만 탐지로 세며 수집 오류는 제외한다.
저장소 production 코드는 변이하지 않았고 실제 HTTP/LLM 반출·Docker 실행은 하지 않았다.

실행 입력 준비는 최종 R에서 다시 한다. 개발용 `dryrun-01~03`은 보존하되 실행 증거로 사용하지 않는다.
⑨ 직전 별도 사용자 반출 재승인 뒤 Claude가 R/benchmark/LLM/tool/fixed SHA에 결속된
`export.json`을 초안 작성하고, 사용자 확인 뒤 0600 저장·독립 raw SHA를 고정한다.
만료는 짧은 실행 창(예: 3시간)으로 한정한다. 공수 승인을 반출 승인으로 대신하지 않는다.

## 묶음 C 진행 — delivery/completion component 검증

`release_delivery.verify_delivery()`는 round validator가 독립 검증한 EMAIL target 7개,
round가 결속한 component 3개(`delivery_receipts`, `prepared_attempt`, `smtp_approval`),
R/attempt/resume/capture 시각을 입력으로 받는다. 파일 위치는 private bundle root와 고정
파일명으로만 resolve하며 SHA → strict schema → 상호 결속 순서로 검사한다.

- target은 WARNING 4·EQP_HOLD 3, 고유 action ID/멱등 request hash 7개다.
- `level3-delivery-receipts-v1`은 `PRE_HITL` 시점의 DB callback projection과 WF2 execution
  projection을 별도 배열로 보존한다. 각 7개를 action ID/request hash로 1:1 대조한다.
- callback `SENT`, n8n `success`, 고유 execution/message ID, DB/provider message ID 일치,
  수신자 목록에서 재계산한 v2 hash, 기존 승인요청 3건 execution ID 집합을 검사한다.
- prepared/grant/R/attempt·config allowlist와 **당시 resume 시각**의 승인을 다시 확인한다.
  오프라인 재검증 시각으로 TTL을 판정하지 않는다. `resume_at == expires_at`은 거부한다.
- 확인한 모든 파일을 재조회해 중간 교체도 거부한다. 성공 결과에는 주소·원문을 넣지 않는다.
  DB 7행이나 execution ID만으로 SMTP acceptance를 통과시키지 않는다. 수신함 도달 검증은 아니다.

`release_completion.verify_completion()`은 completion에서 실제 resume claim/HELD outcome/
publish claim/prepared/round1까지 결속을 추적한다. 기존 `classify_state()`와 exact completion
schema를 재사용하고, caller가 검증한 R/attempt/round SHA/capture 시각을 대조한다.
caller가 독립 지정한 게시 root의 `attempt.json`, `golden-flow.json`, `fault-5class.json` bytes를
실제로 읽어 completion의 SHA와 비교한 뒤 lifecycle·게시 파일 drift를 다시 확인한다.
expired grant를 publish 시점에 새로 소비하지 않는다. FAIL completion·잘못된 phase·추가 abort
claim·누락된 연결·다른 R/attempt·다른 게시 bytes는 통과할 수 없다.

`release_round.assess_round()`와 `verify_round()`는 **BATCH_BASELINE_PRE_HITL** 시점의
12-run 입력을 production DTO·`ObservationContext`로 재생한다. canonical incident 12개와
고유 run/action ID, member alarm의 조치 5/4/3, 상태 9/3, R/3종 image label·dataset·fixture·
budget·모델/프롬프트 provenance를 확인한다. Tool의 성공 응답만 조사 근거로 받아들이고
실제 조회 범위·`compared`·가설 인용·파라미터 이탈 산술을 기존 production 함수로 재계산한다.
중첩 DTO에서도 추가 필드·bool/string 수치 위장을 거부하며 lot-history 고유성과 FDC의
wafer 번호·설비·recipe 식별자를 route에 대조한다.

`selector_seq=null`은 mandatory initial FDC(+실패 뒤 1회 retry)에만 허용한다.
나머지 조회는 production graph의 **SELECTED를 덮어쓴 OBSERVED 슬롯**에 1:1 결속하고
tool/실제 argument digest/순서를 검사한다. U10 연구 실행기의 SELECTED+OBSERVED 쌍을
그대로 넣는 형식이 아니다. read 8회·동일 Tool 4회·selector 10회 제한, 정상 stop·degraded·
조사 축 coverage를 판정하고 token 합계/read 횟수/nearest-rank p50·p95를 다시 계산한다.
SMTP 상태·채널/멱등 key·pre-HITL Kafka offset 변화는 **delivery_snapshot_verdict**로
분리한다. 이는 기존 provider acceptance 검사와 합쳐야 하며, DB snapshot만으로
`delivery_integrity=PASS`라고 판단하지 않는다. `batch_summary`를 신뢰하지 않고 재계산해
비교하며 `round1.json`의 private SHA와 검사 중 drift를 검증한다.

`release_aggregate.assess_aggregate()`는 이제 위 세 검사를 **2-component aggregate**로
연결한다. `round1`·`round1_completion`을 시작점으로 prepared/grant/delivery와 resume claim/
HELD outcome/publish claim까지 실제 SHA를 따라가며, caller가 별도로 준 R·ACK attempt와
prepared의 image ID 3종·preflight SHA를 대조한다. completion은 PASS만 수용한다.
게시 root도 caller가 지정하며 artifact 안의 절대경로를 소비하지 않는다.

CM52의 `attempt.json` 기존 6필드·backend/frontend ID+label 문자열을 확인하고,
golden/fault는 **기존 `validate_golden_summary`·`validate_artifact`**로 의미를 검증한다.
golden PASS·fault hard Gate·R·hypothesis model/prompt·정책·공통 evidence SHA를 대조하고
이 검사를 통과한 **바로 그 bytes**를 completion SHA와 다시 비교한다. 검사 종료 시
payload·lifecycle 파일 집합 및 게시 bytes를 재조회한다. 조사 FAIL은 전달 축을 오염시키지
않으며 SMTP provider/grant 실패는 `delivery_integrity=FAIL`에만 반영한다.

aggregate에는 `qualification_scope=SINGLE_STAGE2_BATCH`, `round_count=1`, `run_count=12`,
`repeatability=NOT_MEASURED`와 재계산한 batch summary가 들어간다. `verify_aggregate()`는
저장된 verdict·집계·수신자 hash/count를 다시 계산해 대조한다. 다른 attempt·경로 탈출·symlink·
missing claim·변조된 SHA·거짓 PASS는 거부한다. 이는 반복 재현성을 측정했다는 뜻이 아니다.

`emit_level3_robustness.py --aggregate`는 이미 존재하는 증적을 받아 **repo 밖 0700 root**에
`aggregate.json`만 0600·O_EXCL로 발급한다. 기존 aggregate나 MANIFEST가 있으면 거부한다.
`validate_level3_robustness.py --artifact`는 읽기 전용 offline validator다. 두 명령은 별도의
`--published-root`, `--expect-revision`, `--expect-attempt-id`를 요구하고, emitter는 추가로
`--bundle-root`, `--repository`가 필요하다. 양 축 PASS만 exit 0이고 실패 축은 exit 1이며,
무결성이 검증된 FAIL aggregate는 보존한다. parser/예외 출력에 입력 원문을 복사하지 않고
항상 `deployment_authorized=false`를 명시한다. 후속에서 `--seal`·`--copy-seal`·`--public`을
추가했다(아래 절). **아직 round/completion live capture mode는 없다.**

발급 lock 최초 생성 경합에서 macOS의 `O_CREAT|O_NOFOLLOW` open이 ENOENT로 실패한 사례를
재현했다. 공통 `lifecycle_lock()`은 O_EXCL 생성 후 EEXIST이면 **기존 inode를 열어 flock**하는
방식으로 보완했다. inode 교체·삭제·잠금 재시도 없이 동시 발급을 차단한다.

**당시 경계(아래 2026-09-06 후속 절에서 갱신):** aggregate offline 연결까지 구현했으며 실 수집기·runtime preflight 연결은 아니었다.
round/completion 발급 연결과 DB/n8n capture 진위는
후속 구현해야 한다. 합성 단위 테스트의 `round1`/게시 payload를
진짜 견고성 산출물로 사용하지 않는다. 당시 `u10_preflight` CLI의 robustness/delivery는 NOT_RUN이며
production 허용은 false였다. Stage2의 Level 3 plain full/hold 차단은 계속 유지한다.

신규 회귀 62건, release/CI 집중 212 passed, 메모리 검사 제거 변이 9/9 탐지(rc=1만 RED).
관련 선택 회귀 1295 passed·1 skipped. skip은 기존 Stage2 shellcheck 검사(도구 미설치)이며,
Stage2 실행 회귀는 별도 재확인에서도 43 passed다. 원격 CI 결과는 아니다.
실 SMTP·LLM·DB·Docker 실행 및 사용자 grant 발급은 하지 않았다.
후속 round 재계산 회귀 60건 추가, release/CI 집중 **272 passed**.
후속 최종 코드의 관련 선택 회귀 **1355 passed·1 skipped**(255.84초, exit 0).
skip은 동일한 shellcheck 미설치 검사이며 실 실행/배포 결과가 아니다.
모집단·pin·trace/조회·산술·compared·예산·전달·Kafka·집계·자료형·식별자 검사 제거 변이
**12/12 탐지**(rc=1만 RED, source bytes 보존).
양성 fixture는 canonical incident **키만** 빌리며 route·alarm·수치·LLM·전달 상태는
합성이다. 실제 최종 데이터셋 12-run 결과나 운영 qualification으로 해석하지 않는다.
aggregate/CLI/동시 발급/공통 lock 보완 회귀 55건 추가, release/CI 집중 **327 passed**.
실 subprocess 2개 동시 발급 시험을 별도로 5회 반복해 모두 단일 발급·기존 증적 보존을 확인했다.
aggregate chain·판정·게시/schema/SHA·drift·lock 검사를 제거한 메모리 변이 **11/11 탐지**
(pytest rc=1만 RED, 원본 source bytes 보존).
이번 최종 선택 회귀 **1410 passed·1 skipped**(259.29초, exit 0), skip은 shellcheck 미설치다.
**묶음 C 전체 완료·독립 리뷰 요청 상태는 아니다.** 남은 연결을 같은 묶음으로 이어서 구현한다.

### 후속: WF2 실제 API 읽기 경계 (방대혁/C · V5-C-7.1 · 묶음 C 진행 중)

`release_n8n.py`는 Stage2에 연결할 **읽기 전용 어댑터**다. 자동 실행하거나 공용 n8n 설정을
바꾸지 않는다. `N8nEvidenceApi`는 명시한 origin의 public API GET(workflow·execution·목록)만
사용하며 API key는 메모리/header로만 받는다. redirect·환경 proxy·retry 호출은 없고 응답은
4 MiB, 목록은 100건×20페이지 상한이다. 불완전 페이지·중복 cursor/ID를 성공으로 축약하지 않는다.

- `probe_evidence`: 준비 단계에서 독립 pin한 WF2 ID/version·active와 성공/실패 데이터 저장
  `all`을 확인하고 **이미 존재하는** webhook execution의 실제 payload/SMTP 결과를 읽는다.
  시험 메일을 만들지 않는다. workflow를 다시 읽어 변경도 검사한다.
- **현재 저장소 WF2 기본 설정은 성공/실패 저장 모두 `none`**이므로 해당 설정 그대로면
  `N8N_EVIDENCE_RETENTION_REQUIRED`로 차단한다. 준비를 통과시키려고 저장 정책을 자동 수정하지
  않는다. 공용 담당자가 비밀/수신자 데이터 보존 정책·접근권한·보존 기간을 검토하고 실제 설정과
  기존 execution을 제공해야 한다. 이 어댑터 추가는 공용 설정 또는 API 접근 승인 자체가 아니다.
- `collect_acceptances`: resume 이후 WF2의 **전체 상태** 목록을 순회한 뒤 exact 7건을 읽는다.
  잘된 7건만 고르는 status filter나 action 필터는 없다. 같은 WF2에 추가 실행·retry가 있으면
  차단하며 오래된 실행은 metadata만 확인한다. 목록 2회·projection 2회·workflow 2회 비교로
  관측 중 drift를 검사한다. n8n 원자적 snapshot 또는 이후 추가 실행 부재의 증명은 아니다.
- 실제 `Validate Email Payload`와 `Send Email` node 결과를 사용한다. 요청 recipients와
  SMTP `envelope.to`·`accepted`가 모두 일치하고 `rejected=[]`인지 확인하며
  `messageId`가 있어야 한다. 도메인만 casefold하고 local part는 보존한다. 다중 node 실행/아이템/
  분기·누락/정리된 원문·실패 결과를 첫 성공으로 대체하지 않는다. n8n 비-success status는
  성공으로 바꾸지 않고 기존 delivery validator가 별도 축에서 판정한다.
- 외부 시각은 n8n RFC3339 milliseconds를 검증하고 출력은 기존 UTC 초 schema로 정규화한다.
  키·메일 본문·webhook header/HMAC·workflow credentials/raw JSON은 저장/로그/출력하지 않는다.
  반환 DTO는 기존 private `ProviderAcceptance`뿐이다. 최종 수신자 hash와 독립 DB callback·
  grant·config·approval receipt 조인은 기존 `verify_delivery()`가 담당한다.

upstream 근거: [n8n public API 인증 정의](https://github.com/n8n-io/n8n/blob/master/packages/cli/src/public-api/v1/openapi.yml),
[Email Send 구현의 SMTP 결과 반환](https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/nodes/EmailSend/v2/send.operation.ts).
배포된 n8n의 API/실행 저장본 호환성은 실제 준비 probe에서 확인해야 하며, 테스트 응답을 공용 관측으로
간주하지 않는다. `test_agent_release_n8n.py`의 합성 응답 회귀와 기존 delivery chain 연결 검증을
CI에 등록했다. DB callback 조립은 아래 후속 구현이며 실제 SMTP config reader·prepared writer·
Stage2 7-mode·실 capture 발급은 남았다.

### 후속: DB callback·WF2 조립 (2026-09-06 · 방대혁/C · V5-C-7.1)

`release_database.read_delivery_database()`는 caller가 제공한 engine의 host alias·E2E database·
`kosa_app` 설정을 접속 전에 검사한다. 접속 뒤 `REPEATABLE READ READ ONLY`를 명시하고
statement/lock timeout을 적용한 다음, 실제 database/role·transaction readback과
`pg_control_system().system_identifier`를 prepared identity와 대조한다. 확인할 수 없으면
실패하며 관리자 계정으로 재접속하거나 권한을 자동 부여하지 않는다.
[PostgreSQL의 system identifier는 cluster 단위 값](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-CONTROLDATA)이므로
database 이름도 함께 비교한다. 실제 서버의 함수 접근권한/identity probe는 아직 미검증이다.

- run/action/link 12개·approval 3개·delivery 10개를 각각 `public` 테이블에서 읽는다.
  조인이나 run 필터로 orphan/추가 행을 숨기지 않고, 상한+1행을 읽어 초과를 거부한다.
  raw evidence·LLM prompt·평가 label·메일 본문·delivery result/error·승인자 정보는 SELECT하지 않는다.
- 독립 batch run→action pin 12쌍, canonical incident 12개, CREATED/retry 없음·Level 3·
  run/link/action lot/chamber·조치 일치·5/4/3, 정확한 HOLD 소유 approval PENDING 3개를 대조한다.
  채널/key는 EMAIL 7+MES_MOCK 3으로 결속한다. FAILED run이나 WAITING/UNKNOWN 등 delivery
  상태를 SENT로 바꾸지 않는다. 조치 규칙과 조사 품질 PASS는 기존 round validator가 판정한다.
- `collect_delivery_receipts()`는 DB snapshot → live config port → WF2 → **새 DB transaction**
  → config 재조회 순서다. HTTP 동안 DB transaction을 열어 두지 않는다. 초 미만 완료 시각까지
  포함해 DB drift를 검사한 뒤에만 기존 UTC 초 단위 callback DTO로 변환한다.
  config는 prepared digest allowlist에서 값 자체를 복원하지 않고 caller의 명시적 live reader로
  받아 pin/drift를 확인한다. 그 live reader의 운영 어댑터는 후속 작업이며 기본 성공값은 없다.
- 승인 메일의 독립 execution ID 3개는 SHA-pinned `PRE_APPROVAL/n8n-wf2.json` 입력을 받으며
  WF2 결과에서 추측하지 않는다. 파일의 action 모집단을 DB target에 대조하고 HTTP 이후에는
  action별 execution ID가 정확히 같은지 비교한다(동일 ID 집합만 비교하지 않는다).
  반환은 private snapshot/`DeliveryReceipts` 모델뿐이다. **이 함수는 SMTP 승인 검증·artifact
  발급·qualification 판정기가 아니다.** Stage2 lock/grant/runtime/시점/파일 결속 뒤 기존
  `verify_delivery()`가 DB callback↔provider message ID·수신자·grant 전체를 재검증해야 한다.
- private delivery DTO의 `PENDING` 오기를 실제 공통 Enum의 **`WAITING`**으로 수정했다.
  EMAIL DTO도 공통 전송 7상태를 보존하며 `PENDING`은 approval 전용이다. 아직 실 qualification
  발급 전인 묶음 C 내부 수정이고 runtime/public API 상태 계약은 변경하지 않았다.

신규 회귀는 실제 SELECT를 합성 SQLite에서 실행하고 PostgreSQL identity/transaction 응답만
명시적으로 대체한다. HTTP는 MockTransport다. SQL→WF2→private DTO→기존 offline delivery
validator 양성 연결과 불일치/누락/초과/타임스탬프·config drift/비밀 미노출을 검증한다.
**공용 PostgreSQL·SMTP·n8n 실검증 결과로 해석하지 않는다.** 묶음 C 전체 완료 후 독립 리뷰한다.

### v61: HELD 전 독립 n8n 실행 증적 (2026-09-06 · 사용자 보완 승인)

기존 golden-flow SMTP receipt는 `{action_id,status,receipt_id}`이며 execution ID가 아니다.
실행 ID의 출처를 별도 N8N_EXECUTIONS 증적의 `{workflow,action_id,status,execution_id}`로
정정했다. 기존 public/golden-flow schema는 바꾸지 않는다. Level 3에서만 해당 n8n 파일의
수집을 **12-run 이후·round1/HELD 이전**으로 옮긴다. inbox receipt는 HELD 이후 별도 수집한다.

- `record_release_approval_evidence.py`: 사람이 실제 n8n 실행 상세에서 확인한 3쌍을
  `$A/evidence/artifacts/PRE_APPROVAL/n8n-wf2.json`에 O_EXCL·0600으로 기록한다.
  동일 attempt의 실행 중인 resume claim SHA·owner 생존을 확인하고, source fsync 뒤
  `$A/approval-execution-evidence.ready` 빈 0600 파일을 O_EXCL로 만든다. 두 파일 모두
  private 수집 입력이며 robustness 정본 bundle 밖이다. 기존 파일은 덮어쓰지 않는다.
- `wait_release_approval_evidence.py`: Stage2의 inherited EX lock/부모 PID·host/boot/birth·
  UNRESOLVED resume claim을 전후 검사하며 완료 표시 부재만 최대 300초 대기한다.
  완료 표시가 보이면 source 파일을 SHA 고정·strict parse·재조회한다. 쓰는 중인 JSON을
  성공 입력으로 보지 않으며, 게시된 bad input은 polling으로 복구하지 않는다.
- `collect_delivery_receipts`는 이제 **파일 root + Component**를 필수로 받는다. 무출처
  ID 목록 입력은 제거했다. DB target action 3개 → fresh WF2 7건의 action↔ID 매핑 →
  DB/config/source 전후 동일성을 검사한다. receipt_id 전용, 누락/중복/다른 action,
  동일 집합 내 ID 뒤바뀜, source 공백 1바이트 변경도 실패한다.
- owner만 cleanup/terminal을 발급한다. timeout/불일치는 resume의 STAGE2_STEP_FAILED로
  정리할 조건이며, 이 reader/writer가 새 claim·HELD·abort·배포·메일을 만들지는 않는다.
  Level 2는 기존 HELD 이후 수동 수집을 유지하고 새 완료 표시나 phase artifact를 만들지 않는다.
- 독립성은 **별도 관측·입력 경로**를 뜻한다. n8n과 다른 서비스의 증명 또는 사람이 올바르게
  관측했다는 암호학적 증명은 아니다. 실제 callback↔SMTP messageId·recipient·grant 교차검증은
  기존 validator가 계속 수행하며 inbox 도달 여부와 구분한다.

새 barrier는 owner가 호출할 leaf이며 **Stage2 7-mode 전체 연결 완료를 뜻하지 않는다**.
Claude 묶음 C 리뷰에서 입력 출처·수집 순서·부분 파일 경쟁·1:1 join·lock/cleanup/Level 2를 확인한다.

### 후속: Stage2 고정 이미지·지속 runner 하위 실행부 (방대혁/C · V5-C-7.1)

`release_runtime.ComposeRuntime`은 Stage2가 호출할 **하위 Docker adapter**다. 별도 상위
controller/운영 CLI를 만들지 않았고 `cm52_stage2.sh`의 승인 없는 Level 3 실행 차단은 유지한다.
team down/복원·reset·승인·lock·preflight·workload 선택·artifact 발급은 이 adapter의 책임이 아니다.

- 팀 env는 0600·현재 owner·regular·single-link·NOFOLLOW로 읽고 원본 bytes를 결속한다.
  host export의 Compose 우선순위로 DB/recipient/credential 설정이 바뀌지 않도록 파일에
  정의된 키와 AGENT_/COMPOSE_/CM52_PIN_/revision/tag override를 자식 환경에서 제거한 뒤
  검증된 R·이미지·reports/runner pin만 넣는다. `${...}` indirection은 지원하지 않는다.
  이미지 inspect 및 각 Docker 명령 직전에 파일 조건/bytes를 다시 대조해 변경 시 다음
  명령을 차단한다. 두 관측 사이 변경 후 원복 및 최종 확인과 Docker read 사이 race까지
  원자적으로 방지한다는 의미는 아니다.
- `create()`: checked-in Compose 경로와 저장소 밖 owner-only reports root, role별 immutable
  image ID/40자 label을 검사한다. 기존 대상 service/one-off container가 있으면 거부한다.
  Level 3 전용 override로 Compose가 3개 service를 `--no-build --pull never --no-recreate`로
  **생성만** 하고, 실제 `.Image`/project/service/상태를 두 번 확인한다. `create`의 생성 전용·
  빌드/당겨오기 옵션은 [Docker 공식 계약](https://docs.docker.com/reference/cli/docker/compose/create/)을 따른다.
- `start(created)`: 관측한 ID/image/설정/created 상태를 다시 대조한 뒤 **그 container ID 3개만**
  `docker start`한다. 이미 started/recreated된 대상은 재사용하지 않는다. running 상태와
  StartedAt를 다시 관측하며, frontend readiness 및 DB/effective env는 이후 A preflight가 맡는다.
- `exec_runner(running, argv)`: 세 service의 ID·StartedAt/설정 drift를 재확인하고 동일 runner에
  `docker exec --user … --workdir /workspace/backend`한다. `compose run --rm`이나 재생성은 없다.
  **이 함수 자체가 grant를 검증하지 않는다.** Stage2 owner가 같은 lifecycle lock 안에서
  prepared/runtime/grant 검증을 끝낸 후에만 workload 명령을 전달해야 한다.
- `docker-compose.e2e-level3.yml`은 이 adapter에서만 추가한다. 기본 team/E2E 파일 및 기존
  Level 2 경로는 변경하지 않는다. backend/runner `3/true`, artifact path/ACK 빈 값, 이미지
  3개를 고정하며 runner는 기존 env·network·secret·mount 상속을 유지한다.
- checked-in `scripts/e2e_runner_idle.py`는 Python signal 대기만 한다. ASGI/worker·config
  import·network·`sleep infinity` 의존이 없고 SIGTERM/SIGINT를 처리한다. 실제 inspect에서
  inert command/entrypoint·init/restart·user/workdir, reports 쓰기/읽기 권한과 Kafka secret
  mount 두 개를 검사한다. raw Env·다른 label·secret 원본 경로·driver stderr는 내보내지 않는다.

검증은 합성 Docker command/inspect 응답과 **daemon 없는 실제 Compose config 렌더링**이다.
실 컨테이너 생성/기동·이미지 label 관측·readiness·외부 효과를 검증한 것은 아니다. 두 번의
관측은 원자적 Docker 잠금이 아니며 caller lock/후속 readback을 대체하지 않는다. 부분 실패는
재시도/자동 정리하지 않고 오류를 반환한다. Stage2가 먼저 cleanup trap을 설치해야 한다.
Kafka 등 보조 서비스 준비/정리도 Stage2 소유이며 이 하위 adapter는 대상 3-service만 다룬다.
실행계획의 prepared writer·7-mode·preflight 연결이 남아 있으므로 운영 명령으로 안내하지 않는다.

### 후속: private prepared 발급부 (방대혁/C · V5-C-7.1 · 묶음 C 진행)

`release_prepare.py`와 `scripts/emit_prepared_attempt.py`는 Stage2가 수집한 private 입력을
`$A/robustness/prepared-attempt.json`으로 고정하는 helper다. **새 live capture/상위 실행기가
아니며** DB/n8n/Docker를 호출하지 않는다. Git은 local clean main/40자 R만 읽고 변경하지 않는다.

- 입력은 `$A/preparation-capture.json`(내부 `level3-preparation-capture-v1`),
  `$A/e2e-level3-preflight.json`(A CLI 출력), `observer-baseline.json`, `stage2-log.jsonl`,
  report root 기준 별도 CM-4.7 final receipt다. 모든 파일은 private이며 capture에는 exact
  recipient가 있다. **이 transport 파일은 canonical bundle 또는 public artifact가 아니다.**
- capture의 `IntegrityObservation`·지속 runtime snapshot·backend/runner DB identity·recipient·
  비밀 제외 SMTP config·n8n probe·이전 production 복원 context를 strict 모델로 검사한다.
  독립 caller R/image 3개와 capture/preflight/reset SHA pin, A 결과와 실제 preflight 출력,
  container ID/StartedAt/image·Level 3 budget/env·6-check READY·두 runtime의 DB/recipient를 결속한다.
  U10 연구 verdict가 부정이어도 그 이유만으로 prepare를 막지 않는다.
- CM-4.7 final의 version/task/epoch/run ID·PASS·pre/applied/post SHA 형식과 observer 불변
  envelope, baseline 3축/로그 counter와 시각을 확인한다. **CM-4.7 pre/applied/post 전체 chain을
  재검증하거나 reset의 진위를 증명하는 것은 아니다.** 해당 확인은 기존 수동 선행 절차 소유다.
  CM-4.7 도구는 수정하거나 실행하지 않았다.
- 로그는 step 3/3a/3b PASS prefix이며 마지막은 3b여야 한다. workload/hold/cleanup 기록은
  거부한다. prefix 길이/SHA·preflight/reset/baseline SHA·config digest는 실제 bytes에서 계산한다.
  bound 이전 artifact는 고정된 복원 경로의 private bytes/SHA를 다시 검사하고 context를 보존한다.
- `prepared_at`은 capture 완료 UTC 초, `expires_at`은 그 시각 +30분이다. writer 시각은 그
  구간 안이어야 하며 오래된 입력을 뒤늦게 저장해 lease를 갱신하지 않는다. 최종 쓰기 직전에도
  시각·Git·입력 bytes를 재확인한다. 빈 bundle만 허용하며 lock/O_EXCL/0600으로 prepared 1개만
  발급한다. grant·claim·outcome·hold·round는 생성하지 않는다.
- `lifecycle_lock(..., relative_directory=...)`를 추가해 writer의 lock/read/write를 protected
  report root부터 directory FD로 순회한다. 중간 `cm-5.2`/attempt/robustness symlink도 거부한다.
  기존 caller의 기본 lock 경로/동작은 유지한다.
- stdout에는 SHA·시각·hash/count·`production is DOWN`과 다음 mode만 있으며 exact recipient/
  config/secret/경로는 없다. `smtp_send_authorized=false`, `deployment_authorized=false`다.
  실제 SMTP grant는 별도 사람이 확인한 `grant_smtp_send.py`만 발급한다.

검증은 임시 Git/합성 capture·receipt, 실제 A validator 함수와 실제 writer CLI subprocess다.
**저장된 typed observation 자체는 live 관측의 진위 증명이 아니다.** Stage2의 A preflight/probe/
live identity·SMTP config collector와 prepared writer 호출, lifecycle lock 소유·cleanup 처리는
아직 연결 전이다. CLI를 독립 운영 절차로 사용하거나 prepared만으로 workload를 시작하지 않는다.
helper는 기본적으로 발급 구간 lock을 직접 잡는다. 상위 owner가 이미 같은 lock을 보유했다면
이제 실제 상속 FD를 검증해 이어 쓸 수 있다(아래 후속 절). Stage2가 이 FD를 전달하고 cleanup까지
보유하는 호출 경로 자체는 아직 미연결이며 lock 검사를 생략하는 옵션은 없다.

### 후속: prepare 관측 수집 연결 (방대혁/C · V5-C-7.1 · 묶음 C 진행)

`release_context.py`·`read_preparation_context.py`는 실제 Backend/runner container에서
준비용 DB identity와 effective recipient만 읽는 private 경계다. public health는 바꾸지 않는다.

- 고정 container ID로 `docker exec`하며 환경변수/DSN/recipient override를 받지 않는다.
  stdout은 **exact recipient가 있는 private transport**다. 공개 로그에 tee하거나 게시하지 않는다.
- 실제 app engine URL이 E2E DB/app role인지 접속 전에 검사한다. 새 read-only/repeatable-read
  transaction에서 `current_database/current_user/pg_control_system`을 읽고 rollback/close한다.
  business table SELECT·commit·DDL·고권한 fallback은 없다. system identifier를 읽을 권한이
  없으면 실패하며 host alias는 실제 연결 URL에서 얻는다.
- Level 3/true/ACK 없음과 production recipient parser의 effective 목록을 확인한다.
  recipient v2 결과와 다르면 거부하므로 production parser가 local-part case-fold로 제거한
  주소를 그대로 승인 대상으로 저장하지 않는다. config/engine은 명시 live 호출에서만 import한다.
- subprocess의 고정 timeout/출력 상한/strict schema를 검사하고 실패에는 비밀·DSN·원본
  주소·stderr 대신 고정 오류 코드만 반환한다. 성공 projection 자체는 private다.

`release_capture.collect_preparation()`은 새 상위 실행기가 아니라 Stage2가 호출할 읽기 조립부다.

1. local clean main/R 검사, 지속 runtime의 3개 image/ID/StartedAt/mount 재검사.
2. Backend·runner context와 **필수 live 비밀 제외 SMTP reader** 결과 수집/교차 결속.
3. 기존 A `verify_preflight_integrity`를 `e2e_level3/pre_u9`로 **정확히 1회** 실행.
4. 관측한 workflow version으로 n8n **기존 execution 1건** read-only probe. 테스트 메일은 없다.
5. context/SMTP/runtime을 다시 읽어 drift를 확인하고 A의 private 평가 파일도 다시 검증한다.
   이 파일 검증은 두 번째 preflight/배포가 아니다. Git/시각을 재확인해 `PreparationCapture`를 반환한다.

U10 부정 연구 verdict는 보존하며 prepare만으로 production을 허용하지 않는다. collector에는
파일 발급·SMTP grant·lifecycle claim·reset·서비스 생성/기동·workload 코드가 없다. capture와
A report를 private 저장하고 prepared writer를 호출할 책임/lock/cleanup은 Stage2에 남는다.

**아직 운영 연결은 미완료다.** `read_smtp_config`의 실 runtime 구현은 남아 있으며 인자에
reader가 없거나 실패하면 차단한다. n8n public API가 반환하는 credential ID를 SMTP host/port로
추정하거나 저장된 allowlist를 live 설정으로 대신 쓰지 않는다. credential export나 retention
자동 변경도 하지 않는다. 동일한 포트는 delivery 수집부에서도 사용해야 한다.

검증은 임시 Git·합성 PG identity/transaction·가짜 Docker process·HTTP MockTransport다.
실제 A/runtime/n8n validator를 사용하고 수집 → private prepared 발급까지 연결하지만,
**공용 DB·n8n·SMTP·Docker daemon 실관측 결과가 아니다.** 반복 읽기는 원자적 snapshot이 아니며
중간에 바뀌었다가 돌아온 상태를 모두 감지한다는 보장도 없다. Stage2 7-mode와 lock/cleanup,
실 SMTP config·round capture·전환·seal 연결은 계속 구현한다.

### 후속: 부모·자식 lifecycle lock 공유 (방대혁/C · V5-C-7.1 · 묶음 C 진행)

`lifecycle_lock()`은 획득한 non-inheritable FD를 반환한다. 기존 caller가 반환값을 무시하는
경로는 그대로 동작한다. 부모가 자식을 동기적으로 호출할 때만 `subprocess.pass_fds`로 같은
open-file description을 명시 상속하고, 자식은 `inherited_fd`로 이를 빌려 쓴다.

- protected report root에서 상대 디렉토리를 FD로 순회한다. private regular file·0600·owner·
  single link, RDWR access, 대상 `.lifecycle.lock`과 device/inode 일치를 확인한다.
- 별도 FD의 SH nonblocking probe가 차단되는지와 전달 FD의 EX nonblocking flock이
  성공하는지를 모두 검사한다. unlocked/shared FD나 같은 inode를 따로 연 FD는 불충분하다.
  다른 attempt의 lock, symlink/교체된 inode, FD 번호만 전달하는 호출은 거부한다.
- 자식은 FD를 복제해 쓰고 close만 한다. **자식은 LOCK_UN을 하지 않는다.** 부모는 자식이
  끝나기 전에 owner context를 종료하면 안 되며 분류→실행→cleanup→terminal까지 보유한다.
  상속받은 여러 자식을 병렬 실행해도 각각 별도 mutex가 생기는 것은 아니다. 직렬 호출은
  owner 책임이다. 이 primitive는 process 실행/대기, source-state 판정, claim 발급을 대행하지 않는다.
- `issue_prepared(..., lifecycle_lock_fd=...)`와 내부 CLI `--lifecycle-lock-fd`를 연결했다.
  이 옵션은 lock 우회가 아니라 실제 소유권 검증 입력이다. 없으면 기존 직접 획득 경로다.
  prepared 파일 쓰기 직전에도 lock 경로/inode/소유권을 다시 검사한다. CLI output에 FD나
  recipient를 새로 노출하지 않으며 SMTP/production 허용은 계속 false다.

검증은 로컬 OS flock와 실제 Python 자식 프로세스, 임시 Git/private prepared fixture를 쓴다.
자식의 정상/오류/강제 종료 뒤 부모 lock 유지, 부모 비정상 종료 뒤 살아 있는 자식의 lock 유지와
자식 종료 후 해제를 확인했다. 이는 **현재 lock 소유** 확인이지 과거 전 구간 소유 증명이 아니다.
Stage2의 실제 7-mode 호출·자식 대기·cleanup/terminal·stale owner 복구 연결은 여전히 남아 있다.
새 상위 controller를 만들거나 Stage2의 기존 Level 3 실행 차단을 해제하지 않았다.

### 후속: Stage2 7-mode 진입 검증 (방대혁/C · V5-C-7.1)

`cm52_stage2.sh` parser는 `full|hold|prepare|resume_workload|resume|abort|recover`를
옵션 순서와 무관하게 결정한다. 잘못된 조합·승인 인자 누락은 파일/lock/cleanup 전에 거부한다.
`release_entry.py`/`inspect_stage2_prepared.py`는 repo 밖 protected root에서 exact prepared
경로와 lifecycle을 directory FD로 읽고 두 번 대조한다. 출력의 `execution_authorized=false`는
의도적이다. **7-mode 실제 runtime/claim/cleanup 연결은 아직 미완성**이며 새 실행 mode는
`LEVEL3_STAGE2_INTEGRATION_INCOMPLETE`로 차단된다. read-only 분류는 lock 아래 재검증,
TTL/grant/live drift 검사나 stale owner 사망 입증을 대신하지 않는다.

### 후속: private seal·public derivation (2026-09-06 · 방대혁/C · V5-C-7.1)

`release_seal.PRIVATE_PAYLOAD_NAMES`가 정상 경로의 immutable payload **9파일** 집합을
소유한다. aggregate의 기존 8파일 + `aggregate.json`이며 `MANIFEST.sha256`/`.lifecycle.lock`
자체는 manifest에서 제외한다. seal writer는 lifecycle lock 아래 실제 aggregate 전체를
재검산하고 파일 집합/bytes drift를 확인한 뒤 정렬된 `sha256  relative_path` LF 목록을
0600·O_EXCL로 발급한다. 누락·extra/abort 파일·nested directory·symlink·hardlink·잘못된 권한·
manifest 순서/CRLF/중복/자기 참조는 거부한다. 일치하는 manifest만으로 PASS를 인정하지 않는다.

`emit_level3_robustness.py --seal`로 봉인, `--copy-seal --destination <protected-empty-dir>`로
**9payload + manifest 전체 byte-identical 복제**가 가능하다. 두 root는 repo 밖 0700이어야 하며
서로 중첩될 수 없다. copy는 manifest-last이고 실패한 부분 파일은 복구 증거로 보존한다.
재시도 overwrite/삭제/부분 복사 seal 간주는 없다. 검증은
`validate_level3_robustness.py --artifact <aggregate.json> --sealed`이다.

공개 파생은 `emit_level3_robustness.py --public --public-root <repo>/output/v5-c-7.1/<R>`이다.
해당 revision 디렉터리는 호출자가 0700으로 먼저 준비한다. 별도 schema
`level3-robustness-public-v1`/`robustness-public.json`만 발급하며 exact recipient·raw delivery/
workflow/prompt/credential은 투영하지 않는다. 1회/12건/재현성 미측정과 전체 batch_summary,
recipient hash/version/count, 완성 manifest SHA와 코드 소유 derivation rule SHA를 포함한다.
emitter/validator 모두 원본 재검산·manifest SHA·canonical encoding·exact recipient/email/
URL/credential marker scan을 수행한다. scan은 추가 방어선이며 임의 비밀 탐지 보장은 아니다.
공개 파일은 `--robustness-artifact`의 canonical 입력으로 사용할 수 없다.

공개 검증은 `validate_level3_robustness.py --artifact <robustness-public.json>
--public-bundle-root <private-root>`로 원본에서 다시 파생해 exact 대조한다. 모든 emitter와
validator는 `--published-root`·독립 기대 R/attempt를 요구하고 배포 허용은 항상 false다.
CI 회귀도 동일 `PRIVATE_PAYLOAD_NAMES`를 사용해 향후 모든 `upload-artifact` step이
9파일+manifest의 `!**/<name>` 제외를 전부 선언하도록 강제한다. 현재 uploader는 없다.

### 후속: 공용 n8n session 읽기 호환 (2026-09-06 · 방대혁/C · V5-C-7.1)

실제 공용 n8n은 `2.32.7`이며 로컬 env에 UI 계정은 있지만 public API key는 없다.
`release_n8n_session.N8nSessionEvidenceApi`는 명시 계정으로 로그인한 뒤 GET만 제공한다.
HTTP는 caller의 명시 `allow_insecure_http=True`가 필요하며 URL만 보고 승인하지 않는다.
POST는 로그인 하나뿐이고 key 생성·credential test/export·workflow update/retry는 없다.
세션 cookie·비밀번호·원 execution은 메모리에만 머물며 redirect/proxy/retry를 사용하지 않는다.
UI 계약은 현재 버전에 pin해 다른 버전은 차단한다.

워크플로 retention 생략/DEFAULT는 **매번 읽은** instance 기본값에서 결정하고 declared/default
둘을 private memory에 남겨 drift를 비교한다. 명시 `none`을 `all`로 바꾸지 않는다.
실행의 flatted JSON은 reference/depth/방문 수/확장 bytes를 제한해 복원한다. 저장 버전은
기존 workflowData.versionId 또는 실제 n8n의 workflowVersionId이며 둘 다 있으면 일치해야 한다.
editor 실행 목록은 public API pagination과 다르므로 **estimated=false, count=실제 목록 길이,
100건 이하인 완전한 한 페이지**만 수용한다. 초과를 잘라 성공시키지 않고 명시 차단한다.

`smtp_transport()`는 활성 published WF2의 연결 credential을 서버측 마스킹된 UI GET으로
읽고 host/port/from/secure만 투영한다. 생략된 port/secure는 pin된 타입의 465/true 기본값을
사용한다. 근거: [n8n 2.32.7 credential service](https://github.com/n8n-io/n8n/blob/n8n%402.32.7/packages/cli/src/credentials/credentials.service.ts),
[SMTP type](https://github.com/n8n-io/n8n/blob/n8n%402.32.7/packages/nodes-base/credentials/Smtp.credentials.ts).
**이는 완성된 SmtpConfigSnapshot이 아니다.** recipient는 실제 backend/runner context에서,
WF2 callback endpoint는 현재 n8n `BACKEND_BASE_URL`에서 별도로 읽어야 한다.
이전 execution URL이나 로컬 `.env.team` 값을 현재 n8n 환경값으로 추정하지 않는다.

승인된 실제 읽기 검증: HTTP 로그인 PASS, 활성 WF2/SMTP 비밀 제외 조회 PASS, 기존 execution
89에 대한 실제 `probe_evidence` PASS. 저장소/공용 WF 설정은 변경하지 않았고 신규 SMTP/LLM/
Kafka 효과 0이다. 이 probe는 과거 한 건의 조회 가능성이지 새 12-run의 delivery PASS가 아니다.
브라우저에서도 활성 WF2의 `$env.BACKEND_BASE_URL` 참조를 직접 확인했다. UI의 기존 실행
preview는 과거 데이터이며 현재 env 증명이 아니다. Environments/Variables 화면에서도
현재 process env 조회 기능은 확인되지 않았다. 기존 WF를 임의 수정하거나 Execute step으로
이전 SMTP/callback을 재실행하지 않는다.

후속으로 사용자가 새 조회용 workflow 생성·1회 실행·삭제를 명시 승인했다. 2026-09-06
09:47:42 KST에 비활성 임시 workflow `c7pef22239a4e754`의 수동 execution `90`으로
`$env.BACKEND_BASE_URL` 하나만 직접 읽었다. Manual Trigger/Code 두 노드만 사용했고
credentials·SMTP·Kafka·HTTP 요청 노드는 없었다. 조회 success 후 정확한 생성 대상의
ID/name/nodes/connections/비활성 상태를 대조해 archive/delete했다. 사후 workflow
`exists=false`, scoped execution 목록 0건, 실행 GET `200 {}`로 실행 data 부재를 확인했다.
기존 WF2·3·4의 설정/노드/버전 projection SHA는 전후 동일했다. 비밀 제외 관측값은
`output/V5-C-7.1_구현보고.md`에 기록하며 일반 공개 qualification artifact로 발급하지 않는다.

이 작업은 **단발성 운영 진단**이며 `N8nSessionEvidenceApi`에 mutation 권한을 추가하지 않는다.
삭제된 임시 workflow를 permanent observer로 간주하지 않으며, 이번 수동 실행의 값은 이후
prepare/resume 시점의 current config 증명을 대신하지 않는다. callback 네트워크 도달성이나
실 delivery PASS도 아니다. 재관측 경로/권한과 Stage2 live 연결은 여전히 남아 있다.
API 계약 근거: [n8n 2.32.7 workflow controller](https://github.com/n8n-io/n8n/blob/n8n%402.32.7/packages/cli/src/workflows/workflows.controller.ts),
[수동 실행 DTO](https://github.com/n8n-io/n8n/blob/n8n%402.32.7/packages/%40n8n/api-types/src/dto/workflows/manual-run.dto.ts),
[임시 대상 archive/delete](https://github.com/n8n-io/n8n/blob/n8n%402.32.7/packages/cli/src/workflows/workflow.service.ts).

### 후속: 생산 세 Gate·현재 설정·phase 연결부 (2026-09-06 · 방대혁/C · V5-C-7.1)

위 묶음 A의 `NOT_RUN` 설명은 당시 구현 이력이다. 현재 `u10_preflight.py`는
`production_level3`에서 `--robustness-artifact <private aggregate.json>`와
`--robustness-published-root <CM52 게시물 디렉터리>`를 받아 전체 원본을 다시 검산한다.
다른 profile은 두 인자를 거부하며 기존 NOT_RUN 출력 계약을 유지한다. production_level3에서
인자가 없거나 증적이 부적합하면 robustness/delivery는 FAIL이다. **exit는 계속 integrity만
표현한다**. `allowed_actions.production_level3`의 세 축 AND를 읽어야 한다.

`release_gate.py`는 aggregate 선언 PASS를 신뢰하지 않고 round/completion/manifest 전 단계의
원본 및 CM52 게시물 의미를 검산한다. delivery 파일만 잘못된 경우 조사 robustness와 분리한다.
실제 backend의 모델·prompt·endpoint SHA/config digest·게시 attempt와 **실제 `/reports`의
attempt/golden/fault raw SHA**를 비교한다. 같은 attempt 경로지만 다른 내용인 게시물을 허용하지
않는다. 전체 읽기 전후 원본·runtime을 다시 대조한다. 연구 부정 판정 자체는 enable 차단축이 아니다.

`release_model.RuntimeLlmConfiguration`은 U10 실험 DTO와 별개다. 실제 graph는 seed를 보내지
않으므로 null이며, 실제 `LLM_TEMPERATURE`를 기록하고 reasoning 모델에서는 null이다.
U10의 temperature 0·명시 seed를 생산 실행에 허위 기재하거나 생산 모델 호출을 변경하지 않는다.
현재까지 실제 qualification round가 발급되지 않은 상태에서 이 차이를 정정했다.

`enable_production_level3.py`는 명시 operator 전환 CLI이며 **호출하면 실제 env/서비스를 변경**한다.
backend/frontend 고정 이미지와 R·private root·readonly reports mount를 확인하고,
지속 CLOSED fence → 모든 RUNNING/WAITING_APPROVAL 0 → Level 2 검증 → 3 전환/검증 →
2 rollback 리허설/검증 → 다시 3 전환/세 Gate 검증 → OPEN 순서다. Agent/메일/Kafka/reset은
실행하지 않는다. env의 autonomy/enabled/demo ACK 세 키만 변경하며 build/pull/down은 없다.
실패·첫 SIGINT/SIGTERM에서는 한 차례 Level 2 복원을 시도하고 검증 후에만 OPEN한다.
SIGKILL·복원 실패는 CLOSED가 지속된다. `--restore-level2`도 정상 backend에서 quiescence를
읽을 수 있어야 하므로 backend 자체가 내려가 DB를 읽을 수 없는 상황은 자동 복구 완료로
주장하지 않는다. frontend만 없는 경우에는 backend 관측 후 복원을 시도할 수 있다.

`AgentRuntime.start_run`은 초기 등록 동안 shared lock을 보유한다. 생산 Level 3는 현재
`BISTEL_SOURCE_REVISION`/ACK와 동일한 OPEN Level 3 fence를 필수로 요구하며, 파일이 없거나
binding이 다르면 503이다. Level 1/2·격리 E2E의 파일 미존재는 기존 동작을 보존하지만 존재하는
CLOSED 파일은 신규 등록을 차단한다. 기존 실행 이어가기/승인 결정 경로는 이 등록 fence를 거치지 않는다.

`release_n8n_probe.CallbackObserver`는 읽기 전용 session과 분리된 **임시 metadata 변경** adapter다.
명시 `allow_temporary_workflow=True` 없이는 로그인조차 하지 않는다. 코드 소유 두 노드만
생성·1회 실행·exact 대상 archive/delete하며, cleanup 확인 실패 시 현재값을 반환하지 않는다.
SMTP·Kafka·callback HTTP 노드는 없다. `read_smtp_snapshot`은 동일 session의 WF2/3/4 전후
버전/설정과 SMTP transport를 대조하고 checked-in callback builder의 정확한 parameters를 검증한다.
이 반복 동작의 실제 운영 승인은 별도이며, 이번 구현 검증에서는 Mock HTTP만 실행했다.

`release_resume.observe_resume`는 saved PASS 대신 현재 고정 컨테이너·DB/app role·recipient·
runtime budget·SMTP digest를 전후로 읽는다. `release_phase`는 Bash가 보유한 lock FD를 빌려
분류/승인 검증 후 claim을 쓰고 cleanup/restore 결과 이후 terminal을 O_EXCL 발급한다.
잘못된 grant/만료/runtime drift는 resume claim 전에 ABORT로 분기한다. PUBLISH는 TTL을
재검증하지 않고, 실패하더라도 HELD의 **pre-HITL 관측 7/3** 범위를 보존한다. stale recovery는
새 claim/작업 실행을 하지 않고 외부 효과 미확인을 임의의 0으로 바꾸지 않는다.

`read_release_runs.py`는 기존 graph checkpoint와 실제 Tool 감사 입력/결과를 읽는다.
12-run/최대 100 audit row씩으로 제한하고 E2E app-role readonly transaction과 model/checkpoint
전후 일치를 확인한다. 별도 Agent 실행기나 LLM 재호출은 없다. stdout은 private 원본이므로
공개 로그에 출력하면 안 된다. 이것만으로 완성 round/delivery/Kafka qualification은 발급되지 않는다.

### 후속: Stage2 abort/recover 실제 연결 (2026-09-06 · 방대혁/C · V5-C-7.1)

두 mode는 이제 초기 차단 대신 실제 흐름을 호출한다. `lock_stage2.py`가 동일 PID로 정본
Bash를 exec하고 EX FD를 전달하며, Bash가 begin→cleanup→restore→finish를 소유한다.
각 leaf는 private root/준비 SHA/claim SHA/parent/FD를 검사한다. 별도 controller나 8번째
mode는 없고, 거부된 전이·살아 있는 owner·잠금 경합에는 cleanup을 허용하지 않는다.

cleanup은 prepared ID/image와 E2E project/service를 대조하여 정확한 container만 stop/rm한다.
같은 프로젝트의 Kafka/MES는 기존 두 service만 허용하며 volume은 삭제하지 않는다.
E2E 부재는 `CLEANUP_E2E_ABSENT`를 포함한 NOT_ATTEMPTED다. 정본 §0′에 맞춰 ABORTED의
과거 OK/OK-only 제한을 보완했지만, basis 없는 NOT_ATTEMPTED나 FAILED 결과는 계속 거부한다.

복원은 E2E 잔존 0→production fence CLOSED→고정 이미지의 read-only quiescence 프로세스→
env 3키/이전 artifact 2경로의 expected bytes 재확인·원자 교체→인프라/고정 application pair
복원→A Level2 preflight/이전 artifact/실 env/평가 API 확인→OPEN 순서다. env 교체를 비협조
외부 writer와의 전역 원자 transaction이라고 주장하지 않는다. U10 기존 CLI 입력은 host 변수
`CM52_U10_ARTIFACT`·`CM52_U10_EVALUATION_RECEIPT`·`CM52_U10_BENCHMARK`·
`CM52_U10_BENCHMARK_SHA256`로 전달하며 누락/실패를 저장된 PASS로 대신하지 않는다.

terminal은 cleanup/restore 후 O_EXCL 1회 발급한다. recover의 기존 효과는 INDETERMINATE이며,
stale publish는 기존 게시물 SHA를 포함한 FAIL completion만 만든다. aggregate/seal은 발급하지
않는다. 실 Bash/exec/FD/claim/terminal 및 명시 Docker/HTTP/OS leaf fake 회귀로 검증했고,
공용 배포 실증이나 Claude 독립 리뷰 완료는 아니다. prepare/resume_workload/정상 publish,
step 8 backend 교체 rebind·receipt/round·scan·aggregate/seal 연결은 아직 남아 있다.

## 아직 증명하지 않는 것

13차 리뷰 보완 회귀는 비정상 selector 종료 뒤 가설 성공이어도 completion false(구조 오류·
timeout·dependency 3건), graph 목록에 현재 chamber가 잘못 포함돼도 SIBLING 거부(1건),
정상 binding 양성 대조 후 CURRENT/ADJACENT target 교환 거부(상류·하류 2건)를 검증한다.
각각 F5/O1/O3 가드 무력화 변이를 탐지하며, 이 테스트 fixture를 실제 DB snapshot으로 주장하지 않는다.

- CF 8종 synthetic 시나리오가 실제 현장의 고장 분포/성능을 대표한다는 주장.
  재고·원천 pin·합성 treatment 완전성은 확인하지만 실험 설계의 외적 타당성은 별개다.
  CF-1/6·CF-2/7·CF-5/8은 같은 factual incident를 공유하고 합성 treatment만 다르므로
  서로 독립인 현장 incident 8건으로 해석하지 않는다. U6 보고에도 이 한계를 명시한다.
- provider 호출·selector 결정별 실행 trace의 진위, 32건의 실제 실행, latency/token 실측.
  단위 테스트의 CF ID와 model 이름은 계약 검사용 가짜 입력이며 실험 결과가 아니다.
- 최종 실실행 revision에서의 main/이미지 실관측·반출 승인·실 LLM 실행, robustness·delivery,
  receipt/seal·immutable 게시·production 전환의 4축 검증.

다음은 묶음 C의 prepare/resume_workload/정상 publish 호출·Kafka 보조 서비스 수명과
backend 교체 rebind·receipt/round 발급·scan/seal 연결, SMTP observer 연결 및 Common 인계다.
production preflight/wrapper와 seal/public 하위 경로는 구현됐지만 Stage2 실제 연결과
묶음 C 전체는 미완성이다.
묶음 C 전체를 완성한 뒤 독립 리뷰한다.
실행 코드가 완성되어도 최종 merged clean main R과 **별도 LLM 데이터 반출 승인** 전에는
32회 실실행을 하지 않는다. SMTP 7통 승인 역시 별개다.
