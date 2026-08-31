"""Pure scanner shared by the compatibility-alias contract and removal judge."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FEATURE_ROOT = REPOSITORY_ROOT / "frontend" / "src" / "features"
APP_ROOT = REPOSITORY_ROOT / "backend" / "app"


def resolve_python_symbol(symbol: str) -> Any:
    """Resolve a registry symbol lazily.

    Importing this helper itself has no application side effect.
    """

    parts = symbol.split(".")
    if not parts or parts.pop(0) != "backend":
        raise ValueError(f"unsupported Python symbol: {symbol}")
    for split_at in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:split_at])
        try:
            value: Any = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        for attribute in parts[split_at:]:
            value = getattr(value, attribute)
        return value
    raise ValueError(f"importable module not found: {symbol}")


def source_reads_any_alias(source: str, aliases: list[str]) -> bool:
    """Conservatively recognize dot, optional-dot, and bracket property reads."""

    return any(
        re.search(
            rf"(?:\?*\.\s*{re.escape(alias)}\b|"
            rf"\[\s*['\"]{re.escape(alias)}['\"]\s*\])",
            source,
        )
        for alias in aliases
    )


def _feature_sources(repository_root: Path) -> list[Path]:
    root = repository_root / "frontend" / "src" / "features"
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".js", ".jsx"}
    )


def _resolved_import(path: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    unresolved = path.parent / specifier
    candidates = [unresolved, unresolved.with_suffix(".js"), unresolved / "index.js"]
    return next(
        (candidate.resolve() for candidate in candidates if candidate.is_file()),
        None,
    )


def _source_imports_export(
    path: Path,
    source: str,
    *,
    module_path: Path,
    export_name: str,
) -> bool:
    for match in re.finditer(
        r"import\s*\{(?P<names>.*?)\}\s*from\s*['\"](?P<source>[^'\"]+)['\"]",
        source,
        flags=re.DOTALL,
    ):
        if _resolved_import(path, match.group("source")) != module_path.resolve():
            continue
        imported = {
            part.strip().split()[0]
            for part in match.group("names").split(",")
            if part.strip()
        }
        if export_name in imported:
            return True
    return False


def derived_feature_consumer_paths(
    entry: dict[str, Any], *, repository_root: Path = REPOSITORY_ROOT
) -> set[str]:
    paths: set[str] = set()
    if entry["kind"] == "dto_field":
        aliases = entry["compatibility_fields"]
        for path in _feature_sources(repository_root):
            if source_reads_any_alias(path.read_text(encoding="utf-8"), aliases):
                paths.add(path.relative_to(repository_root).as_posix())
        return paths

    module_ref, export_name = entry["symbol"].split("#", 1)
    module_path = repository_root / module_ref
    for path in _feature_sources(repository_root):
        source = path.read_text(encoding="utf-8")
        if _source_imports_export(
            path, source, module_path=module_path, export_name=export_name
        ):
            paths.add(path.relative_to(repository_root).as_posix())
    return paths


def _javascript_export_exists(reference: str, repository_root: Path) -> bool:
    path_text, separator, symbol = reference.partition("#")
    if not separator or not symbol:
        return False
    path = repository_root / path_text
    if not path.is_file():
        return False
    source = path.read_text(encoding="utf-8")
    return bool(
        re.search(
            rf"\bexport\s+(?:async\s+)?(?:function|class|const|let|var)\s+{re.escape(symbol)}\b",
            source,
        )
        or re.search(rf"\bexport\s*\{{[^}}]*\b{re.escape(symbol)}\b", source)
    )


def _endpoint_exists(reference: str, repository_root: Path) -> bool:
    method, separator, route = reference.partition(" ")
    if not separator or method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return False
    decorator = method.lower()
    pattern = re.compile(
        rf"@(?:app|router)\.{decorator}\(\s*['\"]{re.escape(route)}['\"]"
    )
    return any(
        pattern.search(path.read_text(encoding="utf-8"))
        for path in (repository_root / "backend" / "app").rglob("*.py")
    )


def replacement_live(
    entry: dict[str, Any], *, repository_root: Path = REPOSITORY_ROOT
) -> tuple[bool, str]:
    """Return prerequisite status and the public evidence basis."""

    if entry["kind"] == "dto_field":
        try:
            model = resolve_python_symbol(entry["symbol"])
        except (AttributeError, ImportError, ValueError):
            return False, "canonical_fields_exist"
        fields = getattr(model, "model_fields", {})
        return set(entry["canonical_fields"]) <= set(fields), "canonical_fields_exist"
    kind = entry.get("replacement_kind")
    replacement = entry.get("replacement")
    if not isinstance(replacement, str):
        return False, "replacement_missing"
    if kind == "export":
        return _javascript_export_exists(replacement, repository_root), "export_exists"
    if kind == "endpoint":
        return _endpoint_exists(replacement, repository_root), "route_exists"
    return False, "replacement_kind_unsupported"
