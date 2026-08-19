# 디자인 Export 아카이브 규칙

> [!CAUTION]
> **FINAL-DOC — VISUAL ARCHIVE ONLY.** 이 폴더는 재설계 이전 디자인 export 이력이다.
> 레이아웃·Mock·수치·필드·상태명·API 계약을 신규 구현·테스트·제출 근거로 사용하지 않는다.

현재 Frontend 작업 규칙은 [frontend/AGENTS.md](../../AGENTS.md)를 따른다. 기능·데이터·화면·API
기준은 다음 활성 문서를 직접 확인한다.

- [요구사항 정의서 v2.1 작업본](../../../docs/specifications/요구사항정의서_v2_1_작업본.md)
- [시스템 설계서 v2.1 작업본](../../../docs/specifications/시스템설계서_v2_1_작업본.md)
- [역할분담 v10.1 작업본](../../../docs/specifications/FDC_프로젝트_역할분담_v10_1_작업본.md)
- [API 명세서 v3 작업본](../../../docs/deliverables/api/API명세서_v3_작업본.md)

WBS v5와 역할별 Task가 확정되기 전에는 이 archive를 바탕으로 신규 화면을 구현하지 않는다.
최종 canonical 화면은 Dashboard·Alarm History·Agent·Documents·Ontology 5개이며, Mock은 최종
source manifest와 API v3 fixture에서만 만든다.

> 이 파일은 같은 폴더의 `AGENTS.md`와 내용이 같아야 한다.
