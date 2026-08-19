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
| V5-B-1.1 | graph 독립 검증 44/85 | 1.5h |
| V5-B-1.2 | 안전 loader | 2.0h |
| V5-B-1.3 | stable `relation_id` | 1.0h |
| V5-B-2.1 | RAG correction overlay | 2.0h |
| V5-B-2.2 | **임베딩 provider·model·차원 확정** | 1.5h |
| V5-B-2.3 | corpus STAGING → ACTIVE | 2.0h |
| V5-B-3.1 | `POST /documents/search` | 1.5h |
| V5-B-3.2 | `GET /ontology/graph` | 1.5h |
| V5-B-3.3 | `get_equipment_context` Tool | 1.5h |
| V5-B-3.4 | `search_documents` Tool | 1.0h |
| V5-B-4.1 | 화면 4 Documents | 2.0h |
| V5-B-4.2 | 화면 5 Ontology | 2.5h |
| V5-B-4.3 | 검색 평가 artifact | 2.0h |
| V5-B-4.4 | 하이브리드 검색 (P2) | 2.0h |

**합계 22.0h** (P2 2.0h 제외)

---

## 완료 기준 (최종 실측값)

```text
Neo4j            44 nodes / 85 relationships · 필수 속성·방향·중복 0
                 고정 설비 간 UPSTREAM_OF 0건
문서 ID          DOC-SPEC-PH9000 · DOC-SPEC-ET7500 · DOC-TROUBLE-FDC 승계
corpus           STAGING 적재 → 문서 수·chunk 수·차원·hash·검색 smoke 검증 → ACTIVE swap
검색 결과        document / chunk / corpus revision + 실제 근거 내용 반환
평가             독립 fixture 기준 Recall@K · MRR · 실패 사례 · corpus revision 기록
```

---

## 주의

**`master.cypher` 첫 문장 `MATCH (n) DETACH DELETE n`을 공용 Neo4j에 직접 실행하지 않는다.**
empty / fingerprint / backup / confirm guard를 통과한 loader로만 갱신한다.

**임베딩 provider·model·차원은 이 역할이 확정한다.** 참고 구현
(`backend/app/services/rag.py`)의 키워드 스코어는 파일럿이며 주석도 "실서비스는 임베딩+벡터검색으로
교체"라고 적고 있다. 확정값을 `.env`와 corpus revision metadata
(`embedding_model_code`·`embedding_dim`)에 함께 기록하고, corpus revision이 바뀌면 두 값도 함께
versioning한다.

**원본 RAG 파일을 수정하지 않는다.** correction overlay와 corpus revision으로 관리하고 원문·정정
사유·corrected hash·embedding 정보를 provenance로 남긴다.

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
직접 접속 대신 Backend의 read-only `GET /ontology/graph`만 호출한다.

**graph는 공정 구조·ID 정합 근거일 뿐이다.** 특정 LOT의 upstream 설비를 결정하지 않는다.
`relation_id`는 방향·type·business endpoint의 canonical tuple로 만들고 Neo4j elementId를 API
provenance로 쓰지 않는다.

---

## API

```http
POST /documents/search      호환 필수. query · model_code? · top_k
GET  /ontology/graph        보안 필수. read-only graph adapter
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
설계 v2.1      5.1 Neo4j 기준 · 5.2 안전 적재 · 5.3 RAG correction overlay
역할분담 v10.1  7. B — Knowledge Full-stack
기준표          7. Neo4j·RAG 주의
```
