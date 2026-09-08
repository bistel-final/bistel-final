# V5-C-7.1 U10 — Luna 요청 파라미터 계약

담당: 방대혁(C/Common). 2026-09-06 사용자 `진행해줘`는 `gpt-5.6-luna` 유지 방향의
호환 코드 보완 승인이다. 이 문서는 새 실행 승인서가 아니며 이전 R의 bundle·llm.json·export.json을
덮어쓰거나 새 R에 재사용하지 않는다. 공용 배포·SMTP·Kafka·조치 규칙은 변경하지 않는다.

## 원인 및 호환 경계

기존 U10은 `temperature=0`·integer seed의 6필드 설정만 증명한다. 공통 LLM 클라이언트는
Luna를 추론 모델로 분류해 temperature 대신 reasoning effort를 전송한다. 따라서 기존 승인서를
그대로 둔 채 모델 거부 검사만 제거하면 실제로 보내지 않은 temperature를 적용했다고 기록하게 된다.

공식 [Luna 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.6-luna)는 Chat Completions,
구조화 출력 및 `low` reasoning을 지원한다고 설명한다. 이번 수정은 기존 Chat Completions 경로와
프롬프트를 유지한다. 공통 클라이언트·production 동작은 수정하지 않는다.

temperature·seed 미전송은 이 실험의 **명시적 요청 정책**이다. seed의 모델별 지원 여부를 추정하거나
provider가 무시할 수 있는 seed로 결정론을 주장하지 않는다. 이 프로필의 결과는 재현성 보장이나
temperature=0 실험이 아니다. 실제 제공자 호환성·성능은 승인 후 관측에서만 확인된다.

## 새로운 llm.json 형식

아래는 **미승인 예시**다. 토큰 상한 1500은 테스트 예시이며 실행 시에는 확인한 `LLM_MAX_TOKENS`와
동일한 값을 명시하고 승인받아야 한다. 기존 승인 파일을 이 예시로 자동 변환하지 않는다.

```json
{
  "hypothesis_model_revision": "gpt-5.6-luna",
  "selector_model_revision": "gpt-5.6-luna",
  "hypothesis_prompt_version": "agent-hypothesis-v3-ko4",
  "selector_prompt_version": "agent-react-v2-ko4",
  "temperature": null,
  "seed": null,
  "request_policy": "U10-LUNA-REASONING-V1",
  "reasoning_effort": "low",
  "max_completion_tokens": 1500
}
```

- 새 정책은 가설·selector 모두 exact `gpt-5.6-luna`, temperature/seed null, effort low,
  정수 출력 토큰 상한 1~128000을 요구한다. 부분 설정·혼합 모델·0/seed 적용 주장은 거부한다.
- 기존 6필드 설정은 그대로 직렬화된다. 추가 필드가 없는 기존 모델/중첩 artifact의 canonical bytes와
  SHA를 보존하며, 기존 추론 모델 + temperature=0 설정은 계속 실행 거부한다.
- 새 9필드 설정 전체는 canonical llm_config SHA를 통해 grant·batch binding·artifact에 결속된다.
  raw 파일 SHA(CLI 인자)와 model canonical SHA(export binding)는 구분한다.
- 기존 `u10-comparison-v1` envelope와 판정 규칙·32 attempt 모집단은 유지한다. llm 블록에
  명시적 정책을 additive로 기록하며 이전 코드가 새 프로필을 검증할 수 있다고 주장하지 않는다.

## 실행 전 / HTTP 경계

U12 후속 보완부터 신규 selector는 `agent-react-v2-ko4`다. ko1·ko2·ko3 저장본은 읽지만 새 실행
admission은 실제 prompt 상수와 exact 대조하므로 ko1·ko2·ko3 설정을 거부한다. ko2·ko3·ko4는 동일한
private trace 존재·순서·호출 계수 검증을 사용하며 과거 bytes/SHA를 보존한다. 위 예시는 새 승인이 아니며
기존 llm/export 파일·claim을 수정하지 않는다. 합성 문서의 bounded 발췌가 가설뿐 아니라
selector 입력에도 전달됨을 새 export 범위 확인에 포함한다.

1. revision·raw SHA·export 승인 검증 후, claim·DB·DNS 전에 런타임 설정을 검증한다.
2. 모델·reasoning effort·출력 토큰 상한이 승인 설정과 일치해야 한다. 런타임 temperature는
   새 프로필에서만 미전송 값으로 취급한다. `.env`를 수정하거나 전역 값을 강제로 바꾸지 않는다.
3. 각 HTTP 요청 직전(transport retry·가설 교정 포함)에 승인/설정/endpoint·key를 다시 검증한다.
4. 실제 요청에는 temperature·seed·max_tokens가 없어야 하고, reasoning_effort와
   max_completion_tokens가 승인값과 exact 일치해야 한다. 불일치는 HTTP 호출 전에 거부한다.
5. 기존 effect observer·동시 실행 claim·실패 시 무삭제/무재시도·cleanup 후 발급 규칙을 유지한다.

`32`는 **CF 8종 × 정책 2종 × 각 2회 = attempt 수**다. 각 attempt의 selector·가설·교정·transport
재시도로 HTTP 요청 수는 32보다 클 수 있다. 실제 요청 수는 `u10-io-observations.json`의
provider_requests로 확인한다. “LLM HTTP 호출 정확히 32회”라고 표시하지 않는다.

## 재실행 인계

Claude 구현리뷰 → 커밋·PR CI·merge → 새 clean main R′ 고정 → R′에서 dry-run bundle 새 발급 →
실측 런타임과 일치하는 새 llm.json(위 프로필) 작성 → canonical binding으로 export.json 최종 확인·
0600·3시간 TTL·raw SHA 고정 → Codex execute 1회 → Claude validator/receipt 재검증 순서다.
이전 `f892addf97af7816004dc03a945b92946fda9535` 출력·승인서는 이력으로 보존한다.
