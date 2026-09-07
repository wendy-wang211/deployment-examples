"""Structured, secret-safe audit events for MQTT/security integration."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO


class AuditError(ValueError):
    """Raised for invalid audit values."""


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


EVENT_TYPES = frozenset({
    "handshake_started", "handshake_succeeded", "handshake_failed", "session_closed",
    "record_accepted", "record_rejected", "transport_message_rejected",
    "transport_route_mismatch", "transport_duplicate", "transport_message_conflict",
    "broker_connected", "broker_disconnected", "configuration_loaded",
})


class AuditLogger:
    """Write one-line JSON events without accepting secret-bearing fields."""

    def __init__(self, sink: TextIO | None = None, *, path: str | Path | None = None) -> None:
        if (sink is None) == (path is None):
            raise ValueError("provide exactly one audit sink or path")
        self._sink = sink
        self._owned = None
        if path is not None:
            self._owned = open(path, "a", encoding="utf-8", newline="\n")
            self._sink = self._owned

    def close(self) -> None:
        if self._owned is not None:
            self._owned.close()
            self._owned = None

    def __enter__(self) -> "AuditLogger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def emit(self, *, event_type: str, severity: AuditSeverity,
             component: str, outcome: AuditOutcome, reason_code: str,
             **fields: Any) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise AuditError("unsupported event_type")
        if not isinstance(severity, AuditSeverity) or not isinstance(outcome, AuditOutcome):
            raise AuditError("severity and outcome must use audit enums")
        if component not in {"device", "gateway", "mqtt_adapter", "broker"}:
            raise AuditError("unsupported component")
        if not isinstance(reason_code, str) or not reason_code or len(reason_code) > 64:
            raise AuditError("invalid reason_code")
        forbidden = {"private_key", "shared_secret", "kem_shared_secret", "finished_key",
                     "traffic_key", "application_key", "plaintext", "ciphertext",
                     "authentication_tag", "nonce", "payload", "password", "token"}
        if forbidden.intersection(fields) or _contains_forbidden(fields.get("details"), forbidden):
            raise AuditError("secret or sensitive payload fields are forbidden")
        event: dict[str, Any] = {
            "audit_version": "audit-v1",
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "event_type": event_type,
            "severity": severity.value,
            "component": component,
            "outcome": outcome.value,
            "reason_code": reason_code,
        }
        allowed = {"route_device_id", "route_gateway_id", "authenticated_peer_identity_id",
                   "local_identity_id", "handshake_id", "session_id", "direction",
                   "record_sequence", "message_id", "remote_endpoint", "details"}
        unknown = set(fields) - allowed
        if unknown:
            raise AuditError(f"unsupported audit fields: {sorted(unknown)}")
        event.update(fields)
        json.dump(event, self._sink, ensure_ascii=True, separators=(",", ":"))
        self._sink.write("\n")
        self._sink.flush()
        return event


def _contains_forbidden(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in forbidden or _contains_forbidden(item, forbidden)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item, forbidden) for item in value)
    return False
