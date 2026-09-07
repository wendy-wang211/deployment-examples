# 成员 B 完整成果：MQTT、审计与部署对接

> 项目：面向物联网场景的后量子混合安全通信网关  
> 成员 B 职责：MQTT 传输封装、传输行为协调、结构化审计与本地部署  
> 文档状态：可评审、可演示、可测试的独立交付成果  
> 重要边界：本包**不包含密码协议实现，也不重新定义密码协议**

---

## 1. 项目背景

项目主线已经能够在同一 Python 进程内完成设备与网关之间的认证握手和受保护应用记录收发，但这些对象当前只是 Python 进程内对象，不能直接作为 MQTT 消息发送。

成员 B 的任务是设计并实现外层传输接口，使后续主线能够把已经严格编码的握手消息和应用记录放入 MQTT 网络中传输，同时解决以下问题：

1. 三类握手消息分别发布到什么主题；
2. 握手完成前还没有最终 `session_id`，如何关联同一次握手；
3. MQTT Envelope 应包含哪些字段；
4. MQTT QoS 1 可能重复投递，如何避免重复推进安全状态机；
5. MQTT 消息可能乱序，如何与严格有序的安全记录层协调；
6. 同一个握手对象如何保证串行调用；
7. 握手、会话和记录异常应记录哪些审计事件；
8. 如何避免审计日志泄露密钥、共享秘密或业务明文；
9. 如何在本地启动 Mosquitto、网关进程和设备进程进行演示。

本交付包针对上述问题提供：

- 完整设计文档；
- 可运行的 MQTT Envelope 代码；
- MQTT 主题生成和校验代码；
- 握手及应用记录去重、排序、串行调度代码；
- 结构化脱敏审计代码；
- JSON Schema；
- 非敏感消息示例；
- Mosquitto 本地配置和 ACL；
- 独立演示程序；
- 自动化测试及验收报告。

---

## 2. 成员 B 的职责边界

### 2.1 本包负责

本包只负责以下外层功能：

```text
主线编码后的公开消息字节
          ↓
MQTT Envelope 封装
          ↓
MQTT 主题路由
          ↓
重复投递去重 / 乱序缓冲 / 串行调度
          ↓
将原始字节交回主线严格解码和处理
          ↓
输出不含秘密的结构化审计事件
```

具体包括：

- MQTT 主题命名；
- MQTT Envelope 字段；
- `handshake_id` 关联规则；
- `message_id` 传输去重规则；
- QoS 和 Retain 建议；
- 主题与 Envelope 一致性校验；
- 握手阶段消息顺序管理；
- 应用记录传输层排序；
- 重复及冲突检测；
- 审计事件和脱敏规则；
- Broker 本地部署配置。

### 2.2 本包不负责

本包没有、也不应该实现以下内容：

- X25519、ML-KEM、ML-DSA、AES-GCM、HKDF 等密码算法；
- 设备和网关的密码身份认证；
- 签名输入、签名上下文和 transcript；
- ServerFinished 或 ClientFinished；
- 混合共享秘密及密钥派生；
- `session_id` 的密码学计算；
- nonce、AAD、流量密钥；
- 安全记录的加密和解密；
- 安全主线对象的正式 wire codec；
- 业务设备与认证身份的可信映射验证；
- 密钥持久化、轮换和吊销。

本包中的 `payload` 始终被视为**不透明字节**。这些字节必须由安全主线以后提供的严格、唯一、版本化 wire codec 产生。

---

## 3. 交付目录

```text
member-b-standalone/
├── README.md                         # 本文档：完整使用和设计说明
├── 交付清单.txt                      # 文件交付清单
├── 测试与验收报告.md                 # 测试结果和边界检查
├── demo.py                           # 不含密码实现的传输层演示
├── run_demo.ps1                      # Windows 一键演示脚本
├── requirements.txt                 # 核心代码无第三方依赖
│
├── member_b_mqtt/
│   ├── __init__.py                   # 公共 API 导出
│   ├── mqtt_transport.py             # Envelope、主题及严格校验
│   ├── session_router.py             # 串行、去重和乱序处理
│   └── audit.py                      # JSON Lines 脱敏审计
│
├── docs/
│   └── MQTT_审计与部署设计.md        # 详细设计报告
│
├── examples/
│   ├── client-hello.json             # ClientHello 非敏感示例
│   ├── server-flight.json            # ServerFlight 非敏感示例
│   ├── client-flight.json            # ClientFlight 非敏感示例
│   ├── application-record.json       # 应用记录非敏感示例
│   └── audit-handshake-succeeded.json# 审计事件示例
│
├── schemas/
│   ├── mqtt-envelope-v1.schema.json  # Envelope JSON Schema
│   └── audit-event-v1.schema.json    # 审计事件 JSON Schema
│
├── deployment/
│   ├── 启动说明.txt
│   └── mosquitto/
│       ├── mosquitto.local.conf      # 仅限本机演示的 Broker 配置
│       ├── acl.example               # 最小主题权限示例
│       └── passwords.example.txt     # 密码文件创建说明
│
└── tests/
    └── test_member_b.py              # 标准库 unittest 测试
```

---

## 4. 总体架构

### 4.1 组件关系

```text
┌──────────────────┐
│    设备主线进程   │
│  产生握手/记录字节 │
└────────┬─────────┘
         │ opaque bytes
         ▼
┌──────────────────────────────┐
│ 成员 B：设备侧 MQTT 适配层    │
│ - MqttEnvelope               │
│ - topic_for                  │
│ - SessionRouter              │
│ - AuditLogger                │
└────────┬─────────────────────┘
         │ QoS 1 / retain=false
         ▼
┌──────────────────┐
│ Mosquitto Broker │
└────────┬─────────┘
         │ QoS 1（可能重复）
         ▼
┌──────────────────────────────┐
│ 成员 B：网关侧 MQTT 适配层    │
│ - decode envelope            │
│ - validate topic             │
│ - deduplicate / reorder      │
│ - audit                      │
└────────┬─────────────────────┘
         │ opaque bytes
         ▼
┌──────────────────┐
│    网关主线进程   │
│ 严格解码并处理消息 │
└──────────────────┘
```

### 4.2 信任边界

MQTT Broker、主题和 Envelope 都属于传输层。即使 Broker 已使用用户名、密码、TLS 或 ACL，也不能因此认定 MQTT 中声明的设备就是经过密码认证的设备。

以下字段只能用于路由：

```text
route_device_id
route_gateway_id
handshake_id
message_id
MQTT client_id
MQTT username
```

其中任何字段都不能替代主线的身份验证结果。只有主线成功完成对端认证后，才能生成或记录：

```text
authenticated_peer_identity_id
```

---

## 5. MQTT 主题规范

### 5.1 主题根路径

```text
pqc-iot/v1
```

`v1` 是 MQTT 外层主题版本，不表示密码协议以后必须永远保持不变。若主题结构发生不兼容修改，应创建新的主题版本，而不是静默改变已有主题含义。

### 5.2 完整主题表

| 方向 | 消息类型 | MQTT 主题 | 订阅者 | QoS | Retain |
|---|---|---|---|---:|---|
| 设备 → 网关 | `client_hello` | `pqc-iot/v1/gateways/{gateway_id}/handshakes/{handshake_id}/client-hello` | 目标网关 | 1 | false |
| 网关 → 设备 | `server_flight` | `pqc-iot/v1/devices/{device_id}/handshakes/{handshake_id}/server-flight` | 发起设备 | 1 | false |
| 设备 → 网关 | `client_flight` | `pqc-iot/v1/gateways/{gateway_id}/handshakes/{handshake_id}/client-flight` | 目标网关 | 1 | false |
| 设备 → 网关 | `application_record` | `pqc-iot/v1/gateways/{gateway_id}/sessions/{session_id}/records/up` | 目标网关 | 1 | false |
| 网关 → 设备 | `application_record` | `pqc-iot/v1/devices/{device_id}/sessions/{session_id}/records/down` | 目标设备 | 1 | false |

### 5.3 路由 ID 规则

`route_device_id` 和 `route_gateway_id` 建议限制为：

```regex
[A-Za-z0-9_.-]{1,64}
```

禁止：

- `/`：会改变主题层级；
- `+`、`#`：属于 MQTT 通配符；
- 空格和控制字符；
- 空字符串；
- 超过 64 字符；
- 隐式 trim 或大小写转换。

代码会严格验证这些规则。

### 5.4 主题与 Envelope 双重校验

收到 MQTT 消息后必须同时解析主题和 Envelope，并调用：

```python
validate_topic_matches(topic, envelope)
```

例如，主题声明目标网关为 `gateway-01`，但 Envelope 中写的是 `gateway-02`，消息必须在进入主线前被拒绝并记录：

```text
transport_route_mismatch
```

不能简单相信主题，也不能简单相信 Envelope。

---

## 6. 消息 Envelope 规范

### 6.1 消息类型

本包严格区分四类传输消息：

| `message_type` | 用途 | 关联 ID |
|---|---|---|
| `client_hello` | 设备发起握手 | `handshake_id` |
| `server_flight` | 网关返回握手消息 | `handshake_id` |
| `client_flight` | 设备发送最终握手消息 | `handshake_id` |
| `application_record` | 传输受保护业务记录 | `session_id` |

### 6.2 字段表

| 字段 | 类型 | 必填条件 | 约束和用途 |
|---|---|---|---|
| `envelope_version` | string | 始终 | 固定 `mqtt-envelope-v1` |
| `protocol_version` | string | 始终 | 固定 `auth-v1`，仅用于传输分流 |
| `message_type` | enum | 始终 | 四类消息之一 |
| `message_id` | UUID v4 | 始终 | 传输去重，必须为规范小写形式 |
| `route_device_id` | string | 始终 | 未认证的设备路由声明 |
| `route_gateway_id` | string | 始终 | 未认证的网关路由声明 |
| `handshake_id` | string | 握手消息 | 32 位小写 hex |
| `session_id` | string | 应用记录 | 由主线提供，不由本包计算 |
| `direction` | enum | 应用记录 | `device_to_gateway` 或 `gateway_to_device` |
| `record_sequence` | integer | 应用记录 | `0..2^24-1`，用于进入主线前调度 |
| `payload_content_type` | string | 始终 | 固定 `application/vnd.pqc-iot.auth-v1+binary` |
| `payload_b64u` | string | 始终 | 无 `=` 填充的 Base64URL |

### 6.3 握手消息字段组合

握手消息必须包含：

```text
handshake_id
```

不得包含：

```text
session_id
direction
record_sequence
```

### 6.4 应用记录字段组合

应用记录必须包含：

```text
session_id
direction
record_sequence
```

不得包含：

```text
handshake_id
```

### 6.5 严格解析规则

`MqttEnvelope.decode()` 会拒绝：

- 非 UTF-8 数据；
- 非 JSON 对象；
- 重复 JSON 字段；
- 未知字段；
- 缺少字段；
- 未知 Envelope 版本；
- 未知协议版本；
- 未知消息类型；
- 非规范 UUID v4；
- 非法路由 ID；
- 非法 `handshake_id`；
- 含 `=` 填充或字符非法的 Base64URL；
- 字段组合错误；
- 超出序号范围；
- 超出传输大小限制。

严格拒绝未知字段是为了避免不同版本实现对同一消息产生不同理解。

---

## 7. handshake_id 设计

### 7.1 为什么需要 handshake_id

三类握手消息发生在最终会话建立之前。此时主线还不能提供最终 `session_id`，因此不能用 `session_id` 路由以下消息：

```text
ClientHello → ServerFlight → ClientFlight
```

本方案使用独立的 `handshake_id` 将三条消息关联到同一传输状态。

### 7.2 生成规则

建议由发起设备执行：

```python
import secrets
handshake_id = secrets.token_hex(16)
```

结果是 128 位随机值的 32 位小写十六进制文本。

### 7.3 安全边界

`handshake_id`：

- 只用于路由、缓存和审计关联；
- 不构成身份认证；
- 不应由设备 ID 或时间戳直接推导；
- 不应重复用于另一轮握手；
- 不应代替最终 `session_id`；
- 不应由成员 B 加入主线密码 transcript，除非主线协议负责人正式设计并审核。

---

## 8. 消息时序

```text
设备适配层                      MQTT Broker                    网关适配层
    │                               │                              │
    │ 生成随机 handshake_id         │                              │
    │ 封装 ClientHello              │                              │
    ├── client-hello, QoS 1 ───────►│                              │
    │                               ├── 可能重复投递 ─────────────►│
    │                               │                    校验主题/Envelope
    │                               │                    按 handshake_id 串行
    │                               │                    调用主线处理一次
    │                               │                    缓存 ServerFlight 响应
    │                               │                              │
    │                               │◄── server-flight, QoS 1 ─────┤
    │◄── 可能重复投递 ──────────────┤                              │
    │ 校验并交给主线                │                              │
    │ 封装 ClientFlight             │                              │
    ├── client-flight, QoS 1 ──────►│                              │
    │                               ├─────────────────────────────►│
    │                               │                    主线确认最终握手消息
    │                               │                    服务端才能开放记录
    │                               │                              │
    │ 主线产生应用记录字节          │                              │
    ├── records/up, QoS 1 ─────────►│─────────────────────────────►│
    │                               │                    去重/有界排序
    │                               │                    再交给主线验证
```

注意：设备本地生成 `ClientFlight` 不代表网关已经收到并验证。网关只有在主线确认最终握手消息成功后，才可以接受应用记录。

---

## 9. QoS、Retain 与 MQTT 会话建议

### 9.1 QoS

握手和应用记录推荐：

```text
QoS = 1
```

原因：

- QoS 0 可能丢失关键握手消息；
- QoS 1 提供至少一次投递，适合当前实验原型；
- QoS 1 会重复投递，所以必须使用本包的去重逻辑；
- QoS 2 成本更高，而且不能替代应用层状态管理和主线的重放防护。

### 9.2 Retain

所有握手消息和应用记录必须：

```text
retain = false
```

否则，新连接可能收到 Broker 保存的旧握手消息或旧会话记录。

本地 Mosquitto 示例进一步设置：

```text
retain_available false
```

### 9.3 DUP 标志

不能只依靠 MQTT `DUP` 标志判断重复消息，因为它不是稳定的端到端消息身份。实际判断使用：

- `message_id`；
- payload SHA-256 摘要；
- `handshake_id`；
- `session_id`；
- `direction`；
- `record_sequence`。

### 9.4 MQTT Client ID

设备和网关应使用稳定且唯一的 MQTT Client ID，例如：

```text
device-demo-01
gateway-demo
```

但 MQTT Client ID 仍然只是 Broker 层身份，不能代替主线密码认证身份。

---

## 10. 握手串行调度和重复投递

实现位置：

```text
member_b_mqtt/session_router.py
```

### 10.1 状态键

每次握手使用：

```text
(route_gateway_id, handshake_id)
```

作为传输状态键，并为每个键建立独立锁，确保同一握手对象的操作串行执行。

### 10.2 正常处理

首次收到合法阶段消息时：

1. 计算 payload SHA-256 摘要；
2. 检查当前预期握手阶段；
3. 只调用处理器一次；
4. 缓存 `message_id`、摘要和响应；
5. 推进传输阶段。

### 10.3 相同 message_id、相同 payload

视为 MQTT 重复投递：

- 不再次调用处理器；
- 不重复推进握手对象；
- 返回缓存响应或静默确认。

### 10.4 不同 message_id、相同阶段、相同 payload

视为发送方重试：

- 不再次调用处理器；
- 使用之前缓存的结果。

### 10.5 相同 message_id、不同 payload

视为传输冲突：

- 抛出 `MessageConflictError`；
- 不选择其中一条继续；
- 记录 `transport_message_conflict`；
- 上层应终止或隔离该握手状态。

### 10.6 阶段错误

例如未收到 `client_hello` 就先收到 `client_flight`：

- 不越过前置状态；
- 不调用主线安全对象；
- 拒绝消息；
- 记录 `transport_message_rejected` 或 `handshake_failed`。

---

## 11. 应用记录的重复和乱序协调

### 11.1 调度键

每个方向独立使用：

```text
(session_id, direction)
```

这样上行和下行不会共享同一个接收序号状态。

### 11.2 期望序号

传输层维护：

```text
next_expected_sequence
```

它只用于在调用主线严格记录处理接口之前排序，不能直接修改主线内部序号。

### 11.3 收到期望记录

如果：

```text
record_sequence == next_expected_sequence
```

则：

1. 交给上层处理器；
2. 上层再交给主线严格验证；
3. 成功后记录已接受摘要；
4. 增加传输侧期望值；
5. 连续释放缓存中的后续记录。

### 11.4 收到未来记录

如果：

```text
record_sequence > next_expected_sequence
```

则暂存，不立即交给主线。

默认建议：

```text
最大重排记录数：32
最长等待时间：5 秒
```

这些数值应在实际性能测试后调整。

### 11.5 收到历史记录

如果序号小于期望值，并且 payload 摘要与已成功记录一致：

- 视为 QoS 1 重复投递；
- 不再次调用主线；
- 不重复写数据库或触发业务动作。

### 11.6 相同序号但不同内容

如果同一 `(session_id, direction, record_sequence)` 对应不同内容：

- 抛出 `MessageConflictError`；
- 记录高严重度冲突事件；
- 上层应关闭当前会话；
- 创建新 `handshake_id` 并重新握手。

### 11.7 缓冲超限或超时

抛出：

```text
ReorderLimitError
```

正确恢复过程：

1. 停止向旧会话继续交付记录；
2. 记录 `session_closed`；
3. 关闭旧主线会话对象；
4. 丢弃旧传输缓存；
5. 生成新 `handshake_id`；
6. 执行完整新握手。

禁止：

- 重置旧会话的安全序号；
- 修改主线私有字段；
- 忽略主线认证错误；
- 使用旧密钥从序号 0 重新开始；
- 恢复已经失败或关闭的握手对象。

---

## 12. 异常处理矩阵

| 场景 | 是否调用主线 | 传输层处理 | 建议审计事件 |
|---|---|---|---|
| JSON 不是合法 UTF-8 | 否 | 拒绝 | `transport_message_rejected` |
| JSON 重复字段 | 否 | 拒绝 | `transport_message_rejected` |
| Envelope 未知字段 | 否 | 拒绝 | `transport_message_rejected` |
| 未知版本或类型 | 否 | 拒绝 | `transport_message_rejected` |
| 主题和 Envelope 不一致 | 否 | 拒绝 | `transport_route_mismatch` |
| 相同消息重复投递 | 否，不重复调用 | 返回缓存或丢弃 | `transport_duplicate`（可采样） |
| 同 ID 但内容不同 | 否 | 冲突拒绝 | `transport_message_conflict` |
| 握手阶段错误 | 否 | 拒绝，不越级 | `handshake_failed` |
| 主线握手验证失败 | 已调用 | 终止原对象 | `handshake_failed` |
| 未完成握手收到应用记录 | 否 | 拒绝 | `record_rejected` |
| 未来记录序号 | 否，暂不调用 | 有界缓存 | `record_reorder_buffered`（指标或采样） |
| 重排缓存超时或超限 | 否 | 关闭旧会话 | `session_closed` |
| 历史相同记录 | 否，不重复调用 | 去重丢弃 | `transport_duplicate`（可采样） |
| 同序号不同内容 | 否 | 冲突并关闭 | `transport_message_conflict` |
| 主线记录认证失败 | 已调用 | 关闭旧会话 | `record_rejected`、`session_closed` |
| Broker 断线 | 否 | 重连，状态超时则新握手 | `broker_disconnected` |

---

## 13. 结构化审计

实现位置：

```text
member_b_mqtt/audit.py
```

审计采用 JSON Lines：每行一个独立 JSON 对象，便于日志收集、查询和轮转。

### 13.1 事件类型

```text
handshake_started
handshake_succeeded
handshake_failed
session_closed
record_accepted
record_rejected
transport_message_rejected
transport_route_mismatch
transport_duplicate
transport_message_conflict
broker_connected
broker_disconnected
configuration_loaded
```

### 13.2 字段表

| 字段 | 说明 |
|---|---|
| `audit_version` | 固定 `audit-v1` |
| `event_id` | UUID v4 |
| `timestamp` | UTC RFC 3339，毫秒精度 |
| `event_type` | 稳定事件名称 |
| `severity` | `info` / `warning` / `error` / `critical` |
| `component` | `device` / `gateway` / `mqtt_adapter` / `broker` |
| `outcome` | `success` / `failure` / `unknown` |
| `reason_code` | 稳定、低敏原因码 |
| `route_device_id` | 可选，未认证路由声明 |
| `route_gateway_id` | 可选，未认证路由声明 |
| `authenticated_peer_identity_id` | 可选，只能来自主线认证结果 |
| `local_identity_id` | 可选，本地配置身份 |
| `handshake_id` | 可选，握手关联 |
| `session_id` | 可选，会话关联 |
| `direction` | 可选，应用记录方向 |
| `record_sequence` | 可选，应用记录序号 |
| `message_id` | 可选，传输消息标识 |
| `remote_endpoint` | 可选，遵循隐私策略 |
| `details` | 受控非敏感信息对象 |

### 13.3 禁止记录的数据

代码拒绝顶层或嵌套详情中的以下字段：

```text
private_key
shared_secret
kem_shared_secret
finished_key
traffic_key
application_key
plaintext
ciphertext
authentication_tag
nonce
payload
password
token
```

还应避免记录：

- 完整签名输入；
- 完整 transcript；
- 完整敏感业务数据；
- MQTT 密码和访问令牌；
- 未经批准的完整网络 payload；
- 包含秘密的异常对象和堆栈局部变量。

### 13.4 审计调用示例

```python
from member_b_mqtt import AuditLogger, AuditOutcome, AuditSeverity

with AuditLogger(path="audit.jsonl") as audit:
    audit.emit(
        event_type="handshake_succeeded",
        severity=AuditSeverity.INFO,
        component="mqtt_adapter",
        outcome=AuditOutcome.SUCCESS,
        reason_code="mainline_confirmed",
        route_device_id="device-01",
        route_gateway_id="gateway-01",
        handshake_id="0123456789abcdef0123456789abcdef",
        authenticated_peer_identity_id="identity-from-mainline",
        details={"transport": "mqtt", "qos": 1},
    )
```

---

## 14. 代码 API 使用方法

### 14.1 创建 ClientHello Envelope

```python
from member_b_mqtt import MessageType, MqttEnvelope, topic_for

hello = MqttEnvelope.new(
    MessageType.CLIENT_HELLO,
    route_device_id="device-01",
    route_gateway_id="gateway-01",
    handshake_id="0123456789abcdef0123456789abcdef",
    payload=encoded_client_hello_from_mainline,
)

mqtt_topic = topic_for(hello)
mqtt_payload = hello.encode()

mqtt_client.publish(
    mqtt_topic,
    mqtt_payload,
    qos=1,
    retain=False,
)
```

### 14.2 接收并校验 Envelope

```python
from member_b_mqtt import MqttEnvelope, validate_topic_matches

envelope = MqttEnvelope.decode(received_payload)
validate_topic_matches(received_topic, envelope)
```

只有上述步骤成功后，才能进入路由和主线解码阶段。

### 14.3 握手消息去重和串行处理

```python
from member_b_mqtt import SessionRouter

router = SessionRouter()

def handle_handshake(envelope):
    # 由主线严格解码 envelope.payload
    # 然后调用主线握手接口
    # 最后返回编码后的响应字节
    return encoded_response

response = router.accept_handshake(envelope, handle_handshake)
```

处理器对同一个有效输入只会执行一次。重复投递返回缓存结果。

### 14.4 创建应用记录 Envelope

```python
from member_b_mqtt import Direction, MessageType, MqttEnvelope

record_envelope = MqttEnvelope.new(
    MessageType.APPLICATION_RECORD,
    route_device_id="device-01",
    route_gateway_id="gateway-01",
    session_id=session_id_text_from_mainline,
    direction=Direction.DEVICE_TO_GATEWAY,
    record_sequence=record_sequence_from_mainline,
    payload=encoded_secure_record_from_mainline,
)
```

### 14.5 应用记录排序

```python
def handle_record(envelope):
    # 由主线严格解码和验证 envelope.payload
    # 成功后再提交业务数据
    mainline_process_record(envelope.payload)

released = router.accept_record(record_envelope, handle_record)
```

如果记录是未来序号，`released` 为空且不会调用处理器；缺失记录到达后会按顺序释放连续记录。

---

## 15. 非敏感示例和 JSON Schema

### 15.1 示例

```text
examples/client-hello.json
examples/server-flight.json
examples/client-flight.json
examples/application-record.json
examples/audit-handshake-succeeded.json
```

示例中的 `payload_b64u` 只是占位字节，不是真实签名、密钥、共享秘密或业务数据。

### 15.2 Schema

```text
schemas/mqtt-envelope-v1.schema.json
schemas/audit-event-v1.schema.json
```

Schema 可用于文档审查和外部工具的初步验证。实际 Python 程序仍应使用 `MqttEnvelope.decode()`，因为代码额外执行重复 JSON 字段拒绝、严格字段集合和规范编码检查。

---

## 16. 运行独立演示

### 16.1 环境要求

- Windows、Linux 或 macOS；
- Python 3.10 或更高版本；
- 核心演示不需要第三方 Python 包。

### 16.2 Windows 一键运行

```powershell
.\run_demo.ps1
```

### 16.3 通用运行方式

```powershell
python demo.py
```

### 16.4 演示内容

演示程序执行以下步骤：

1. 使用安全随机数生成 `handshake_id`；
2. 创建 ClientHello Envelope；
3. 生成对应 MQTT 主题；
4. 编码并重新解码 Envelope；
5. 校验主题与 Envelope 一致；
6. 将完全相同的握手消息提交两次；
7. 验证实际处理器只执行一次；
8. 先提交序号 1 的应用记录；
9. 再提交序号 0 的应用记录；
10. 验证最终交付顺序为 `[0, 1]`；
11. 输出一条不含秘密的审计事件。

### 16.5 预期输出

```text
成员 B 传输层演示成功
ClientHello 主题: pqc-iot/v1/gateways/gateway-demo/handshakes/.../client-hello
重复握手实际处理次数: 1
乱序记录最终交付顺序: [0, 1]
脱敏审计事件: {...}
```

演示中的 payload 是占位数据，不能用于证明密码协议已经通过网络端到端运行。

---

## 17. 自动化测试

### 17.1 运行

```powershell
python -m unittest discover -s tests -v
```

### 17.2 当前结果

```text
Ran 7 tests
OK
```

### 17.3 覆盖场景

1. Envelope 编码和解码；
2. 主题生成及主题/Envelope 一致性；
3. 未知 JSON 字段拒绝；
4. 重复 JSON 字段拒绝；
5. 重复握手只调用处理器一次；
6. 相同消息 ID、不同 payload 冲突拒绝；
7. 应用记录有界乱序缓存；
8. 乱序记录按 `[0, 1]` 顺序交付；
9. 审计日志输出；
10. 嵌套敏感字段拒绝。

测试和边界检查的详细结果见：

```text
测试与验收报告.md
```

---

## 18. Mosquitto 本地部署

### 18.1 配置文件

```text
deployment/mosquitto/mosquitto.local.conf
deployment/mosquitto/acl.example
deployment/mosquitto/passwords.example.txt
```

本地演示配置：

- 仅监听 `127.0.0.1:1883`；
- 禁止匿名访问；
- 使用密码文件；
- 使用 ACL；
- 禁止 retained message；
- 关闭持久化；
- 将日志输出到控制台；
- 设置消息大小上限。

### 18.2 创建账户

打开 PowerShell，进入：

```text
deployment/mosquitto
```

创建网关账号：

```powershell
mosquitto_passwd -c .\passwd gateway-demo
```

创建设备账号：

```powershell
mosquitto_passwd .\passwd device-demo-01
```

交互输入密码。不要把明文密码写入命令参数、配置文件、代码或 Git 仓库。

### 18.3 启动 Broker

```powershell
mosquitto -c .\mosquitto.local.conf -v
```

### 18.4 推荐进程启动顺序

1. 检查 Mosquitto 配置、ACL 和密码文件权限；
2. 启动 Broker；
3. 确认匿名连接失败；
4. 启动网关 MQTT 适配进程；
5. 网关订阅自身 ClientHello、ClientFlight 和记录上行主题；
6. 启动设备 MQTT 适配进程；
7. 设备订阅自身 ServerFlight 和记录下行主题；
8. 设备生成新的 `handshake_id`；
9. 设备发布 ClientHello；
10. 按三消息时序完成传输；
11. 主线确认服务端握手成功后再允许应用记录；
12. 演示重复消息、乱序记录和审计输出；
13. 演示结束时正常关闭连接和会话。

### 18.5 ACL 意义

ACL 可以限制 MQTT 账号允许发布和订阅的主题，例如设备只能：

- 向指定网关发布握手消息和记录上行；
- 订阅自己的 ServerFlight 和记录下行。

但 ACL 只能减少错误路由和越权发布，不能替代主线密码身份认证。

### 18.6 生产环境要求

本地示例不能直接用于公网生产。生产部署至少应增加：

- TLS Broker 监听；
- Broker 证书验证；
- 每设备独立凭据或客户端证书；
- 独立服务账户；
- 密码文件和私钥文件权限；
- 审计日志轮转和保留策略；
- 系统时间同步；
- 连接、速率、消息大小和并发握手限制；
- Broker 高可用和安全更新流程；
- 配置变更审批与备份。

---

## 19. 对接主线的推荐流程

### 19.1 发送端

```text
主线产生消息对象
→ 主线严格 wire codec 编码为 bytes
→ 成员 B MqttEnvelope.new()
→ topic_for()
→ MQTT publish(qos=1, retain=false)
```

### 19.2 接收端

```text
MQTT 收到 topic + payload
→ MqttEnvelope.decode()
→ validate_topic_matches()
→ SessionRouter 去重/排序
→ 主线严格 wire codec 解码 envelope.payload
→ 主线处理握手或安全记录
→ AuditLogger 输出结果
```

### 19.3 主线需要提供的接口

成员 B 对接前需要主线明确提供：

1. 四类对象的严格版本化编码和解码函数；
2. 每类 wire message 最大长度；
3. `session_id` 的规范文本编码；
4. 应用记录可公开读取的方向和序号；
5. 主线成功认证后的只读身份上下文；
6. 稳定异常类别，而不是依赖异常文本；
7. 会话关闭及幂等 `close()` 语义；
8. 握手和会话对象的线程安全声明。

成员 B 不应通过访问主线私有字段来获取上述信息。

---

## 20. 验收清单

### MQTT 与 Envelope

- [x] 三类握手消息和应用记录已分开；
- [x] 主题表已完成；
- [x] Envelope 字段表已完成；
- [x] 握手使用独立 `handshake_id`；
- [x] 应用记录使用主线 `session_id`；
- [x] 路由 ID 明确不等于认证身份；
- [x] 主题和 Envelope 可进行一致性校验；
- [x] 提供 JSON Schema 和非敏感示例。

### 传输行为

- [x] 建议 QoS 1；
- [x] 握手和记录 retain=false；
- [x] 重复握手不重复推进处理器；
- [x] 同 ID 不同内容会被拒绝；
- [x] 同一握手操作串行；
- [x] 应用记录按会话和方向串行；
- [x] 未来序号有界缓存；
- [x] 历史相同记录去重；
- [x] 同序号不同内容冲突拒绝；
- [x] 不通过重置序号或忽略认证错误恢复。

### 审计与部署

- [x] 握手成功、失败事件；
- [x] 会话关闭事件；
- [x] 记录接受、拒绝事件；
- [x] 传输重复、冲突、路由不匹配事件；
- [x] 敏感字段禁止规则；
- [x] Mosquitto 本地配置；
- [x] 最小 ACL 示例；
- [x] Broker、网关、设备启动顺序；
- [x] 独立演示程序；
- [x] 自动化测试和验收报告。

### 边界

- [x] 不包含 `crypto/`；
- [x] 不包含 `security/`；
- [x] 不包含 `gateway/`；
- [x] 不包含 `.git/`；
- [x] 不包含私钥或真实会话秘密；
- [x] 不重新定义密码协议。

---

## 21. 当前限制

虽然本包已经完成成员 B 的独立职责，但它不代表整个安全网关已经可以生产部署。当前限制包括：

- 没有主线安全对象正式 wire codec；
- `demo.py` 使用占位 payload，不进行真实认证或加解密；
- 没有实现真实 Paho MQTT 设备/网关进程；
- 传输状态只保存在内存中；
- 没有跨进程恢复和会话持久化；
- 没有集群 Broker 场景验证；
- 重排窗口和超时时间尚未压测；
- 没有正式日志平台和告警系统；
- 本地 Mosquitto 配置不是生产加固配置。

因此，准确描述应为：

> 已完成成员 B 的 MQTT 外层消息规范、传输去重与排序、结构化审计和本地部署设计，并提供可运行的独立 Python 原型与测试；等待主线严格 wire codec 和真实 MQTT 进程进行最终集成。

---

## 22. 快速命令汇总

### 运行演示

```powershell
python demo.py
```

### 运行测试

```powershell
python -m unittest discover -s tests -v
```

### 创建 Mosquitto 用户

```powershell
mosquitto_passwd -c .\passwd gateway-demo
mosquitto_passwd .\passwd device-demo-01
```

### 启动本地 Broker

```powershell
mosquitto -c .\mosquitto.local.conf -v
```

---

## 23. 最终交付结论

本独立成果包已经覆盖成员 B 的全部要求：

- 有清晰的 MQTT 主题表；
- 有完整 Envelope 字段表；
- 有三类握手消息和应用记录的非敏感示例；
- 有独立握手关联标识设计；
- 有消息时序；
- 有 QoS、Retain、重复和乱序处理规则；
- 有同一握手串行调度代码；
- 有审计事件、字段和敏感信息禁止规则；
- 有本地 Broker、设备、网关启动说明；
- 有 Envelope、主题、调度和审计可运行代码；
- 有 Schema、测试、演示和验收报告；
- 没有包含或重新实现主线密码功能。
