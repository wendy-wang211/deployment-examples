"""MQTT transport envelope and topic helpers for auth-v1.

This module deliberately treats the security payload as opaque bytes. The
security package owns encoding/decoding of handshake messages and SecureRecord;
this layer only provides authenticated-transport routing metadata.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


ENVELOPE_VERSION = "mqtt-envelope-v1"
PROTOCOL_VERSION = "auth-v1"
PAYLOAD_CONTENT_TYPE = "application/vnd.pqc-iot.auth-v1+binary"
TOPIC_ROOT = "pqc-iot/v1"
MAX_ROUTE_ID_LENGTH = 64
MAX_HANDSHAKE_ID_LENGTH = 32
MAX_SESSION_ID_LENGTH = 256
MAX_PAYLOAD_ENCODED_LENGTH = 1_048_576
MAX_RECORD_SEQUENCE = 2**24 - 1
_ROUTE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HANDSHAKE_RE = re.compile(r"^[0-9a-f]{32}$")
_B64U_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TransportError(ValueError):
    """Base error for malformed transport data."""


class EnvelopeValidationError(TransportError):
    """Raised when an envelope violates the transport contract."""


class TopicError(TransportError):
    """Raised for invalid or unexpected MQTT topics."""


class MessageType(str, Enum):
    CLIENT_HELLO = "client_hello"
    SERVER_FLIGHT = "server_flight"
    CLIENT_FLIGHT = "client_flight"
    APPLICATION_RECORD = "application_record"


class Direction(str, Enum):
    DEVICE_TO_GATEWAY = "device_to_gateway"
    GATEWAY_TO_DEVICE = "gateway_to_device"


@dataclass(frozen=True, slots=True)
class MqttEnvelope:
    """Strict, JSON-compatible MQTT wrapper around opaque security bytes."""

    message_type: MessageType
    message_id: str
    route_device_id: str
    route_gateway_id: str
    payload: bytes
    handshake_id: str | None = None
    session_id: str | None = None
    direction: Direction | None = None
    record_sequence: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message_type, MessageType):
            raise TypeError("message_type must be MessageType")
        _validate_uuid(self.message_id, "message_id")
        _validate_route_id(self.route_device_id, "route_device_id")
        _validate_route_id(self.route_gateway_id, "route_gateway_id")
        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be bytes")
        encoded_length = len(_encode_b64u(self.payload))
        if encoded_length > MAX_PAYLOAD_ENCODED_LENGTH:
            raise EnvelopeValidationError("payload exceeds transport size limit")
        if self.message_type is MessageType.APPLICATION_RECORD:
            if self.handshake_id is not None:
                raise EnvelopeValidationError("application record cannot contain handshake_id")
            _validate_session_id(self.session_id)
            if not isinstance(self.direction, Direction):
                raise EnvelopeValidationError("application record requires direction")
            _validate_sequence(self.record_sequence)
        else:
            if self.handshake_id is None:
                raise EnvelopeValidationError("handshake message requires handshake_id")
            _validate_handshake_id(self.handshake_id)
            if self.session_id is not None or self.direction is not None or self.record_sequence is not None:
                raise EnvelopeValidationError("handshake message contains record fields")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "envelope_version": ENVELOPE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "message_type": self.message_type.value,
            "message_id": self.message_id,
            "route_device_id": self.route_device_id,
            "route_gateway_id": self.route_gateway_id,
            "payload_content_type": PAYLOAD_CONTENT_TYPE,
            "payload_b64u": _encode_b64u(self.payload),
        }
        if self.message_type is MessageType.APPLICATION_RECORD:
            result.update(session_id=self.session_id, direction=self.direction.value,
                          record_sequence=self.record_sequence)
        else:
            result["handshake_id"] = self.handshake_id
        return result

    def encode(self) -> bytes:
        """Return canonical UTF-8 JSON with no duplicate or extra fields."""
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    @classmethod
    def decode(cls, raw: bytes | str) -> "MqttEnvelope":
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, bytes) or len(raw) > MAX_PAYLOAD_ENCODED_LENGTH + 4096:
            raise EnvelopeValidationError("envelope must be bounded UTF-8 bytes")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, EnvelopeValidationError) as exc:
            raise EnvelopeValidationError("invalid envelope JSON") from exc
        if not isinstance(value, dict):
            raise EnvelopeValidationError("envelope must be a JSON object")
        required = {"envelope_version", "protocol_version", "message_type", "message_id",
                    "route_device_id", "route_gateway_id", "payload_content_type", "payload_b64u"}
        if not required.issubset(value):
            raise EnvelopeValidationError("envelope is missing required fields")
        if value.get("envelope_version") != ENVELOPE_VERSION or value.get("protocol_version") != PROTOCOL_VERSION:
            raise EnvelopeValidationError("unsupported envelope or protocol version")
        try:
            message_type = MessageType(value["message_type"])
        except (KeyError, ValueError, TypeError) as exc:
            raise EnvelopeValidationError("unsupported message_type") from exc
        allowed = required | ({"handshake_id"} if message_type is not MessageType.APPLICATION_RECORD
                             else {"session_id", "direction", "record_sequence"})
        if set(value) != allowed:
            raise EnvelopeValidationError("unexpected or missing envelope fields")
        if value["payload_content_type"] != PAYLOAD_CONTENT_TYPE:
            raise EnvelopeValidationError("unsupported payload content type")
        payload = _decode_b64u(value["payload_b64u"])
        try:
            direction = Direction(value["direction"]) if message_type is MessageType.APPLICATION_RECORD else None
            return cls(message_type=message_type, message_id=value["message_id"],
                       route_device_id=value["route_device_id"], route_gateway_id=value["route_gateway_id"],
                       payload=payload, handshake_id=value.get("handshake_id"),
                       session_id=value.get("session_id"), direction=direction,
                       record_sequence=value.get("record_sequence"))
        except (KeyError, TypeError, ValueError) as exc:
            raise EnvelopeValidationError("invalid envelope field") from exc

    @classmethod
    def new(cls, message_type: MessageType, *, route_device_id: str,
            route_gateway_id: str, payload: bytes, handshake_id: str | None = None,
            session_id: str | None = None, direction: Direction | None = None,
            record_sequence: int | None = None) -> "MqttEnvelope":
        return cls(message_type, str(uuid.uuid4()), route_device_id, route_gateway_id,
                   payload, handshake_id, session_id, direction, record_sequence)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnvelopeValidationError("duplicate JSON field")
        result[key] = value
    return result


def _encode_b64u(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64u(value: Any) -> bytes:
    if not isinstance(value, str) or not value or len(value) > MAX_PAYLOAD_ENCODED_LENGTH or _B64U_RE.fullmatch(value) is None:
        raise EnvelopeValidationError("payload_b64u must be unpadded Base64URL")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise EnvelopeValidationError("invalid payload_b64u") from exc


def _validate_uuid(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise EnvelopeValidationError(f"{name} must be UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise EnvelopeValidationError(f"{name} must be UUID") from exc
    if str(parsed) != value.lower() or parsed.version != 4:
        raise EnvelopeValidationError(f"{name} must be lowercase UUID v4")


def _validate_route_id(value: Any, name: str) -> None:
    if not isinstance(value, str) or len(value) > MAX_ROUTE_ID_LENGTH or _ROUTE_RE.fullmatch(value) is None:
        raise EnvelopeValidationError(f"{name} has invalid route identifier")


def _validate_handshake_id(value: Any) -> None:
    if not isinstance(value, str) or _HANDSHAKE_RE.fullmatch(value) is None:
        raise EnvelopeValidationError("handshake_id must be 32 lowercase hex characters")


def _validate_session_id(value: Any) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_SESSION_ID_LENGTH:
        raise EnvelopeValidationError("session_id has invalid length")


def _validate_sequence(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RECORD_SEQUENCE:
        raise EnvelopeValidationError("record_sequence is outside the permitted range")


def topic_for(envelope: MqttEnvelope) -> str:
    """Build the topic for an already validated envelope."""
    if envelope.message_type is MessageType.CLIENT_HELLO:
        return f"{TOPIC_ROOT}/gateways/{envelope.route_gateway_id}/handshakes/{envelope.handshake_id}/client-hello"
    if envelope.message_type is MessageType.SERVER_FLIGHT:
        return f"{TOPIC_ROOT}/devices/{envelope.route_device_id}/handshakes/{envelope.handshake_id}/server-flight"
    if envelope.message_type is MessageType.CLIENT_FLIGHT:
        return f"{TOPIC_ROOT}/gateways/{envelope.route_gateway_id}/handshakes/{envelope.handshake_id}/client-flight"
    suffix = "up" if envelope.direction is Direction.DEVICE_TO_GATEWAY else "down"
    owner = "gateways" if suffix == "up" else "devices"
    route_id = envelope.route_gateway_id if suffix == "up" else envelope.route_device_id
    return f"{TOPIC_ROOT}/{owner}/{route_id}/sessions/{envelope.session_id}/records/{suffix}"


def validate_topic_matches(topic: str, envelope: MqttEnvelope) -> None:
    if not isinstance(topic, str) or topic_for(envelope) != topic:
        raise TopicError("topic does not match envelope routing metadata")
