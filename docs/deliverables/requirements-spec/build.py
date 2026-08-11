"""요구사항정의서 제출용 PDF 생성.

원본 `docs/specifications/요구사항정의서_v1_9_최종.md`은 건드리지 않는다.
개념 다이어그램은 `output/diagrams/`, 실제 화면 캡처는 `output/screens/`에서 그대로 읽는다
(둘 다 개인 검토용 gitignore 대상). "이 제목·문장 뒤에 이 그림을 넣는다"는 매핑만 이 스크립트가 갖고,
렌더링 단계에서만 끼워 넣으므로 원본 md 는 그대로 4명이 계속 고칠 수 있다.

```bash
source .venv/bin/activate
pip install -r docs/deliverables/requirements-spec/requirements.txt
python docs/deliverables/requirements-spec/build.py
```
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs" / "deliverables" / "_shared"))

from doc_pdf import ImageSpec, build_pdf  # noqa: E402

SRC = ROOT / "docs" / "specifications" / "요구사항정의서_v1_9_최종.md"
OUT = Path(__file__).parent / "요구사항정의서.pdf"
DIAG = ROOT / "output" / "diagrams"
SCREENS = ROOT / "output" / "screens"


def diagram(name: str, caption: str, max_height_mm: float = 225.0) -> ImageSpec:
    return ImageSpec(path=DIAG / f"{name}.png", caption=caption, max_height_mm=max_height_mm)


def screen(name: str, caption: str, max_height_mm: float = 230.0) -> ImageSpec:
    return ImageSpec(path=SCREENS / f"{name}.png", caption=caption, max_height_mm=max_height_mm)


HEADING_IMAGES: dict[str, list[ImageSpec]] = {
    "### 2.3 시스템 개요": [
        diagram("03_공정_데이터플로우", "그림 1. 공정 흐름과 데이터 플로우 — WAFER 처리 경로와 단계별 적재 테이블"),
    ],
    "## 3. 용어 정의": [
        diagram("04_도메인구조", "그림 2. 도메인 구조 — 구역·공정·설비·챔버 계층 구조와 레시피·STEP·센서의 역할"),
    ],
    "## 부록 B. 골든 시나리오 상세": [
        diagram("11_골든시나리오", "그림 10. 골든 시나리오 5종 — 정상 3종·장애 2종"),
    ],
}

# 화면 캡처 순서는 실제 사이드바 내비게이션 순서를 따른다(Layout.jsx MENUS).
# Knowledge(/knowledge)는 화면 재설계가 팀 회의로 확정되기 전이라 이번 제출에서 캡처를 뺀다.
LINE_IMAGES: dict[str, list[ImageSpec]] = {
    "**최종 화면 구성 (8개)**": [
        screen("1_알람대시보드", "그림 3. 알람 대시보드 (/dashboard)"),
        screen("2_알람목록", "그림 4. 알람 목록·상세 (/alarms/:alarmId)"),
        screen("3_트레이스뷰어", "그림 5. 트레이스 뷰어 (/traces)"),
        screen("4_Agent분석승인", "그림 6. Agent 실행·승인 (/agent-runs/:runId)"),
        screen("5_조치목록", "그림 7. 조치 목록 (/actions)"),
        screen("6_자연어분석", "그림 8. 자연어 분석 (/analytics)"),
        screen("7_감사로그", "그림 9. 감사로그 (/audit-logs) — 지면 관계상 이력 상단부만 표시"),
    ],
}


# 가나다순. 역할 표기(A/B/C/D)는 표지에 올리지 않는다.
TEAM_MEMBERS = "강연권 · 방대혁 · 신동원 · 천승현"


def main() -> None:
    out = build_pdf(
        md_path=SRC,
        out_path=OUT,
        doc_title="요구사항 정의서",
        subtitle="2026.08.11 · PhotoEtch",
        topic="LangGraph 기반 반도체 FDC 이상감지 에이전트",
        cover_mode="table",
        heading_images=HEADING_IMAGES,
        line_images=LINE_IMAGES,
        # 표지에는 내부 프로세스 메타데이터(문서번호·버전·멘토·작성자·최종검토·참조문서)를 싣지 않는다.
        drop_cover_labels={"문서 번호", "버전", "멘토", "작성자", "최종 검토", "참조 문서"},
        cover_overrides={"팀명": "PhotoEtch"},
        extra_cover_rows=[["팀원", TEAM_MEMBERS]],
        # 개정 이력(및 그 안의 멘토 요구사항 매핑 각주)은 내부 작업 기록이라 제출본 본문에서 뺀다.
        drop_sections=["### 개정 이력"],
    )
    print(f"{out.name}: {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
