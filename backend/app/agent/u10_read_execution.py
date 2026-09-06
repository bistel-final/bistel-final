"""U10 fixed-policy/read execution core; no provider, DB or release entrypoint.

The eventual runner must bind snapshot-authorized arguments and a read-only,
hard-timeout adapter. This in-memory session is not the production DB budget
reservation boundary and never invokes a selector, hypothesis or send action.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from threading import Lock
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    canonical_json,
    digest,
)
from app.agent.u10_comparison import (
    SLOTS,
    TOOLS,
    EvidenceIds,
    Inventory,
    ReadCall,
    SkippedSlot,
    Slot,
)

FIXED_POLICY_SPEC = {
    "version": "FIXED_POLICY_V21",
    "slots": list(SLOTS),
    "tools": TOOLS,
    "read_cap": 8,
    "same_tool_cap": 4,
    "retry_per_selection": 1,
    "selection_cap": 10,
    "document_query": {
        "version": "fixed-document-query-v1",
        "fields": ["model_code", "parameter_ids_sorted_unique"],
        "suffixes": ["FDC 이상 원인 점검", "FDC 점검 절차"],
        "separator": " ",
        "max_length": 200,
        "model_filter": "snapshot_model_code",
    },
}


def fixed_policy_sha256() -> str:
    return digest(canonical_json(FIXED_POLICY_SPEC))


class DocumentContext(EvidenceModel):
    """Snapshot identifiers only: no oracle, hypothesis or fixture ID fields."""

    model_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
    parameter_ids: Annotated[
        list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]],
        Field(max_length=32),
    ]


def fixed_policy_document_query(context: DocumentContext, slot: Slot) -> str:
    if slot not in ("DOCUMENT_1", "DOCUMENT_2"):
        raise EvidenceError("DOCUMENT_SLOT_INVALID")
    spec = FIXED_POLICY_SPEC["document_query"]
    suffix = spec["suffixes"][0 if slot == "DOCUMENT_1" else 1]
    # Reserve suffix space so both queries remain distinct even at the cap.
    prefix = spec["separator"].join(
        [context.model_code, *sorted(set(context.parameter_ids))]
    )
    prefix = prefix[: spec["max_length"] - len(suffix) - 1].rstrip()
    return f"{prefix} {suffix}"


class ReadRequest(EvidenceModel):
    slot: Slot
    arguments: dict[str, Any]

    @model_validator(mode="after")
    def bounded_json(self) -> ReadRequest:
        if len(canonical_json(self.arguments)) > 16384:
            raise ValueError("READ_ARGUMENTS_TOO_LARGE")
        return self


class ReadObservation(EvidenceModel):
    status: Literal["SUCCESS", "ERROR", "TIMEOUT"]
    evidence_ids: EvidenceIds

    @model_validator(mode="after")
    def failed_has_no_evidence(self) -> ReadObservation:
        if self.status != "SUCCESS" and self.evidence_ids.values:
            raise ValueError("FAILED_READ_EVIDENCE_INVALID")
        return self


def _empty_ids() -> EvidenceIds:
    return EvidenceIds(values=[], sha256=digest(canonical_json([])))


class ReadSession:
    """Single-policy/attempt state shared by fixed and future ReAct runner.

    Call ``execute`` once per guarded selection. ERROR/TIMEOUT retries the exact
    same canonical arguments once, consuming budget again. A budget rejection
    occurs before adapter invocation and preserves every prior attempt. The
    runner must mark an interrupted/incomplete investigation accordingly.
    """

    def __init__(
        self,
        inventory: Inventory,
        invoke: Callable[[str, dict[str, Any]], ReadObservation],
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._inventory = inventory.model_copy(deep=True)
        self._invoke = invoke
        self._clock = clock_ns
        self._calls: list[ReadCall] = []
        self._selections = 0
        self._tool_counts: Counter[str] = Counter()
        self._reservations = 0
        self._lock = Lock()
        self._broken = False

    @property
    def calls(self) -> list[ReadCall]:
        return [call.model_copy(deep=True) for call in self._calls]

    def execute(self, request: ReadRequest) -> list[ReadCall]:
        if not self._lock.acquire(blocking=False):
            raise EvidenceError("READ_SESSION_BUSY")
        try:
            if self._broken:
                raise EvidenceError("READ_SESSION_INVALID")
            return self._execute(request)
        except BaseException:
            # Never continue after an unrecorded/malformed observation or a
            # partial selection interrupted by a cap. The runner retains calls
            # but must treat the session as terminal, not invent completion.
            self._broken = True
            raise
        finally:
            self._lock.release()

    def _execute(self, request: ReadRequest) -> list[ReadCall]:
        # Revalidate copied values: Pydantic frozen models still contain mutable dicts.
        request = ReadRequest.model_validate(request.model_dump())
        if not self._inventory.available_slots()[request.slot]:
            raise EvidenceError("NO_CANDIDATE_CALL")
        if self._selections >= FIXED_POLICY_SPEC["selection_cap"]:
            raise EvidenceError("READ_SELECTION_CAP")
        body = canonical_json(request.arguments)
        tool = TOOLS[request.slot]
        self._selections += 1
        start_index = len(self._calls)
        for retry in range(FIXED_POLICY_SPEC["retry_per_selection"] + 1):
            if self._reservations >= FIXED_POLICY_SPEC["read_cap"]:
                raise EvidenceError("READ_BUDGET_EXHAUSTED")
            if self._tool_counts[tool] >= FIXED_POLICY_SPEC["same_tool_cap"]:
                raise EvidenceError("TOOL_BUDGET_EXCEEDED")
            # Reserve BEFORE invoking, including callback exceptions and timeouts.
            self._reservations += 1
            self._tool_counts[tool] += 1
            started = self._clock()
            try:
                observation = self._invoke(tool, json.loads(body))
            except TimeoutError:
                observation = ReadObservation(
                    status="TIMEOUT", evidence_ids=_empty_ids()
                )
            except Exception:
                # Raw dependency exceptions must never enter the evidence record.
                observation = ReadObservation(status="ERROR", evidence_ids=_empty_ids())
            ended = self._clock()
            if ended < started:
                raise EvidenceError("MONOTONIC_CLOCK_INVALID")
            if not isinstance(observation, ReadObservation):
                raise EvidenceError("READ_OBSERVATION_INVALID")
            observation = ReadObservation.model_validate(observation.model_dump())
            call = ReadCall(
                slot=request.slot,
                tool=tool,
                selection=self._selections,
                retry=retry,
                input_digest=digest(body),
                status=observation.status,
                latency_ms=(ended - started) // 1_000_000,
                evidence_ids=observation.evidence_ids,
            )
            self._calls.append(call)
            if call.status == "SUCCESS":
                break
        return self.calls[start_index:]


def fixed_policy_requests(
    inventory: Inventory,
    bound_inputs: dict[str, dict[str, Any]],
    context: DocumentContext,
) -> tuple[list[ReadRequest], list[SkippedSlot]]:
    """Validate the complete fixed path BEFORE any read adapter is called.

    The future snapshot adapter owns the non-document input allowlists. This
    function forbids missing/extra slots and owns both document query strings.
    """
    available = inventory.available_slots()
    expected = {
        slot for slot in SLOTS if available[slot] and not slot.startswith("DOCUMENT_")
    }
    if set(bound_inputs) != expected:
        raise EvidenceError("FIXED_INPUT_SLOTS_MISMATCH")
    requests, skips = [], []
    for slot in SLOTS:
        if not available[slot]:
            skips.append(SkippedSlot(slot=slot, reason="NO_CANDIDATE"))
            continue
        arguments = (
            {
                "query": fixed_policy_document_query(context, slot),
                "model_code": context.model_code,
            }
            if slot.startswith("DOCUMENT_")
            else bound_inputs[slot]
        )
        requests.append(
            ReadRequest(slot=slot, arguments=arguments).model_copy(deep=True)
        )
    return requests, skips


def execute_fixed_policy(
    inventory: Inventory,
    bound_inputs: dict[str, dict[str, Any]],
    context: DocumentContext,
    invoke: Callable[[str, dict[str, Any]], ReadObservation],
    *,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> tuple[list[ReadCall], list[SkippedSlot]]:
    requests, skips = fixed_policy_requests(inventory, bound_inputs, context)
    session = ReadSession(inventory, invoke, clock_ns=clock_ns)
    for request in requests:
        if len(session.calls) == FIXED_POLICY_SPEC["read_cap"]:
            break
        try:
            session.execute(request)
        except EvidenceError as exc:
            if str(exc) != "READ_BUDGET_EXHAUSTED":
                raise
            break
    return session.calls, skips
