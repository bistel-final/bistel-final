# 에이전트 정확성 우선 개발 점검 — V5-C-7.1

담당: 방대혁(C/Common). 2026-09-07 사용자 지시: 충분한 조사에서 정확성을 먼저
확인하고 운영 비용·종료 상한은 실측 뒤 정한다. 이 문서는 정식 U10 비교·release
artifact 계약을 대체하지 않는다.

2026-09-07 후속 사용자 승인으로 **새 운영 Level 3 실행에도 read24/selector28/동일Tool8을
별도 `PRODUCTION_WIDE_V1`로 결속하는 구현을 추가했다**. 아래 개발 실측과 기존 U10은
그대로 보존한다. 운영 적용·기존 실행 호환·새 release 증적 조건은
[운영 조사 예산 계약](production-investigation-budget.md)을 따른다. 코드 구현과 공용 배포는 별개다.

## 실행 범위

`backend/scripts/probe_agent_quality.py`는 production graph의 selector→Tool 결과→
재선택→가설 생성→조치 결정 경로를 실행한 뒤 `decide_action`에서 중단한다.
조치 저장·메일·Kafka·MES 호출 포트는 금지되어 있고 repository·transaction·Tool은
합성 메모리 구현이다. 운영 run `COMPLETED`나 외부 효과 성공을 발급하지 않는다.

기본 8개 입력 패턴은 코드에서 새로 작성한 정상/알람 불일치, 현재 이탈, 상류 전파,
형제 정상 대조, 이전 lot 추세, 문서 실패 후 복구, 두 번째 wafer만 이탈,
같은 문서 반복이다. 별도 변형 8개는 하한 이탈·감소 추세·정상값 변경을 사용하며,
하한용 문서도 함께 주입한다. `base`/`holdout`은 각각 8개, `all`은 총 16개다.
변형은 같은 합성 구조의 수치 변형이지 독립적인 운영 장애 표본이 아니다.
원본 최종 12건·CF8·Neo4j/문서 corpus의 검증 결과가 아니다.
각 케이스의 이름·평가 기준·기대 fault label을 모델 입력에 넣지 않는다.

## 조사 여유와 기존 계약 보존

| 범위 | 읽기 | selector | 동일 Tool 시도 | 가설 출력 토큰 |
| --- | ---: | ---: | ---: | ---: |
| profile 없는 기존 production/U10 계약 | 8 | 10 | 4 | 기존 설정 |
| 격리 DEVELOPMENT_WIDE | 24 | 28 | 8 | 4096 |
| 신규 production PRODUCTION_WIDE_V1 | 24 | 28 | 8 | 운영 설정 유지 |

실험 프로필은 고정 불변 객체로 명시 주입하며 Tool ledger와 다르면 첫 추가 호출 전에
실패한다. SUCCESS/ERROR/TIMEOUT을 모두 시도 횟수에 포함한다. 효과용 2회는 여전히
예약만 되어 있고 실험에서 실행하지 않는다. selector의 스키마·중복·인용·도구 허용
guard는 유지한다. `recursion_limit=100`으로 LangGraph 기본 재귀 상한이 조사 예산보다
먼저 끊지 않게 한다. 이 개발 실행은 운영 env·이전 run·기존 Fixed 정책·U10 schema/평가식을
변경하지 않는다. 후속 운영 확대는 별도 저장 profile과 release 결속으로 구별한다.

ko4 selector는 실제 문서 DTO의 누적 hit, 고유 chunk, 마지막 새 본문 수,
연속 신규 없음, 마지막 성공/실패를 구분한다. 같은 ID의 본문 변경은 새 정보이고
점수·순서 변경만으로는 새 정보가 아니다. 실패는 신규 없음으로 오인하지 않는다.
이 정보는 모델의 다음 질문/도구 선택을 돕지만 도구 순서나 조기 stop을 강제하지 않는다.
ko1~ko3 저장본 읽기 및 기존 bytes/SHA는 보존한다.

실제 원장의 요청별 실패도 다음 선택과 최종 가설에 전달한다. 같은 run·같은 canonical
요청의 NOT_FOUND/POLICY_REJECTED는 다시 선택할 수 없고, TIMEOUT/unknown ERROR·다른
대상·새 질의는 구분한다. U10의 기존 공통 retry1은 바꾸지 않는다. 실패한 원문의 질의·
접속 정보는 전달하지 않으며 실패 ID가 정상 근거 인용 후보에 섞이지 않는다.

`agent-hypothesis-v3-ko4`(ko3에 사유별 재작성 지시 추가)는 실패 요청과 다른 성공 요청, FDC 이탈과 제품 계측 PASS,
형제 현재 관측과 과거 부족을 구분한다. CURRENT_CHAMBER는 단순 조회 위치가 아니라
실제 이상 근거가 있는 소재 주장으로 새 생성 시 검증한다. 정상 현재·STABLE 이력만으로
CURRENT를 허용하지 않으며, 특정 고장유형과 confidence도 이탈의 크기만으로 추정하지
않도록 안내한다. snapshot 사용 시 중복 원시 FDC 배열은 생략한다. 기존 가설 저장본
읽기·public schema·조치 결정 정책은 보존한다.
한국어 서술에 다른 문자권의 단어가 섞이는 경우는 기존 `KOREAN_OUTPUT_REQUIRED`
교정으로 처리한다. 실제 근거의 식별자와 Latin·Greek 과학 표기는 보존하며, 이것은
모든 자연어 품질 문제를 자동 검출하는 언어 판별기가 아니다.

## 실행과 증적

저장소 루트에서 기본 명령은 plan만 발급하고 HTTP를 호출하지 않는다.

```sh
.venv/bin/python backend/scripts/probe_agent_quality.py
.venv/bin/python backend/scripts/probe_agent_quality.py --execute --suite base
.venv/bin/python backend/scripts/probe_agent_quality.py --execute --suite all --max-http 512
```

실행에는 사용자 LLM 승인이 필요하다. provider는 기존 OpenAI/Luna low이며 요청별
출력 상한만 격리 프로세스에서 4096으로 적용한다. 한 실행의 기본 HTTP 상한은 256회이며
명시 인자로 최대 512회까지 선언할 수 있다. 전송 재시도·교정도 모두 포함한다.
상한은 개발 중 무한 반복 방지 장치이며 품질 PASS 기준이
아니다. 실패를 지우거나 같은 결과 디렉터리에 재시도하지 않는다. 원본 데이터의 새로운
반출 승인이 있는 것으로 해석하지 않는다.

새 0700 디렉터리의 plan/request/response/case/result JSON은 0600·no-clobber다.
plan에 revision, dirty 상태, 소스별 SHA, 프로필, 모델, 입력과 평가 기준을 기록하고
종료 시 소스 변경 여부를 검증한다. API key/headers는 기록하지 않는다. 관측 불가 usage는
0이 아닌 미관측으로 남긴다. 기존 output 아래 정식 artifact·receipt·grant는 수정하지 않는다.

## 판정

실행 완료와 품질 합격은 별개다. 실제 도구/근거 획득, 새 정보에 따른 선택 변화,
수치·namespace 인용의 정확성, 필요한 대비 관측 누락, 확정 원인/피해의 과장 여부,
자율 stop과 시스템 강제 종료를 각각 검토한다. 특정 도구 순서나 모든 도구 사용을
정답으로 강제하지 않는다. 정상 첫 wafer만 보고 이탈한 두 번째 wafer를 놓치는 등
핵심 근거 누락은 최종 JSON 생성에 성공해도 품질 실패다.

모델의 confidence는 자기보고이지 검증 정확도가 아니다. 단일 합성 묶음의 개선은
원본 전체·실서비스 일반화의 증거가 아니므로 실패 보완 뒤 별도 변형/반복 및 승인된
실제 입력 범위의 점검이 필요하다. 이 실측 후 사용자 승인으로 신규 운영 상한24를 결정했으나
그 상한이 모든 실제 케이스에 충분하다는 검증이나 운영 품질 합격을 뜻하지 않는다.
도구 선택·인자·최종 결과를 분리하여 평가하는 원칙은
[OpenAI 평가 가이드](https://developers.openai.com/api/docs/guides/evaluation-best-practices)를
참고했다.

## 2026-09-07 개발 실측 인계

source SHA 고정 상태의 base8+변형8 실제 자율 조사는 16건 모두 LLM_STOP으로 종료했다.
읽기7~12/개발상한24, guard 거부0. 이어 발견된 최종 서술 문제를 수정하고 같은 관측·
요청/녹화 선택으로 가설만 16건 다시 생성했다(실 HTTP17, 교정1회 포함).
이는 새 자율 조사16건이 아니며 5-class 분류 정확도나 실제 서비스 성능을 입증하지 않는다.
마지막 코드 영향 범위 45파일 1,105 PASS, API 동기 및 Ruff/shellcheck PASS를 확인했다.
상세 실패 이력·비용·SHA·범위는 로컬 `output/v5-c-7.1/20260907-agent-accuracy-improvement.md`
및 두 실행 디렉터리의 독립 리뷰를 따른다. 후속 운영 예산 구현과 정본12건 검증은 별도다.
