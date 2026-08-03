# Development Guide

## 작업 흐름

Issue → Branch → Commit → Pull Request → Review → Merge

## 브랜치 형식

`<type>/<area>-<description>`

### Type

- `feat`: 기능
- `fix`: 버그
- `refactor`: 리팩터링
- `test`: 테스트
- `docs`: 문서
- `chore`: 설정·환경

### Area

- `common`
- `detection`
- `knowledge`
- `agent`
- `analytics`
- `integration`

## 기본 규칙

- 초기 공통 세팅 이후 `main`에 직접 push하지 않는다.
- 브랜치명은 영문 소문자와 하이픈만 사용한다.
- 기능 개발 전 이슈를 먼저 생성한다.
- PR에 `Closes #이슈번호`를 작성한다.
- API·Tool 계약 변경 시 `docs/contracts.md`를 함께 수정한다.
- `.env`, 비밀번호, API Key를 커밋하지 않는다.

## Pull Request 자동 검사

PR을 생성하거나 수정하거나 브랜치에 코드를 추가 push하면 다음 검사가 실행된다.

- `Backend CI`: Ruff·pytest
- `PR Policy`: 브랜치명·이슈 연결·PR 양식

PR 본문에는 관련 이슈를 다음 형식으로 작성한다.

```text
Closes #1