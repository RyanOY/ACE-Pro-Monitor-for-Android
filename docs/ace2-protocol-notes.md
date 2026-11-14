# Anycubic ACE 系列串口协议调研笔记

> 面向 AceSolo（Android USB Host 接入）的第一轮协议资料汇总。
> 结论均标注来源等级；**本项目自研实现尚未开始，本文所有帧示例都是按协议合成的，不是实测抓包**。
> **v0.2 补充（2026-11）**：上一句是 v0.1 写作时的状态，已过时——v0.2.0 已实现 USB 串口连接、`DISCOVER_DEVICE`/`ASSIGN_DEVICE_ID`/`GET_INFO` 握手、`DRYING` 启动/停止与 2 s 状态轮询。帧示例仍是**按协议合成、未在真机抓包验证**。实现清单见新增的 §0 与 §4.1 的「v0.2 实现」列，操作与帧逐字节说明见 [`DRYING.md`](./DRYING.md)。
>
> - 标记说明：✅ 有来源的事实（文档/代码/实测记录可查）｜🔶 推断或社区实现约定（多个实现一致，但无官方说明）｜❓ 待验证（存在冲突或只有单一来源）｜⚠️ 安全注意
> - 调研时间：2026-11（来源 URL 见各节）
> - 适用范围：**Anycubic ACE 2 Pro（二代）** 为主要目标；同时给出 ACE Pro（一代）对照，因为两者协议完全不同，接错必失败。

---

## 目录

0. [v0.2 实现状态](#0-v02-实现状态)
1. [概览与代际差异](#1-概览与代际差异)
2. [物理层](#2-物理层)
3. [帧格式](#3-帧格式)
4. [命令表](#4-命令表)
5. [状态字段](#5-状态字段)
6. [社区实现参考](#6-社区实现参考)
7. [建议的第一轮抓包与验证步骤](#7-建议的第一轮抓包与验证步骤)
8. [风险与安全注意](#8-风险与安全注意)
9. [附录 A：protobuf 消息字段速查](#附录-aprotobuf-消息字段速查)
10. [附录 B：待验证清单](#附录-b待验证清单)

---

## 0. v0.2 实现状态

> 本节是 v0.1 调研文档的增量备注：**下面的原始调研内容全部保留**，这里只记录哪些结论已经落进 v0.2 代码、哪些还没有。更新于 2026-11。

v0.2.0（Android，`com.acesolo`，`versionCode 2`）的实际实现范围：

| 能力 | 状态 | 说明 |
|---|---|---|
| USB Host 串口连接（230400 8N1） | ✅ 已实现 | CH340/CH341A/CP210x/FTDI/PL2303；官方 CH343 线（`1a86:55d3`）在 usb-serial-for-android 3.8.1 下无驱动 |
| 自动握手：DISCOVER → ASSIGN(1) → GET_INFO | ✅ 已实现 | 每次连接重新握手；地址上电后不持久 |
| `DRYING` 启动 / 停止（cmd 11） | ✅ 已实现 | 30–65 ℃ / 10–1440 分钟，UI 二次确认；`auto_roll` 固定 false |
| `GET_STATUS` 2 s 轮询与状态卡 | ✅ 已实现 | 工作状态 / 温度 / 湿度 / 烘干状态 / 目标温度 / 剩余时间 |
| 原始帧 hex 日志 + 手动 hex 发送 | ✅ 已实现 | 沿用 v0.1 调试台 |
| 进料/回抽、RFID、固件升级（IAP）、多机总线 | ✖ 未实现 | 部分命令在 `Ace2Protocol.kt` 里有 payload 封装，界面未接（见 §4.1 新增列） |
| 真机抓包验证 | ✖ 未验证 | **本文所有协议结论仍来自社区逆向；帧示例为合成，不是实测** |

- 帧格式 / CRC 以 §3 的**驱动实现布局**为准：`FF AA | flags | id(le16) | cmd | len | payload | crc16(le16) | FE`，CRC16/MCRF4XX 覆盖 `frame[2 .. 7+len)`。
- 已知单位差异：`DRYING` **请求**的 `duration` = 分钟，`GET_STATUS` 里的 `duration` / `remain_time` = 秒（§5.4 已就地标注）。
- 烘干报文逐字节说明、真实请求帧与 CRC 自校验脚本见 [`DRYING.md`](./DRYING.md)。

---

## 1. 概览与代际差异

Anycubic 的耗材管理盒（ACE = Anycubic Color Engine）目前有两代主机接口，**物理层、波特率、帧格式、载荷编码全部不同**：

| 项目 | ACE Pro（一代） | ACE 2 Pro（二代，本项目目标） |
|---|---|---|
| 物理层 | 直接 USB（USB CDC 串口） | **RS-485 半双工**，经 USB 转 RS-485 适配器 ✅ |
| 波特率 | 名义 115200 8N1（CDC ACM，实际速率由 USB 决定）🔶 | **230400 8N1** ✅（三个独立来源一致） |
| 线缆 | USB D+/D−/GND 直连 | 官方「USB to RS485 Signal Cable」（CH343）✅ |
| 帧头 | `FF AA` | `FF AA` ✅ |
| 帧结构 | `FF AA` + u16 小端 JSON 长度 + JSON + CRC16 + `FE` ✅ | `FF AA` + 地址 + seq(u16) + cmd + len + protobuf + CRC16 + `FE` ✅ |
| 载荷 | JSON-RPC（`id`/`method`/`params`）✅ | **Protocol Buffers proto3**（nanopb 编解码），自定义 schema，包名 `ace_com` ✅ |
| 校验 | CRC-16/MCRF4XX（poly 0x8408 反射，init 0xFFFF）✅ | 同族 CRC-16（社区代码称 KERMIT；参数同上）✅ |
| 槽位 | 4 个 | 4 个（index 0–3）✅ |
| 社区状态 | 较成熟（Klipper 插件、日志分析） | 已可读写（多个 Klipper/固件项目实现并上机验证）✅ |

**一句话结论**：ACE 2 Pro = **RS-485（USB 适配器）+ 230400 8N1 + `FF AA` 定界帧 + `FF AA` 帧内地址/序号 + 自定义 protobuf 载荷 + CRC16 + `FE` 结束符**。官方**没有公开协议文档**（多次检索无果，产品页只讲配件；见 §6 末尾），全部细节来自社区对固件的逆向与实测，因此下文严格区分「已验证」与「推断」。

社区协议来源主要有两条独立线：

- **固件逆向线**：hakimio 的 gist（MCU 反汇编分析，原始一手材料）+ Simon-CR 的固件研究仓库（在真机上逐字段验证，并重建了完整 `.proto`）。
- **驱动实现线**：hakimio/U1-Ace 与 decay71/multiACE 的 Klipper 驱动（跑在真实打印机上），以及 paxx12 Snapmaker U1 固件集成、Preston Brown 的一代日志分析等。

两线交叉一致的结论可信度高；只有单线且未上机的，一律标 🔶/❓。

---

## 2. 物理层

### 2.1 电气与串口参数（ACE 2 Pro）

| 参数 | 值 | 等级 | 依据 |
|---|---|---|---|
| 接口 | RS-485 半双工差分（A/B 一对 + GND） | ✅ | Simon-CR 文档、Snapmaker 接线指南 |
| 波特率 | **230400** | ✅ | hakimio gist「230400 baud」；hakimio `ace2_protocol.py` 头注释；multiACE V2 `DEFAULT_BAUD=230400` |
| 数据位/校验/停止位 | **8 / None / 1（8N1）** | ✅ | 同上（多处写明 8N1） |
| 流控 | 无（不用 RTS/CTS 硬流控，也不用 XON/XOFF） | 🔶 | 所有实现均未使用流控 |
| 主从 | 单主机、可多从机（地址 0–127） | ✅ | Simon-CR 总线分析：帧内地址按相等比较，回复置 bit7 |
| 上电默认地址 | 每台设备每次上电都回到地址 0（地址不持久化） | ✅ | Simon-CR `docs/13`：`Nothing is persisted.` |
| 多机发现 | 命令 0 `DISCOVER_DEVICE` → 命令 1 `ASSIGN_DEVICE_ID` | ✅ | proto 消息、multiACE 握手流程 |
| 总线争用 | 半双工无冲突检测；两台主机不能共总线 | ✅ | Simon-CR `docs/13`（结论：不是电气问题，是状态机问题） |
| 设备端 UART | MCU UART4，BRR=0x38400（反推 230400 8N1） | ✅ | hakimio gist |

> ⚠️ 常见误解：multiACE 的 README 里 `baud: 115200` 是给**一代 ACE Pro（V1）** 的旧配置，V2 代码里 `open_transport` 对二代硬编码 230400。看到 115200 不要照抄到二代。

### 2.2 接口连接器与转接

ACE 2 Pro 侧面是 6 针 Micro-Fit 3.0（2×3）信号口，社区实测的引脚定义（从正面/插合面看，卡扣朝上）✅：

```text
             clip/latch
                ||
      +-------------------+
      | [NC]  [D+]  [D-]  |
      | [NC]  [GND] [5V]  |
      +-------------------+
```

接线（来自 Snapmaker U1 扩展固件的 ACE 接线指南，已在该 U1 上实测 ACE 2 Pro 可用）✅：

| 适配器信号 | ACE 2 Pro 连接器 | 说明 |
|---|---|---|
| GND | GND | **必须接**（共地） |
| A / 485+ / D+ | D+ 或 D− | 不通信时与 B 对调再试 |
| B / 485− / D− | D− 或 D+ | 同上 |
| VCC / 5V | **不接** | 不要把适配器 5V 灌进 ACE |

要点：

- **CH343 优先**（官方线就是它，最接近原厂主机路径）。CH340/CH341 也能用，但需要在主机侧放行其 USB ID。
- USB VID:PID：官方/兼容 CH343 适配器在 Linux 上枚举为 `1a86:55d3`（CH343 CDC-ACM，`/dev/ttyACM*`）✅；常见 CH340 为 `1a86:7523`（`/dev/ttyUSB*`，非标准 CDC，需要厂商驱动）✅。
- **绝对不能把 ACE 2 Pro 直接接到 USB D+/D−**：它是 RS-485 差分，不是 USB。一代 ACE Pro 才是直连 USB。
- RS-485 的 A/B 命名没有统一标准，接反不损坏但收不到数据；「枚举成功但无应答 → 保持 GND，只对调 A/B」。
- 接线前断电，注意 6 针方向（防呆卡扣）。

### 2.3 Android USB Host 接入要点（v0.2 已按此接入）

以下是工程结论，非协议事实（🔶，基于 Android USB Host API 与 CDC-ACM 规范）：

1. **USB Host 模式 + OTG**：手机需支持 OTG；App 通过 `UsbManager` 枚举设备，按 VID/PID 过滤（`1a86:55d3` CDC-ACM；`1a86:7523` CH340 需要 vendor 驱动）。需在 `AndroidManifest.xml` 声明 `android.hardware.usb.host`，并处理 USB 权限（`PendingIntent` / `requestPermission`）。
2. **CDC-ACM 最小实现**：claim interface 0 → 发 `SET_LINE_CODING`（class request 0x20，`dwDTERate=230400`，`bCharFormat=0`，`bParityType=0`，`bDataBits=8`，7 字节小端结构）→ 视需要 `SET_CONTROL_LINE_STATE` 置 DTR/RTS → 用 bulk OUT/IN 端点读写。官方线（CH343）走这条路即可，**不必**引入重型驱动库；若兼容 CH340，再考虑 `usb-serial-for-android`。
3. **读**：bulk IN 端点阻塞读 + 超时（200–500 ms），累加到环形缓冲，按 §3 状态机切帧；不要假设「一次 bulk 读 = 一帧」，串口是字节流，可能半帧、也可能多帧粘连。
4. **写**：一次 bulk OUT 写完整帧；若单次写不完则循环写剩余。命令**串行**发送：发一帧 → 等应答（同 seq/同 cmd）→ 再发下一帧，不要流水线。
5. **本地回显**：个别 RS-485 适配器会把发出去的字节回读。判断方法：收到的字节流与刚发送的帧完全相同 → 视为回显丢弃（🔶 待实测确认官方线是否回显）。
6. **供电**：ACE 自身由 24V 电源供电，手机 OTG 只给适配器供电，5V 线不接 ACE。不要指望手机给 ACE 供电。
7. **权限与稳定性**：USB 设备拔出/休眠会导致连接断开，需在 `UsbManager.ACTION_USB_DEVICE_DETACHED` 里清理并给出重连流程；轮询期间保持屏幕/前台服务。

### 2.4 拓扑：一条总线只能有一个主机

- ACE 2 Pro 只有一对 RS-485，**同一时刻只能有一个主机**。打印机主板与手机同时当主机会互相打断（半双工无冲突检测）。✅
- 更隐蔽的问题是**状态分裂**：`ready` 只表示「就绪但空闲」，两台主机都轮询时，一台的 `STOP` 可能取消另一台的进料，甚至「看起来取消了其实没有」。✅（Simon-CR `docs/13` 的结论：这是 split-brain 问题，加仲裁也解决不了，必须物理或逻辑上保证单一主机。）
- 因此：**调试/抓包时先把 ACE 从打印机上摘下，手机独占总线；或只做「只收听」旁路监听（只接 A/B/GND，不发送）。**
- 多台 ACE 可菊花链（0—1—2—3）共用一条总线，用命令 0/1 发现并分配地址；每台上电都回到地址 0，所以每次上电后必须重新枚举/分配。✅

---

## 3. 帧格式

### 3.1 帧结构总表（ACE 2 Pro，V2）

| 偏移 | 长度 | 字段 | 值 / 说明 | 等级 |
|---|---|---|---|---|
| 0 | 1 | 前导 1 | `0xFF` | ✅ |
| 1 | 1 | 前导 2 | `0xAA` | ✅ |
| 2 | 1 | 地址 / 方向字节（社区代码常叫 `flags`） | 请求：目标设备地址 `0x00–0x7F`（单机默认 0）；应答：`设备地址 \| 0x80` | ✅ |
| 3 | 1 | seq 低字节 | 16 位序号小端 | ✅ |
| 4 | 1 | seq 高字节 | 同上 | ✅ |
| 5 | 1 | 命令码 cmd | 见 §4 | ✅ |
| 6 | 1 | 载荷长度 len | 0–100 字节（`MAX_PAYLOAD_LEN=100`） | ✅ |
| 7 | len | 载荷 payload | protobuf 编码（见 §5、附录 A） | ✅ |
| 7+len | 1 | CRC 低字节 | 小端存放 | ✅ |
| 8+len | 1 | CRC 高字节 | 同上 | ✅ |
| 9+len | 1 | 结束符 | `0xFE` | ✅ |

- 帧开销：帧头 7 字节（2 前导 + 5 内层头）+ CRC 2 字节 + 结束符 1 字节 = **10 字节**；**最短帧 10 字节**（空载荷），**最长帧 110 字节**（100 字节载荷）。✅（与 multiACE 的 `MIN_FRAME_LEN=10`、`MAX_PAYLOAD_LEN=100` 一致）
- 内层头 = `[地址][seq_lo][seq_hi][cmd][len]`，共 5 字节。✅
- **字节序**：seq 与 CRC 都是小端；多字节 protobuf 字段按 protobuf 自身规则（varint / 小端 fixed32/float / 大端 fixed64/double 由 wire type 决定）。✅
- 帧间空闲/最小间隔：**没有找到明确数值**，社区实现均按「一问一答、收到应答再发下一帧」处理，没有依赖固定帧间隔。❓（见附录 B）

> 注：不同来源对第 2 字节的叫法不一致——驱动代码里叫 `flags`（请求 0x00 / 应答 0x80），固件分析认为它是**总线地址**，回复时「或上 bit7」。两种说法在单机场景下等价。文档统一按「地址/方向字节」描述，写代码时可以用常量 `ADDR_HOST=0x00`、`RESP_FLAG=0x80`。

### 3.2 seq（序号）规则

- 16 位无符号，社区实现从 1 开始、每帧递增（回绕继续）。🔶
- 应答帧里的 seq 与请求相同（按 seq + cmd 匹配请求与应答）。🔶（multiACE/hakimio 解码都保留 seq 用于匹配；固件分析显示设备也校验/保存序号）
- 固件反汇编（hakimio gist）称：执行 `ASSIGN_DEVICE_ID` 后，主机选的 seq 值被存进设备 `word_2000111C`，之后主机帧的 seq 需要递增。✅（结论：多机场景务必严格递增；单机建议同样从 1 开始严格 +1，回绕到 1。）
- 若应答 seq 与请求不符 → 视为过期/其他主机应答，丢弃。🔶

### 3.3 校验算法（CRC-16）

参数（多来源一致）：

| 参数 | 值 |
|---|---|
| 多项式 | `0x1021`（反射形式 `0x8408`），refin=true、refout=true |
| 初值 | `0xFFFF` |
| 结果异或 | `0x0000` |
| 计算范围 | 内层 `[地址][seq_lo][seq_hi][cmd][len][payload...]`（即帧偏移 2 到 7+len−1） |
| 存放 | 小端（低字节在前） |

标准自检：字节串 `"123456789"` 计算结果为 **`0x6F91`**（即 CRC-16/MCRF4XX；社区代码常把它叫 CRC-16/KERMIT，但 KERMIT 的初值是 0x0000，名字不重要，按上表参数实现即可）。一代 ACE Pro 的 JSON 协议用的也是同一族 CRC（CRC-16/MCRF4XX）。✅

参考实现（与 multiACE `crc16_kermit` 等价）：

```python
def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF
```

### 3.4 组帧示例（合成帧，非实测）

以下帧按本文档规则生成，可用来对照你的编码器是否一致；**不是抓包记录**。地址 0、单机场景。

| 请求 | 完整帧（hex） |
|---|---|
| `GET_INFO`（cmd 7，seq 1，空载荷） | `FF AA 00 01 00 07 00 3B 61 FE` |
| `GET_STATUS`（cmd 6，seq 2，空载荷） | `FF AA 00 02 00 06 00 2E 5D FE` |
| `GET_TEMP`（cmd 64，seq 3） | `FF AA 00 03 00 40 00 23 53 FE` |
| `GET_FEED_INFO`（cmd 76，seq 4） | `FF AA 00 04 00 4C 00 A2 AD FE` |
| `FEED_OR_ROLLBACK`（cmd 8，seq 5，槽位 2、速度 50、长度 200、mode 0） | `FF AA 00 05 00 08 07 08 02 10 32 18 C8 01 EB 36 FE` |
| `STOP_FEED_OR_ROLLBACK`（cmd 9，seq 6，槽位 2） | `FF AA 00 06 00 09 02 08 02 7C CA FE` |

示例中 protobuf 载荷的字段号/编码：

- `FEED_OR_ROLLBACK`：`08 02` = 字段 1(index)=2；`10 32` = 字段 2(speed)=50；`18 C8 01` = 字段 3(length)=200；mode=0 在 proto3 中是默认值，编码器可省略（社区实现也会省略）。
- `STOP`：`08 02` = 字段 1(index)=2。
- 应答帧：地址字节为 `0x80 | 设备地址`；例如设备地址 0 应答 `GET_INFO`：`FF AA 80 01 00 07 13 0A 07 56 31 2E 31 2E 33 31 12 06 56 31 2E 30 2E 30 18 01 AC BB FE`（载荷 = version="V1.1.31"、boot_version="V1.0.0"、first_request=true；这是**假设值**，用于演示字段编码）。

### 3.5 请求/应答匹配与超时

建议实现规则（🔶 工程约定，非协议强制）：

1. 每个请求分配唯一 seq（1 → 0xFFFF 递增，回绕）。
2. 发送后等待应答，超时 300–800 ms（230400 波特率 + 设备处理时间足够；⚠️ 不要用 10 ms 级超时，固件有排队/退避）。
3. 收到帧：先验 CRC → 验结束符 → `地址 & 0x80` 必须为真（否则是别人的请求/回显）→ seq 与 cmd 匹配 → 取载荷。
4. 超时可重发**同一帧**最多 1–2 次；连续失败则重建端口/提示检查接线。
5. 写类命令返回的 `GenericResponse.code == 0` 只表示「命令被接受」，**不表示动作完成**；完成判据看 `GET_STATUS` 的槽位状态回到 `ready`（见 §5、§8）。

### 3.6 流式解析状态机

串口是字节流，必须容错解析。multiACE 的 `decode_frames` 逻辑可直接参考（✅ 实现已上机）：

1. 缓冲不足 `MIN_FRAME_LEN(10)` → 等。
2. 找 `FF AA`；找不到则丢弃（注意末尾单独的 `0xFF` 要保留，可能是下一帧前导的一半）。
3. 不足 7 字节（内层头）→ 等。
4. 取 `len = buf[6]`；`len > 100` → 判坏帧，跳过 2 字节重新找前导。
5. 不足 `7 + len + 3` 字节 → 等。
6. 检查 `buf[7+len+2] == 0xFE`，且 CRC 匹配 → 输出一帧。
7. 否则坏帧：跳 2 字节重新同步（不要整段丢弃，避免连带丢下一帧）。

### 3.7 帧布局的一处来源冲突（需要抓包定案）

- **hakimio gist 的 MCU 段**把内层头描述为 `[SEQ:1][TYPE_HI:1][TYPE_LO:1]`，即字节 2 是序号、3–4 是 16 位命令/类型。
- **hakimio 自己的驱动 `ace2_protocol.py`、decay71/multiACE 的 V2 编解码、Simon-CR 的总线分析**则一致使用 `[地址/flags][seq_lo][seq_hi][cmd][len]`：地址在字节 2，seq 是 16 位小端在字节 3–4，cmd 在字节 5。
- 两线都能跑通真实硬件（Klipper 实测），且 Simon-CR 明确说字节 2 是「按相等比较的静态总线地址，回复时或上 bit7」。❓ 结论：**以驱动实现的布局为准**（§3.1），但第一轮抓包时应专门核对字节 2/3/4（见 §7 验收项）。

---

## 4. 命令表

命名来自社区实现（multiACE `Cmd` 枚举 / Simon-CR 重建的 proto / hakimio schema），**不是官方名称**，🔶。命令号与字段号有固件描述符支撑，可信度较高（✅，见 §6）。

### 4.1 已知命令

| cmd | 名称 | 请求字段（字段号） | 应答 | 用途/备注 | v0.2 实现 |
|---|---|---|---|---|---|
| 0 | `DISCOVER_DEVICE` | 空 | `DiscoverDeviceResponse{uid1=1,uid2=2,uid3=3}` | 多机发现；单机可跳过（地址 0）✅ | ✅ 连接后自动发（flags=0） |
| 1 | `ASSIGN_DEVICE_ID` | `AssignDeviceIdRequest{uid1=1,uid2=2,uid3=3,device_id=4}` | 通用应答 | 给设备分配地址；之后按地址通信 ✅ | ✅ 固定分配 `device_id=1` |
| 5 | `IAP_VERSION` | ？ | ？ | 固件升级/版本相关，未验证 ❓ | ✖ 未实现 |
| 6 | `GET_STATUS` | 空 | `StatusResponse`（§5.1） | **最核心的只读轮询命令** ✅ | ✅ 每 2 s 轮询 + 手动查询 |
| 7 | `GET_INFO` | 空 | `InfoResponse{version=1,boot_version=2,first_request=3}` | 版本信息、是否首次请求 ✅ | ✅ 握手时读固件版本 |
| 8 | `FEED_OR_ROLLBACK` | `FeedOrRollbackRequest{index=1,speed=2,length=3,mode=4}` | `GenericResponse{code=1}` | 进料/回抽/助力（§4.3）✅ | ◻ payload 已封装，UI 未接 |
| 9 | `STOP_FEED_OR_ROLLBACK` | `StopFeedOrRollbackRequest{index=1}` | 通用应答 | 停止该槽位动作（不区分进/回抽）✅ | ◻ payload 已封装，UI 未接 |
| 10 | `UPDATE_SPEED` | `UpdateSpeedRequest{index=1,speed=2}` | 通用应答 | 运行中改速（V1 兼容用）✅ | ◻ payload 已封装，UI 未接 |
| 11 | `DRYING` | `DryingRequest{temp=1,duration=2,auto_roll=3}` | 通用应答 | 开始/停止干燥（`temp=0,duration=0` 即停止）✅ | ✅ 启动 / 停止烘干 |
| 12 | `SET_DRY_TEMP` | `SetDryTempRequest{temp=1}` | 通用应答 | 设干燥目标温度 ✅ | ◻ payload 已封装，UI 未接 |
| 13 | `GET_FILAMENT_INFO` | `GetMaterialInfoRequest` / index | `FilamentInfoResponse`（RFID 信息） | 读耗材/RFID 缓存（§5.7）✅ | ◻ payload 已封装，UI 未接 |
| 14 | `SET_RFID_ENABLE` | `SetRfidEnableRequest{index=1,enable=2}` | 通用应答 | 开关 RFID ✅ | ◻ payload 已封装，UI 未接 |
| 19 | `SET_FEED_CHECK` | `SetFeedCheckRequest{check_length=1,error_length=2}` | 通用应答 | 进料检测阈值（错误长度默认 254）✅ | ✖ 未实现 |
| 64 | `GET_TEMP` | 空 | `GetTempResponse`（6–7 个 float32，§5.5） | 温度/湿度 ✅ | ✖ 未实现 |
| 65 | `SET_DRY_POWER` | `SetDryPowerRequest{power=1}` | 通用应答 | 干燥功率 ✅ | ✖ 未实现 |
| 66 | `SET_VALVE` | `SetValveRequest{valve1=1,valve2=2}` | 通用应答 | 气阀（2 个 bool）✅ | ✖ 未实现 |
| 68 | `FILAMENT_IDENTIFY` | index | 通用应答；失败码 6=`READFAILED` | 触发 RFID 识别（设备忙时 `FAILED`）✅ | ✖ 未实现 |
| 70 | `FLASH_LED` | `FlashLedRequest{components=1,loop=2,quick1=3,slow1=4,quick2=5,slow2=6}` | 通用应答 | 闪灯（状态指示，可用于「找到设备」）✅ | ✖ 未实现 |
| 71 | `SET_FAN` | `SetFanRequest{speed=1,fan1=2,fan2=3}` | 通用应答 | 风扇 ✅ | ✖ 未实现 |
| 73 | `GET_KEY_STATE` / `GET_SENSOR_STATE` | 空 | `GetSensorStateResponse{state=1}` | 按键/传感器状态（两种命名指同一命令）🔶 | ✖ 未实现 |
| 76 | `GET_FEED_INFO` | 空 | `FeedInfoResponse{feed_info=1 repeated}`（每槽位一条） | 进料计数/长度/编码器（§5.6）✅ | ✖ 未实现 |

**「v0.2 实现」列图例**：✅ = 已接入并在连接流程/烘干中实际发送；◻ = `Ace2Protocol.kt` 里有 payload 编码函数，但界面未调用（改了也发不出去）；✖ = 未实现，仅作资料。

命令号 67、69、74、77、78 在固件里有痕迹但未在命令表中注册（社区观察到 67=DRY_TEST、69=RFID_TEST、74=SET_KEY_LOG_ENABLE，72/77/78 只有占位名）；**不要调用**，行为未知。✅/❓（来自固件调度表分析，未实测）

### 4.2 应答码（`GenericResponse.code`，字段 1，int32）

| code | 含义 | 等级 |
|---|---|---|
| 0 | `SUCCESS`（命令已接受） | ✅ |
| 1 | `PARAM_ERROR`（参数越界，如 speed>100、index>3、mode>3） | ✅ |
| 2 | `FORBIDDEN`（当前状态不允许该操作，如槽位忙/未就绪） | ✅ |
| 3 | `FAILED`（执行失败；`FILAMENT_IDENTIFY` 在忙时返回它） | ✅ |
| 6 | `READFAILED`（读 RFID 失败，命令 68 专用） | ✅ |

### 4.3 `FEED_OR_ROLLBACK` 的四种 mode（关键）

| mode | 槽位状态 | 行为 | 长度/速度参数 |
|---|---|---|---|
| 0 | `feeding`（1） | 正向进料 `length` mm，速度 `speed` | 都生效 |
| 1 | `rollback`（2） | 反向回抽 `length` mm | 都生效 |
| 2 | `assisting`（3） | 连续送料助力（开环，直到 STOP 或阻力/缓冲触发） | **忽略** length，speed 语义未完全确认 |
| 3 | `rollback_assisting`（4） | 连续回抽助力（开环） | 同上 |

- mode 0/1 是「定长动作」；mode 2/3 是「持续动作」，必须由主机停止或由缓冲传感器/阻力条件终止。✅（Simon-CR 实测：无背压时会一直转；assist 需要主机侧空闲处理，否则 `ASSIST_ERROR`）
- 速度上限 100（百分比，不是 mm/s），超限 → `PARAM_ERROR`；index 0–3。✅
- 准入：只有槽位处于 `ready` 才能接受新的 feed/rollback，否则 `FORBIDDEN`；一次只允许一个动作在飞。✅（Simon-CR `docs/05` 有准入表）
- 停止：`STOP` 返回成功 ≠ 电机立刻停；实测「对没在动的电机发 STOP 也返回成功」。判断停止完成看 `GET_STATUS` 槽位状态，必要时轮询到 `ready` 再发下一条。✅（Simon-CR `docs/09`）

### 4.4 典型交互时序（建议实现）

```text
[只读启动]
  (可选) DISCOVER_DEVICE → 得 uid1..3
  (多机) ASSIGN_DEVICE_ID(uid..., device_id=1..127)
  GET_INFO        → version / boot_version
  GET_STATUS      → 4 槽状态 + 干燥仓 + 温湿度
  GET_TEMP        → 6 个温度 float
  GET_FEED_INFO   → 4 槽计数
  → 之后 1 Hz 轮询 GET_STATUS（最多 2–4 Hz，总线共享时更低）

[一次进料]
  GET_STATUS 确认目标槽 ready
  FEED_OR_ROLLBACK(index, speed, length, mode=0) → code=0
  每 200–500 ms GET_STATUS，直到槽位回到 ready（超时上限，例如 length/speed 估算 ×3）
  异常 → STOP_FEED_OR_ROLLBACK(index) → 等 ready

[停止/急停]
  STOP_FEED_OR_ROLLBACK(index) → code=0（或 FORBIDDEN/超时）
  轮询 GET_STATUS 确认回 ready；仍不回 → 重复 STOP；再不行 → 断电（⚠️ 见 §8）
```

---

## 5. 状态字段

### 5.1 `GET_STATUS`（cmd 6）应答 `StatusResponse`

| 字段 | 类型 | 含义 | 等级 |
|---|---|---|---|
| 1 | uint32 | 工作状态：0=init、1=ready、2=busy、3=upgrade | ✅（multiACE `WORK_STATES`） |
| 2 | DryStatus | 干燥仓状态（§5.4） | ✅ |
| 3 | int32 | 箱内温度，°C | ✅ |
| 4 | uint32 | 箱内湿度，%RH | ✅ |
| 5 | bool | `rfid1_enable`（观察值常为 1，语义=槽 1/2 组 RFID 使能） | 🔶 |
| 6 | bool | `rfid2_enable`（同上，另一组） | 🔶 |
| 7 | uint32 | `feed_assist_count`，仅助力进行中出现 | ✅ |
| 8 | uint32 | `cont_assist_time`，**毫秒**（一代是秒） | ✅ |
| 9 | `repeated SlotStatus`（最多 4 条） | 每个槽位一条（§5.2） | ✅ |

实现提醒：字段 9 是 repeated，空槽也可能**不出现该条**；按「缺省 = 空槽/未知」处理，不要按数组下标硬对应（虽然实践中按顺序出现，🔶）。静止时子消息可能只有字段 2（filament_state）而没有字段 1（slot_state）——`slot_state` 缺省按 0=ready 处理。✅（Simon-CR：`At rest only field 2 is present.`）

### 5.2 `SlotStatus`（槽位状态，字段 1 的枚举）

| 值 | 名称 | 说明 |
|---|---|---|
| 0 | `ready` | 就绪/空闲（无动作） |
| 1 | `feeding` | 正在进料 |
| 2 | `rollback` / `unwinding` | 正在回抽 |
| 3 | `assisting` | 送料助力中（mode 2） |
| 4 | `rollback_assisting` | 回抽助力中（mode 3，实测出现过） |
| 5 | `preloading` | 自动预载中 |
| 6 | `upgrading` | 升级中 |
| 129 | `feed_error` | 进料错误 |
| 130 | `rollback_error` | 回抽错误 |
| 131 | `assist_error` | 助力错误 |
| 132 | `preload_error` | 预载错误 |
| 133 | `stuck_error` | 卡住 |
| 134 | `tangled_error` | 缠料 |
| 135 | `motor_error` | 电机错误 |

来源：multiACE `SLOT_STATES`（129–135）✅；129–133 在 Simon-CR 里记为「gear error」范围，具体名称以 multiACE 为准（🔶 名称映射，数值本身 ✅）。

### 5.3 耗材状态 `filament_state`（`SlotStatus` 字段 2）

| 值 | 含义 | 等级 |
|---|---|---|
| 0 | `empty`（无耗材；字段缺省也按 0） | ✅ |
| 1 | `unknown`（有丝但未识别/无法识别） | ✅ |
| 2 | `identified`（已识别 RFID） | ✅ |
| 3 | `identifying`（正在识别） | ✅ |

注意：**没有逐槽温度**。ACE 只在箱体层面报告温度/湿度（字段 3/4）和干燥仓（字段 2），不存在「每个槽位的耗材温度」这种字段——不要臆造。

### 5.4 干燥仓 `DryStatus`（`StatusResponse` 字段 2）

| 字段 | 类型 | 含义 | 等级 |
|---|---|---|---|
| 1 | uint32 | 状态：低 4 位为状态码 `0 stop / 1 starting / 2 keeping / 3 stopping / 4 ptc_error / 5 ntc_error`；bit7=`rotisserie_active`（转盘旋转中），bit6=`auto_roll_allowed` | ✅（multiACE `DRY_STATES`、`DRY_STATUS_STATE_MASK=0x0F`） |
| 2 | uint32 | 目标温度 °C | ✅ |
| 3 | uint32 | 总时长，**秒** | ✅ |
| 4 | uint32 | 剩余时间，**秒** | ✅ |

⚠️ 单位坑：`DRYING` **请求**的 `duration` 是**分钟**，而状态里的 `duration`/`remain_time` 是**秒**（相差 60 倍）。✅（Simon-CR 明确记录）

> **v0.2 就地修正（2026-11）**：本段 v0.1 原文写「本项目 MVP **不做加热**」，该结论已被 v0.2 推翻——v0.2 已实现烘干启动/停止（`DRYING`，cmd 11）：请求按**分钟**下发（10–1440），应答按**秒**显示（UI 用 `剩余秒/60` 换算成 `x分y秒`）；停止 = `temp=0, duration=0, auto_roll=false`。真实请求帧与 python3 CRC 自校验见 [`DRYING.md`](./DRYING.md#64-真实请求帧启动烘干-55---240-分钟)。

### 5.5 `GET_TEMP`（cmd 64）应答 `GetTempResponse`

重建的 proto 有 **7 个 float32**（字段 1–7），字段名只部分有语义（✅ 字段号/类型来自固件描述符，🔶 语义来自社区）：

| 字段 | 类型 | 社区语义 | 等级 |
|---|---|---|---|
| 1 | float32 | 恒为 0（未使用） | ✅（实测） |
| 2 | float32 | 恒为 0（未使用） | ✅（实测） |
| 3 | float32 | `temp_a`：箱体 1 温度 °C | 🔶 |
| 4 | float32 | `temp_b`：箱体 2 温度 °C | 🔶 |
| 5 | float32 | `temp_c`：PTC 1 温度 °C | 🔶 |
| 6 | float32 | `temp_d`：PTC 2 温度 °C | 🔶 |
| 7 | float32 | `temp_e`：环境温度 °C | 🔶 |

hakimio 的驱动把 6 个 float 解成 `box1_temp, box2_temp, ptc1_temp, ptc2_temp, env_temp, env_humidity`（即字段 1–6，与上面语义不同）——❓ 两处命名/字段数不一致。**第一轮抓包必须实测**：发 `GET_TEMP` 看有几个字段、数值与面板显示对比、断电/放热后怎么变。

### 5.6 `GET_FEED_INFO`（cmd 76）应答 `FeedInfoResponse`

| 字段 | 类型 | 含义 | 等级 |
|---|---|---|---|
| 1 | repeated `FeedInfo`（每槽位） | `steps=1`、`length=2`、`decoder=3` | ✅ |

- `length`：主机命令的累计长度（mm，int32，可能为负）。
- `steps`：电机计数（与 `length` 成比例）。
- `decoder`：编码器计数（部分固件/状态下才有值）。
- 实测标定：约 **12.35 counts/mm**（`steps/length` ≈ 12.348–12.351，两根不同半径的盘一致 → 是进料齿轮计数，**不是**卷盘旋转计数，所以不能用它推剩余量）。✅（Simon-CR 实测）

### 5.7 `GET_FILAMENT_INFO`（cmd 13）/ RFID 信息

`FilamentInfoResponse`（✅ 字段号来自固件描述符）：

| 字段 | 类型 | 含义 | 单位/备注 |
|---|---|---|---|
| 1 | uint32 | index（槽位） | 0–3 |
| 2 | uint32 | version | RFID 数据版本 |
| 3 | string | sku | 字符串，**设备侧上限 16 字节**（写长 URI 会被截断到 16） |
| 4 | string | type | 材料类型（如 PLA/PETG） |
| 5 | repeated `ColorInfo` | 颜色 | `rgba=1`（uint32，RGBA 打包；R 在 bit24–31） |
| 6 | `ExtruderTemp` | 喷头温度范围 | `min=1,max=2,min_speed=3,max_speed=4` |
| 7 | `HotbedTemp` | 热床温度范围 | `min=1,max=2` |
| 8 | uint32 | 线径 | **×0.01 mm**（175 = 1.75 mm） |
| 9 | uint32 | 总长 | m？/g？（❓ 社区用法不一，先按原始值展示） |
| 10 | uint32 | icon_type | 图标 |
| 11 | uint32 | remainder | 剩余量（❓ 单位未确认） |
| 12 | uint32 | code | 材料码 |

RFID 行为（✅ Simon-CR 真机实测）：

- 天线固定在槽位，标签随料盘转，**需要盘旋转约一整圈（满盘约 600 mm 进料）才能读到**。
- 设备忙时 `FILAMENT_IDENTIFY` 返回 `FAILED`；识别只在停转时做。
- 解码结果**缓存到槽位读空为止**：换盘不一定要读空，但料必须离开槽口让槽位变 empty，否则旧数据仍在。
- **不暴露标签 UID**：只有解码字段，无法拿 NFC UID 与外部数据库匹配；要匹配只能往 sku 里写短 ID（≤16 字节）。

### 5.8 `GET_INFO`（cmd 7）应答 `InfoResponse`

| 字段 | 类型 | 含义 |
|---|---|---|
| 1 | string | `version`（如 `V1.1.31`，char[12] → 最多 11 字符）✅ |
| 2 | string | `boot_version`（char[12]）✅ |
| 3 | bool | `first_request`（是否首次请求）✅ |

---

## 6. 社区实现参考

按可信度/用途排序。**凡是本文引用以上来源的字段名，都来自这些链接**。

| 来源 | 链接 | 提供什么 | 可信度 |
|---|---|---|---|
| hakimio：ACE 2 Pro 固件协议分析（gist） | <https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9> | **一手逆向**：230400 8N1、帧格式、CRC、8 状态解析机、seq 校验、protobuf 调度、33 条命令、UART4 BRR=0x38400、OTA 同口 | ★★★★★（固件反汇编，部分结论后被真机修正） |
| hakimio：交互 shell + protobuf schema（gist） | <https://gist.github.com/hakimio/551915aa02b7e248721bed672ad46e0b> | 交互式 shell 工具 + proto 定义；头注释即「CH343 / 230400 / 8N1 / FF AA …」 | ★★★★★ |
| hakimio/U1-Ace：`ace2_protocol.py` | <https://github.com/hakimio/U1-Ace/blob/ace2/src/ace2_protocol.py>（raw: <https://raw.githubusercontent.com/hakimio/U1-Ace/ace2/src/ace2_protocol.py>） | **可直接读的 V2 编解码**：CRC、组帧/解帧、`_decode_status`/`_decode_temp`/`_decode_filament_info`、DISCOVER/ASSIGN 组包 | ★★★★☆（实现已用） |
| decay71/multiACE：`ace_protocol_v2.py` | <https://github.com/decay71/multiACE/blob/main/multiace/klipper/extras/ace_protocol_v2.py> | V2 编解码 + 全部状态枚举 + V1↔V2 方法映射 + 探测工具；同时支持一代/二代 | ★★★★☆（Klipper 实机） |
| Simon-CR/ace2-pro-firmware-research | <https://github.com/Simon-CR/ace2-pro-firmware-research> | **真机逐字段验证**：重建的 `protocol/ace2-v1.1.31.proto`、`protocol-findings.md`（65 KB）、`docs/05-protocol-notes.md`（四种 FEED mode + 准入表）、`docs/06-protobuf-descriptors.md`、`docs/13-standalone-control-box.md`（单主机论证） | ★★★★★（独立验证 + 详细记录） |
| printers-for-people/ACEResearch | <https://github.com/printers-for-people/ACEResearch/blob/main/PROTOCOL.md> | **一代** ACE Pro 的 JSON 协议、RPC 方法/字段、帧结构；作者注明「未测试」 | ★★★☆☆（一代参考） |
| prestonbrown/helixscreen：ACE 日志分析 | <https://github.com/prestonbrown/helixscreen/blob/main/docs/devel/printer-research/ANYCUBIC_ACE_KOBRA_S1_LOG_ANALYSIS.md> | 一代 ACE Pro 与 Kobra S1 的真实抓包分析（1 Hz `get_status` 心跳、USB CDC） | ★★★☆☆（一代日志） |
| paxx12-snapmaker-u1：ACE 接线/测试指南 | <https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/anycubic_ace.md>（本文引用 Codeberg 快照） | **接线**：6 针定义、A/B 对调、GND、不接 5V、CH343 优先、U1 上 ACE 2 Pro 实测通过 | ★★★★☆（硬件接线） |
| Anycubic 官方产品页（USB→RS485 信号线） | <https://us.anycubic3d.com/products/usb-to-rs485-signal-cable> ｜ <https://de.anycubic.com/products/ace-2-pro-usb-to-rs485-signal-cable> | 官方配件存在性与名称（「ACE 2 PRO USB to RS485 Signal Cable」） | 仅配件信息；抓取时返回 HTTP 429，内容未取到 |

**官方协议文档结论**：截至调研时，Anycubic 未公开 ACE/ACE 2 Pro 的串口协议、`.proto` 或寄存器/命令手册；官方只提供产品与配件页面。上文所有字段号来自社区对固件（protobuf 描述符表）的恢复，不是 Anycubic 发布。❓ 若后续发现官方文档，应优先于社区结论。

protobuf 澄清（对应任务问题）：**是 Google Protocol Buffers（proto3 线格式），设备端用 nanopb 0.4.x 编解码；schema 是 Anycubic 自定义的，包名 `ace_com`，没有官方 `.proto` 文件公开**。✅（gist 头注释 + Simon-CR 从固件 `msgdesc`/`field_info` 表恢复）社区重建的 schema 在 Simon-CR 仓库 `protocol/ace2-v1.1.31.proto`（约 40 条消息）；字段号/线格式已被描述符证明，**部分字段名是社区约定**（本文标 🔶）。

---

## 7. 建议的第一轮抓包与验证步骤

目标：**在不破坏设备、不加热、不冒险走料的前提下**，用最短路径确认本文 §2–§5 的协议细节，并拿到我们自己的第一手抓包。

### 7.1 三条路径，建议按顺序

**路径 A（最快、最安全）：旁路监听打印机 ↔ ACE 的真实流量。**
把 USB-RS485 适配器只接 `A/B/GND` 并在总线上并联（只收不发），让打印机当主机，用 PC/手机记录全部字节。这样能一次性确认：真实帧格式（地址字节到底是 0x00 还是别的）、seq 行为、CRC、真实命令集合、面板操作与状态字段的对应关系。**监听期间绝对不能发送任何字节**（半双工，一发就可能撞掉打印机的命令）。
风险：需要并线/夹线，注意 A/B 极性与共地；打印机可能在打印，别碰机械部分。

**路径 B：PC + Python（pyserial）直连，做只读探测。**
把 ACE 从打印机摘下，用 `1a86:55d3` 适配器接 PC，跑下面 §7.3 的脚本：`GET_INFO → GET_STATUS → GET_TEMP → GET_FEED_INFO` 循环。这是验证「我们自己的组帧/CRC/解析」最方便的环境。

**路径 C：Android 手机 USB Host 直连。**
协议验证通过后，把同样的组帧/解帧移到手机（§2.3）。USB Host + CDC-ACM 的实现细节比 PC 多，所以放在最后，而不是第一步。

### 7.2 准备清单

- [ ] ACE 2 Pro + 24V 电源（**独立供电**，不要从手机取电）
- [ ] 官方/兼容 USB-RS485 适配器（**CH343 / `1a86:55d3` 优先**；CH340 备选）
- [ ] 杜邦线/6 针 Micro-Fit 转接（GND、A、B 三根；5V 不接）
- [ ] 万用表（确认 A/B 没有短路、GND 连通；可选：确认总线空闲差分电平）
- [ ] PC（Linux/Windows 均可）+ Python 3 + `pyserial`；或逻辑分析仪/示波器（可选，用于确认 230400 与半双工时序）
- [ ] 记录工具：串口十六进制日志（保存原始 `.bin`，不要只存文本）

### 7.3 最小探测脚本（PC，只读）

```python
# 只读探测：GET_INFO / GET_STATUS / GET_TEMP / GET_FEED_INFO
# 依赖 pyserial: pip install pyserial
import serial, time

PREAMBLE = b'\xff\xaa'; END = 0xfe
ADDR = 0x00

def crc16(data, crc=0xFFFF):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF

def build(seq, cmd, payload=b''):
    inner = bytes([ADDR, seq & 0xFF, seq >> 8, cmd, len(payload)]) + payload
    c = crc16(inner)
    return PREAMBLE + inner + bytes([c & 0xFF, c >> 8, END])

def decode(buf):
    out = []
    while True:
        i = buf.find(PREAMBLE)
        if i < 0: break
        buf = buf[i:]
        if len(buf) < 7: break
        if buf[6] > 100: buf = buf[2:]; continue
        total = 7 + buf[6] + 3
        if len(buf) < total: break
        inner = buf[2:7 + buf[6]]
        crc_in = buf[7 + buf[6]] | (buf[8 + buf[6]] << 8)
        if buf[total - 1] != END or crc16(inner) != crc_in:
            buf = buf[2:]; continue
        if buf[2] & 0x80:
            out.append({'addr': buf[2], 'seq': buf[3] | (buf[4] << 8),
                        'cmd': buf[5], 'payload': bytes(buf[7:7 + buf[6]])})
        buf = buf[total:]
    return out, buf

ser = serial.Serial('/dev/ttyACM0', 230400, bytesize=8, parity='N',
                    stopbits=1, timeout=0.05)
seq = 0
for cmd_name, cmd in [('GET_INFO', 7), ('GET_STATUS', 6),
                      ('GET_TEMP', 64), ('GET_FEED_INFO', 76)]:
    seq += 1
    ser.reset_input_buffer()
    ser.write(build(seq, cmd))
    deadline = time.time() + 1.0
    buf = b''
    while time.time() < deadline:
        buf += ser.read(256)
        frames, buf = decode(buf)
        for f in frames:
            if f['seq'] == seq and f['cmd'] == cmd:
                print(cmd_name, 'OK', f['payload'].hex(' '))
                deadline = 0
    time.sleep(0.2)
```

跑通后：把 `payload` 打印出来，对照 §5 手工/小工具解码 protobuf；先把 `GET_INFO` 的版本字符串解出来，能解出可读字符串就说明 protobuf 路径正确。

### 7.4 只读验收清单（第一轮必须全部通过）

1. **帧同步率**：连续发 100 次只读命令，100 次都能切出帧，0 次误同步。
2. **CRC**：所有应答 CRC 正确（脚本统计 `crc_ok=100/100`）。
3. **地址/方向字节**：应答字节 `0x80`（单机、地址 0）——**同时记录原始值**，用于定案 §3.7。
4. **seq**：应答 seq 与请求一致（若不一致，记录规律）。
5. **`GET_INFO`**：解出非空 `version` 字符串，形如 `V1.x.y`，且与打印机屏/标签一致。
6. **`GET_STATUS`**：4 个槽位状态与**实物一致**（空槽 filament_state=0；插一轴丝再读=非 0）；箱内温度/湿度与面板显示接近（差几度正常，注意是否偏移）。
7. **`GET_TEMP`**：确认字段个数（6 还是 7）与数值含义；用「手捂/吹风/关机冷却」造变化，定位哪个字段是环境温度、哪个是箱体温度。
8. **`GET_FEED_INFO`**：空载不动时计数不跳；若允许手动拨动进料齿轮，应看到计数变化（可选）。
9. **原始日志**：保存每帧 `ts, dir, hex, crc_ok, addr, seq, cmd, len, payload_hex`（JSONL），留作后续对照。

### 7.5 写操作验证（只读全过之后，隔离环境）

⚠️ 先满足：**空载/短料、手在急停旁、只试一个槽位、短距离**。

1. 用 `GET_STATUS` 确认目标槽 `ready`。
2. `FEED_OR_ROLLBACK(index=0, speed=10, length=20, mode=0)` → 期望 `code=0`，随后 `GET_STATUS` 出现 `feeding`，约 1–2 s 后回 `ready`；进料方向、声音、面板状态记录。
3. `FEED_OR_ROLLBACK(index=0, speed=10, length=20, mode=1)` → 回抽，确认方向相反、`GET_FEED_INFO.length` 递减。
4. 运行中发 `STOP_FEED_OR_ROLLBACK(0)` → 记录是否立即停、`GET_STATUS` 多久回 `ready`、`code` 是否 0。
5. 非法参数验证：`speed=200` → `PARAM_ERROR(1)`；槽位忙时再发 feed → `FORBIDDEN(2)`；`index=7` → `PARAM_ERROR`。
6. 记录「发 STOP 但动作已完成」时返回什么（预期成功，说明不能靠返回码判断停止）。

### 7.6 常见故障排查

| 现象 | 优先检查 |
|---|---|
| 适配器枚举了但完全无回包 | A/B 对调；GND 是否接；波特率是否 230400；ACE 是否上电/是否休眠（按键唤醒）；地址是否 0 |
| 收到乱码/噪声 | 波特率不对；RS485 A/B 接反；GND 未共；线太长/无屏蔽 |
| 有帧但 CRC 全错 | CRC 范围是否漏掉地址字节；初值/字节序；是否把应答当请求 |
| 帧同步但 `len` 离谱 | 字节偏移错（把 seq 当 cmd）；§3.7 冲突尚未定案 |
| 写命令回 `FORBIDDEN` | 槽位忙/未就绪/上一条命令未完成；先 GET_STATUS 等 ready |
| 写命令回 `PARAM_ERROR` | index>3、speed>100、mode>3、length 过大 |
| 应答 seq 对不上 | 残留旧帧；先 flush 输入缓冲；或多机地址未分配 |
| 一会儿通一会儿断 | USB 供电/线材；RS485 终端电阻（长线才需要）；手机 OTG 电流 |

### 7.7 与「双主机」有关的安全抓包

- **旁路监听**（路径 A）时只接 A/B/GND，适配器 RX 常开，**不发送**；不要在打印机正在通信时运行会写串口的软件（包括串口助手的「自动发送」）。
- 如果必须看 TX/RX 双向，最稳的是**逻辑分析仪/RS485 监听器**夹在 A/B 上（纯接收），或让打印机主机记录日志。
- 不要试图「两台主机轮流说话」：半双工无仲裁，状态会分裂（§2.4）。

---

## 8. 风险与安全注意

**默认原则：第一轮只读；所有写操作先空载短距离；加热类功能必须满足下面的前置条件再调用。**

> **v0.2 更新（2026-11）**：v0.1 时「加热类功能暂不做」；v0.2 已实际接入 `DRYING` 的启动/停止（30–65 ℃、10–1440 分钟、二次确认、`auto_roll` 关闭），所以第 1 条不再禁止，而是「按条件做、全程值守」。`SET_DRY_TEMP` / `SET_DRY_POWER` 仍未接入。详见 [`DRYING.md`](./DRYING.md)。

1. ⚠️ **加热（v0.2 已实现 `DRYING`）**：`DRYING`/`SET_DRY_TEMP`/`SET_DRY_POWER` 会让 PTC 加热到 55–65 ℃ 并可能转动卷盘（`auto_roll`）。v0.2 只接了 `DRYING` 启动/停止，安全边界：目标温度 30–65 ℃（官方 `max_dryer_temperature = 65`）、时长 10–1440 分钟、UI 二次确认。即便如此仍必须：料盒盖关闭、箱内只放需要烘干的线材、周围通风无易燃物、**全程有人值守**、随时可断设备电源；无人看管时不要启动，USB 断开后设备不会自动停止。
2. ⚠️ **电机动作**：feed/rollback/assist 会真的转电机。要求：料盘/料路安全、手指远离齿轮、短长度低速先行（speed 10–20、length ≤20 mm）、随时可断电。**assist（mode 2/3）是持续动作**，无人值守时最危险——必须有看门狗定时 STOP；若 STOP 也无效，直接断 ACE 的 24V（不是拔 USB）。
3. ⚠️ **单主机**：不要把手机和打印机同时接到 ACE 的 RS-485 上（§2.4）。切换主机前先断开一方电源/总线。
4. ⚠️ **电气**：不接 ACE 的 5V；共地；断电接线；不要热插拔 6 针连接器（先断 24V）；注意防静电。
5. ⚠️ **固件**：不要尝试 OTA/升级（命令 5 IAP 路径未验证；固件只有 CRC 校验，没有签名验证，刷错可能变砖，且升级中槽位状态 6=`upgrading`）。本项目不涉及刷机。
6. ⚠️ **不要写 RFID**：命令 68/13 的写路径、标签格式未验证；误写可能污染标签。
7. ⚠️ **通信限速**：轮询 ≤ 2–4 Hz，命令串行；长时间高频轮询会占满总线、影响打印机（若共用）与设备散热；写操作后必须留出完成时间。
8. ⚠️ **异常恢复**：任何错误状态（`*_error`、`ASSIST_ERROR`、`STUCK_ERROR`）先停止动作、断电重启 ACE，再排查料路，不要反复硬发命令。
9. ⚠️ **保修/拆机**：拆壳、并线、探针都可能影响保修；旁路监听优先用成品分线器/夹线，不改设备。

---

## 附录 A：protobuf 消息字段速查

来源：Simon-CR 从固件描述符重建的 `protocol/ace2-v1.1.31.proto`；字段号/类型 ✅，字段名部分为社区命名 🔶。

```text
package ace_com;   // proto3 线格式，nanopb 编解码

message GenericResponse        { int32 code = 1; }
message InfoResponse           { string version = 1; string boot_version = 2; bool first_request = 3; }
message GetStatusRequest       { }                       // 空
message StatusResponse {
  uint32 status = 1;                 // 0 init / 1 ready / 2 busy / 3 upgrade
  DryStatus dry_status = 2;
  int32 temp = 3;                    // °C
  uint32 humidity = 4;               // %RH
  bool rfid1_enable = 5;
  bool rfid2_enable = 6;
  uint32 feed_assist_count = 7;
  uint32 cont_assist_time = 8;       // ms
  repeated SlotStatus slot_status = 9;   // max 4
}
message SlotStatus             { uint32 status = 1; uint32 filament = 2; }
message DryStatus              { uint32 status = 1; uint32 target_temp = 2; uint32 duration = 3; uint32 remain_time = 4; }
message GetTempResponse        { float f1 = 1; float f2 = 2; float f3 = 3; float f4 = 4; float f5 = 5; float f6 = 6; float f7 = 7; }
message FeedOrRollbackRequest  { uint32 index = 1; uint32 speed = 2; uint32 length = 3; uint32 mode = 4; }
message StopFeedOrRollbackRequest { uint32 index = 1; }
message UpdateSpeedRequest     { uint32 index = 1; uint32 speed = 2; }
message DryingRequest          { uint32 temp = 1; uint32 duration = 2; bool auto_roll = 3; }  // duration 分钟
message SetDryTempRequest      { uint32 temp = 1; }
message SetDryPowerRequest     { uint32 power = 1; }
message SetFanRequest          { uint32 speed = 1; bool fan1 = 2; bool fan2 = 3; }
message SetValveRequest        { bool valve1 = 1; bool valve2 = 2; }
message SetRfidEnableRequest   { uint32 index = 1; bool enable = 2; }
message SetFeedCheckRequest    { uint32 check_length = 1; uint32 error_length = 2; }
message FlashLedRequest        { uint32 components = 1; uint32 loop = 2; uint32 quick1 = 3; uint32 slow1 = 4; uint32 quick2 = 5; uint32 slow2 = 6; }
message GetSensorStateResponse { uint32 state = 1; }
message DiscoverDeviceResponse { uint32 uid1 = 1; uint32 uid2 = 2; uint32 uid3 = 3; }
message AssignDeviceIdRequest  { uint32 uid1 = 1; uint32 uid2 = 2; uint32 uid3 = 3; uint32 device_id = 4; }
message FeedInfo               { int32 steps = 1; int32 length = 2; int32 decoder = 3; }
message FeedInfoResponse       { repeated FeedInfo feed_info = 1; }   // 每槽位
message ColorInfo              { uint32 rgba = 1; }
message ExtruderTemp           { uint32 min = 1; uint32 max = 2; uint32 min_speed = 3; uint32 max_speed = 4; }
message HotbedTemp             { uint32 min = 1; uint32 max = 2; }
message FilamentInfoResponse   { /* 见 §5.7 */ }
```

载荷大小上限 100 字节；repeated 槽位最多 4；字符串字段在固件里是定长 `char[N]`（sku/type 各 20 字节，version 12 字节）。✅

## 附录 B：待验证清单

| # | 问题 | 影响 | 验证方法 |
|---|---|---|---|
| 1 | 字节 2 是地址还是 flags？ | 组帧正确性 | 抓包看应答字节 2 是否为 `0x80`/`0x81`；单机请求用 0x00 是否通 |
| 2 | seq 是 1 起递增还是可任意？应答是否回显？ | 多机/重发 | 连续多帧不同 seq，观察应答 |
| 3 | `GET_TEMP` 到底 6 个还是 7 个 float，字段语义 | 温度显示 | 抓包数 tag + 造温差 |
| 4 | `FeedInfo.length/steps/decoder` 单位与符号 | 进料进度显示 | 定长 feed 前后对比 |
| 5 | `FilamentInfoResponse` 字段 9/11 单位（长度 m / 余量 g？） | 料量显示 | 已知料盘对比 |
| 6 | mode 2/3 的 speed 是否有意义 | 助力控制 | 不同 speed 观察速度 |
| 7 | STOP 的完成时序/重试上限 | 停止可靠性 | 运行中 STOP，记录回 ready 时间 |
| 8 | 多机 DISCOVER/ASSIGN 细节（广播地址、应答延迟） | 多机支持 | 两台设备抓包 |
| 9 | 帧间最小间隔 | 稳定性 | 压测不同发送间隔 |
| 10 | 设备是否回显 TX 字节（适配器 echo） | 解析实现 | 对比发送与接收字节 |
| 11 | 官方线是否 100% 等同 CH343 CDC-ACM（RTS/DTR 要求） | Android 驱动 | 手机实机枚举 + 试 DTR 开关 |

## 附录 C：本文档使用约定与更新

- 帧示例为**合成**（按 §3 规则计算），仅用于对照编码器；实测抓包记录应另存为 `captures/*.jsonl`（后续任务）。
- 若后续实测推翻本文任何 🔶/❓ 结论，直接在该结论处改为 ✅ 并注明「实测日期 + 抓包文件」。
- 引用外部源码时优先引用 **commit/raw 链接**（仓库可能变动），本文已尽量给出 raw/固定路径。
