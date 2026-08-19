# 06. Frontend 가이드

> 기준 원천: 멘토 최종 패키지 `frontend/`(React/Vite 정본) · 02_API 가이드 (2026-08-18)
> 보조 기준: 요구사항 v2.0 11.2 · 시스템설계서 v2.0 12장
> 마지막 동기화: 2026-08-18

---

## 1. 정본과 전략

- 대시보드 정본은 **멘토 제공 `frontend/`(React/Vite) 실서비스**다.
  `mvp/FDC_알람_MVP.html` 단독 목업은 레거시 참고용.
- 실연동 원칙: **렌더 함수는 그대로 두고 `const DATA = {...}` 를 `fetch()` 결과로 치환**한다.
- 기존 자체 화면(NlqHistoryPanel 등)과 멘토 프론트의 병합 전략은 팀 협의 사항 (README 참조).

## 2. 5화면 구성

| 화면 | 담당 | 내용 |
|---|---|---|
| 1 알람 대시보드 | A | KPI 4종 + 위젯 4종. `GET /alarms` 1회 호출 후 **클라이언트 집계** |
| 2 알람 | A | 트렌드 차트 + 알람 리스트. 행 클릭 → 해당 wafer 트렌드 |
| 3 Agent 분석 | C | KPI + 서브탭(승인·이력 / 실행 이력 / 감사 로그) |
| 4 문서 검색 | B | RAG 검색 결과 목록 |
| 5 자연어 질의 | D | 질문 입력 → SQL·결과 표시 |

전역 필터: `date_from` · `date_to` · `area`(필수) · `equipment` · `chamber`.
**필드명을 API 와 정확히 일치**시킨다 — 불일치는 조용한 0건으로 나타난다.

## 3. 차트 규칙 (확정)

```
일자별 알람 추이     라인차트 (잠정 확정 — 05_답변 우선, 멘토 확인 대기)
챔버별 · 파라미터별  누적막대, OOS/OOC 구분
```

**trace 상세 차트 (화면 2) — 렌더 규칙 강제**

```
X축          시간(measured_at). recipe step 경계마다 세로 구분선 + 'step N' 라벨
한계선       5선(LSL/LCL/TARGET/UCL/USL) 점선 — GET /parameters 값 사용
점 표시      모든 점 시리즈 색(파랑), 알람 발생 점만 색 마킹 (OOS 빨강 · OOC 주황), 크기 동일
툴팁         설비·챔버·recipe·step·lot·wafer·event time(전체)·value
```

## 4. 화면별 데이터 매핑 요점

- **화면 1**: 총 알람=`A.length`, OOS/OOC=type 필터, 이상 챔버=`Set(chamber).size`.
  Agent 분류 위젯은 `GET /agent/runs` 를 fault_code 로 group.
- **화면 2 리스트**: value·LSL·USL 컬럼 — 한계선은 `GET /parameters` 매핑.
  통지 컬럼 = 해당 (lot,chamber) 조치의 `notify_status`(이메일)·`mes_status`(MES).
- **화면 3 승인**: 결정 후 `GET /approvals` + `GET /audit-logs` reload.
- **화면 5 (D)**: 응답 `{generated_sql, columns, rows, is_valid, is_rejected, reject_reason}`.
  거부 시 `reject_reason` 을 사용자에게 그대로 표시. 서버 확장 필드(chart_type 등)는
  있으면 활용, 없어도 화면이 성립해야 한다.

## 5. 상태·통신 원칙

- polling 기반 (실시간 스트리밍 범위 밖).
- 응답 시각 표기 `YYYY-MM-DD HH:MM:SS`.
- 에러 표시: 200+거부(본문 필드) / 422 malformed / 503 의존성 — 04 문서 5장 규약.
