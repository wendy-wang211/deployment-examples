# 成员 B 独立成果：MQTT、审计与部署对接

> 本包只包含成员 B 的工作，不包含原仓库的 `crypto/`、`security/`、密钥、签名、KEM、Finished、会话密钥或记录加解密实现。

## 1. 这个成果做成了什么

本包把成员 B 的任务做成了可交付、可运行、可测试的独立组件：

1. MQTT 主题规范；
2. 四类消息 Envelope；
3. 握手关联 `handshake_id`；
4. 主题和 Envelope 路由一致性校验；
5. QoS 1 重复投递去重；
6. 同一握手串行调度；
7. 应用记录有界乱序缓冲和顺序释放；
8. 冲突消息检测；
9. JSON Lines 脱敏审计；
10. Mosquitto 本地配置和 ACL；
11. JSON Schema、非敏感示例、自动测试和演示程序。

详细主题表、字段表、时序、异常矩阵、审计字段及部署过程见：

```text
docs/MQTT_审计与部署设计.md
```

## 2. 目录

```text
member-b-standalone/
├── README.md
├── 交付清单.txt
├── demo.py
├── run_demo.ps1
├── requirements.txt
├── member_b_mqtt/
│   ├── __init__.py
│   ├── mqtt_transport.py
│   ├── session_router.py
│   └── audit.py
├── docs/
│   └── MQTT_审计与部署设计.md
├── examples/
│   ├── client-hello.json
│   ├── server-flight.json
│   ├── client-flight.json
│   ├── application-record.json
│   └── audit-handshake-succeeded.json
├── schemas/
│   ├── mqtt-envelope-v1.schema.json
│   └── audit-event-v1.schema.json
├── deployment/
│   ├── 启动说明.txt
│   └── mosquitto/
│       ├── mosquitto.local.conf
│       ├── acl.example
│       └── passwords.example.txt
└── tests/
    └── test_member_b.py
```

## 3. 主题表

| 方向 | 类型 | 主题 | QoS | Retain |
|---|---|---|---:|---|
| 设备→网关 | `client_hello` | `pqc-iot/v1/gateways/{gateway}/handshakes/{handshake_id}/client-hello` | 1 | false |
| 网关→设备 | `server_flight` | `pqc-iot/v1/devices/{device}/handshakes/{handshake_id}/server-flight` | 1 | false |
| 设备→网关 | `client_flight` | `pqc-iot/v1/gateways/{gateway}/handshakes/{handshake_id}/client-flight` | 1 | false |
| 设备→网关 | `application_record` | `pqc-iot/v1/gateways/{gateway}/sessions/{session_id}/records/up` | 1 | false |
| 网关→设备 | `application_record` | `pqc-iot/v1/devices/{device}/sessions/{session_id}/records/down` | 1 | false |

主题中的设备/网关 ID 只是路由声明，不是认证身份。

## 4. Envelope 字段

| 字段 | 说明 |
|---|---|
| `envelope_version` | 固定 `mqtt-envelope-v1` |
| `protocol_version` | 固定 `auth-v1` |
| `message_type` | 三类握手消息或应用记录 |
| `message_id` | UUID v4，用于传输去重 |
| `route_device_id` | 未认证设备路由声明 |
| `route_gateway_id` | 未认证网关路由声明 |
| `handshake_id` | 握手消息关联；128 位随机值的 32 位小写 hex |
| `session_id` | 仅应用记录使用，由主线提供 |
| `direction` | 应用记录方向 |
| `record_sequence` | 应用记录调度序号 |
| `payload_b64u` | 主线严格 wire codec 输出的不透明字节 |

握手完成前没有最终 `session_id`，所以三类握手消息使用独立 `handshake_id`。

## 5. 传输行为

- QoS：建议 1。
- Retain：握手和应用记录必须为 false。
- 重复握手：同 ID、同内容不重复推进处理器；返回缓存响应。
- 冲突握手：同 ID、不同内容拒绝。
- 应用记录：按 `(session_id, direction)` 串行。
- 未来序号：最多缓存 32 条、建议等待 5 秒。
- 已处理的相同记录：丢弃，不重复产生业务副作用。
- 同序号不同内容：冲突，关闭会话并审计。
- 不允许通过重置序号、忽略认证错误或恢复失败对象解决 MQTT 投递问题。

## 6. 审计

支持握手开始/成功/失败、会话关闭、记录接受/拒绝、消息拒绝、路由不匹配、重复、冲突、Broker 连接/断开等事件。

审计模块拒绝写入私钥、共享秘密、Finished key、流量密钥、应用密钥、业务明文、完整 payload、密文、认证标签、nonce、密码和 token；`details` 内嵌套的敏感字段也会被拒绝。

## 7. 运行演示

本演示不连接 Broker，也不实现密码协议；它演示 Envelope、主题、重复去重、乱序排序和审计。

```powershell
.\run_demo.ps1
```

或：

```powershell
python demo.py
```

预期看到：

```text
成员 B 传输层演示成功
重复握手实际处理次数: 1
乱序记录最终交付顺序: [0, 1]
```

## 8. 运行测试

无需第三方包：

```powershell
python -m unittest discover -s tests -v
```

## 9. 启动本地 Mosquitto

安装 Mosquitto 后，进入：

```text
deployment/mosquitto
```

创建账号：

```powershell
mosquitto_passwd -c .\passwd gateway-demo
mosquitto_passwd .\passwd device-demo-01
```

启动：

```powershell
mosquitto -c .\mosquitto.local.conf -v
```

配置只监听 `127.0.0.1:1883`、禁止匿名、禁用 retained，并提供最小主题 ACL 示例。生产环境必须另行启用 TLS、独立设备凭据、服务账户权限和日志轮转。

## 10. 交给主线的接口

主线把四类安全对象严格编码成 bytes 后传入：

```python
envelope = MqttEnvelope.new(
    MessageType.CLIENT_HELLO,
    route_device_id="device-01",
    route_gateway_id="gateway-01",
    handshake_id="0123456789abcdef0123456789abcdef",
    payload=encoded_security_message,
)
```

主线接收时：

1. `MqttEnvelope.decode(raw)`；
2. `validate_topic_matches(topic, envelope)`；
3. 交给 `SessionRouter` 去重/排序；
4. 由主线自己的严格 wire codec 解码 `envelope.payload`；
5. 调用主线握手或记录接口；
6. 用 `AuditLogger` 记录非敏感结果。

## 11. 明确不包含

本包没有并且不应该包含：

- 密码原语；
- 身份认证实现；
- 签名、transcript 和 Finished；
- KEM 和共享秘密；
- 密钥派生；
- session_id 计算；
- AES-GCM、nonce、AAD；
- 安全记录加解密；
- 主线安全对象 wire codec。

这些内容由安全主线维护。成员 B 只定义和实现外层 MQTT 传输封装、调度、审计及部署资料。
