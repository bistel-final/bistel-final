"""Markdown -> 제출용 PDF 공용 렌더러 (ReportLab).

표지 + 머리글 + 쪽번호 + 표·인용·코드블록 + 그림 삽입을 지원한다.
`docs/deliverables/system-design/build.py` · `requirements-spec/build.py` 가 이 모듈을 쓴다.

**원본 사양 md 파일은 건드리지 않는다.** 그림은 원본에 이미지 참조를 심는 대신,
호출자가 "이 제목 줄 뒤에 이 그림을 넣는다"는 목록(앵커)을 넘기면 렌더링 단계에서만 끼워 넣는다.
그래서 4명이 원본 md를 동시에 고쳐도 그림 배치 때문에 충돌하지 않는다.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

FONT_DIR = Path(__file__).parent.parent / "fonts"

_FONTS_REGISTERED = False


def _register_fonts() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("PR", str(FONT_DIR / "Pretendard-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("PB", str(FONT_DIR / "Pretendard-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("MONO", str(FONT_DIR / "D2Coding-Bold.ttf")))
    pdfmetrics.registerFontFamily("PR", normal="PR", bold="PB", italic="PR", boldItalic="PB")
    _FONTS_REGISTERED = True


INK, SLATE, LINE = colors.HexColor("#22262b"), colors.HexColor("#6b7885"), colors.HexColor("#dde3e9")
NAVY, ACCENT = colors.HexColor("#18324b"), colors.HexColor("#246b9e")
ZEBRA, QUOTE_BG = colors.HexColor("#f5f7fa"), colors.HexColor("#eaf4fa")

CODE = ParagraphStyle("code", fontName="MONO", fontSize=7.4, leading=11.0, textColor=colors.HexColor("#2f3a45"))


def _styles():
    return {
        "body": ParagraphStyle("body", fontName="PR", fontSize=9.6, leading=15.4, textColor=INK, alignment=TA_LEFT, spaceAfter=6),
        "h1": ParagraphStyle("h1", fontName="PB", fontSize=19, leading=25, textColor=NAVY, spaceAfter=2),
        "h2": ParagraphStyle("h2", fontName="PB", fontSize=16, leading=23, textColor=NAVY, spaceBefore=12, spaceAfter=7),
        "h3": ParagraphStyle("h3", fontName="PB", fontSize=11.6, leading=17, textColor=ACCENT, spaceBefore=11, spaceAfter=3),
        "th": ParagraphStyle("th", fontName="PB", fontSize=8.5, leading=12.4, textColor=colors.white, alignment=1),
        "td": ParagraphStyle("td", fontName="PR", fontSize=8.5, leading=12.6, textColor=INK),
        "quote": ParagraphStyle("quote", fontName="PR", fontSize=9.1, leading=14.6, textColor=colors.HexColor("#1c3d55")),
        "li": ParagraphStyle("li", fontName="PR", fontSize=9.6, leading=15.2, textColor=INK, leftIndent=11, bulletIndent=2, spaceAfter=2.5),
        "caption": ParagraphStyle("caption", fontName="PB", fontSize=8.8, leading=12.6, textColor=SLATE, alignment=1, spaceBefore=4, spaceAfter=12),
        "cover_label": ParagraphStyle("cover_label", fontName="PB", fontSize=9.5, leading=15, textColor=colors.HexColor("#7fb6dd")),
        "cover_value": ParagraphStyle("cover_value", fontName="PR", fontSize=9.5, leading=15, textColor=colors.white),
    }


@dataclass
class ImageSpec:
    """그림 하나. `path` 는 PNG, `caption` 은 그림 아래 굵은 캡션 한 줄이다."""

    path: Path
    caption: str
    max_height_mm: float = 225.0


def inline(t: str) -> str:
    """마크다운 인라인 -> reportlab 마크업. 코드 조각을 먼저 빼둬야 내부 기호가 안 깨진다."""
    keep: list[str] = []

    def stash(m):
        keep.append(m.group(1))
        return f"\x00{len(keep) - 1}\x00"

    t = re.sub(r"`([^`]+)`", stash, t)
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)

    def pop(m):
        return f'<font face="MONO" size="8.4" color="#1d5b86">{html.escape(keep[int(m.group(1))])}</font>'

    return re.sub(r"\x00(\d+)\x00", pop, t)


def build_table(rows: list[list[str]], width: float, styles) -> Table:
    head, body = rows[0], rows[1:]
    ncol = len(head)
    raw = [max(len(re.sub(r"[*`]", "", r[i])) for r in rows) for i in range(ncol)]
    raw = [min(v, 62) ** 0.72 for v in raw]
    total = sum(raw)
    widths = [max(width * v / total, 16 * mm) for v in raw]
    scale = width / sum(widths)
    widths = [w * scale for w in widths]
    data = [[Paragraph(inline(c), styles["th"]) for c in head]] + [
        [Paragraph(inline(c), styles["td"]) for c in r] for r in body
    ]
    st = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c6ced6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.4),
    ]
    for i in range(2, len(data), 2):
        st.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle(st))
    return t


def image_flowables(spec: ImageSpec, width: float, styles) -> list:
    """그림 + 캡션. 원본 비율을 지키며 폭·높이 상한 안에 맞춘다."""
    if not spec.path.exists():
        raise FileNotFoundError(f"그림 원본이 없다: {spec.path}")
    px_w, px_h = ImageReader(str(spec.path)).getSize()
    max_h = spec.max_height_mm * mm
    w = width
    h = w * px_h / px_w
    if h > max_h:
        h = max_h
        w = h * px_w / px_h
    img = Image(str(spec.path), width=w, height=h, hAlign="CENTER")
    cap = Paragraph(inline(spec.caption), styles["caption"])
    return [Spacer(1, 6), KeepTogether([img, cap])]


def parse(
    md: str,
    width: float,
    styles,
    heading_images: dict[str, list[ImageSpec]] | None = None,
    line_images: dict[str, list[ImageSpec]] | None = None,
) -> list:
    heading_images = heading_images or {}
    line_images = line_images or {}
    seen_heading_keys: set[str] = set()
    seen_line_keys: set[str] = set()

    out: list = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j])
                j += 1
            # 한 페이지를 넘는 코드블록은 표 한 칸에 담기면 쪼개지지 않아
            # LayoutError 가 난다. 페이지에 들어갈 만큼씩 잘라 여러 상자로 낸다.
            CHUNK = 52
            out.append(Spacer(1, 3))
            for k in range(0, max(len(buf), 1), CHUNK):
                part = Preformatted("\n".join(buf[k : k + CHUNK]), CODE)
                tbl = Table([[part]], colWidths=[width], hAlign="LEFT")
                tbl.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fb")),
                            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                            ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
                            ("LEFTPADDING", (0, 0), (-1, -1), 9),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                            ("TOPPADDING", (0, 0), (-1, -1), 7),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ]
                    )
                )
                out.append(tbl)
            out.append(Spacer(1, 7))
            i = j + 1
            continue
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            rows, j = [], i
            while j < len(lines) and lines[j].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|$", lines[j].strip()):
                    rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out += [Spacer(1, 2), build_table(rows, width, styles), Spacer(1, 8)]
            i = j
            continue
        if ln.startswith("> "):
            buf, j = [], i
            while j < len(lines) and (lines[j].startswith("> ") or lines[j].strip() == ">"):
                buf.append(lines[j][2:] if len(lines[j]) > 2 else "")
                j += 1
            inner = [Paragraph(inline(p), styles["quote"]) for p in "\n".join(buf).split("\n\n") if p.strip()]
            tbl = Table([[inner]], colWidths=[width], hAlign="LEFT")
            tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), QUOTE_BG),
                        ("LINEBEFORE", (0, 0), (0, -1), 2.2, colors.HexColor("#7ba9c9")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            out += [Spacer(1, 3), tbl, Spacer(1, 7)]
            i = j
            continue
        if re.match(r"^[-*] ", ln):
            while i < len(lines) and re.match(r"^[-*] ", lines[i]):
                out.append(Paragraph(inline(lines[i][2:]), styles["li"], bulletText="•"))
                i += 1
            out.append(Spacer(1, 5))
            continue
        if re.match(r"^\d+\. ", ln):
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                n, txt = lines[i].split(". ", 1)
                out.append(Paragraph(inline(txt), styles["li"], bulletText=f"{n}."))
                i += 1
            out.append(Spacer(1, 5))
            continue
        if ln.startswith("### "):
            out.append(Paragraph(inline(ln[4:]), styles["h3"]))
            for spec in heading_images.get(ln.rstrip(), []):
                out += image_flowables(spec, width, styles)
                seen_heading_keys.add(ln.rstrip())
            i += 1
            continue
        if ln.startswith("## "):
            out += [CondPageBreak(46 * mm), Spacer(1, 6), Paragraph(inline(ln[3:]), styles["h2"])]
            for spec in heading_images.get(ln.rstrip(), []):
                out += image_flowables(spec, width, styles)
                seen_heading_keys.add(ln.rstrip())
            i += 1
            continue
        if ln.startswith("# "):
            out.append(Paragraph(inline(ln[2:]), styles["h1"]))
            i += 1
            continue
        if ln.strip() == "---":
            i += 1
            continue
        if ln.strip():
            # 첫 줄은 조건 없이 먹는다. `|`로 시작하지만 바로 다음 줄이 구분선이 아니라 표로
            # 인식되지 않은 줄(파손된 표 등)이 여기로 떨어지면, 아래 while 조건이 그 줄에서
            # 곧장 거짓이 되어 buf가 비고 i가 그대로 남아 무한루프가 된다. 최소 1줄 전진을 보장한다.
            buf = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||```|> |[-*] |\d+\. |---$)", lines[i]):
                buf.append(lines[i])
                i += 1
            out.append(Paragraph(inline(" ".join(buf)), styles["body"]))
            if len(buf) == 1 and buf[0].strip() in line_images:
                for spec in line_images[buf[0].strip()]:
                    out += image_flowables(spec, width, styles)
                seen_line_keys.add(buf[0].strip())
            continue
        i += 1

    missing_h = set(heading_images) - seen_heading_keys
    missing_l = set(line_images) - seen_line_keys
    if missing_h or missing_l:
        raise ValueError(
            "이미지 앵커를 찾지 못했다 — 원본 md 제목·문구가 바뀌었을 수 있다:\n"
            + "\n".join(f"  [heading] {k}" for k in sorted(missing_h))
            + ("\n" if missing_h and missing_l else "")
            + "\n".join(f"  [line] {k}" for k in sorted(missing_l))
        )
    return out


def split_cover_table(md: str) -> tuple[list[list[str]], str]:
    """맨 앞 표(문서 정보)를 표지용으로 떼어내고 나머지를 본문으로 돌려준다.

    표 바로 뒤에 한 줄짜리 안내 인용문(`> ...`)이 붙어 있으면 표와 함께 버린다 —
    표지로 옮긴 표를 다시 언급하는 각주라 본문에 홀로 남으면 맥락을 잃는다.
    """
    lines = md.split("\n")
    start = next((k for k, r in enumerate(lines) if r.startswith("|")), None)
    if start is None:
        raise ValueError("문서 정보 표를 찾지 못했다")
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    rows = [
        [c.strip() for c in r.strip().strip("|").split("|")]
        for r in lines[start:end]
        if not re.match(r"^\|[\s:|-]+\|$", r.strip())
    ]
    j = end
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    if j < len(lines) and lines[j].startswith("> "):
        while j < len(lines) and (lines[j].startswith("> ") or lines[j].strip() == ">"):
            j += 1
        end = j
    return rows, "\n".join(lines[end:])


def split_cover_blockquote(md: str) -> tuple[list[list[str]], str]:
    """H1 제목 줄 바로 뒤 `> key: value` 블록을 표지 표로 바꾸고, 첫 `---` 다음부터를 본문으로 돌려준다."""
    lines = md.split("\n")
    rows = [["항목", "내용"]]
    for ln in lines:
        if ln.startswith("> "):
            key, _, val = ln[2:].partition(":")
            if val:
                rows.append([key.strip(), val.strip()])
        elif ln.strip() == "---":
            break
    hr = next((k for k, r in enumerate(lines) if r.strip() == "---"), None)
    if hr is None:
        raise ValueError("헤더 구분선(---)을 찾지 못했다")
    return rows, "\n".join(lines[hr + 1 :])


def drop_section(md: str, heading: str) -> str:
    """`heading`(원문 그대로, 예: "## 개정 이력")부터 다음 같은급 이상 헤딩 직전까지 통째로 지운다.

    제출용 PDF에서 개정 이력처럼 내부 프로세스용이라 필요 없는 절을 뺄 때 쓴다.
    원본 md 파일은 건드리지 않는다 — 렌더링 직전 메모리 상의 문자열에서만 잘라낸다.
    """
    lines = md.split("\n")
    level = len(heading) - len(heading.lstrip("#"))
    start = next((k for k, r in enumerate(lines) if r.rstrip() == heading), None)
    if start is None:
        raise ValueError(f"제거할 절을 찾지 못했다: {heading!r}")
    end = start + 1
    while end < len(lines):
        m = re.match(r"^(#{1,6}) ", lines[end])
        if m and len(m.group(1)) <= level:
            break
        end += 1
    return "\n".join(lines[:start] + lines[end:])


def build_pdf(
    md_path: Path,
    out_path: Path,
    doc_title: str,
    subtitle: str,
    cover_mode: str = "table",
    heading_images: dict[str, list[ImageSpec]] | None = None,
    line_images: dict[str, list[ImageSpec]] | None = None,
    footer_label: str = "BISTel FDC",
    drop_cover_labels: set[str] | None = None,
    cover_overrides: dict[str, str] | None = None,
    drop_sections: list[str] | None = None,
    topic: str | None = None,
    extra_cover_rows: list[list[str]] | None = None,
) -> Path:
    _register_fonts()
    styles = _styles()
    md = md_path.read_text(encoding="utf-8")

    LM, RM, TM, BM = 17 * mm, 15 * mm, 22 * mm, 17 * mm
    W, H = A4
    fw = W - LM - RM

    def chrome(canv, doc):
        canv.saveState()
        canv.setFont("PR", 7.6)
        canv.setFillColor(SLATE)
        canv.drawString(LM, H - 13.5 * mm, f"{footer_label}  |  {doc_title}")
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.5)
        canv.line(LM, H - 15.6 * mm, W - RM, H - 15.6 * mm)
        canv.setFont("PR", 8)
        canv.setFillColor(SLATE)
        canv.drawRightString(W - RM, 11 * mm, str(canv.getPageNumber()))
        canv.restoreState()

    def cover(canv, doc):
        canv.saveState()
        canv.setFillColor(NAVY)
        canv.rect(0, H - 96 * mm, W, 96 * mm, stroke=0, fill=1)
        canv.setFillColor(colors.HexColor("#7fb6dd"))
        canv.setFont("PB", 10.5)
        canv.drawString(LM, H - 40 * mm, footer_label)
        canv.setFillColor(colors.white)
        canv.setFont("PB", 27)
        canv.drawString(LM, H - 56 * mm, doc_title)
        y = H - 68 * mm
        if topic:
            canv.setFont("PR", 12.5)
            canv.setFillColor(colors.HexColor("#e7edf3"))
            canv.drawString(LM, y, topic)
            y -= 9 * mm
        canv.setFont("PR", 11)
        canv.setFillColor(colors.HexColor("#cbd5e1"))
        canv.drawString(LM, y, subtitle)
        canv.restoreState()

    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        title=doc_title,
        author="PhotoEtch",
        subject=subtitle,
        leftMargin=LM,
        rightMargin=RM,
        topMargin=TM,
        bottomMargin=BM,
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[Frame(LM, BM, fw, H - 100 * mm - BM, id="c")], onPage=cover),
            PageTemplate(id="main", frames=[Frame(LM, BM, fw, H - TM - BM, id="m")], onPage=chrome),
        ]
    )

    if cover_mode == "table":
        rows, body = split_cover_table(md)
    elif cover_mode == "blockquote":
        rows, body = split_cover_blockquote(md)
    else:
        raise ValueError(f"알 수 없는 cover_mode: {cover_mode}")

    if drop_cover_labels:
        rows = [rows[0]] + [r for r in rows[1:] if r[0] not in drop_cover_labels]
    if cover_overrides:
        rows = [[r[0], cover_overrides.get(r[0], r[1])] for r in rows]
    if extra_cover_rows:
        rows = rows + extra_cover_rows
    for heading in drop_sections or []:
        body = drop_section(body, heading)

    story = [build_table(rows, fw, styles)]
    story += [NextPageTemplate("main"), PageBreak()]
    story += parse(body.lstrip("\n"), fw, styles, heading_images, line_images)
    doc.build(story)
    return out_path
