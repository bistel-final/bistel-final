"""C/Common V5-C-7.1: isolated, synthetic quality development, NOT U10.

No final-data bundle, export grant, shared service, or production composition is
used. Only an explicit --execute enables the bounded official OpenAI transport.
The graph stops before action persistence; all action/effect ports are forbidden.
Test dependencies intentionally make this a development-only entry point.
"""

from __future__ import annotations

# Development entry point must add backend to sys.path before local imports.
# ruff: noqa: E402
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "backend") not in sys.path:
    sys.path.insert(0, str(REPO / "backend"))

import httpx
import pytest

from app.agent import experiment, react
from app.agent.hypothesis import generate_hypothesis
from app.agent.investigation_budget import DEVELOPMENT_WIDE
from app.agent.routing import GraphBoundary
from app.common import llm
from tests.support.agent_quality_development_cases import (
    ALARM,
    CASES,
    NOW,
    DevelopmentCase,
    SyntheticInvestigationTools,
)
from tests.unit import test_agent_graph as harness

KIND = "DEVELOPMENT_SYNTHETIC_QUALITY_NOT_U10_OR_RELEASE"
MODEL = "gpt-5.6-luna"
MAX_HTTP = 512
DEFAULT_HTTP = 256
MAX_COMPLETION_TOKENS = 4096


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def save(root: Path, name: str, value: Any) -> None:
    """Never replace an earlier request, response, case, or result."""
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(plain(value), out, ensure_ascii=False, indent=2)


class Live:
    def __init__(self, root: Path, limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= MAX_HTTP:
            raise ValueError("DEVELOPMENT_HTTP_LIMIT_INVALID")
        self.root, self.limit, self.count = root, limit, 0
        self.calls: list[dict[str, Any]] = []
        self.case_id: str | None = None

    def request(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.count >= self.limit:
            raise llm.LlmDependencyError("DEVELOPMENT_HTTP_LIMIT")
        body = kwargs["json"]
        if (
            url != "https://api.openai.com/v1/chat/completions"
            or body.get("model") != MODEL
            or body.get("max_completion_tokens") != MAX_COMPLETION_TOKENS
            or body.get("reasoning_effort") != "low"
            or "temperature" in body
            or "seed" in body
        ):
            raise llm.LlmDependencyError("DEVELOPMENT_CONFIGURATION_MISMATCH")
        self.count += 1
        number = self.count
        # Headers and API credentials are deliberately never serialized.
        save(self.root, f"request-{number:03d}.json", body)
        started = time.monotonic()
        item: dict[str, Any] = {"http": number, "case": self.case_id}
        try:
            response = httpx.post(url, **kwargs)
        except httpx.HTTPError as exc:
            item.update(error_type=type(exc).__name__, usage_unobserved=True)
            raise
        else:
            try:
                raw = response.json()
            except ValueError:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            item.update(
                status=response.status_code,
                model=raw.get("model"),
                usage=raw.get("usage"),
                choices=raw.get("choices"),
                usage_unobserved=not isinstance(raw.get("usage"), dict),
            )
            return response
        finally:
            item["seconds"] = round(time.monotonic() - started, 3)
            save(self.root, f"response-{number:03d}.json", item)
            self.calls.append(item)
            print(
                json.dumps(
                    {
                        k: item.get(k)
                        for k in ("http", "case", "status", "error_type", "seconds")
                    }
                ),
                flush=True,
            )

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        return llm.chat_with_usage(messages, request_port=self.request, **kwargs)


@contextmanager
def memory_transaction():
    yield object()


def forbidden(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("DEVELOPMENT_EXTERNAL_EFFECT_FORBIDDEN")


def run_case(case: DevelopmentCase, complete: Any) -> dict[str, Any]:
    from tests.support.agent_quality_holdout_cases import (
        HoldoutCase,
        SyntheticHoldoutInvestigationTools,
    )

    factory = (
        SyntheticHoldoutInvestigationTools
        if isinstance(case, HoldoutCase)
        else SyntheticInvestigationTools
    )
    tools = factory(case, budget_limit=26, read_limit=24)
    selections: list[dict[str, Any]] = []

    def select(context):
        outcome = react.select_next_step(context, completion_port=complete)
        selections.append({"selection": outcome.selection, "context": context})
        return outcome

    def hypothesis(fdc, graph, docs, route, gaps, investigation):
        return generate_hypothesis(
            fdc, graph, docs, route, gaps, investigation, completion_port=complete
        )

    with pytest.MonkeyPatch.context() as patch:
        # The harness binds repositories to in-memory Python objects only.
        harness._build(
            patch,
            tools=tools,
            level_route=case.route(),
            now=lambda: NOW,
            diagnostic_wafer_refs=case.diagnostic_wafer_refs,
        )
        graph = experiment.build_level_graph(
            3,
            selector_port=select,
            hypothesis_port=hypothesis,
            clock=lambda: NOW,
            tools=tools,
            transactions=memory_transaction,
            routing_graph=GraphBoundary(forbidden, forbidden),
            configured_llm_model=MODEL,
            experimental_investigation_budget=DEVELOPMENT_WIDE,
        )
        config = {
            "configurable": {"thread_id": "synthetic-quality-development"},
            "recursion_limit": 100,
        }
        state = graph.invoke({"requested_alarm": ALARM}, config=config)
        next_nodes = graph.get_state(config).next
        history = [
            {"tool": call.tool_name, "input": call.input, "status": call.status}
            for call in tools.history("RUN-1")
        ]
        return plain(
            {
                "case_id": case.case_id,
                "title": case.title,
                "acceptance_for_human_review_only": case.acceptance,
                "investigation_completed": state.get("hypothesis") is not None,
                "stopped_before_external_effects": next_nodes == ("persist_action",),
                "next_nodes": next_nodes,
                "selections": selections,
                "hypothesis": state.get("hypothesis"),
                "react_trace": state.get("react_trace"),
                "errors": state.get("errors"),
                "tool_budget": tools.budget("RUN-1"),
                "read_history": history,
                "observations": {
                    key: state.get(key)
                    for key in (
                        "fdc_evidence",
                        "graph_evidence",
                        "fdc_evidence_set",
                        "document_evidence_set",
                        "history_evidence_set",
                        "metrology_evidence_set",
                    )
                },
                "real_shared_service_effects": 0,
                "automated_quality_verdict": "NOT_GRADED_REQUIRES_EVIDENCE_REVIEW",
            }
        )


def configuration() -> dict[str, Any]:
    base, key = llm._resolve_endpoint()
    if (
        base != "https://api.openai.com/v1"
        or not key
        or llm.LLM_PROVIDER != "openai"
        or llm.configured_model() != MODEL
        or os.getenv("LLM_REASONING_EFFORT", "low").strip() != "low"
    ):
        raise ValueError("DEVELOPMENT_CONFIGURATION_MISMATCH")
    return {
        "model": MODEL,
        "endpoint": base,
        "reasoning_effort": "low",
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "runtime_default_max_completion_tokens": llm.LLM_MAX_TOKENS,
        "transport_retries": llm._retry_max(),
        "temperature": None,
        "seed": None,
    }


def selected_cases(suite: str) -> tuple[DevelopmentCase, ...]:
    from tests.support.agent_quality_holdout_cases import HOLDOUT_CASES

    if suite == "base":
        return CASES
    if suite == "holdout":
        return HOLDOUT_CASES
    if suite == "all":
        return (*CASES, *HOLDOUT_CASES)
    raise ValueError("DEVELOPMENT_SUITE_INVALID")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-http", type=int, default=DEFAULT_HTTP)
    parser.add_argument("--suite", choices=("base", "holdout", "all"), default="base")
    args = parser.parse_args()
    if not 1 <= args.max_http <= MAX_HTTP:
        parser.error(f"--max-http must be 1..{MAX_HTTP}")
    settings = configuration()
    cases = selected_cases(args.suite)
    files = sorted(
        {
            *REPO.glob("backend/app/agent/*.py"),
            *REPO.glob("backend/app/common/*.py"),
            Path(__file__).resolve(),
            REPO / "backend/tests/support/agent_quality_development_cases.py",
            REPO / "backend/tests/support/agent_quality_holdout_cases.py",
            REPO / "backend/tests/unit/test_agent_graph.py",
        }
    )
    plan = {
        "kind": KIND,
        "created_at": datetime.now(UTC).isoformat(),
        "git_revision": git("rev-parse", "HEAD"),
        "working_tree_status": git("status", "--porcelain"),
        "source_sha256": {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in files},
        "configuration": settings,
        "profile": asdict(DEVELOPMENT_WIDE),
        "max_http_requests_including_retries": args.max_http,
        "suite": args.suite,
        "cases": [asdict(case) for case in cases],
        "source_data": "Authored synthetic DTOs only; no CF8/final12/source corpus",
        "scope": "Single fixed suite, all failures retained; no U10/release verdict",
        "quality_priority": (
            "Grounded investigation and conclusions before operating cap selection"
        ),
        "real_shared_service_effects": 0,
    }
    parent = REPO / "output/v5-c-7.1"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="development-wide-quality-", dir=parent))
    save(root, "plan.json", plan)
    print(
        json.dumps(
            {
                "output": str(root),
                "execute": args.execute,
                "cases": len(cases),
                "max_http": args.max_http,
            }
        ),
        flush=True,
    )
    if not args.execute:
        return 0
    live = Live(root, args.max_http)
    results = []
    with pytest.MonkeyPatch.context() as patch:
        # This isolated process only. Production config/env files stay unchanged.
        patch.setattr(llm, "LLM_MAX_TOKENS", MAX_COMPLETION_TOKENS)
        for case in cases:
            live.case_id = case.case_id
            start = live.count
            try:
                row = run_case(case, live.complete)
            except Exception as exc:
                row = {
                    "case_id": case.case_id,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                    "investigation_completed": False,
                }
            row["http_requests"] = live.count - start
            results.append(row)
            save(root, f"case-{case.case_id}.json", row)
            print(
                json.dumps(
                    {
                        key: row.get(key)
                        for key in (
                            "case_id",
                            "investigation_completed",
                            "http_requests",
                            "error_type",
                        )
                    }
                ),
                flush=True,
            )
            if live.count >= args.max_http:
                break
    final_sources = {
        str(p.relative_to(REPO)): sha(p.read_bytes()) if p.is_file() else None
        for p in files
    }
    source_changed = final_sources != plan["source_sha256"]
    save(
        root,
        "result.json",
        {
            "kind": KIND,
            "source_changed_during_run": source_changed,
            "final_source_sha256": final_sources,
            "cases": results,
            "all_cases_attempted": len(results) == len(cases),
            "http_requests": live.count,
            "usage": [
                {
                    "http": row["http"],
                    "usage": row.get("usage"),
                    "usage_unobserved": row["usage_unobserved"],
                }
                for row in live.calls
            ],
            "official_u10_verdict_unchanged": True,
            "automated_quality_verdict": "NOT_GRADED_REQUIRES_EVIDENCE_REVIEW",
        },
    )
    return (
        0
        if not source_changed
        and len(results) == len(cases)
        and all(
            row.get("investigation_completed")
            and row.get("stopped_before_external_effects")
            for row in results
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
