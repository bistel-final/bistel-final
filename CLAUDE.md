# 프로젝트 규칙

## Mock 데이터 (frontend)
- Mock 데이터는 사용자가 제공한 실측값만 사용한다. 없는 값(알람 ID, 센서명, 장비, 시각 등)을 임의로 만들지 않는다. 필요한 값이 없으면 사용자에게 먼저 물어본다.
- 실측 원본: `frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/` (FDC Platform.dc.html, alarms-data.js 51건)
- R02_OOC 룰 뱃지는 OOC이므로 앰버 계열로 표시.
- PHOTO 알람은 전부 PHO-01-C1 발생, PHO-01-C2는 알람 0건.

## Frontend 구조
- 컴포넌트는 직접 fetch 금지 — 모든 데이터는 `src/shared/api/` 모듈을 통해서만 가져온다.
- `VITE_USE_MOCK !== 'false'`이면 각 feature의 `mock/` 데이터를 300ms 지연 Promise로 반환한다.
- feature 폴더(detection/agent/knowledge/analytics)는 담당자별 독립 수정 영역 — 다른 feature를 import하지 않는다.
- TypeScript 금지(JSX만), 기존 eslint 설정 준수, 기존 의존성 버전 변경 금지(추가만 허용).
