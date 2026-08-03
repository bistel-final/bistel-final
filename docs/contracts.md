# API and Tool Contracts

## REST API 오류 규칙

- 요청 형식이 잘못된 경우 `422 Unprocessable Entity`
- 식별자에 해당하는 데이터가 없는 경우 `404 Not Found`
- 동일 승인 건이 이미 처리된 경우 `409 Conflict`

## Tool 공통 응답

Tool은 오류가 발생해도 예외로 그래프를 종료하지 않고 정상 JSON으로 반환합니다.

```json
{
  "ok": false,
  "reason": "오류 사유"
}
```

`data`, `error`, `meta`를 두는 별도의 공통 래퍼는 추가하지 않습니다. 호출 상태·지연시간·입출력은 `agent_tool_call`에 기록합니다.

## Tool 목록

| Tool | 담당 | 핵심 입력 |
|---|---|---|
| `get_fdc_summary` | A | `lot_hist_id` |
| `get_equipment_context` | B | `chamber_id` |
| `search_documents` | B | `query`, `model_code`, `top_k` |
| `send_action` | C | `action_id`, `agent_run_id` |
| `generate_analysis_plan` | D | `question` |

## 계약 변경 규칙

API나 Tool의 함수명·필드·상태코드를 변경할 때는 코드, 테스트, 이 문서를 같은 PR에서 수정합니다.
