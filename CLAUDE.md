# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트. FastAPI + React 모노레포.

> [!CAUTION]
> 신규 `kosa_0813.zip` 전환 작업 중이다. 기존 51건·Fault 정답·ACT-0001~0010·4단계 조치를
> 구현 근거로 사용하지 않는다. `docs/ai-context/01`~`07`, `PROMPT_TEMPLATE.md`, `tasks/*.md`는
> v1.9/v1.10/v9.6 구 이력이므로 v2 재생성 전까지 읽기·복사·프롬프트 입력을 금지한다.

작업 전 반드시 다음 순서로 읽는다.

1. `docs/ai-context/README.md` — v2.0 문서 우선순위와 라우팅 표
2. `docs/specifications/요구사항정의서_v2_0_작업본.md` — 사용자 동작·수용 기준
3. `docs/specifications/시스템설계서_v2_0_작업본.md` — 구현·데이터 계약
4. `docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md` — 담당·소유권
5. `docs/planning/Task분해_WBS_v4_작업본.md` — 현재 수행할 해당 `V4-*` Task

작업 요청·계획·완료 보고에는 담당자와 해당 `V4-*` Task ID를 명시한다. 위 원본과 WBS가
충돌하면 요구사항 → 시스템설계서 → 역할분담 → WBS 순으로 판단하고 충돌을 보고한다.

> 이 파일은 `AGENTS.md` 와 내용이 같아야 한다. 규칙 본문을 여기에 복제하지 않는다.
