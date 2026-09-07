"""成员 B 独立演示：只演示 MQTT 外壳、主题、去重、排序和审计。

payload 是主线严格 wire codec 的占位输出，本程序不实现密码协议。
"""

from __future__ import annotations

import io
import secrets

from member_b_mqtt import (
    AuditLogger, AuditOutcome, AuditSeverity, Direction, MessageType,
    MqttEnvelope, SessionRouter, topic_for, validate_topic_matches,
)


def main() -> None:
    handshake_id = secrets.token_hex(16)
    router = SessionRouter(max_reorder=32, reorder_timeout=5.0)
    audit_output = io.StringIO()
    audit = AuditLogger(sink=audit_output)

    hello = MqttEnvelope.new(
        MessageType.CLIENT_HELLO,
        route_device_id="device-demo-01",
        route_gateway_id="gateway-demo",
        handshake_id=handshake_id,
        payload=b"placeholder-client-hello-wire-bytes",
    )
    topic = topic_for(hello)
    validate_topic_matches(topic, MqttEnvelope.decode(hello.encode()))

    calls: list[str] = []
    router.accept_handshake(hello, lambda _: calls.append("client_hello") or b"cached-response")
    router.accept_handshake(hello, lambda _: calls.append("must-not-run"))
    assert calls == ["client_hello"]

    delivered: list[int] = []
    record_1 = _record(1, b"placeholder-record-1")
    record_0 = _record(0, b"placeholder-record-0")
    router.accept_record(record_1, lambda item: delivered.append(item.record_sequence))
    router.accept_record(record_0, lambda item: delivered.append(item.record_sequence))
    assert delivered == [0, 1]

    audit.emit(
        event_type="handshake_succeeded",
        severity=AuditSeverity.INFO,
        component="mqtt_adapter",
        outcome=AuditOutcome.SUCCESS,
        reason_code="demo_transport_completed",
        route_device_id="device-demo-01",
        route_gateway_id="gateway-demo",
        handshake_id=handshake_id,
        details={"note": "placeholder payload only"},
    )

    print("成员 B 传输层演示成功")
    print(f"ClientHello 主题: {topic}")
    print(f"重复握手实际处理次数: {len(calls)}")
    print(f"乱序记录最终交付顺序: {delivered}")
    print(f"脱敏审计事件: {audit_output.getvalue().strip()}")


def _record(sequence: int, payload: bytes) -> MqttEnvelope:
    return MqttEnvelope.new(
        MessageType.APPLICATION_RECORD,
        route_device_id="device-demo-01",
        route_gateway_id="gateway-demo",
        session_id="demo-session-id-from-mainline",
        direction=Direction.DEVICE_TO_GATEWAY,
        record_sequence=sequence,
        payload=payload,
    )


if __name__ == "__main__":
    main()
