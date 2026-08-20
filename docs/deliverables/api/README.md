# API 명세서 생성

> [!CAUTION]
> **FINAL-DOC — 구 PDF·생성기 사용 중지.** 기존 `API명세서.md`·`.csv`·`.pdf`와
> `build_api_spec.py`는 최종 `project.zip` 이전 산출물이다. 현재 계약 기준은
> `API명세서_v3_작업본.md`·`.csv`이며, v3 계약·생성기가 함께 갱신되기 전에는 구 PDF를
> 제출·구현 근거로 사용하거나 기존 생성기를 실행하지 않는다. 아래 명령은 이전 epoch 재현
> 이력일 뿐 현재 실행 절차가 아니다.

API v3의 기능 범위와 정책은 요구사항정의서 v2.1과 시스템설계서 v2.1을 따른다. 외부 최소
호환 9개 endpoint에는 `POST /agent/ask`가 포함되고 Text2SQL·Analytics는 선택 확장으로
구분한다. Neo4j credential 노출을 대체하는 `GET /relations/chambers/{chamber_id}`는 별도 보안 필수 public
계약이고, `POST /internal/actions/{action_id}/delivery`는 n8n·Kafka 결과 write-back용 필수
내부 계약이다. `/health`·`/health/ready`는 별도 필수 운영 계약으로 둔다. 필드 타입의 최종 코드
기준은 새 WBS에 따라 갱신할 Backend Pydantic DTO다.

기존 산출물 생성 절차는 이력 재현 용도다.

```bash
cd bistel-final
source .venv/bin/activate
pip install -r docs/deliverables/api/requirements.txt
python docs/deliverables/api/build_api_spec.py
```

생성기는 macOS의 AppleGothic, Windows의 맑은 고딕, Linux의 NanumGothic 순서로 한글 글꼴을 찾는다. 다른 글꼴을 사용하려면 `API_SPEC_FONT`에 TTF 파일 경로를 지정한다.

v3 계약을 확정한 뒤에는 Backend DTO·계약 테스트와 생성기를 함께 갱신하고 CSV·Markdown·PDF를
동일 revision으로 생성한다. 생성 후에는 다음을 확인한다.

- 외부 최소 호환 9개 endpoint와 명시된 확장 endpoint의 누락·혼입 여부
- `git diff --check`
- PDF 전 페이지 렌더링 결과의 글자 잘림·겹침·빈 페이지 여부

`/health`·`/health/ready`는 내부 운영·개발 진단 엔드포인트라 외부 최소 호환 9개 업무
API에 포함하지 않는다. 운영 appendix에 분리해 기록한다.

생성 결과를 수동으로 따로 편집하지 않는다. 수정이 필요하면 DTO 또는 생성기를 고친 뒤 세 형식을 다시 만든다.
