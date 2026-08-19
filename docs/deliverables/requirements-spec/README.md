# 요구사항 정의서 제출용 PDF 생성

> [!CAUTION]
> **FINAL-DOC — 구 PDF·생성기 사용 중지.** `요구사항정의서.pdf`와 이 폴더의 `build.py`는
> v1.9 이전 baseline용 산출물이다. 현재 기준은
> [`요구사항정의서_v2_1_작업본.md`](../../specifications/요구사항정의서_v2_1_작업본.md)이며,
> v2.1 원본·삽입 자산·생성기가 함께 갱신되기 전에는 구 PDF를 제출·구현 근거로 사용하거나
> 아래 생성 명령을 실행하지 않는다. 이하 내용은 이전 epoch 재현 이력이다.

`요구사항정의서.pdf`는 원본 `docs/specifications/요구사항정의서_v1_9_최종.md`을 그대로 렌더링하되,
공정·도메인 다이어그램 2종, 실제 화면 캡처 7종, 골든 시나리오 다이어그램 1종을 끼워 넣는다.

**원본 md는 건드리지 않는다.** 시스템 설계서 쪽과 같은 방식 — `build.py`가 삽입 위치를 갖고
렌더링 단계에서만 끼워 넣는다. 자세한 원리는 `../system-design/README.md` 참고.

```bash
cd bistel-final
source .venv/bin/activate
pip install -r docs/deliverables/requirements-spec/requirements.txt
python docs/deliverables/requirements-spec/build.py
```

## 그림 원본

- 개념 다이어그램(공정 흐름 · 도메인 구조 · 골든 시나리오) → `output/diagrams/`
- 실제 화면 캡처 7장 → `output/screens/` — `npm run build && npm run preview` 로 띄운 화면을 Chrome headless로 찍는다

둘 다 개인 검토용(`.gitignore` 대상)이라 로컬에 없으면 먼저 만들어야 한다.

## 화면 캡처를 다시 찍을 때

Knowledge(`/knowledge`)는 화면 재설계가 팀 회의로 확정되기 전이라 이번 제출에서 뺐다. 사이드바가
8메뉴로 바뀌면(=Knowledge가 내비게이션에 들어오면) `build.py`의 `LINE_IMAGES` 목록에 8번째 캡처를 추가한다.

감사로그(`/audit-logs`)는 이벤트가 계속 쌓이는 화면이라 전체를 한 장에 담으면 세로로 너무 길어진다.
캡처 스크립트가 상단 2600 CSS px에서 자른다 — 캡션에 "지면 관계상 이력 상단부만 표시"를 그대로 둔다.

```bash
cd frontend && npm run build && npx vite preview --port 4173 --strictPort &
# Chrome headless 로 각 라우트를 2배율 전체 페이지 캡처 → 배경색 기준으로 하단 여백 정리
# 구체적인 명령은 output/README.md 참고
```

## 계약을 바꿨을 때

본문 내용을 바꾸려면 원본 md를 먼저 고친다. 그림을 추가·교체하려면 `build.py`의
`HEADING_IMAGES`·`LINE_IMAGES` 딕셔너리만 고친다. 원본의 제목·문구가 바뀌면 앵커를 못 찾아
`build.py`가 즉시 예외로 멈춘다 — 그림이 조용히 빠진 채 PDF가 나오지 않는다.
