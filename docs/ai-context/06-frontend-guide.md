# 06. Frontend 가이드

> [!CAUTION]
> **사용 중지 — 아래 본문은 v1.9/v1.10/v9.6 기준의 구 이력이며 구현 근거로 사용하면 안 됩니다.**
> v2 요약 문서가 재생성되기 전에는 `docs/specifications/요구사항정의서_v2_0_작업본.md`,
> `docs/specifications/시스템설계서_v2_0_작업본.md`,
> `docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md`와
> `docs/planning/Task분해_WBS_v4_작업본.md`의 해당 `V4-*` Task만 사용하십시오.
> 아래 본문은 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-11

React 단일 애플리케이션 안에서 기능별로 나눈다. 각 담당자가 자기 Backend와 화면을 직접 연결한다.

---

## 1. 라우트와 담당

| 경로 | 화면 | 담당 |
|---|---|---|
| `/dashboard` | 알람 대시보드 | A |
| `/alarms`, `/alarms/:alarmId` | 알람 목록·상세·Agent 분석 연결 | A + C |
| `/traces` | 트레이스 뷰어 | A |
| `/actions` | 조치 목록·승인 대기 | C |
| `/agent-runs/:runId` | Agent 실행 근거·승인 | C |
| `/knowledge` | 관계·문서 근거 | B |
| `/analytics` | 자연어 질의·동적 차트 | D |
| `/audit-logs` | 감사로그 | D |

**8화면이다.** `/`는 `/dashboard`로 리다이렉트하고, `/alarms/:alarmId`는 알람 목록·상세 화면의 상태이므로 별도 화면으로 세지 않는다. 승인은 Agent 실행 근거를 본 뒤 `/agent-runs/:runId`에서 수행하고, 승인 대기는 `/actions`의 기본 필터로 본다.

---

## 2. 화면별 계약

| 화면 | 주요 컴포넌트 | 호출 API | 사용자 동작 |
|---|---|---|---|
| 알람 대시보드 | 기간·계층 필터, 승인 대기 목록, 알람 추이, 파라미터 TOP5, 설비별 건수, 최근 알람 | `GET /dashboard/summary` | 서버가 반환한 기간·기준일 표시, 승인 대기는 실행 상세, 최근 알람은 알람 상세로 이동 |
| 알람 목록·상세 | 필터 그리드, 알람 속성, 최신 Agent 상태·원인·조치·근거 | `GET /alarms`, `GET /alarms/{id}`, 필요 시 `GET /agent/runs/{run_id}` | 미처리·FAILED만 실행 버튼 노출, 409 메시지 구분, trace 이동 |
| 트레이스 뷰어 | AREA·설비·챔버·파라미터, 레시피·LOT·WAFER 다중 선택, 한계선 5종 | `GET /traces/catalog`, `POST /traces/search` | x축 시간, 파라미터별 차트, LOT·WAFER 구간 표시, Empty 안내 |
| 조치 목록 | 승인·전송·조치·설비·챔버·기간 필터, 알람 수 | `GET /actions`, `GET /actions/{id}` | 승인 대기가 기본 필터, 항목 선택 시 실행 상세 이동 |
| Agent 실행 근거·승인 | 센서·관계·문서 근거, 권고 조치, 승인/반려 폼 | `GET /agent/runs`, `GET /agent/runs/{run_id}`, `GET /approvals`, `POST /approvals/{id}/decision` | 항목 선택 → 근거 로드 → 결정, 409 처리, 성공 후 목록·상세 동시 갱신 |
| 관계·문서 근거 | 장비/챔버 검색, upstream·downstream·sibling, 문서 hit·score·본문 | 관계 2종 GET, `POST /documents/search`, `GET /documents/{id}` | 노드 선택·문서 펼치기, 0건은 Empty |
| 자연어 분석 | 질문 입력, SQL·표·통계·동적 차트, 질의·평가 이력 | `POST /analytics/query`, `POST /analytics/validate`, `GET /analytics/history`, `GET /analytics/evaluations` | 실행·재질의, **200 정책 거부와 실행 오류 구분**, Backend 차트 계획 그대로 렌더링 |
| 감사로그 | 기간·이벤트·주체 필터, 이벤트 수, before/after 펼침 | `GET /audit-logs` | 결정론적 페이지 이동, 필터 초기화, 조회만 |

### 화면 전이

```
대시보드/알람 목록 → 알람 상세 → Trace
                              → Agent 실행 → 실행 근거·승인 ─ 승인 → 조치 전송 상태
                                                                └ 반려 → CANCELED
조치 목록 → Agent 실행 근거              자동 조치 → 조치 전송 상태
                                                                                  → 감사로그
```

---

## 3. 상태 원칙

- 모든 비동기 화면은 **Loading · Error · Empty · Success**를 구분한다 (NFR-17)
- API base URL은 `VITE_API_BASE_URL` **한 곳에서만** 읽는다. 최종 Compose에서는 상대 경로 `/api`
- Axios 공통 client가 REST 오류 계약(`{code, message, details}`)을 변환한다
- **운영 경로에 Mock 데이터를 남기지 않는다.** 장애 시나리오용 Mock은 테스트 dependency에서만
- 페이지 목록은 항상 Backend의 `total`·`page`·`size`를 기준으로 한다. **프론트가 임의 정렬·재집계로 API 결과를 바꾸지 않는다**
- 차트 라이브러리·관계도 라이브러리는 담당자가 선택하되 **API DTO는 바꾸지 않는다**
- `chart_type`을 프론트에서 다시 판단하지 않는다. Backend 계획을 그대로 렌더링한다
- Axios 공통 timeout은 일반 API에 적용하되, `POST /analytics/query`는 LLM 60초 × 최대 2회 시도와 처리 여유를 포괄하도록 요청별 `timeout=150000ms`를 쓴다

### 대시보드 기간 상태

**서버가 자동 적용한 기간과 사용자가 직접 고른 기간을 별도 상태로 관리한다.**

```
최초 진입          date_from·date_to 생략
                   → API가 선택 계층의 데이터 최소·최대일을 date_range로 반환
                   → 기간 내 최신 데이터 일자를 reference_date로 표시
한쪽 경계만 지정 → 나머지 경계는 서버가 해당 계층의 데이터 경계로 보완
사용자가 기간 선택 후 계층 변경 → 선택한 기간을 유지하고 서버 응답으로 차트·목록 갱신
```

AREA·설비·챔버·파라미터 선택지는 상수로 박지 않고 응답의 `hierarchy`·`sensor_catalog`로 채운다. 데이터가 늘어도 화면이 따라간다.

### Agent 실행·승인 polling

```
POST /agent/runs → 202 RUNNING
  → GET /agent/runs/{run_id} 2초 간격 polling
  → WAITING_APPROVAL · COMPLETED · FAILED 에서 중지
  → 화면 이탈 시 polling 취소, 재진입 시 저장된 run_id 의 현재 상태부터 조회
```

승인 결정 API도 `RUNNING`을 반환하므로 같은 방식으로 상세를 polling한다.

요청 결과가 즉시 최종 상태가 아닐 수 있는 화면은 `agent_run_status`·`approval_status`·`send_status`를 **각각 표시**한다.

---

## 4. 실행

Node 버전은 `.nvmrc`의 `22.14.0`을 쓴다.

```bash
cd frontend
cp .env.example .env
npm ci          # npm install 이 아니다. lockfile 기준으로 설치
npm run dev     # http://localhost:5173
npm run lint
npm run build
```

`package-lock.json`을 커밋하고 `npm ci`로 설치한다 (NFR-15 재현성).

### API 경로

```
Vite 개발    /api → http://localhost:8000 프록시
             rewrite: path => path.replace(/^\/api/, '')
최종 Compose 브라우저 → Nginx /api → http://backend:8000/ (prefix 제거)
```

Nginx가 `/api/`를 SPA fallback보다 **먼저** 매칭한다. 정적 `/` 요청만 `try_files $uri $uri/ /index.html`.
외부 브라우저는 배포 PC와 같은 Origin의 `/api`를 쓴다. **Docker 서비스명이나 접속자 PC의 `localhost:8000`을 호출하지 않는다.**

CORS는 `CORS_ORIGINS`의 명시적 Origin만 허용한다. `*` 금지.

---

## 5. 범위 밖

```
대화형 챗봇     실시간 WebSocket     모바일 최적화     사용자 인증·권한
다단계 승인     화려한 애니메이션    추가 9번째 업무 화면
```

대화에 가장 가까운 기능은 자연어 질의(Text2SQL)다.

---

## 원본 절

```
설계 12.1  라우트와 담당·8화면 계약
설계 12.2  프론트 상태 원칙
설계 10.1~10.5  API DTO
설계 13.4  Vite·Nginx 경로·빌드
요구사항 4.1 최종 화면 구성 · FR-I-02·03 · NFR-12·NFR-17
```
