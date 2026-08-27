"""Canonical cs2 RAG chunking contract shared by loader and tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

CHUNK_SCHEMA_VERSION = "cs2"
CHUNK_ID_FORMAT = "<document_id>:cs2:<seq:04d>"
FALLBACK_SECTION_TITLE = "문서 안내"
H2_HEADING_REGEX = r"^##\s+(.+?)\s*$"
H3_HEADING_REGEX = r"^###\s+(.+?)\s*$"

CHUNK_CONTRACT: dict[str, Any] = {
    "chunk_id_format": CHUNK_ID_FORMAT,
    "continuation_title_format": "<section> (<piece_index_1_based>)",
    "contract": "rag-chunk-cs2",
    "decode": "utf-8",
    "embedding_input_format": "<document_title> / <section>\n\n<content>",
    "fallback_section_title": FALLBACK_SECTION_TITLE,
    "h2_body_handling": "emit-only-when-nonempty",
    "h2_heading_regex": H2_HEADING_REGEX,
    "h3_heading_regex": H3_HEADING_REGEX,
    "heading_hierarchy": "H2-topic;H3-independent-child-with-H2-title-context",
    "line_endings": "LF",
    "long_section_split": "blank-line-paragraph-greedy;oversize-paragraph-unsplit",
    "max_chars": 1000,
    "normalization": "NFC",
    "preamble_handling": "prepend-to-first-H2",
    "sequence": "1-based-after-semantic-split",
    "short_piece_merge": "none",
    "version": 2,
}


def canonical_contract_json(contract: dict[str, Any] = CHUNK_CONTRACT) -> str:
    return json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


CHUNK_CONTRACT_SHA256 = hashlib.sha256(
    canonical_contract_json().encode("utf-8")
).hexdigest()
MAX_CHARS = int(CHUNK_CONTRACT["max_chars"])
