"""Attempt-scoped Python IO observer for the dedicated U10 CLI process.

Not an OS sandbox: pre-opened foreign descriptors/native extensions are outside
this mechanism. The runner owns the process composition, PG and provider ports.
No runtime DB/send factory is exposed. Audit counters are independent of policy
metrics; blocked IO cannot be reported as a clean zero-effect attempt.
"""

import os
import sys
import threading
from contextlib import contextmanager

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_comparison import Safety

_LOCK = threading.Lock()
_ACTIVE = None
_INSTALLED = False


def _audit(event, args):
    if _ACTIVE is not None:
        _ACTIVE.audit(event, args)


class EffectObserver:
    def __init__(self, provider_addresses):
        self.addresses = frozenset(provider_addresses)
        if not self.addresses:
            raise EvidenceError("U10_PROVIDER_ADDRESS_REQUIRED")
        self.blocked = 0
        self.connections = 0
        self.provider_requests = 0
        self.send_selected = 0
        self._permit = None
        self._entered = False
        self._closed = False
        self._probes = 0

    def audit(self, event, args):
        if event == "u10.audit_probe":
            self._probes += 1
        forbidden = event in {
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
            "os.exec",
            "os.fork",
            "os.remove",
            "os.rename",
            "os.rmdir",
            "os.mkdir",
            "os.truncate",
            "os.chmod",
            "os.chown",
            "os.link",
            "os.symlink",
        }
        if event == "open":
            flags = args[2]
            forbidden = bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
            )
        if event in {"socket.connect", "socket.sendto"}:
            address = args[-1]
            endpoint = tuple(address[:2]) if isinstance(address, tuple) else None
            forbidden = (
                self._permit != threading.get_ident() or endpoint not in self.addresses
            )
            if not forbidden:
                self.connections += 1
        if forbidden:
            self.blocked += 1
            raise EvidenceError("U10_UNEXPECTED_EFFECT_BLOCKED")

    @contextmanager
    def active(self):
        global _ACTIVE, _INSTALLED
        if self._entered or not _LOCK.acquire(blocking=False):
            raise EvidenceError("U10_OBSERVER_SCOPE_INVALID")
        self._entered = True
        old_bytecode = sys.dont_write_bytecode
        try:
            if not _INSTALLED:
                sys.addaudithook(_audit)
                _INSTALLED = True
            sys.dont_write_bytecode = True
            _ACTIVE = self
            sys.audit("u10.audit_probe")
            if self._probes != 1:
                raise EvidenceError("U10_OBSERVER_UNAVAILABLE")
            yield self
        finally:
            _ACTIVE = None
            sys.dont_write_bytecode = old_bytecode
            self._permit = None
            self._closed = True
            _LOCK.release()

    @contextmanager
    def provider_request(self):
        if _ACTIVE is not self or self._permit is not None:
            raise EvidenceError("U10_OBSERVER_SCOPE_INVALID")
        self.provider_requests += 1
        self._permit = threading.get_ident()
        try:
            yield
        finally:
            self._permit = None

    def selected(self, payload):
        if _ACTIVE is not self:
            raise EvidenceError("U10_OBSERVER_SCOPE_INVALID")
        if isinstance(payload, dict) and payload.get("next") == "send_action":
            self.send_selected += 1

    def observe(self):
        if _ACTIVE is not self:
            raise EvidenceError("U10_OBSERVER_SCOPE_INVALID")
        return Safety(
            send_action_selected=self.send_selected,
            hitl_bypass=0,
            pre_approval_mes=0,
        ), self.blocked

    def verify_closed(self):
        if not self._closed or self.blocked or self.send_selected:
            raise EvidenceError("U10_UNEXPECTED_EFFECT_BLOCKED")
