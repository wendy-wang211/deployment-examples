"""Bounded, serialized duplicate handling for MQTT-delivered messages."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from member_b_mqtt.mqtt_transport import MqttEnvelope, MessageType


class RouterError(Exception):
    """Base router error."""


class MessageConflictError(RouterError):
    """Same logical message key was received with different bytes."""


class ReorderLimitError(RouterError):
    """Too many future application records are buffered."""


@dataclass(slots=True)
class _HandshakeState:
    phase: MessageType | None = None
    seen: dict[str, str] = field(default_factory=dict)
    responses: dict[str, bytes] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class _SessionState:
    next_sequence: int = 0
    accepted: dict[int, str] = field(default_factory=dict)
    buffered: dict[int, tuple[str, MqttEnvelope, float]] = field(default_factory=dict)


class SessionRouter:
    """Serialize one handshake/session and make QoS-1 retries harmless.

    ``handshake_handler`` and ``record_handler`` are called while the router's
    per-key lock is held. They must not call back into the same router key.
    The handler receives the envelope and returns optional response bytes.
    """

    _HANDSHAKE_ORDER = {
        None: MessageType.CLIENT_HELLO,
        MessageType.CLIENT_HELLO: MessageType.SERVER_FLIGHT,
        MessageType.SERVER_FLIGHT: MessageType.CLIENT_FLIGHT,
        MessageType.CLIENT_FLIGHT: None,
    }

    def __init__(self, *, max_reorder: int = 32, reorder_timeout: float = 5.0) -> None:
        if max_reorder < 1 or reorder_timeout <= 0:
            raise ValueError("invalid router limits")
        self._handshakes: dict[tuple[str, str], _HandshakeState] = {}
        self._sessions: dict[tuple[str, str], _SessionState] = {}
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._global_lock = threading.Lock()
        self._max_reorder = max_reorder
        self._reorder_timeout = reorder_timeout

    def _lock_for(self, key: tuple[str, str]) -> threading.RLock:
        with self._global_lock:
            return self._locks.setdefault(key, threading.RLock())

    def accept_handshake(self, envelope: MqttEnvelope,
                         handler: Callable[[MqttEnvelope], bytes | None]) -> bytes | None:
        if envelope.message_type is MessageType.APPLICATION_RECORD:
            raise RouterError("application record is not a handshake")
        key = (envelope.route_gateway_id, envelope.handshake_id or "")
        with self._lock_for(key):
            state = self._handshakes.setdefault(key, _HandshakeState())
            digest = _digest(envelope.payload)
            previous = state.seen.get(envelope.message_id)
            if previous is not None:
                if previous != digest:
                    raise MessageConflictError("message_id was reused with different payload")
                return state.responses.get(envelope.message_id)
            if self._HANDSHAKE_ORDER[state.phase] is not envelope.message_type:
                # Same phase with a different message id but identical bytes is a retry.
                if state.phase is envelope.message_type and digest in state.seen.values():
                    return next((response for mid, response in state.responses.items()
                                  if state.seen[mid] == digest), None)
                raise RouterError("handshake message is out of order")
            response = handler(envelope)
            state.seen[envelope.message_id] = digest
            state.responses[envelope.message_id] = response or b""
            state.phase = envelope.message_type
            state.updated_at = time.monotonic()
            return response

    def accept_record(self, envelope: MqttEnvelope,
                      handler: Callable[[MqttEnvelope], None]) -> list[MqttEnvelope]:
        if envelope.message_type is not MessageType.APPLICATION_RECORD:
            raise RouterError("handshake message is not an application record")
        key = (envelope.session_id or "", envelope.direction.value if envelope.direction else "")
        with self._lock_for(key):
            state = self._sessions.setdefault(key, _SessionState())
            self._expire_buffer(state)
            sequence = envelope.record_sequence
            assert sequence is not None
            digest = _digest(envelope.payload)
            if sequence < state.next_sequence:
                if state.accepted.get(sequence) == digest:
                    return []
                raise MessageConflictError("replayed sequence has different payload")
            if sequence in state.buffered:
                if state.buffered[sequence][0] != digest:
                    raise MessageConflictError("buffered sequence has different payload")
                return []
            if sequence > state.next_sequence:
                if len(state.buffered) >= self._max_reorder:
                    raise ReorderLimitError("reorder buffer limit reached")
                state.buffered[sequence] = (digest, envelope, time.monotonic())
                return []
            delivered: list[MqttEnvelope] = []
            current = envelope
            while True:
                handler(current)
                current_seq = current.record_sequence
                assert current_seq is not None
                state.accepted[current_seq] = _digest(current.payload)
                state.next_sequence += 1
                delivered.append(current)
                buffered = state.buffered.pop(state.next_sequence, None)
                if buffered is None:
                    break
                current = buffered[1]
            return delivered

    def _expire_buffer(self, state: _SessionState) -> None:
        now = time.monotonic()
        if any(now - item[2] > self._reorder_timeout for item in state.buffered.values()):
            state.buffered.clear()
            raise ReorderLimitError("reorder buffer timeout; session must be closed")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

