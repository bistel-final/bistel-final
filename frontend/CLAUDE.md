# Frontend 규칙

> [!CAUTION]
> 멘토님 제공 최종 `project.zip` 기준 문서 재기준화 중이다. `kosa_0813`, 요구사항·설계 v2.0 이하, WBS v4 이하와
> 기존 디자인 export의 수치·필드·Mock을 사용하지 않는다. WBS v5 확정 전 신규 화면 구현을
> 시작하지 않는다.

## 디자인 기준

- 최종 패키지의 Dashboard·Alarm History·Agent·Documents·Ontology 5개 화면을 canonical
  사용자 영역으로 사용한다. Text2SQL·Analytics와 기존 8개 route family는 새 요구사항에서
  확장으로 명시한 경우에만 유지한다.
- `frontend/_design_export/v2/`는 시각·레이아웃 참고본이다. 데이터·API 계약의 근거가 아니다.
- `frontend/_design_export/BISTelligence FDC 이상감지 플랫폼/`은 재설계 이전 이력이며
  레이아웃·Mock·계약의 기준으로 사용하지 않는다.
- 화면 책임과 경로는 요구사항 v2.1과 API 명세 v3을 따른다. 최종 패키지의 Ontology iframe처럼
  Neo4j 비밀번호를 Frontend에 노출하지 않고 canonical `GET /ontology/graph` Backend adapter를
  사용한다.

## Mock 데이터

- Mock은 최종 source manifest와 API v3 fixture에서만 생성한다. 새 manifest 준비 전에는
  패키지 참고 화면의 내장 데이터를 복사하거나 빠진 값을 임의 생성하지 않는다.
- 저장 알람 기준값은 TRACE 138 + SUMMARY 51 = 189이며 R03 3건은 명시적 파생 source다.
- 제공 `action_history` 12건은 평가·화면 참고 fixture다. Runtime 실행 이력으로 표시하지 않는다.
- `lot_history.fault_code`는 공개 합성 평가 라벨이지만 화면 Runtime 응답·Agent 입력·Mock 근거에는
  포함하지 않는다. 검토·평가 화면에서만 `SYNTHETIC_GENERATOR` 출처를 명시해 사용한다.
- canonical 필드는 `AlarmRef`, `parameter_id`, `alarm_type`,
  `MONITORING|WARNING|EQP_HOLD`, 외부 `EMAIL|MES`와 내부 `EMAIL|MES_MOCK` adapter다.
- 축약 키를 feature 컴포넌트에 새로 확산하지 않는다. API v3의 deprecated 호환 alias는 Backend
  serializer 한 곳에서만 제공하고 `shared/api/`가 canonical field로 정규화한다. Frontend 전환 뒤
  alias를 제거한다.

## 구조

- 컴포넌트는 직접 fetch하지 않고 모든 데이터는 `src/shared/api/`를 통해 가져온다.
- Mock과 실제 API는 같은 canonical DTO fixture의 contract test를 통과해야 한다.
- feature 폴더(`detection`, `agent`, `knowledge`, `analytics`)는 담당자별 독립 수정 영역이다.
  다른 feature를 직접 import하지 않는다.
- TypeScript를 새로 도입하지 않고 현재 JSX·ESLint·의존성 버전을 유지한다.

> 이 파일은 `frontend/AGENTS.md`와 내용이 같아야 한다.
