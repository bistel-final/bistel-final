# B — Knowledge

> [!CAUTION]
> **사용 중지 — 아래 본문은 이전 epoch·부분 동기화 이력이며 구현 근거로 사용하면 안 됩니다.**
> 현재는 `docs/ai-context/README.md`에서 안내하는 최종 패키지 기준표와 v2.1 요구사항·설계·
> 역할분담·API v3만 사용합니다. WBS v5와 새 `V5-B-*` Task 문서가 확정되기 전에는 아래 본문의
> 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-11
> 담당: 강연권 · 모듈 `backend/app/knowledge/` · `frontend/src/features/knowledge/`

Neo4j 관계 조회와 pgvector 문서 검색, Tool·API·관계 화면, 검색 품질 평가를 책임진다.

**기반구축(최초 적재)은 완료 상태다.** B의 책임은 적재 결과 **검증**과 서비스 로직 구현이다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-B-01 | Neo4j 적재 확인 | 필수 |
| FR-B-02 | 문서 청크·임베딩 검증 | 필수 |
| FR-B-03 | Tool `get_equipment_context` | 필수 |
| FR-B-04 | Tool `search_documents` | 필수 |
| FR-B-05 | 임베딩 모델 싱글턴 | 필수 |
| FR-B-06 | 관계·근거 API·화면 | 필수 |
| FR-B-07 | 검색 품질 평가 | 필수 |
| FR-B-08 | 하이브리드 검색 (pg_trgm) | 도전 |

---

## 완료 기준

```
Neo4j        노드 24 · 관계 26, UPSTREAM_OF 조회 성공
문서         document 3 · document_chunk 39 (ET-7500 13 · PH-9000 12 · TROUBLE 14)
             embedding IS NULL 0건, 차원 1024
             청크 길이 120자 미만 0건 · 1,200자 초과 0건
관계 Tool    PHO-01-C1 조회 시 downstream에 ETC-01 포함
문서 Tool    고정 시험 질문 3종에서 관련 절 상위 반환
싱글턴       임베딩 모델 생성 횟수 1회 (로그 또는 단위 테스트로 확인)
평가         문서 골드 10문항 이상 Recall@4 >= 0.80, MRR >= 0.70
             관계 골드 6건(챔버 4 + 장비 2) 정확도 100%
```

**고정 시험 질문 3종** (가이드 03 원문 그대로)

```
반사파가 올라가면 무슨 문제인가
포커스가 벗어나면 CD가 어떻게 되나
장비를 세우려면 승인이 필요한가
```

**관계 골드셋 경계 필수**: PHO-01-C1 downstream 존재 / ETC-01-C1 downstream 없음·upstream 존재

---

## 주의

**Cypher는 parameterized만 쓴다.** 입력 ID를 쿼리 문자열에 삽입하지 않는다.

**배포 `master.cypher`의 노드 속성만으로 DTO를 만들 수 없다.**
Chamber는 `chamber_id`·`chamber_no`만, Equipment는 장비 속성만 가진다.
`Chamber-[:PART_OF]->Equipment-[:LOCATED_IN]->Area`, `Equipment-[:PERFORMS]->ProcessStep`
관계를 map projection으로 따라가 `equipment_id`·`model_code`·`area_id`·`step_id`를 조합한다. (설계 6.1)

**반환 순서를 고정한다.** upstream/downstream은 `equipment_id ASC`, sibling은 `chamber_id ASC`.
동일 입력에서 JSON 순서가 흔들리면 계약 테스트가 깨진다.

**`model_code` 지정 시 COMMON을 항상 포함한다.**
`(document.model_code = :model_code OR document.model_code = 'COMMON')` — TROUBLE 가이드가 COMMON이다.

**`score = 1 - cosine_distance`**, top_k 기본 4·허용 1~10, 동률 정렬은 `score DESC, doc_id ASC, chunk_seq ASC`.

**임베딩 모델은 process-local lazy singleton + lock**이다. 런타임 네트워크 다운로드를 하지 않고
`EMBEDDING_MODEL_PATH`의 사전 다운로드본을 쓴다. (설계 6.2)

**저장 본문에는 제목 접두어를 넣지 않는다.** 임베딩 입력을 만들 때만 "문서제목 / 절제목"을 앞에 붙인다.

**검색 결과 0건은 오류가 아니다.** HTTP 200 + 빈 hits.

**문서 식별자 이름을 분리한다.** API `document_id`는 DB `document.doc_id`·`document_chunk.doc_id`에 대응한다. `doc_type`은 `SPEC | MANUAL | TROUBLESHOOT | null`만 허용한다.

---

## API

```http
GET  /relations/chambers/{chamber_id}
GET  /relations/equipment/{equipment_id}
POST /documents/search              query, model_code?, top_k=4
GET  /documents/{document_id}
```

없는 ID는 404. DTO는 설계 10.3.

---

## 화면

| 경로 | 내용 |
|---|---|
| `/knowledge` | 장비·챔버 검색, upstream/downstream/sibling 관계도, 문서 hit·score·본문 |

노드 선택·문서 펼치기, 검색 0건은 Empty 표시. 관계도 라이브러리는 자유 선택하되 **API DTO는 바꾸지 않는다.**

---

## 원본 절

```
설계 6.1  Neo4j 조회
설계 6.2  임베딩 모델 싱글턴
설계 6.3  문서 검색
설계 6.4  품질 평가
설계 6.5  기반 데이터·임베딩 provenance 검증
설계 10.3  B Knowledge API DTO
요구사항 5.2  FR-B-01~08
요구사항 7.4  RAG 문서셋
```
