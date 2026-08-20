# B — Knowledge

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-20
> 담당: 강연권 · 모듈 `backend/app/knowledge/` · `frontend/src/features/knowledge/`

Neo4j 44/85 안전 검증, RAG 원본 보존·정정본 적재, 임베딩 검색, 근거 provenance와 화면 4·5를
책임진다. B의 WBS 범위는 **13 Task / 21.5h**이며, P0 기능 구현과 P1 운영 검증을 분리한다.
corpus revision Task는 만들지 않는다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-B-01 | Neo4j 검증 | 필수 |
| FR-B-02 | 문서 정합성 | 필수 |
| FR-B-03 | `get_equipment_context` | 필수 |
| FR-B-04 | `search_documents` | 필수 |
| FR-B-05 | RAG 스키마·임베딩 재사용 | 필수 |
| FR-B-06 | Documents·Ontology 화면 | 필수 |
| FR-B-07 | 검색·관계 평가 | 필수 |
| FR-B-08 | 하이브리드 검색 | 도전·WBS v5 미편성 |

관련 비기능 요구사항은 NFR-02(비밀정보), NFR-06(재현성), NFR-09(Tool 공통 계약),
NFR-11(API 계약), NFR-14(적재 안전성), NFR-17(UI 상태)다.

## Task (WBS v5 정본)

| ID | P | 완료 기준 | FR/NFR | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-B-1.1 | P0 | RAG 스키마 단일 소유. 완료: ① `03_db/01_schema.sql`의 `vector` extension·`document`·`document_chunk`만 3개 DB에 생성한다(`embedding vector(1024)`). `pg_trgm`·`document_corpus`·corpus revision과 ①의 나머지 table은 채택하지 않는다. CM-3.5 role matrix에 맞춘 최소권한 GRANT를 적용하고 PUBLIC 권한·Common migration 중복 객체는 0건이다 | FR-B-02, FR-B-05, NFR-02 | V5-CM-3.1, V5-CM-3.5 | 1.0h |
| V5-B-1.2 | P0 | RAG 문서 기능 정합. 완료: 불변 원본은 ③ `project.zip`·CM-1.3 hash로 보존하고 저장소에는 별도 corrected Markdown 3종만 정본으로 둔다. 고정 EQP upstream, score·metrology·반복·하류·후속 정상 기반 조치 상하향, 구 10건 서술을 제거한다. R03·R01·Fault 후보·PH/ET 범위를 최종 계약에 맞추고 Markdown 구조와 금지 문구 검사를 통과한다. 자동 marker·상세 provenance 검증은 B-1.4로 분리한다 | FR-B-02, NFR-06 | V5-CM-1.3 | 1.5h |
| V5-B-1.3 | P0 | RAG 기능 적재. 완료: ① `load_documents.py`의 청킹·임베딩 로직을 최소 adapter로 재사용해 B-1.2 corrected 경로만 받는다. 명시 DSN과 대상 DB allowlist(`kosa_agent`, `kosa_agent_e2e`)를 강제하고 기본 DSN·원본 fallback·`--reset`을 거부한다. DB별 한 transaction으로 3문서와 canonical `<document_id>:cs1:<4자리>` chunk를 적재하며 `BAAI/bge-m3`·1024, 문서 3건·chunk 1건 이상/문서·embedding NULL 0·대표 검색 smoke를 확인한다. 이 완료 뒤 B-2 기능 구현은 운영 검증을 기다리지 않고 시작할 수 있다 | FR-B-02, FR-B-05, NFR-14 | V5-B-1.1, V5-B-1.2, V5-CM-3.5 | 1.5h |
| V5-B-1.4 | P1 | RAG 운영 검증 강화. 완료: B-1.3 적재를 `kosa_text2sql`까지 확장해 3 DB를 정렬하고 권한·live fingerprint·중복 0·idempotent no-op을 검증한다. source/corrected SHA-256·정정 사유, cs1 contract hash, 고정 model revision·weights hash·dimension, 3 document ID·chunk 수·검색 smoke를 DB별 COMMITTED marker에 marker-last로 기록하며 readiness가 live DB와 대조할 수 있게 한다 | FR-B-02, FR-B-05, NFR-06, NFR-14 | V5-B-1.3, V5-B-2.1 | 2.0h |
| V5-B-2.1 | P0 | DocumentSearchRepository·Service. 완료: pgvector 검색을 구현하고 `query`·`model_code`·`top_k`를 지원한다. embedding 모델은 process당 1회 생성해 재사용한다(singleton). **API와 Tool이 이 Service를 공유하며 검색 로직을 중복 구현하지 않는다** | FR-B-04 | V5-B-1.3 | 2.0h |
| V5-B-2.2 | P0 | `search_documents(query, model_code=None, top_k=4)` Tool. 완료: exact signature로 `DocumentSearchService`를 호출해 chunk·score·근거 ID를 반환한다. 0건·timeout·오류는 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따른다 | FR-B-04, NFR-09 | V5-B-2.1 | 1.5h |
| V5-B-2.3 | P0 | `POST /documents/search`. 완료: Documents 화면이 쓰는 검색 API로, Tool과 **동일한** `DocumentSearchService`를 재사용한다. 실제 근거 내용(document·chunk)을 반환한다 | FR-B-04 | V5-B-2.1 | 1.0h |
| V5-B-3.1 | P0 | final `master.cypher` offline parser·fixture. 완료: ③ 원본을 DB 접속 없이 destructive 문장과 seed 100개 statement로 분리·파싱하고 node 44·relationship 85, label/type·business key·방향·중복 0을 고정 fixture와 단위 테스트로 검증한다. source hash 불일치·미지원 statement는 safe apply 전에 실패한다 | FR-B-01, NFR-06 | V5-CM-1.3 | 2.0h |
| V5-B-3.2 | P0 | GraphService·`get_equipment_context(chamber_id)` Tool. 완료: CM-2.7이 적용·marker한 graph를 읽는 GraphRepository·Service를 만들고 exact signature로 장비·모델·AREA·Process Step·인접 Step·파라미터·형제 chamber와 stable relation/graph provenance를 반환한다. 0건·timeout·오류는 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따르며 elementId·고정 설비 upstream·LOT routing 추정은 노출하지 않는다 | FR-B-03, NFR-09 | V5-CM-2.7 | 1.5h |
| V5-B-3.3 | P0 | 단일 `GET /relations/chambers/{chamber_id}`. 완료: CM-2.7 marker가 가리키는 graph의 chamber 중심 read-only 응답을 B-3.2 `GraphService`에서 만들고 Neo4j 자격증명·Cypher·elementId를 노출하지 않는다. 같은 Method+Path의 다른 DTO를 만들지 않으며 노드 타입 확장은 같은 응답 shape의 `/relations/{node_type}/{node_id}`로만 확장한다 | FR-B-06, NFR-02, NFR-11 | V5-CM-2.7, V5-B-3.2 | 1.5h |
| V5-B-4.1 | P1 | 화면 4 Documents. 완료: `POST /documents/search`를 실제 연동해 근거·deep link와 Loading·Error·Empty·Success를 표시한다 | FR-B-06, FR-I-02, NFR-17 | V5-B-2.3 | 2.0h |
| V5-B-4.2 | P1 | 화면 5 Ontology. 완료: chamber를 선택해 단일 관계 API의 장비·모델·AREA·Process Step·인접 Step·파라미터를 시각화하고 Loading·Error·Empty·Success를 검증한다. Neo4j Browser iframe·비밀정보 노출은 0건이다 | FR-B-06, NFR-02, NFR-17 | V5-B-3.3 | 2.0h |
| V5-B-4.3 | P1 | 최소 검증·평가. 완료: B-1.4 운영 검증 artifact, RAG 검색 contract·embedding singleton·Neo4j 44/85·chamber 관계 fixture를 검증하고 **Recall@4 ≥ 0.80, MRR ≥ 0.70, 관계 질문 100%**와 실패 사례를 artifact에 기록한다. 이 Task는 B-2/B-3/B-4 기능 구현의 착수 gate가 아니라 최종 인수 gate다 | FR-B-07 | V5-B-1.4, V5-B-2.2, V5-B-3.2 | 2.0h |

**합계 13 Task / 21.5h** (P0 기능 13.5h / P1 화면·운영 검증 8.0h, P2 없음)

---

## 원천·소유권 경계

`docs/reference/배포패키지_기준.md`에 따라 **③에 있으면 ③, ③에 없는 것만 ①**을 쓴다.

```text
③ project.zip (최종)     RAG 문서 3종 원본 · master.cypher · base 9 table
① 교육생 배포패키지        vector/document/document_chunk 스키마 · load_documents.py
                         BAAI/bge-m3 · embedding 1024
```

- **B-1.1 소유:** ①의 `vector` extension, `document`, `document_chunk`만 3개 DB에 채택한다.
  `pg_trgm`은 만들지 않고 CM-3.5 role matrix의 최소권한 GRANT를 적용한다.
- **B-1.2 소유:** ③ `sample/rag/*.md` 원본은 최종 ZIP·CM-1.3 hash로 보존하고 저장소에는 별도
  corrected Markdown 정본만 만든다. **B-1.4 소유:** source/corrected SHA-256과 정정 사유를
  운영 검증 marker에 기록한다.
- ①의 Runtime table이나 ③ 원본 문서 자체를 수정하지 않는다.
- overlay·`document_corpus`·corpus revision 구조는 만들지 않는다. corrected 경로와 원본 hash로
  출처를 추적한다.

## P0 기능 adapter 불변식

`V5-B-1.3`은 ① `load_documents.py`의 핵심 청킹·임베딩 로직을 최종 환경에 맞게 감싸는 최소
기능 adapter다. 아래 조건만 통과하면 B-2 기능 구현을 시작하며 B-1.4를 기다리지 않는다.

1. 입력은 B-1.2 corrected 경로만 허용하며 원본 `02_docs_rag` fallback은 금지한다.
2. 명시 DSN과 `kosa_agent|kosa_agent_e2e` allowlist를 검사하고 기본 DSN·`--reset`을 거부한다.
3. 각 DB의 문서·chunk·embedding은 하나의 transaction에서 반영하고 실패 시 전부 rollback한다.
4. 문서 ID는 canonical ID, chunk ID는 `<document_id>:cs1:<4자리 순번>`을 사용한다.
5. 문서 3건·chunk 1건 이상/문서·embedding NULL 0·vector 차원 1024·대표 검색 smoke를 확인한다.

## P1 운영 검증 불변식

`V5-B-1.4`는 동작이 확인된 B-1.3을 바꾸는 재설계가 아니라 공용 3 DB 운영 검증을 보강한다.

1. `kosa_text2sql`까지 같은 정본을 적재해 3 DB의 RAG 상태를 정렬한다.
2. 최소권한·live fingerprint·중복 0·동일 입력 재실행 idempotent no-op을 검증한다.
3. source/corrected SHA-256·정정 사유와 cs1 contract hash를 기록한다.
4. embedding model revision·weights SHA-256·dimension 1024를 검증한다.
5. live 검증이 끝난 뒤에만 DB별 COMMITTED marker를 marker-last로 기록한다.
6. `/health/ready`가 marker와 live DB fingerprint·검색 smoke를 다시 대조할 수 있어야 한다.

## API·Tool 계약

```http
POST /documents/search
GET  /relations/chambers/{chamber_id}
```

- `POST /documents/search`와 `search_documents(query, model_code=None, top_k=4)` Tool은 동일한
  `DocumentSearchService`를 쓴다.
- `GET /relations/chambers/{chamber_id}`는 Ontology의 유일한 chamber API다. 같은 Method+Path의
  별도 DTO를 만들지 않는다.
- `get_equipment_context(chamber_id)` Tool과 관계 API는 동일한 `GraphService`를 쓰되 Tool은 Agent용
  provenance, API는 read-only 화면 projection을 반환한다.
- Tool의 0건·timeout·오류는 공통 `ok`·`reason`·빈 payload와 공통 reason prefix를 따른다.
- Neo4j credential·Cypher·elementId, 고정 설비 upstream, 특정 LOT routing 추정은 노출하지 않는다.

## 완료 기준·평가

```text
Neo4j            44 nodes / 85 relationships · 필수 속성·방향·중복 0
                 고정 설비 간 UPSTREAM_OF 0건
문서 ID          DOC-SPEC-PH9000 · DOC-SPEC-ET7500 · DOC-TROUBLE-FDC 승계
임베딩           BAAI/bge-m3 · 1024차원 · process당 1회 생성(singleton)
P0 기능 smoke    문서 3건 · chunk 1건 이상/문서 · embedding NULL 0 · vector 차원 1024
P1 운영 검증     3 DB · 중복 0 · idempotent no-op · fingerprint/marker/readiness
검색 평가        Recall@4 >= 0.80 · MRR >= 0.70 · 실패 사례 기록
관계 평가        chamber 관계 질문 100% · 44/85 fixture 고정
```

Documents와 Ontology 화면은 각각 Loading·Error·Empty·Success 네 상태를 component test로
검증한다. `V5-B-3.1`은 `master.cypher`를 DB 접속 없이 100개 seed statement, 44 node,
85 relationship fixture로 검증한다. `MATCH (n) DETACH DELETE n`은 공용 Neo4j에서 직접 실행하지
않고, 검증 결과를 받은 `V5-CM-2.7`이 destructive 문장을 제거해 안전 적용·marker한다.

## 선행조건·협업 주의

- RAG schema·적재는 `V5-CM-3.1`과 role matrix `V5-CM-3.5` 이후, corrected source는
  `V5-CM-1.3` 이후다.
- graph는 `V5-CM-1.3` 이후 `V5-B-3.1`이 offline 검증하고, 그 결과를 선행으로 둔
  `V5-CM-2.7`이 안전 적용한 뒤 `V5-B-3.2`·`V5-B-3.3`이 읽는다.
- C의 Level 1·2는 `V5-B-2.2`와 `V5-B-3.2`가 모두 끝나야 통합할 수 있다.
- graph는 공정 구조·ID 정합 근거일 뿐 특정 LOT의 실제 routing 근거가 아니다.
- `relation_id`는 방향·type·business endpoint의 canonical tuple로 만든다.
- FR-B-08 하이브리드 검색은 도전 요구사항이지만 WBS v5에는 Task가 없으므로 필수 범위에
  끼워 넣지 않는다.

## 원본 절

```text
요구사항 v2.1  5.2 FR-B-01~08
설계 v2.1      5.1 Neo4j 기준 · 5.2 안전 적재 · 5.3 RAG 문서 정정
역할분담 v10.1  7. B — Knowledge Full-stack
기준표          7. Neo4j·RAG 주의
```
