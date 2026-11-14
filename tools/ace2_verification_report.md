# ACE2 协议实现独立验证报告

- 被验证对象（只读，未修改）：`app/src/main/java/com/acesolo/Ace2Protocol.kt`（560 行，md5 `96d11a5572173c27165d01d8881463d1`）、`Ace2Client.kt`（472 行，md5 `6dc138cbd14b4326fa2f7fdd73eb4f40`）
- 验证者：sign-dev（独立于 Lead）
- 验证时间：2026-11-14
- 参考实现（唯一权威判据）：`/storage/emulated/0/dsh/.acesolo-ci/ref/extras_ace_protocol.py`、`extras_ace_protocol_ace2.py`，上游 <https://github.com/ducati1198/acepro-any-klipper>

## 结论（TL;DR）

**协议编码/解码层与参考实现字节级一致：120 项检查全部 PASS，0 FAIL。**

- CRC16/MCRF4XX 黄金值 `CRC(b"123456789") == 0x6F91` 复现 ✅
- 12 条请求帧（discover / assign / get_status / get_info / 烘干启停 / 边界 / 喂料 / 换温）与参考实现逐字节相同 ✅
- 5 组应答解析（状态、固件信息、UID、GenericResponse code=0/2）全部一致 ✅
- 7 组帧解析健壮性用例（正常、垃圾前缀、半截帧、CRC 错、跨包半个帧头、双帧连读、flags/device_id）全部一致 ✅

**未发现需要修改 Kotlin 的协议缺陷。**另有 4 条会话层/防御性建议（见「风险清单」），均为低风险增强项，不影响与参考实现的一致性结论。

## 交付物

| 文件 | 作用 |
| --- | --- |
| `tools/ace2_vectors.py` | 仅移植参考实现逻辑（不看 Kotlin）生成黄金向量；`--check` 校验 JSON 未漂移 |
| `tools/ace2_vectors.json` | 黄金向量（5 CRC + 12 请求 + 5 应答 + 7 健壮性） |
| `tools/verify_ace2.py` | 逐行转写 Kotlin（每条注释标 Kotlin 行号）→ 与黄金向量逐字节比对 + 10 高风险点审查；有 FAIL 时退出码 1 |
| `tools/ace2_verification_report.md` | 本报告 |

复现：

```bash
cd /storage/emulated/0/dsh/AceSolo
python3 tools/ace2_vectors.py            # 重新生成 JSON，打印 0x6F91
python3 tools/ace2_vectors.py --check    # JSON 与生成器一致
python3 tools/verify_ace2.py             # 120 PASS / 0 FAIL，退出码 0
```

## 方法（避免「同一个 bug 抄两遍」）

1. **第一步只读参考实现**：先按参考仓库的 `crc16_mcrf4xx`、`_pb_*`、`_encode_request_payload`、`_decode_response_payload`、`serialize_request_frame`、`extract_responses`、烘干 builder 独立移植成 `tools/ace2_vectors.py`，生成 JSON。此阶段刻意不读 Kotlin。
2. **第二步才读 Kotlin**：把 `Ace2Protocol.kt` 逐行转写为 Python（`k_*` 函数，注释标 Kotlin 行号），与 JSON 做字节级/字段级比对；`Ace2Client.kt` 只做静态审查（无法在主机上跑 Handler）。
3. **额外交叉验证（提高黄金向量可信度）**：把参考仓库两个模块拼成临时包后**直接执行参考实现**，对全部向量再比对一遍——12/12 帧、5/5 CRC、5/5 应答解码、7/7 健壮性用例与 JSON 完全一致（临时目录已清理）。因此「向量本身写错」的概率已被排除。

## 黄金向量覆盖（任务 a–k）

| 用例 | 期望（关键字节） | Kotlin 结果 |
| --- | --- | --- |
| a CRC 已知值 | `0x6F91` | PASS |
| b CRC 空数据/`00`/`FF`/`FF AA` | `0xFFFF / 0x0F87 / 0x00FF / 0x0528` | PASS（5/5） |
| c DISCOVER_DEVICE id=1 空 payload | `FF AA 00 01 00 00 00 33 2C FE`（10B） | PASS |
| d ASSIGN uid1..3 + device_id=1 | `FF AA 00 02 00 01 14 … 23 8A FE`（30B） | PASS |
| e GET_STATUS id=3 | `FF AA 01 03 00 06 00 D1 4A FE` | PASS |
| f GET_INFO id=4 | `FF AA 01 04 00 07 00 28 04 FE` | PASS |
| g 启动烘干 55℃/240min/false | payload `08 37 10 F0 01 18 00` | PASS |
| h 停止烘干 0/0/false | payload `08 00 10 00 18 00`（显式写零，不做 proto3 省略） | PASS |
| i 边界 30/10 与 65/1440 | `08 1E 10 0A 18 00` / `08 41 10 A0 0B 18 00` | PASS |
| j GET_STATUS 应答解析 | work=ready、dry_state=2、target=55、duration=240min、remain=1320s、temp=52、hum=38、2 槽位（0=ready/1=feeding，filament=3） | PASS（逐字段） |
| k 健壮性 | 正常/垃圾前缀/半截帧缓冲/CRC 错丢弃且后续帧照常/跨包 `FF` 保留/双帧连读 | PASS（7/7） |

附加向量：GET_INFO 字符串字段（wire type 2）、DISCOVER 应答 UID、GenericResponse code=0（SUCCESS）与 code=2（FORBIDDEN）。

## 10 个高风险点逐条结论

| # | 审查点 | 结论 | 依据 |
| --- | --- | --- | --- |
| 1 | CRC 覆盖 `flags..payload` 末尾，小端追加 | **PASS** | `Ace2Protocol.kt:521` `crc16(frameBytes.copyOfRange(2, 7+payloadLen))`；`:149-150` 小端写；全部帧字节级一致 |
| 2 | id 小端，`id_lo` 紧跟 flags | **PASS** | `:137-138`；向量 id=10→`0A 00`、id=1→`01 00` |
| 3 | flags：DISCOVER/ASSIGN=0，其余 `device_id&0x7F`，且 1..127 | **PASS** | `:156-162`（越界 `require` 抛异常）；`:27-28` 常量 |
| 4 | len 1 字节且 ≤100；最小帧 10 字节 | **PASS**（附注） | `:134` require；`:140` 写 len；payload=100→帧长 110，payload=101 抛异常。附注：参考实现不拦 len>100，Kotlin 改为丢 1 字节重同步（更安全）；若将来做 IAP 固件分片（payload 可能 >100）需放开该上限 |
| 5 | protobuf wire type：uint32/bool=0，string/bytes=2 | **PASS** | `:447-459`；请求 payload 字段 tag 全为 0，GET_INFO 字符串为 2 |
| 6 | 烘干字段 1=temp/2=duration/3=auto_roll；停止全 0；请求时长单位分钟 | **PASS** | `:184-190`；停止 payload `08 00 10 00 18 00`；`:236` 状态 field3/4 为秒，UI（`MainActivity.kt:337`）用 `/60` 显示分钟 |
| 7 | 状态字段 1/2/3/4/9 + duration 秒/60 | **PASS** | `:238-271`；向量 j 全部字段核对通过（duration 14400s→240min） |
| 8 | 应答 `0x80` 位、device_id 提取 | **PASS**（有建议） | `:331-332` `isResponse`/`deviceId`；解析器保留 flags。**但** `Ace2Client.handleFrame` 只用 id 匹配、未校验 `isResponse`，见风险 R1 |
| 9 | resync 保留尾部 `0xFF`；CRC 错不阻塞后续帧 | **PASS** | `:547-552` keepSplitHeader；`:522` CRC 错 `continue`；k 用例验证 |
| 10 | 会话层：id 递增/匹配、超时重试、单在途请求、状态机、断线清理、回调切主线程 | **PASS** | `Ace2Client.kt:124-135` 串口数据 `handler.post` 切主线程（不在串口线程碰 View）；`:230-259` 单 inFlight + 队列；`:261-274` 超时/重试；`:236-237` id 递增并在 0xFFFF 回绕；`:103-121,294-305` 断线清理；2s 轮询、1200ms/2000ms 超时 |

## 风险清单（按严重度，均非协议字节差异；未改动 Kotlin）

| 严重度 | 位置 | 问题 | 建议 |
| --- | --- | --- | --- |
| 低 | `Ace2Client.kt:276-292` | `handleFrame` 只用 `requestId == inFlightId` 匹配，**未校验 `frame.isResponse`**。若 USB 适配器回显自己发出的帧（flags 无 0x80，id 恰好等于 inFlightId），会被误当作应答：DISCOVER 会解析出全 0 UID、ASSIGN 会把 uid1 当 code 导致误报错 | 匹配条件加 `frame.isResponse &&`（`Ace2Frame.isResponse` 已实现，`Ace2Protocol.kt:331`） |
| 低 | `Ace2Client.kt:280-290` | 同上位置未校验 `frame.commandCode == request.command` | 一并校验命令码，可挡掉同 id 串帧 |
| 低 | `Ace2Client.kt:250-256` | `writeBytes` 返回 false 时直接 `return`，未 `pump()` 队列、也未启动超时；极端情况下队列停摆（实际串口关闭会走 `onSerialDisconnected → resetSession` 清队列，风险低） | 失败分支补 `pump()` 或清队列 |
| 提示 | `Ace2Protocol.kt:532-533` | 解析缓冲 >1024B 直接丢弃；参考实现无该上限 | 对 ≤100B 的 ACE2 帧无影响，仅记录差异 |
| 提示 | 未实现 | Kotlin 只解析 DISCOVER/GET_INFO/GenericResponse/GET_STATUS；未实现 GET_FILAMENT_INFO、GET_FEED_INFO 等参考实现里的解码（当前 MVP 也不需要） | 后续要用再移植，参考实现 `_decode_response_payload` 已有完整实现 |

## 一致性证据摘要

- 请求帧：12/12 与参考实现逐字节相同（含 discover/assign 广播 flags=0、地址命令 flags=1、id 小端）。
- 应答解析：5/5 与参考实现字段值相同；状态向量逐字段一致（含 2 个槽位的 state_code/filament/rfid）。
- 健壮性：7/7 行为相同（`Resync: skipping 4 bytes` / `Resync: dropped junk (10 bytes)` / `Invalid CRC` 三个 notice 语义也一致）。
- 静态检查：`Ace2Client` 的串口回调均经 `handler.post` 到主线程，符合「不得在串口线程碰 View」的要求。

## 限制

- 未上真机、未编译（本机无 Android SDK），只验证协议逻辑与会话层静态结构。
- 参考实现自述「社区逆向、未在真机验证」；本报告只证明「Kotlin 与参考实现一致」，不证明「参考实现与真实 ACE2 固件一致」——后者需要实测抓包。
- 未覆盖 IAP/固件升级、RFID、除湿以外的其他命令编码。
