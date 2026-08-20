# B — Knowledge

> 기준 원천: 멘토 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-19
> 담당: 강연권 · 모듈 `backend/app/knowledge/` · `frontend/src/features/knowledge/`

Neo4j 44/85 안전 검증, RAG 문서 정정과 임베딩 검색, 근거 provenance, 화면 4·5를 책임진다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-B-01 | Neo4j 검증 | 필수 |
| FR-B-02 | 문서 정합성 | 필수 |
| FR-B-03 | `get_equipment_context` | 필수 |
| FR-B-04 | `search_documents` | 필수 |
| FR-B-05 | 임베딩 재사용 | 필수 |
| FR-B-06 | Documents·Ontology 화면 | 필수 |
| FR-B-07 | 검색 평가 | 필수 |
| FR-B-08 | 하이브리드 검색 | 도전 |

## Task (WBS v5)

| ID | 내용 | 공수 |
|---|---|---:|
| V5-B-1.1 | RAG 스키마 (`document`·`document_chunk`) | 1.0h |
| V5-B-1.2 | RAG 문서 정합성 수정 | 2.0h |
| V5-B-1.3 | RAG 적재 (`load_documents.py`) | 2.0h |
| V5-B-2.1 | DocumentSearchRepository·Service | 2.0h |
| V5-B-2.2 | `search_documents` Tool | 1.0h |
| V5-B-2.3 | `POST /documents/search` | 1.0h |
| V5-B-3.1 | GraphRepository·GraphService (44/85 검증 포함) | 2.5h |
| V5-B-3.2 | `get_equipment_context` Tool | 1.0h |
| V5-B-3.3 | `GET /relations/chambers/{chamber_id}` | 1.5h |
| V5-B-4.1 | 화면 4 Documents | 2.0h |
| V5-B-4.2 | 화면 5 Ontology | 2.5h |
| V5-B-4.3 | 최소 검증·평가 | 2.0h |

**합계 20.5h** (P2 없음)

---

## 완료 기준 (최종 실측값)

```text
Neo4j            44 nodes / 85 relationships · 필수 속성·방향·중복 0
                 고정 설비 간 UPSTREAM_OF 0건
문서 ID          DOC-SPEC-PH9000 · DOC-SPEC-ET7500 · DOC-TROUBLE-FDC 승계
임베딩           BAAI/bge-m3 · 1024차원 (배포본 ① 고정값)
적재 검증        문서·chunk 중복 0 · embedding NULL 0 · vector 차원 1024
검색 결과        document / chunk + 실제 근거 내용 반환
Service 공유     API와 Tool이 동일한 DocumentSearchService·GraphService 사용
평가             Recall@4 · MRR · 실패 사례 · chamber 관계 fixture
```

---

## 출처 — 어디서 가져오는가

`docs/reference/배포패키지_기준.md`가 정본이다. **③에 있으면 ③, ③에 없는 것만 ①**.

```text
③ project.zip (최종)     RAG 문서 3종 · master.cypher · base 9 table
① 교육생 배포패키지        document·document_chunk 스키마 · load_documents.py · bge-m3 1024
```

①에도 RAG 문서 3종과 `master.cypher`가 있지만 **내용이 다르다**(해시 전부 상이).
③이 최신 장비 문서·관계를 반영하므로 ③을 쓴다.

---

## 주의

**`master.cypher` 첫 문장 `MATCH (n) DETACH DELETE n`을 공용 Neo4j에 직접 실행하지 않는다.**
empty / fingerprint / backup / confirm guard를 통과한 loader로만 갱신한다.

**임베딩은 재선정하지 않는다.** 배포본 ①의 `load_documents.py:42-43`이 `BAAI/bge-m3`·1024를
기본값으로 고정하고 `01_schema.sql`이 `embedding vector(1024)`로 선언한다. ③의 참고 구현
(`backend/app/services/rag.py`)은 키워드 스코어 파일럿이며 주석도 "실서비스는 임베딩+벡터검색으로
교체"라고 적고 있다. 모델은 process당 1회 생성해 재사용한다(singleton).

**정정본을 정본으로 쓰되 원본은 보존한다.** overlay·corpus revision 구조는 만들지 않는다.
③의 `sample/rag/*.md`는 `V5-CM-1.1`이 해시로 등록했으므로 **원본을 그 자리에서 수정하지 않고**
정정본을 저장소 별도 경로에 둔다. `load_documents.py`는 `02_docs_rag`를 읽으므로(`:36`·`:122`)
입력 경로를 정정본으로 바꾼다.

**정정 대상은 다음과 같다.**

```text
SPEC_PH-9000  고정 "EQP01 → EQP04 로 등록" 문구
              → 상하류는 CT-PHOTO → CT-ETCH step 수준에서만 정의
              → 특정 wafer의 실제 설비는 lot_history로 조회
적용 범위      PH-9000 = EQP01~03 · RECIPE01/03   ET-7500 = EQP04~06 · RECIPE02/04
              (원문은 RECIPE01/02만 언급하지만 실제 데이터는 01·03 / 02·04)
TROUBLE       metrology FAIL·반복·하류 진행 기반 조치 상향, 원인 설명·후속 정상 기반 하향 제거
              "한 챔버 OOS 3개 이상" 축약 → 같은 chamber·parameter·recipe step 연속 3
              R01은 raw 한 점의 USL/LSL 이탈 즉시 TRACE 알람. Fault 후보에 OTH 포함
              ACT-0001~0010 기준 구 10건 서술 → 최종 ACT-0001~0012 reference
              EQP_HOLD EMAIL은 승인 요청 알림, Kafka MES Mock은 승인 후에만 실행
anomaly score  score로 조치를 상향한다는 구 문구 제거
```

**Neo4j Browser iframe과 Frontend 비밀번호 노출을 수용하지 않는다.** 참고 React의 `neo4j/password`
직접 접속 대신 Backend의 read-only `GET /relations/chambers/{chamber_id}`만 호출한다.

**graph는 공정 구조·ID 정합 근거일 뿐이다.** 특정 LOT의 upstream 설비를 결정하지 않는다.
`relation_id`는 방향·type·business endpoint의 canonical tuple로 만들고 Neo4j elementId를 API
provenance로 쓰지 않는다.

---

## API

```http
POST /documents/search      호환 필수. query · model_code? · top_k
GET  /relations/chambers/{chamber_id}   보안 필수. read-only 관계 adapter
```

선택 확장: 장비·챔버별 관계 조회, 문서 상세.

---

## 화면

| 화면 | 내용 |
|---|---|
| 4 Documents | 문서 검색·근거 표시, Agent 화면 deep link |
| 5 Ontology | graph API 기반 시각화. 비밀정보 노출 0 |

---

## 원본 절

```text
요구사항 v2.1  5.2 FR-B-01~08
설계 v2.1      5.1 Neo4j 기준 · 5.2 안전 적재 · 5.3 RAG 문서 정정
역할분담 v10.1  7. B — Knowledge Full-stack
기준표          7. Neo4j·RAG 주의
```
