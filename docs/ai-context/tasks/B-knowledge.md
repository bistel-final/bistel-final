# B — Knowledge

> 기준: 멘토 최종 패키지 (2026-08-18) · 역할분담 v10.0 B · WBS `V4-B-*`
> 마지막 동기화: 2026-08-18

## 담당 범위

RAG(문서 임베딩·검색), Neo4j 마스터 그래프, 문서 검색 화면(4).

## 최소 구현 (골든 시나리오)

```
POST /documents/search   body {query, model_code?, top_k}
→ [{ doc_id, title, section, score, content }]
```

## 핵심 규칙

- **RAG 원문은 패키지 `sample/rag/` 수정본이 정본** — 기존 임베딩은 폐기하고
  재임베딩한다. 수정 내용: R01/R02/R03 정리 · LOT_HOLD 삭제 ·
  고정 설비 상하류 서술 삭제 · sensor→parameter.
- 청크는 제목 단위, `model_code` 필터로 장비 스펙 문서를 좁히면 정확도가 오른다.
- **Neo4j 는 마스터/구조만.** 설비 간 상하류 엣지는 존재하지 않는다 —
  공정 흐름은 ProcessStep NEXT_STEP, 실제 라우팅은 lot_history.
  파라미터 값·trace·alarm 은 전부 PostgreSQL (공유 키로 조인).
- 임베딩 모델·차원 등 구현 상세는 설계 5.2 · WBS `V4-B-*` 기준.
