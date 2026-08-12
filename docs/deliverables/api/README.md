# API 명세서 생성

이 폴더의 CSV·Markdown·PDF는 `build_api_spec.py` 한 파일에서 함께 생성한다. 필드 타입의 코드 기준은 Backend Pydantic DTO이며, 기능 범위와 정책은 요구사항정의서 v1.9와 시스템설계서 v1.10을 따른다.

```bash
cd bistel-final
source .venv/bin/activate
pip install -r docs/deliverables/api/requirements.txt
python docs/deliverables/api/build_api_spec.py
```

생성기는 macOS의 AppleGothic, Windows의 맑은 고딕, Linux의 NanumGothic 순서로 한글 글꼴을 찾는다. 다른 글꼴을 사용하려면 `API_SPEC_FONT`에 TTF 파일 경로를 지정한다.

API 계약을 바꾸면 Backend DTO와 계약 테스트를 먼저 수정한 뒤 CSV·Markdown·PDF를 모두 다시 생성한다. 생성 후에는 다음을 확인한다.

- 엔드포인트 22개와 공통 오류·감사 이벤트 9종의 누락 여부
- `git diff --check`
- PDF 전 페이지 렌더링 결과의 글자 잘림·겹침·빈 페이지 여부

`/health`·`/health/ready`는 내부 운영·개발 진단 엔드포인트라 22개 업무 API와 제출용 API 명세서에는 포함하지 않는다. 이 경계는 요구사항 FR-I-05와 시스템설계서 10.1을 따른다.

생성 결과를 수동으로 따로 편집하지 않는다. 수정이 필요하면 DTO 또는 생성기를 고친 뒤 세 형식을 다시 만든다.
