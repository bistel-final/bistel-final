# 사용자 승인 변경: 이메일 알림 · 자동 MES Mock 연동

2026-09-06 · 담당 방대혁(C/Common) · `V5-C-7.1` 범위 변경.
영향 계약: `V5-C-3.3`, `V5-C-4.3`, `V5-C-4.5`, `V5-C-5.2`, CM-5.2 실행 증적.

## 결정과 범위

사용자는 멘토 피드백에 따라 승인/반려와 이메일 확인 기록을 제외하기로 동의했다.
새 팀 실행 정책은 `MOCK-NOTIFY-V1`이다. 확인 링크·확인 완료 버튼·열람 추적·확인 테이블/API를
추가하지 않는다. 이메일은 발송 처리 결과만 기록하고 사용자가 읽었는지는 주장하지 않는다.
에이전트 Tool 호출이 n8n을 실행하며, 사용자 입력은 Kafka 발행 조건이 아니다.

| 규칙 조치 | 새 정책의 외부 효과 | 사람 대기 |
|---|---|---|
| MONITORING | 내부 기록만 | 없음 |
| WARNING | n8n SMTP 조치 알림 | 없음 |
| EQP_HOLD | n8n SMTP 조치 알림 → Kafka 요청 → MES Mock 모의 응답 → 결과 반영 | 없음 |

`EQP_HOLD`/`HOLD`는 기존 코드·메시지 호환 명칭이다. 물리 설비 제어를 구현하거나 증명하지 않는다.
화면에는 **MES Mock 응답 확인**으로 표시하며 설비 정지·LOT 배출·재가동 완료로 표현하지 않는다.
그래프 `COMPLETED`는 실행 종료이지 모든 외부 효과 성공을 뜻하지 않는다. EMAIL/MES 각각의
`WAITING/SENDING/SENT/FAILED/UNKNOWN`을 별도로 확인한다. 이메일 실패/불확실은 사람 승인 대기를
만들지 않으며, 이미 claim한 전송을 자동 재발행하지 않는다. 구성·정합성 오류는 계속 차단한다.

## 저장 · API · n8n 호환

- 새 조치는 `requires_approval=false`, approval row 없음, EMAIL/MES 초기 `WAITING`이다.
  기존 `action_history` 호환값 `approval_required=N`, `approval_status=AUTO`, 시스템 actor를 사용한다.
  사람의 `APPROVED` 기록을 위조하거나 기존 승인 대기를 자동 승인하지 않는다.
- 생성 run의 `evidence.action_provenance.action_policy_version`에 정책을 고정한다.
  정책이 다른 기존 action 재사용은 거부한다. MES claim은 CREATED 연결·incident·run 상태·정책·장비
  유일성을 같은 트랜잭션에서 검증한 뒤 기존 row lock/CAS를 사용한다.
- 공개 action DTO에 `delivery_policy: ACTION-POLICY-V1 | MOCK-NOTIFY-V1`을 추가한다.
  누락 정책은 기존 V1 호환이다. 새 정책은 EQP_HOLD도 `approval_status=null`이며 상세 approval도 없다.
  기존 7개 화면/경로를 유지하고 메뉴는 `Agent 분석 · 조치`로 표현한다.
- WF2의 기존 11-field 스키마에 `email_kind=ACTION_NOTIFY` 조합을 추가한다.
  action은 WARNING/EQP_HOLD, `approval_id=null`; HMAC·recipient 검증·결과 반영은 유지한다.
- WF3의 11-field 스키마는 유지한다. 새 정책의 `decided_by=policy:MOCK-NOTIFY-V1`,
  `decided_at=delivery.started_at`은 자동 발행 정책/시각이며 사람의 승인 또는 확인이 아니다.
- 기존 `ACTION-POLICY-V1` 승인·반려 경로와 이력은 보존한다. 변경 전 데이터나 원본 ZIP을 수정하지 않는다.
  데이터 epoch, 12 incident, 규칙별 5/4/3 분포, ground-truth 격리는 그대로다. DB migration은 없다.

## 검증 · 배포 경계

신규 정상 실행의 목표는 **12 COMPLETED · 조치 5/4/3 · 이메일 7건 · MES Mock 요청/응답 3건 · 승인 0건**이다.
이는 실행 목표이며 이번 변경에서 공용 실측을 완료했다는 뜻이 아니다.
과거 9 COMPLETED/3 WAITING_APPROVAL·승인 전 Kafka 0건·승인 요청 메일을 요구하는 v61/Stage2/golden-flow
증적과 혼합하지 않는다. 원본/기발급 artifact의 내용·SHA·attempt_id는 그대로 보존한다.

`AGENT_ACTION_POLICY`의 배포 기본값은 `ACTION-POLICY-V1`이다. 코드 리뷰와 WF2 적용 확인 후 새 실행에만
명시적으로 `MOCK-NOTIFY-V1`을 선택한다. 이번 작업은 실제 `.env`, 공용 n8n, 실행 중 컨테이너를 변경하지 않는다.
`runtime-manifest.json`의 WF2 SHA는 수정한 **저장소 소스**의 핀이지 공용 import 완료 증명이 아니다.

묶음 C의 새 정책용 Stage2 prepare/resume/publish·v2 증적 chain·golden-flow·평가·Gate가 연결됐다.
새 production Level 3는 예전 receipt가 아니라 보호된 `release-grant.json`의 R/attempt/policy와
bundle/publication SHA 결속을 검증한다. actual preflight의 전체 재계산과 CLOSED fence의
qualification→grant→실 L3 검증→rollback rehearsal→OPEN을 별도로 요구한다.
기존 Level 3 증적을 새 정책의 배포 허가로 재사용하지 않는다. plain legacy full/hold는 새 정책을
계속 거부하며 새 실행은 prepare→별도 SMTP_SEND_GRANT→resume 경로만 사용한다.
**공용 실측·실 artifact 발급·운영 전환은 미수행**이다. 구현 연결과 운영 완료를 구분한다.
운영 인계: [MOCK-NOTIFY Stage2 실행 절차](mock-notify-stage2-runbook.md).

배포 순서: Claude 구현리뷰 → 보완/커밋·CI·머지 → clean 40자리 revision 및 이미지 결속 →
WF2 소스/공용 runtime 일치 확인 → 신규 실행·채널 결과 검증 → 새 정책용 증적 발급.
GitHub 작업은 Claude가 담당하며 이번 단위에서 commit/push/공용 메일·Kafka 발행은 하지 않는다.
