# Design Handoff: BISTel FDC Anomaly Agent Platform — 화면 7종

> [!CAUTION]
> **FINAL-DOC — VISUAL HISTORY ONLY.** 이 번들은 이전 epoch의 화면·레이아웃 이력이며,
> 7화면 구조·수치·Mock·상태명·DTO·API 계약을 현재 구현·테스트·제출 근거로 사용하지 않는다.
> 현재 규칙은 [frontend/AGENTS.md](../../AGENTS.md), 기능·화면 기준은
> [요구사항 v2.1](../../../docs/specifications/요구사항정의서_v2_1_작업본.md)과
> [시스템설계 v2.1](../../../docs/specifications/시스템설계서_v2_1_작업본.md), API 기준은
> [API v3](../../../docs/deliverables/api/API명세서_v3_작업본.md)를 따른다. 아래 본문은
> 시각적 변천을 보존하기 위한 archive다.

## Overview
반도체 FDC(Fault Detection & Classification) 이상 감지 Agent 플랫폼의 7개 화면 하이파이 디자인 번들이다. 최종 요구사항 v1.9의 8개 화면 중 Knowledge 화면은 이 번들 이후 추가된 범위이므로 현재 React 구현과 `docs/specifications/`를 따른다.
알람 대시보드 → 알람 목록 → 트레이스 뷰어 → Agent 분석·승인 → 조치 목록 → 자연어 분석 → 감사로그.

## About the Design Files
이 번들의 `.dc.html` 파일들은 **HTML로 만든 디자인 레퍼런스**다 — 의도한 외관과 동작을 보여주는 프로토타입이지, 그대로 복사할 프로덕션 코드가 아니다. 목표는 이 디자인을 대상 코드베이스의 기존 환경(React + Tailwind 예정)에서 **재구현**하는 것이다.

- 각 `.dc.html`은 브라우저에서 바로 열린다 (`fdc.css`, `support.js`, `Sidebar.dc.html`이 같은 폴더에 있어야 함).
- 마크업은 `<x-dc>` 태그 안에 있고, 데이터는 파일 하단 `<script data-dc-script>`의 `renderVals()`에 배열로 있다. `{{ }}` 홀 + `<sc-for>`/`<sc-if>`는 각각 값 바인딩 / 반복 / 조건부 렌더다.
- `support.js`는 프로토타입 런타임일 뿐이다 — 포팅 대상 아님.

## Fidelity
**High-fidelity.** 색·타이포·간격·배지 스타일·데이터 값 전부 확정. 픽셀 단위로 재현할 것.
데이터 수치는 실측 CSV와 대조 완료된 값이므로 임의로 바꾸지 말 것.

## Design Tokens (`fdc.css` :root)
| 토큰 | 값 | 용도 |
|---|---|---|
| --navy | #1E3A5C | 사이드바 배경, 제목, 헤더 바 |
| --navy-2 | #2E527C | 사이드바 활성 메뉴 |
| --blue | #2062A8 | 브랜드 액센트: 버튼, 링크, 차트 라인, 바 |
| --red | #B03A4E | EQP_HOLD · R03 · OOS · HUMAN · 승인 대기 · HIGH |
| --amber | #B97F14 | LOT_HOLD · OOC · MEDIUM · WAITING_APPROVAL |
| --green | #2E7D4F | MONITOR · IN_CONTROL · 승인됨/자동 · SENT |
| --ink | #22384F | 본문 텍스트 |
| --g1 | #5A6B7E | 보조 텍스트 (회색 1단계) |
| --g2 | #98A5B3 | 흐린 텍스트, SYSTEM 배지 (회색 2단계) |
| --line | #E2E7EE | 테두리 |
| --bg | #F4F6F9 | 페이지 배경 |

틴트(연한 배경) 변형은 위 상태색의 파생: `.t-red` #F9EDEF/#E3BAC3, `.t-amber` #FAF3E1/#E2CC98, `.t-green` #EBF4EE/#BAD8C5, `.t-gray` #EFF2F5/#D8DEE5, `.t-blue` #EBF2F9/#BBD2E8, `.t-navy` #EBEFF4/#C2CEDC.
이 팔레트 밖 임의 hex 금지. 간격은 4px 배수 그리드.

## Typography
- 본문: Pretendard (fallback: system sans) — 페이지 제목 22px/800, 카드 제목 15px/800, 본문 12.5~13px, 캡션 11~12px.
- 코드/ID/수치: IBM Plex Mono — ID 강조 12~13px/700, 배지 10.5px/600, 보조 11px.

## 상태→색 매핑 (전 화면 공통, 고정)
- 조치코드: EQP_HOLD 적 · LOT_HOLD 황 · MONITOR 녹
- 룰: R03만 적색 강조(solid), R01/R02 회색 틴트
- 판정: OOS 적 · OOC 황 · IN_CONTROL 녹
- 주체: AGENT 남색(solid navy) · HUMAN 적(solid red) · SYSTEM 회색(solid gray)
- 승인: 승인 대기 적 · 승인됨/자동 녹

## 공통 컴포넌트 클래스 (`fdc.css`) → Tailwind 컴포넌트 1:1 대응 의도
- `.sidebar` + `.side-item(.on)` + `.side-foot` — 네이비 사이드바, 7메뉴, 하단 "● Agent 파이프라인 가동 중" (`Sidebar.dc.html`, `active` prop 1~7)
- `.badge` + solid(`.bg-*`) / tint(`.t-*`) 변형 — radius 999px, 높이 20px, mono 10.5px
- `.card` `.card-h` `.card-t` `.card-note` — radius 10px, 테두리 --line
- `.tbl` — 헤더 11px --g1, 행 padding 13px 12px, 짝수행 `.alt`(#F8FAFC), 승인대기 행 `.row-red`, 선택 행 `.row-sel`
- `.select` `.f-field` `.f-label` — 필터 드롭다운 (높이 36px, radius 8px)
- `.btn-primary` `.btn-outline` `.btn-outline-red` `.btn-sm`
- `.tab(.on)` — 조치 목록 상태 탭
- `.pg(.on)` `.pager` — 페이지네이션
- `.state-box.state-red/.state-green` — 감사로그 before/after 상태 박스
- `.hbar-track/.hbar-fill(.dim)` — 수평 집계 바
- `.dashed-card` — 점선 안내 카드
- `.kv` — mean/std/min/max 통계 그리드

## Screens
레이아웃 공통: 좌측 사이드바 236px 고정 + 우측 콘텐츠(패딩 28px), 기준 폭 1620px+.

### 01 알람 대시보드 (`01_알람대시보드.dc.html`)
상단: 제목 + 필터 4종(기간·공정·장비·챔버 — 파라미터 없음). 「처리 필요」 밴드(좌측 3px 적색 보더): 승인 대기 2건 리스트(APR-0003 · LOT-260010 · ETC-01-C1 · ET_CF4 · EQP_HOLD(t-red) · R03_CONSEC · 38분 전 / APR-0002 · LOT-260007 · PHO-01-C1 · PH_FOCUS · 25시간 전, requested_at 내림차순, 각 행 검토→04) + 실행 실패 0 · 전송 실패 0 회색 카드("재실행 대상 없음"/"재시도 대상 없음"). 「알람 추이」 라인차트(6/1~6/4, OOS 적 0→11→20→6 · OOC 녹 4→3→7→0, y 0~30, R03 세로 점선 6/2·6/3·6/4 + bg-red 칩, 하단 주석 "점선은 R03_CONSEC 발생 시점 — 장비 정지 판정이 걸린 날") + 「파라미터별」 바(PH_FOCUS 22 · ET_REFL 15 · ET_CF4 14 + 나머지 5종 안내 점선 박스). 「설비별」(ETC-01 29건: C2 15·C1 14 / PHO-01 22건: C1 22·C2 "이상 없음", 설비 바는 navy·챔버 바는 blue 55% 투명) + 「최근 알람」 테이블 6행(전부 ET_CF4·ETC-01-C1·OOS·ACT-0010, ALM-0048 07:15 R03 행 강조 row-red + bg-red 배지, 나머지 R01 t-gray).

### 02 알람 목록 (`02_알람목록.dc.html`)
필터 5종(기간·공정·설비·챔버·파라미터) → 좌 알람 테이블(12행/51건, 발생 시각 내림차순, ALM-0022 선택 강조) + 우 470px 상세 패널(좌측 3px 파란 보더): ALM-0022 헤더, PH_FOCUS 미니 SPC 차트(SVG, 피크 65.353 적색 강조, USL/UCL/TARGET/LCL/LSL = 60/36/0/-36/-60), 같은 incident 6건 칩 내비, Agent 판단 카드(FOC 포커스 이탈, ACT-0005 · EQP_HOLD · 승인 대기, 분석 보기→), URL 안내 점선 카드. 페이지네이션 1–12/51.

### 03 트레이스 뷰어 (`03_트레이스뷰어.dc.html`)
필터 2줄(AREA→설비→챔버→파라미터 / 레시피→LOT→WAFER→기간 + 조회). PH_FOCUS·PH_DOSE SPC 차트 세로 스택(SVG, W1·W3·W5 경계 점선, ALM-0022 적색 세로선+칩). 우측 330px: 구간 통계(EXPOSE OOS: 57.240/8.589/44.616/69.377 · DEVELOP OOC: 16.612/10.926/0.144/38.309), anomaly_score 0.84(임계 0.62, "판정에는 쓰지 않는다"), 이 구간의 알람 4건(R03 카드 + R01 3행: 0021 W5·06:45:45, 0020 W3·06:41:45, 0019 W1·06:37:47).

### 04 Agent 분석·승인 (`04_Agent분석승인.dc.html`)
네이비 헤더 바: RUN-20260603-0005 · WAITING_APPROVAL · 설비/챔버/파라미터/LOT/기간. 좌 360px 판정 컬럼(좌측 3px 적색 보더): FOC 판정 카드(confidence 0.87), 권고 조치(EQP_HOLD · HIGH · R03_CONSEC), 승인 폼(결정자 bang), "승인하면" 4줄 미리보기, Tool 호출(get_fdc_summary 412ms · get_equipment_context 388ms · search_documents 1,240ms — 구분선 아래 decide_action 6ms는 Tool이 아니라 「노드」). 우측 근거 5카드(각각 색 보더+미니차트+「읽는 법」): ①문제 파라미터(적, "연속 3 WAFER OOS · 최대 69.377 (USL 60)") ②같은 챔버 다른 파라미터(청) ③형제 챔버 PHO-01-C2 정상 WAFER(녹) ④상류 PHOTO vs 하류 ETCH(남, 하향 판정 근거) ⑤계측 CD 결과(황, 상향 판정 근거).

### 05 조치 목록 (`05_조치목록.dc.html`)
필터 4종 + 상태 탭(승인 대기 2 · 전송 실패 0 · 진행 중 0 · 완료 8 · 전체 10). 테이블 10행: 승인 대기 ACT-0010(06-04 07:11)·ACT-0005(06-03 06:39) 상단 고정(적색 행 + 검토→ 버튼), 이하 시각 내림차순 0008→0009→0006→0007→0004→0003→0002→0001. 알람 수 6·6·5·7·3·6·3·5·6·4건.

### 06 자연어 분석 (`06_자연어분석.dc.html`)
질문 입력 + 예시 칩 5개(마지막 "알람 테이블 전부 지워줘"는 적색). 생성 SQL 카드(코드 블록 + 검증 체크 5항목 + SQL 수정·재검증). 결과 카드(표/통계/차트 탭, bar 차트 PHO-01-C1 22 · ETC-01-C2 15 · ETC-01-C1 14, "PHO-01-C2 는 기간 내 알람 0건" 안내). 우측 360px 최근 질의 5건(성공 3 · 거부 2, 거부는 적색 틴트+사유). 헤더 우측 "읽기 전용 · 허용 테이블 16종".

### 07 감사로그 (`07_감사로그.dc.html`)
필터(기간·이벤트·주체·대상 ID=APR-0001). 좌 타임라인은 시각 오름차순의 상태 전이를 보여준다. 최종 구현은 원본 시안의 `ACTION_APPROVED`·`ACTION_SEND_STARTED` 이름을 사용하지 않고 시스템설계서 11장의 `APPROVAL_DECIDED`·`ACTION_SENT` 등 감사 이벤트 9종을 따른다. 각 카드: 이벤트명 + 주체 배지 + entity·id + before(적)→after(녹) 상태 박스(신규 생성은 after만). 우측 360px: 이벤트 유형별 집계와 "이 화면이 증명하는 것" 5항목 카드(적색 보더). 헤더 우측 "append-only · 수정 · 삭제 경로 없음".

## Interactions & Behavior (프로토타입에 구현된 범위)
- 사이드바 메뉴 = 화면 간 링크. 알람 행 조치 → 05, 크게 보기 → 03, 분석 보기/검토 → 04, 알람 수 → 02.
- 정렬·필터·페이지네이션·승인 폼은 정적 표현이다. 실제 동작(선택 상태의 URL 반영 `/alarms/ALM-0022`, 알람→트레이스 조건 프리필, 승인 시 상태 전이 4단계)은 화면 내 안내 문구에 명세돼 있다.
- 호버: 사이드바 메뉴 밝아짐, 버튼 --blue→--navy, 링크 --blue→--navy.

## Assets
외부 이미지 없음. 차트는 전부 인라인 SVG (좌표는 실측 데이터 기반). 폰트: Pretendard(CDN), IBM Plex Mono(Google Fonts).

## Files
- `01_알람대시보드.dc.html` ~ `07_감사로그.dc.html` — 화면 7종
- `Sidebar.dc.html` — 공용 사이드바 (active prop)
- `fdc.css` — 전체 토큰 + 공통 클래스 (스타일의 단일 소스)
- `support.js` — 프로토타입 런타임 (포팅 대상 아님)
