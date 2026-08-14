"""V4 manifest 검증기의 호환 CLI 진입점.

구 CM-0.4의 단일 16-table DB verifier는 폐기했다. 실제 계약·구현은
``manifest_v3.py``에 있으며, 기존 스크립트 경로를 사용하는 호출자만 이 얇은
entrypoint를 거친다.
"""

from __future__ import annotations

from manifest_v3 import main

if __name__ == "__main__":
    raise SystemExit(main())
