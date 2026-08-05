# 06. Frontend 가이드

> 기준 요구사항: v1.8 / 시스템설계서: v1.2 / 역할분담: v9.5
> 마지막 동기화: 2026-08-05

React 단일 애플리케이션 안에서 기능별로 나눈다. 각 담당자가 자기 Backend와 화면을 직접 연결한다.

---

## 1. 라우트와 담당

| 경로 | 화면 | 담당 |
|---|---|---|
| `/` | 운영 대시보드 | A |
| `/alarms`, `/alarms/:alarmId` | 알람 목록·상세·Agent 분석 연결 | A + C |
| `/traces/:lotHistId` | 센서 trace | A |
| `/approvals` | 승인 큐·Agent 분석 | C |
| `/relations` | 관계 그래프·문서 근거 | B |
| `/analytics` | 자연어 질의·동적 차트 | D |
| `/audit-logs` | 감사로그 | D |

**7화면이다.** `/alarms/:alarmId`는 알람 화면의 상세 상태이며 8번째로 세지 않는다.
Agent 분석은 알람 상세에, 승인은 승인 큐에 포함한다. 별도 화면을 만들지 않는다.

> 현재 스캐폴딩은 `/` → `/dashboard` 리다이렉트 구조이고 `/alarms/:alarmId`가 없다.
> 설계 12.1 기준으로 맞추는 작업이 남아 있다.

---

## 2. 화면별 계약

| 화면 | 주요 컴포넌트 | 호출 API | 사용자 동작 |
|---|---|---|---|
| 운영 대시보드 | 기준일/AREA 필터, KPI 카드, 전체 챔버 상태, 최근 5건 | `GET /dashboard/summary` | 자동·수동 날짜 상태 구분, 최근 알람 선택 시 상세 이동 |
| 알람 목록·상세 | 필터 그리드, 알람 속성, 최신 Agent 상태·원인·조치·근거 | `GET /alarms`, `GET /alarms/{id}`, 필요 시 `GET /agent/runs/{run_id}` | 미처리·FAILED만 실행 버튼 노출, 409 메시지 구분, trace 이동 |
| Trace | 센서 선택, 한계선 5개와 측정값 차트 | `GET /traces/{lotHistId}` | 센서·Recipe Step 변경, Empty 센서 안내 |
| 승인 큐 | PENDING 목록, 관련 알람, 원인·관계·문서 근거, 권고 조치, 승인/반려 폼 | `GET /approvals`, `GET /agent/runs/{run_id}`, `POST /approvals/{id}/decision` | 항목 선택 → 근거 로드 → 결정, 409 처리, 성공 후 목록·상세 동시 갱신 |
| 관계·문서 근거 | 장비/챔버 검색, upstream·downstream·sibling, 문서 hit·score·본문 | 관계 2종 GET, `POST /documents/search`, `GET /documents/{id}` | 노드 선택·문서 펼치기, 0건은 Empty |
| 자연어 분석 | 질문 입력, SQL·표·통계·동적 차트, 평가 이력 | `POST /analytics/query`, `POST /analytics/validate`, `GET /analytics/evaluations` | 실행·재질의, **정책 거부와 실행 오류 구분** |
| 감사로그 | 기간·이벤트·주체 필터, 이벤트 수, before/after 펼침 | `GET /audit-logs` | 결정론적 페이지 이동, 필터 초기화, 조회만 |

### 화면 전이

```
대시보드/알람 목록 → 알람 상세 → Trace
                              → Agent 실행·근거 ─ EQP_HOLD → 승인 큐 ─ 승인 → 조치 전송 상태
                                                                     └ 반려 → CANCELED
                                                자동 조치 → 조치 전송 상태
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

### 대시보드 날짜 상태

**자동 기준일과 사용자가 고른 날짜를 별도 상태로 관리한다.**

```
최초 진입          AREA 전체 · date 미지정으로 호출
                   → API 가 반환한 reference_date 를 날짜 선택기에 표시 (배포 기준 2026-06-04)
날짜 직접 고르기 전 AREA 변경 → date 를 생략해 해당 AREA 의 최신 일자를 다시 선택
날짜 직접 고른 뒤   AREA 변경 → 선택한 날짜 유지
```

`metrology_pass_rate`가 `null`이면 `0%`가 아니라 **`N/A`** 로 표시한다.

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
다단계 승인     화려한 애니메이션    8번째 화면
```

대화에 가장 가까운 기능은 자연어 질의(Text2SQL)다.

---

## 원본 절

```
설계 12.1  라우트와 담당·7화면 계약
설계 12.2  프론트 상태 원칙
설계 10.1~10.5  API DTO
설계 13.4  Vite·Nginx 경로·빌드
요구사항 4.1 최종 화면 구성 · FR-I-02·03 · NFR-12·NFR-17
```
