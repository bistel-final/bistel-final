# 시스템 설계서 제출용 PDF 생성

> [!CAUTION]
> **FINAL-DOC — 구 PDF·생성기 사용 중지.** `시스템설계서.pdf`와 이 폴더의 `build.py`는
> v1.10 이전 baseline용 산출물이다. 현재 기준은
> [`시스템설계서_v2_1_작업본.md`](../../specifications/시스템설계서_v2_1_작업본.md)이며,
> v2.1 작업본은 교차검토를 완료했고 V5 Task 기준으로 구현한다. 원본·삽입 자산·생성기가 함께
> 갱신되기 전에는 구 PDF를 제출·구현 근거로 사용하거나
> 아래 생성 명령을 실행하지 않는다. 이하 내용은 이전 epoch 재현 이력이다.

`시스템설계서.pdf`는 원본 `docs/specifications/시스템설계서_v1_10_최종.md`을 그대로 렌더링하되,
정해진 제목 줄 뒤에 아키텍처·데이터플로우·DB·LangGraph·Text2SQL·Tool 다이어그램 11종을 끼워 넣는다.

**원본 md는 건드리지 않는다.** 어느 그림을 어느 절 뒤에 넣을지는 이 폴더의 `build.py`가 갖고 있고,
렌더링 단계에서만 끼워 넣는다. 그래서 4명이 원본을 계속 고쳐도 그림 배치 때문에 충돌하지 않는다.

```bash
cd bistel-final
source .venv/bin/activate
pip install -r docs/deliverables/system-design/requirements.txt
python docs/deliverables/system-design/build.py
```

## 그림 원본

그림은 `output/diagrams/`(개인 검토용, `.gitignore` 대상)의 PNG를 그대로 읽는다. 로컬에 없으면 먼저 만든다.

```bash
cd output/diagrams
../../.venv/bin/python 01_시스템아키텍처.py   # 등 필요한 스크립트 실행 → SVG
# SVG → PNG 재현 절차는 v2.1 생성 Task에서 이 문서와 추적 가능한 스크립트로 함께 갱신한다
```

`build.py`의 `img()` 목록이 그림 파일명과 삽입 위치(원본 md의 정확한 제목 줄)를 함께 갖는다.
원본 제목이 바뀌면 `build.py`가 "이미지 앵커를 찾지 못했다"는 오류로 즉시 알려준다 — 조용히 그림이 빠지지 않는다.

## 계약을 바꿨을 때

API 계약과 달리 이 문서는 그림 배치만 하므로, 본문 내용을 바꾸려면 원본 md(`docs/specifications/`)를 먼저 고친다.
그림을 추가·교체하려면 `build.py`의 `IMAGES` 딕셔너리만 고치면 된다.

생성 후 확인할 것:

- 페이지 수가 급격히 줄지 않았는지(그림 앵커 매칭 실패 시 예외로 멈추므로 보통은 안전하지만, 캡션·순서는 직접 확인)
- 그림이 페이지 중간에서 잘리지 않는지 — `KeepTogether`가 안 맞으면 다음 페이지로 통째로 넘어간다(정상)
- `git diff --check`

렌더러 공용 코드(`inline()`·`build_table()`·표지·머리글)는 `docs/deliverables/_shared/doc_pdf.py`에 있고
`requirements-spec/build.py`와 같이 쓴다. 글꼴은 `docs/deliverables/fonts/`에 번들돼 있다(라이선스는 `NOTICE.md`).
