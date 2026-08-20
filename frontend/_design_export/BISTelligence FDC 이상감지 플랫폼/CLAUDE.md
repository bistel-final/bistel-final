# 디자인 Export 아카이브 — 작업 지침 없음

> [!CAUTION]
> **LEGACY VISUAL ARCHIVE ONLY.** 이 디렉터리 아래 파일은 최종 `project.zip` 이전의 시각 이력이다.
> 레이아웃·Mock·수치·필드·상태명·DTO·API·테스트·완료 기준을 신규 구현에 적용하지 않는다.
> 이 파일도 별도의 Frontend 구현 규칙을 정의하지 않는다.

실제 작업은 이 archive 밖에서 [저장소 CLAUDE.md](../../../CLAUDE.md)·
[AGENTS.md](../../../AGENTS.md)와 [AI 문서 라우팅](../../../docs/ai-context/README.md)을 먼저 읽고,
리뷰된 WBS v5 `V5-*` Task를 따른다. canonical 사용자 화면은 Dashboard·Alarm History·Agent·
Documents·Ontology 5개다.
Ontology public API는 선택 chamber의 subgraph와 context를 함께 반환하는
`GET /relations/chambers/{chamber_id}` 하나이며, 이 archive의 7·8화면 구조나 직접 Neo4j 접속은
현재 계약이 아니다.
