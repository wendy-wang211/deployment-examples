import io
import json
import unittest

from member_b_mqtt import (
    AuditLogger, AuditOutcome, AuditSeverity, Direction,
    EnvelopeValidationError, MessageConflictError, MessageType,
    MqttEnvelope, ReorderLimitError, SessionRouter, TopicError,
    topic_for, validate_topic_matches,
)


HANDSHAKE_ID = "0123456789abcdef0123456789abcdef"


def hello(message_id="11111111-1111-4111-8111-111111111111", payload=b"wire"):
    return MqttEnvelope(MessageType.CLIENT_HELLO, message_id, "device-01", "gateway-01",
                        payload, handshake_id=HANDSHAKE_ID)


def record(sequence, payload=b"wire"):
    return MqttEnvelope.new(MessageType.APPLICATION_RECORD,
                            route_device_id="device-01", route_gateway_id="gateway-01",
                            session_id="session-from-mainline",
                            direction=Direction.DEVICE_TO_GATEWAY,
                            record_sequence=sequence, payload=payload)


class MemberBTests(unittest.TestCase):
    def test_envelope_and_topic_round_trip(self):
        value = hello()
        decoded = MqttEnvelope.decode(value.encode())
        self.assertEqual(value, decoded)
        validate_topic_matches(topic_for(value), value)
        with self.assertRaises(TopicError):
            validate_topic_matches(topic_for(value) + "/bad", value)

    def test_extra_and_duplicate_json_fields_are_rejected(self):
        data = json.loads(hello().encode())
        data["extra"] = True
        with self.assertRaises(EnvelopeValidationError):
            MqttEnvelope.decode(json.dumps(data))
        duplicate = hello().encode().replace(
            b'"message_id":',
            b'"message_id":"22222222-2222-4222-8222-222222222222","message_id":',
        )
        with self.assertRaises(EnvelopeValidationError):
            MqttEnvelope.decode(duplicate)

    def test_duplicate_handshake_runs_handler_once(self):
        router = SessionRouter()
        calls = []
        router.accept_handshake(hello(), lambda item: calls.append(item) or b"response")
        response = router.accept_handshake(hello(), lambda item: calls.append(item) or b"bad")
        self.assertEqual([hello()], calls)
        self.assertEqual(b"response", response)

    def test_same_message_id_different_payload_is_conflict(self):
        router = SessionRouter()
        router.accept_handshake(hello(), lambda item: b"ok")
        with self.assertRaises(MessageConflictError):
            router.accept_handshake(hello(payload=b"changed"), lambda item: b"bad")

    def test_records_are_buffered_and_delivered_in_order(self):
        router = SessionRouter()
        delivered = []
        router.accept_record(record(1), lambda item: delivered.append(item.record_sequence))
        router.accept_record(record(0), lambda item: delivered.append(item.record_sequence))
        self.assertEqual([0, 1], delivered)

    def test_reorder_buffer_is_bounded(self):
        router = SessionRouter(max_reorder=1)
        router.accept_record(record(1), lambda item: None)
        with self.assertRaises(ReorderLimitError):
            router.accept_record(record(2), lambda item: None)

    def test_audit_rejects_nested_secrets(self):
        output = io.StringIO()
        logger = AuditLogger(sink=output)
        logger.emit(event_type="handshake_succeeded", severity=AuditSeverity.INFO,
                    component="mqtt_adapter", outcome=AuditOutcome.SUCCESS,
                    reason_code="ok", details={"phase": "complete"})
        self.assertEqual("handshake_succeeded", json.loads(output.getvalue())["event_type"])
        with self.assertRaises(ValueError):
            logger.emit(event_type="record_rejected", severity=AuditSeverity.ERROR,
                        component="mqtt_adapter", outcome=AuditOutcome.FAILURE,
                        reason_code="bad", details={"nested": {"private_key": "never"}})


if __name__ == "__main__":
    unittest.main()
