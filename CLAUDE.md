# BISTel FDC Agent

LangGraph 기반 반도체 FDC 이상감지 에이전트. FastAPI + React 모노레포.

AI 작업 규칙은 `docs/ai-context/` 에 있다. 작업 전 반드시 읽는다.

1. `docs/ai-context/README.md` — 문서 우선순위와 라우팅 표
2. `docs/ai-context/01-project-rules.md` — 강제 규칙
3. `docs/ai-context/02-domain-rules.md` — 도메인 규칙과 불변 수치
4. 담당 파트 — `docs/ai-context/tasks/{A-detection|B-knowledge|C-agent|D-analytics}.md`

원본 사양은 `docs/specifications/` 이며 요약본과 충돌하면 원본이 우선한다.
요청·보고 양식은 `docs/ai-context/PROMPT_TEMPLATE.md` 를 따른다.

세 원본 문서는 합계 약 137K 토큰이다. 통째로 읽지 말고 라우팅 표에서 필요한 절만 읽는다.

> 이 파일은 `AGENTS.md` 와 내용이 같아야 한다. 규칙 본문을 여기에 복제하지 않는다.
