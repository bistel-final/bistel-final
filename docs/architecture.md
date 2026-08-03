# Backend Architecture

## 전체 흐름

```text
React
  ↓ HTTP/JSON
FastAPI
  ├─ PostgreSQL / pgvector
  ├─ Neo4j
  ├─ LangGraph / LLM
  └─ n8n Webhook
```

FastAPI는 팀이 공동으로 사용하는 하나의 모듈형 애플리케이션으로 구성합니다. 개발 시에는 `.env`에 지정된 실제 PostgreSQL·Neo4j·n8n 서버를 사용합니다.

## 모듈

| 모듈 | 역할 | 담당 |
|---|---|---|
| `common` | 환경변수, PostgreSQL, Neo4j 공통 연결 | 공통 |
| `detection` | FDC 요약·규칙·이상감지 | A |
| `knowledge` | Neo4j 관계 조회·RAG 문서 검색 | B |
| `agent` | LangGraph·HITL·조치·n8n | C |
| `analytics` | Text2SQL·통계·차트·감사로그 | D |

## 연결 규칙

- PostgreSQL 일반 기능은 `app.common.db.engine`을 사용합니다.
- Text2SQL은 반드시 `app.common.db.readonly_engine`을 사용합니다.
- Neo4j는 `app.common.neo4j.get_neo4j_driver()`를 공통으로 사용합니다.
- 서버 주소·비밀번호를 코드에 하드코딩하지 않습니다.
