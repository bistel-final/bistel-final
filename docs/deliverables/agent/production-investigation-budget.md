# 신규 운영 Level 3 조사 예산 — V5-C-7.1

담당 방대혁(C/Common). 2026-09-07 사용자 `운영예산 8회로 충분해? 늘려도돼` 승인 반영.
선행 합성 실모델 조사16건의 읽기7~12는 기존8회가 충분하다는 근거가 되지 않는다.
신규 운영에는 조사 여유를 두되 이것을 실제12건 품질 보증으로 해석하지 않는다.

## 실행별 불변 계약

| 실행 | 총 Tool | 읽기 | 효과 예약 | selector | 동일 Tool |
| --- | ---: | ---: | ---: | ---: | ---: |
| Level 1·2 | 8 | 6 | 2 | 해당 없음 | 4 |
| profile 없는 기존 Level 3 | 10 | 8 | 2 | 10 | 4 |
| 신규 Level 3 `PRODUCTION_WIDE_V1` | 26 | 24 | 2 | 28 | 8 |

selector guard 거부 상한2는 유지한다. 횟수는 강제 사용량이 아닌 상한이다. 관측 결과에 따라
모델이 다음 도구와 인자를 선택하고, 근거가 충분하면 `stop`할 수 있다. 가설 출력 토큰 설정과
조치 정책은 이 예산 확대에서 변경하지 않는다.

신규 생성 transaction에서 `agent_run.evidence.investigation_budget_profile`을 저장한다.
이후 환경설정이나 재시작으로 변경하지 않는다. 저장 key 부재만 기존 정책을 의미하며
명시 null·알 수 없는 profile·Level 1·2의 wide 지정은 계약 오류다. 종료 evidence 교체도
profile을 보존하며, 덮어쓰기·삭제·기존 실행에 뒤늦게 profile 추가를 허용하지 않는다.

Tool 예약은 run row의 `FOR UPDATE` 아래에서 저장 profile과 사용/예약 내역을 읽는다.
SUCCESS·ERROR·TIMEOUT·진행 중 예약 모두 카운트하고 효과2회를 읽기로 소비하지 못한다.
graph의 다음 선택/도구 직전에도 실제 DB 원장을 읽으며 checkpoint 숫자를 권한으로 믿지 않는다.
재수화는 DB profile과 checkpoint profile의 일치를 검증하고 누적 사용량을 복원한다.

그래프 기본 실행 ceiling은100 superstep으로 확장 루프를 수용한다. 호출자가 명시한
`recursion_limit`은 그대로 존중한다. 이 엔진 설정으로 DB/selector 예산을 우회할 수 없다.

## API·화면

`GET /agent/runs/{run_id}`의 `investigation_budget`은 Level 1·2 null, 기존 Level 3
`STANDARD`, 신규 Level 3 `PRODUCTION_WIDE_V1`과 각 상한을 제공한다. 공개 trace는 기존
최대11행/selector10회와 신규 최대29행/selector28회를 구분한다. 잔여 읽기는 실제 시도에
기반하며 화면 분모도 서버 값을 쓴다. 조사 원문·private profile 증적 전체는 공개하지 않는다.

## release·기존 증적 보존

새 private readback/prepared/run-capture/round/grant는 같은 profile을 명시하고 신규 budget
SHA를 결속한다. 필드 없는 이전 artifact는 기존 schema 의미와 canonical bytes/SHA를 유지한다.
다른 profile을 가진 prepared/실제 run/round/grant를 섞어 통과시킬 수 없다.

live `kosa_agent`의 **새 Level 3 실행**은 새 wide profile을 검증한 `MOCK-NOTIFY-V1`
grant가 있어야 입장할 수 있다. 기존 legacy grant나 `ACTION-POLICY-V1` receipt만으로 새
wide를 승인하지 않는다. 기존 실행의 재개는 해당 기존 policy/grant 검증과 저장 예산을 유지한다.
E2E는 기존 명시적 격리 gate를 유지한다. 신규 profile이 정식 U10 Fixed/read8 비교 정책이나
과거 NO_GAIN·receipt·artifact를 다시 쓰지는 않는다.

코드 변경만으로 공용 운영 예산이 바뀌지는 않는다. Claude 구현리뷰·커밋·CI 뒤 clean revision
이미지와 새 profile이 결속된 실제12건/release 증적을 발급하고 기존 배포 gate를 통과해야 한다.
기존 grant를 덮어쓰거나 단순 env 숫자 수정으로 우회하지 않는다.
