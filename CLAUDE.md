# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트. FastAPI + React 모노레포.

> [!CAUTION]
> 멘토님 제공 최종 `project.zip`(2026-08-18) 기준이다.
> `kosa_0813`, 요구사항·설계 v2.0 이하/역할 v10.0 이하/WBS v4 이하, `docs/ai-context/01`~`07`,
> `PROMPT_TEMPLATE.md`는 이전 epoch 이력이며 신규 구현 근거로 사용하지 않는다.

작업 전 반드시 다음 순서로 읽는다.

1. `docs/ai-context/README.md` — 전환 상태와 문서 라우팅
2. `docs/reference/mentor-final-20260818/README.md` — 검증된 최종 데이터 기준표
3. `docs/specifications/요구사항정의서_v2_1_작업본.md` — 사용자 동작·수용 기준
4. `docs/specifications/시스템설계서_v2_1_작업본.md` — 구현·데이터 계약
5. `docs/specifications/FDC_프로젝트_역할분담_v10_1_작업본.md` — 담당·소유권
6. `docs/deliverables/api/API명세서_v3_작업본.md` — 외부 API 최소 호환·확장 계약
7. `docs/planning/Task분해_WBS_v5_작업본.md` — `V5-*` Task·선행관계
8. `docs/ai-context/tasks/{A,B,C,D}-*.md` — 역할별 Task·완료 기준·주의

구현 작업은 담당자와 해당 `V5-*` Task ID를 명시한다. 충돌하면 최종 데이터 기준표 → 요구사항 →
시스템설계서 → 역할분담 → API 명세 → WBS 순으로 판단하고 충돌을 보고한다.

> 이 파일과 짝 파일(`CLAUDE.md`·`AGENTS.md`)은 내용이 같아야 한다. 규칙 본문을 여기에 복제하지 않는다.
