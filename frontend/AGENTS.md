# Frontend 규칙

> [!CAUTION]
> 신규 `kosa_0813` epoch 전환 중이다. 데이터 수치·필드·알람·조치·Fault 표시는
> `docs/specifications/`의 v2.0 작업본과 `docs/planning/Task분해_WBS_v4_작업본.md`를 따른다.
> 구 디자인 export의 51개 알람·Fault 정답·ACT-0001~0010을 Mock이나 화면 기대값으로
> 사용하지 않는다.

## 디자인 기준

- `frontend/_design_export/v2/`는 시각·레이아웃 참고본이다. 데이터·API 계약의 근거가 아니다.
- `frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/`은 재설계 이전 이력이며
  레이아웃·Mock·계약의 기준으로 사용하지 않는다.
- 신규 가이드의 5개 기능 영역을 기존 React 8개 화면에 매핑한다. 정확한 route family와
  화면 책임은 v2.0 요구사항 11.2와 시스템설계서 12장을 따른다.

## Mock 데이터

- Mock은 `kosa_0813`의 versioned corrected fixture에서만 생성한다. V4-CM-1의 corrected
  fixture가 준비되기 전에는 구 51건을 복사하거나 빠진 값을 임의 생성하지 않는다.
- `lot_history.fault_code`, Generator 주입 위치, 제공 `action_history` 48건을 Fault·Agent
  정답으로 사용하지 않는다. 제공 action 48건은 evaluation 화면 회귀에서만 `MOCK`으로 표시한다.
- canonical 필드는 `AlarmRef`, `parameter_id`, `alarm_type`,
  `MONITORING|WARNING|EQP_HOLD`, 채널별 `EMAIL|MES_MOCK` delivery다.
- 축약 키 adapter를 새로 만들지 않는다. `shared/api/`와 컴포넌트는 Backend DTO 이름을 그대로 쓴다.

## 구조

- 컴포넌트는 직접 fetch하지 않고 모든 데이터는 `src/shared/api/`를 통해 가져온다.
- Mock과 실제 API는 같은 canonical DTO fixture의 contract test를 통과해야 한다.
- feature 폴더(`detection`, `agent`, `knowledge`, `analytics`)는 담당자별 독립 수정 영역이다.
  다른 feature를 직접 import하지 않는다.
- TypeScript를 새로 도입하지 않고 현재 JSX·ESLint·의존성 버전을 유지한다.

> 이 파일은 `frontend/CLAUDE.md`와 내용이 같아야 한다.
