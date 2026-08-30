# API 명세서 생성

> [!CAUTION]
> **FINAL-DOC — 구 PDF·생성기 사용 중지.** 기존 `API명세서.md`·`.csv`·`.pdf`와
> `build_api_spec.py`는 최종 `project.zip` 이전 산출물이다. 현재 계약 기준은
> `API명세서_v3_작업본.md`·`.csv`이며 교차검토를 완료했다. 구현은 V5 Task 기준으로 진행하며,
> Backend DTO·생성기가 함께 갱신되기 전에는 구 PDF를 제출·구현 근거로 사용하거나 기존
> 생성기를 실행하지 않는다.

API v3의 기능 범위와 정책은 요구사항정의서 v2.1과 시스템설계서 v2.1을 따른다.

| 구분 | 필수 범위 | 비고 |
|---|---:|---|
| 외부 최소 호환 업무 API | 9개 | `POST /agent/ask` 포함 |
| 보안 필수 public API | 1개 | `GET /relations/chambers/{chamber_id}` |
| 실행 필수 public API | 1개 | `POST /agent/runs` |
| 필수 internal callback | 1개 | `POST /internal/actions/{action_id}/delivery` |
| 팀 release 필수 확장 | 5개 | Analytics 4개 + 전역 Audit 1개 |
| 운영·진단 API | 2개 | `/health`, `/health/ready`; 업무 API 수에서 제외 |

따라서 멘토 core public 업무 API는 11개(호환 9 + 보안 1 + 실행 1)이고, 팀 release public
업무 API는 확장 5개를 더한 16개다. internal callback과 health는 각각 별도 scope이며 최종
contract Gate는 core 14 operation + team 5 operation = 19개를 대조한다.

Ontology public API는 선택 chamber의 subgraph와 화면·Agent가 함께 쓰는 context를 한 응답으로
반환한다. 같은 path의 별도 DTO나 전체 graph용 중복 public endpoint를 만들지 않으며, Neo4j
URI·계정·Cypher를 노출하지 않는다. `get_equipment_context`는 같은 B service를 사용하는 내부
Agent Tool adapter다. 멘토 core 분류는 유지하되 Text2SQL·이력·평가와 전역 감사 5개는 팀
release 필수 overlay로 별도 검증한다.

공용 PostgreSQL·Neo4j·n8n은 외부 canonical 서비스다. 팀 compose 범위는
Backend·Frontend·Kafka·MES Mock뿐이며 API 문서나 OpenAPI가 두 번째 DB·Neo4j·n8n을 기본
실행 경로로 안내해서는 안 된다.

`/health`는 외부 의존성과 무관한 process liveness다. `/health/ready`는 PostgreSQL
epoch·schema·role, reference migration marker, Neo4j 44/85 marker, RAG 필수 문서 3종·vector
non-null·1024차원·검색 smoke, n8n, Kafka metadata·필수 topic을 의존성별로 검증하고 필수 항목
실패 시 503을 반환한다. RAG 계약에는 corpus revision·`ACTIVE` 전환·overlay를 추가하지 않는다.

현재 `build_api_spec.py`는 이전 epoch 재현용이다. v3 대응 생성 Task에서 출력 파일명·schema를
명시적으로 분리하기 전에는 실행 명령을 제공하지 않는다.

생성기는 macOS의 AppleGothic, Windows의 맑은 고딕, Linux의 NanumGothic 순서로 한글 글꼴을 찾는다. 다른 글꼴을 사용하려면 `API_SPEC_FONT`에 TTF 파일 경로를 지정한다.

교차검토를 마친 v3 계약을 기준으로 Backend DTO·계약 테스트와 생성기를 함께 갱신하고
CSV·Markdown·PDF를 동일 revision으로 생성한다. 생성 후에는 다음을 확인한다.

- core public 11개(호환 9 + 보안 1 + 실행 1), internal callback 1개, 운영 2개와 팀 release 5개의
  누락·중복·혼입 여부
- Markdown·CSV·PDF·OpenAPI의 path·DTO·Enum 일치 여부
- `git diff --check`
- PDF 전 페이지 렌더링 결과의 글자 잘림·겹침·빈 페이지 여부

`/health`·`/health/ready`는 내부 운영·개발 진단 엔드포인트라 필수 public 업무 API 11개에
포함하지 않는다. 운영 appendix에 분리해 기록한다.

생성 결과를 수동으로 따로 편집하지 않는다. 수정이 필요하면 원본 계약, DTO 또는 생성기를 고친 뒤
세 형식을 다시 만든다.
