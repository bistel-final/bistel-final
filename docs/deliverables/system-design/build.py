"""시스템설계서 제출용 PDF 생성.

원본 `docs/specifications/시스템설계서_v1_10_최종.md`은 건드리지 않는다.
아래 IMAGES 는 "이 제목 줄 뒤에 이 그림을 넣는다"는 매핑이고, 렌더링 단계에서만 끼워 넣는다.
그림 원본은 `output/diagrams/`(개인 검토용, gitignore 대상)이며 이 스크립트가 그 PNG 를 그대로 읽는다.

```bash
source .venv/bin/activate
pip install -r docs/deliverables/system-design/requirements.txt
python docs/deliverables/system-design/build.py
```
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs" / "deliverables" / "_shared"))

from doc_pdf import ImageSpec, build_pdf  # noqa: E402

SRC = ROOT / "docs" / "specifications" / "시스템설계서_v1_10_최종.md"
OUT = Path(__file__).parent / "시스템설계서.pdf"
DIAG = ROOT / "output" / "diagrams"


def img(name: str, caption: str, max_height_mm: float = 225.0) -> ImageSpec:
    return ImageSpec(path=DIAG / f"{name}.png", caption=caption, max_height_mm=max_height_mm)


# 그림 번호는 문서에 등장하는 순서 그대로다 (헤딩 anchor 의 배치 순서 = 본문 순서).
IMAGES: dict[str, list[ImageSpec]] = {
    "### 1.1 논리 구성": [
        img("01_시스템아키텍처", "그림 1. 시스템 아키텍처 — React·FastAPI·LangGraph·PostgreSQL·Neo4j·n8n"),
        img("07_계층데이터플로우", "그림 2. 계층별 데이터 플로우 — 처리 책임과 저장소 접근 계정"),
    ],
    "### 1.3 현재 개발 구성과 최종 배포 구성": [
        img("02_배포구성도", "그림 3. 배포 구성도 — 개발 구성과 최종 Docker Compose 구성", max_height_mm=235),
    ],
    "## 3. 데이터 및 마이그레이션 설계": [
        img("13_DB관계도", "그림 4. DB 관계도 — 테이블 25개와 외래키"),
    ],
    "### 3.2 신규 연결 테이블과 컬럼": [
        img("14_DB상세", "그림 5. DB 상세 — 25개 테이블 전체 컬럼과 신규 테이블 2종·컬럼 6종"),
    ],
    "### 7.2 Node와 Edge": [
        img("08_LangGraph흐름", "그림 6. LangGraph Node·Edge 흐름"),
    ],
    "### 7.5 조치 생성과 승인 트랜잭션": [
        img("10_HITL상태전이", "그림 7. HITL 상태 전이 — WAITING_APPROVAL 부터 승인·반려까지"),
        img("05_E2E트레이스", "그림 8. E2E 트레이스 — EQP_HOLD 승인 경로의 읽기·쓰기", max_height_mm=235),
    ],
    "### 7.7 조치 결정 함수": [
        img("09_조치결정규칙", "그림 9. 조치 결정 규칙 — decide_action() 의 조치 코드 결정 절차"),
    ],
    "### 9.3 sqlglot 검증 순서": [
        img("12_Text2SQL안전검증", "그림 10. Text2SQL 안전 검증 파이프라인 9단계와 차단 지점"),
    ],
    "### 10.6 Tool 5종 고정 계약": [
        img("06_Tool역할", "그림 11. Tool 5종의 역할 분담 — 증상·위치·지식·전송·분석"),
    ],
}


# 가나다순. 역할 표기(A/B/C/D)는 표지에 올리지 않는다.
TEAM_MEMBERS = "강연권 · 방대혁 · 신동원 · 천승현"


def main() -> None:
    out = build_pdf(
        md_path=SRC,
        out_path=OUT,
        doc_title="시스템 설계서",
        subtitle="2026.08.12 · PhotoEtch",
        topic="LangGraph 기반 반도체 FDC 이상감지 에이전트",
        cover_mode="blockquote",
        heading_images=IMAGES,
        # 제출용 세 문서(요구사항 정의서·시스템 설계서·API 명세서)의 표지 표를
        # 작성일 · 최종 수정일 · 팀명 · 팀원 네 줄로 통일한다.
        # 문서 버전 · 기준 문서 · 목표 제출일 · 저장소는 내부 진행용이라 싣지 않는다.
        # "최종 수정일"은 원본 블록쿼트에도 있지만 extra_cover_rows 에서 점(.)
        # 표기로 다시 넣으므로, 여기서 하이픈(-) 표기 원본 줄은 제거해 중복을 막는다.
        drop_cover_labels={"문서 버전", "기준 문서", "목표 제출일", "저장소", "최종 수정일"},
        # 원본 머리말의 "작성일"은 최종 수정 시점이다. 개정 이력 v0.1 기준으로 실제 작성일을 쓴다.
        cover_overrides={"작성일": "2026.08.04"},
        extra_cover_rows=[
            ["최종 수정일", "2026.08.12"],
            ["팀명", "PhotoEtch"],
            ["팀원", TEAM_MEMBERS],
        ],
        # 개정 이력은 내부 작업 기록이라 제출본 본문에서 뺀다.
        drop_sections=["## 개정 이력"],
    )
    print(f"{out.name}: {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
