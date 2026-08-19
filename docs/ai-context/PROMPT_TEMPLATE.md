# AI 작업 요청 템플릿

> 기준: 멘토 최종 패키지 (2026-08-18) · WBS v4
> 마지막 동기화: 2026-08-18

AI 도구(Claude Code · Codex · 웹 채팅)에 작업을 요청할 때 아래 형식을 쓴다.
근거 없는 요청은 반려된다.

---

## 요청 형식

```
담당자: <이름> (<A|B|C|D|Common>)
Task:   V4-<파트>-<번호> <Task 이름>
근거:   <멘토 패키지 문서·절> / <요구사항·설계 v2.0 절> / <FR 번호>

작업 내용:
- <구체적으로 무엇을 만들거나 고치는지>

완료 기준:
- <WBS 완료 기준 문장 그대로 + 검증 방법(테스트)>

제약 (해당 시):
- Tool 반환 {ok, ..., reason} / langgraph==0.2.53 / decide_action 규칙 기반
- LLM SQL 은 kosa_readonly 로만 실행 / fault_code 판단 입력 금지
- 조치 어휘 3단계 (MONITORING·WARNING·EQP_HOLD)
- 금지 용어: sensor · judgement · SPC
```

## 예시

```
담당자: 천승현 (D)
Task:   V4-D-5.3 Analytics API 노출
근거:   패키지 02_API 가이드 화면 5 / 설계 10장 / FR-D-01·02·05

작업 내용:
- POST /analytics/query 를 멘토 확정 6필드 응답으로 구현
  (generated_sql, columns, rows, is_valid, is_rejected, reject_reason)
- 내부 확장 필드(outcome·visualization)는 상위 호환으로 추가

완료 기준:
- 정책 거부가 HTTP 200 + is_rejected=true 로 표현된다
- malformed 422 · 의존성 실패 503
- 회귀 질문셋 시드 3종("챔버별 OOS 알람 수" 등)이 성공 경로를 통과한다
```

## 금지 사항

- README 상태 표에서 ✅ 가 아닌 요약 문서 본문을 프롬프트에 붙여넣지 않는다
- 구본(kosa_0813 이하) 수치·필터값을 기대값으로 쓰지 않는다
- Task ID 없이 "알아서 해줘" 식 요청을 하지 않는다
