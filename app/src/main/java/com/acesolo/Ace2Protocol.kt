package com.acesolo

import java.io.ByteArrayOutputStream
import java.util.Locale

/**
 * Anycubic ACE2 Pro 协议层：CRC16/MCRF4XX、protobuf 编解码、RS-485 帧构建与解析。
 *
 * 参考实现（社区逆向，未在真机验证）：https://github.com/ducati1198/acepro-any-klipper
 *   - crc16_mcrf4xx：extras/ace/protocol.py
 *   - 命令表 / protobuf 工具 / 帧序列化 / 状态解析：extras/ace/protocol_ace2.py
 *
 * 帧格式（请求与应答相同，应答 flags 最高位为 1）：
 *   FF AA | flags(1) | id_lo | id_hi | cmd(1) | len(1) | payload(len) | crc16_LE(2) | FE
 *   - CRC16/MCRF4XX 覆盖 flags..payload 末尾（即 frame[2 .. 7+len)），小端附在 payload 之后
 *   - flags 低 7 位是总线设备地址；DISCOVER_DEVICE / ASSIGN_DEVICE_ID 用 flags=0 广播
 *   - id 是小端 u16，应答原样回填
 */
object Ace2Protocol {

    const val DEFAULT_BAUD = 230400

    // 帧同步/边界
    const val HEAD_0 = 0xFF
    const val HEAD_1 = 0xAA
    const val TAIL = 0xFE
    const val FLAG_RESPONSE = 0x80
    const val DEVICE_ID_MASK = 0x7F
    const val MAX_PAYLOAD = 100
    const val MIN_FRAME_LEN = 10
    const val DEFAULT_DEVICE_ID = 1

    /** 命令码（来自协议 proto 生成的命令表）。 */
    object Cmd {
        const val DISCOVER_DEVICE = 0
        const val ASSIGN_DEVICE_ID = 1
        const val IAP_UPGRADE = 2
        const val IAP_FIRMWARE = 3
        const val IAP_UPGRADE_FINISH = 4
        const val GET_STATUS = 6
        const val GET_INFO = 7
        const val FEED_OR_ROLLBACK = 8
        const val STOP_FEED_OR_ROLLBACK = 9
        const val UPDATE_SPEED = 10
        const val DRYING = 11
        const val SET_DRY_TEMP = 12
        const val GET_FILAMENT_INFO = 13
        const val SET_RFID_ENABLE = 14
        const val LINEAR_KEY_CALIBRATE = 15
        const val SET_FEED_CHECK = 19
        const val GET_TEMP = 64
        const val SET_DRY_POWER = 65
        const val SET_VALVE = 66
        const val FILAMENT_IDENTIFY = 68
        const val RFID_TEST = 69
        const val FLASH_LED = 70
        const val SET_FAN = 71
        const val SET_OUTPUT = 72
        const val GET_KEY_STATE = 73
        const val SET_PTC_TEMP = 75
        const val GET_FEED_INFO = 76
    }

    fun commandName(code: Int): String = when (code) {
        Cmd.DISCOVER_DEVICE -> "DISCOVER_DEVICE"
        Cmd.ASSIGN_DEVICE_ID -> "ASSIGN_DEVICE_ID"
        Cmd.IAP_UPGRADE -> "IAP_UPGRADE"
        Cmd.IAP_FIRMWARE -> "IAP_FIRMWARE"
        Cmd.IAP_UPGRADE_FINISH -> "IAP_UPGRADE_FINISH"
        Cmd.GET_STATUS -> "GET_STATUS"
        Cmd.GET_INFO -> "GET_INFO"
        Cmd.FEED_OR_ROLLBACK -> "FEED_OR_ROLLBACK"
        Cmd.STOP_FEED_OR_ROLLBACK -> "STOP_FEED_OR_ROLLBACK"
        Cmd.UPDATE_SPEED -> "UPDATE_SPEED"
        Cmd.DRYING -> "DRYING"
        Cmd.SET_DRY_TEMP -> "SET_DRY_TEMP"
        Cmd.GET_FILAMENT_INFO -> "GET_FILAMENT_INFO"
        Cmd.SET_RFID_ENABLE -> "SET_RFID_ENABLE"
        Cmd.LINEAR_KEY_CALIBRATE -> "LINEAR_KEY_CALIBRATE"
        Cmd.SET_FEED_CHECK -> "SET_FEED_CHECK"
        Cmd.GET_TEMP -> "GET_TEMP"
        Cmd.SET_DRY_POWER -> "SET_DRY_POWER"
        Cmd.SET_VALVE -> "SET_VALVE"
        Cmd.FILAMENT_IDENTIFY -> "FILAMENT_IDENTIFY"
        Cmd.RFID_TEST -> "RFID_TEST"
        Cmd.FLASH_LED -> "FLASH_LED"
        Cmd.SET_FAN -> "SET_FAN"
        Cmd.SET_OUTPUT -> "SET_OUTPUT"
        Cmd.GET_KEY_STATE -> "GET_KEY_STATE"
        Cmd.SET_PTC_TEMP -> "SET_PTC_TEMP"
        Cmd.GET_FEED_INFO -> "GET_FEED_INFO"
        else -> "CMD_$code"
    }

    /** 应答 code 文本。 */
    fun describeCode(code: Int): String = when (code) {
        0 -> "SUCCESS"
        1 -> "PARAM_ERROR"
        2 -> "FORBIDDEN"
        3 -> "FAILED"
        4 -> "ANTICOLLISION"
        5 -> "NOTAG"
        6 -> "READFAILED"
        400 -> "UNSUPPORTED"
        else -> "CODE_$code"
    }

    /** 工作状态码文本（0 init / 1 ready / 2 busy / 3 upgrading）。 */
    fun workStateName(code: Int): String = when (code) {
        0 -> "init"
        1 -> "ready"
        2 -> "busy"
        3 -> "upgrading"
        else -> "unknown"
    }

    /**
     * CRC-16/MCRF4XX（poly 0x8408 反射，init 0xFFFF，无最终异或）。
     * 黄金校验值：crc16("123456789".toByteArray()) == 0x6F91。
     */
    fun crc16(data: ByteArray): Int {
        var crc = 0xFFFF
        for (byte in data) {
            var mixed = (byte.toInt() and 0xFF) xor (crc and 0xFF)
            mixed = mixed xor ((mixed and 0x0F) shl 4)
            crc = ((mixed shl 8) or (crc ushr 8)) xor (mixed ushr 4) xor (mixed shl 3)
            crc = crc and 0xFFFF
        }
        return crc and 0xFFFF
    }

    /** 组一帧：flags + id + cmd + len + payload + crc(小端) + FE。 */
    fun buildFrame(flags: Int, requestId: Int, command: Int, payload: ByteArray): ByteArray {
        require(payload.size <= MAX_PAYLOAD) { "payload 超过上限 ${MAX_PAYLOAD}B：${payload.size}B" }
        val inner = ByteArrayOutputStream(7 + payload.size)
        inner.write(flags and 0xFF)
        inner.write(requestId and 0xFF)
        inner.write((requestId ushr 8) and 0xFF)
        inner.write(command and 0xFF)
        inner.write(payload.size and 0xFF)
        if (payload.isNotEmpty()) inner.write(payload, 0, payload.size)
        val innerBytes = inner.toByteArray()
        val crc = crc16(innerBytes)

        val out = ByteArrayOutputStream(innerBytes.size + 4)
        out.write(HEAD_0)
        out.write(HEAD_1)
        out.write(innerBytes, 0, innerBytes.size)
        out.write(crc and 0xFF)
        out.write((crc ushr 8) and 0xFF)
        out.write(TAIL)
        return out.toByteArray()
    }

    /** 请求帧的 flags：广播类命令为 0，其余为设备地址（1..127）。 */
    fun requestFlags(command: Int, deviceId: Int): Int = when (command) {
        Cmd.DISCOVER_DEVICE, Cmd.ASSIGN_DEVICE_ID -> 0
        else -> {
            require(deviceId in 1..DEVICE_ID_MASK) { "deviceId 必须在 1..$DEVICE_ID_MASK：$deviceId" }
            deviceId and DEVICE_ID_MASK
        }
    }

    fun buildRequest(
        requestId: Int,
        command: Int,
        payload: ByteArray = ByteArray(0),
        deviceId: Int = DEFAULT_DEVICE_ID
    ): ByteArray = buildFrame(requestFlags(command, deviceId), requestId, command, payload)

    // ------------------------------------------------------------------
    // 各命令的 protobuf payload（字段号与参考实现逐条对应）
    // ------------------------------------------------------------------

    fun discoverPayload(): ByteArray = ByteArray(0)

    fun assignIdPayload(uid: Ace2DeviceUid, deviceId: Int): ByteArray =
        PbMessage.uint32(1, uid.uid1) +
            PbMessage.uint32(2, uid.uid2) +
            PbMessage.uint32(3, uid.uid3) +
            PbMessage.uint32(4, deviceId.toLong())

    /** 启动烘干：temp=field1(℃)、duration=field2(分钟)、auto_roll=field3。 */
    fun dryingPayload(tempC: Int, durationMinutes: Int, autoRoll: Boolean = false): ByteArray =
        PbMessage.uint32(1, tempC.toLong()) +
            PbMessage.uint32(2, durationMinutes.toLong()) +
            PbMessage.bool(3, autoRoll)

    /** 停止烘干：temp=0、duration=0、auto_roll=false（全零 payload）。 */
    fun stopDryingPayload(): ByteArray = dryingPayload(0, 0, false)

    fun feedOrRollbackPayload(index: Int, speed: Int, length: Int, mode: Int): ByteArray =
        PbMessage.uint32(1, index.toLong()) +
            PbMessage.uint32(2, speed.toLong()) +
            PbMessage.uint32(3, length.toLong()) +
            PbMessage.uint32(4, mode.toLong())

    fun stopFeedPayload(index: Int): ByteArray = PbMessage.uint32(1, index.toLong())

    fun updateSpeedPayload(index: Int, speed: Int): ByteArray =
        PbMessage.uint32(1, index.toLong()) + PbMessage.uint32(2, speed.toLong())

    fun getFilamentInfoPayload(index: Int): ByteArray = PbMessage.uint32(1, index.toLong())

    fun setDryTempPayload(tempC: Int): ByteArray = PbMessage.uint32(1, tempC.toLong())

    fun setRfidEnablePayload(index: Int, enable: Boolean): ByteArray =
        PbMessage.uint32(1, index.toLong()) + PbMessage.bool(2, enable)

    // ------------------------------------------------------------------
    // 应答解析
    // ------------------------------------------------------------------

    fun parseDiscover(payload: ByteArray): Ace2DeviceUid {
        val msg = PbMessage.decode(payload)
        return Ace2DeviceUid(msg.varint(1), msg.varint(2), msg.varint(3))
    }

    fun parseInfo(payload: ByteArray): Ace2Info {
        val msg = PbMessage.decode(payload)
        return Ace2Info(
            version = msg.string(1),
            bootVersion = msg.string(2),
            firstRequest = msg.varint(3) != 0L
        )
    }

    fun parseGenericCode(payload: ByteArray): Int {
        if (payload.isEmpty()) return 0
        return PbMessage.decode(payload).varint(1).toInt()
    }

    /**
     * GET_STATUS 应答：
     *   field1 工作状态码，field2 烘干子消息（1 状态码 / 2 目标温度 / 3 时长秒 / 4 剩余秒），
     *   field3 当前温度，field4 湿度，field7 feed assist 计数，field9 槽位列表（1 状态码 / 2 有料标志）。
     */
    fun parseStatus(payload: ByteArray): Ace2Status {
        val msg = PbMessage.decode(payload)
        val dryerBytes = msg.bytes(2)
        val dryer = if (dryerBytes != null && dryerBytes.isNotEmpty()) {
            PbMessage.decode(dryerBytes)
        } else {
            PbMessage.decode(ByteArray(0))
        }
        val slots = ArrayList<Ace2SlotState>()
        val slotPayloads = msg.repeated(9)
        for (i in slotPayloads.indices) {
            val slot = PbMessage.decode(slotPayloads[i])
            slots.add(
                Ace2SlotState(
                    index = i,
                    stateCode = slot.varint(1).toInt(),
                    filamentState = slot.varint(2).toInt()
                )
            )
        }
        return Ace2Status(
            workStateCode = msg.varint(1).toInt(),
            tempC = msg.varint(3).toInt(),
            humidity = msg.varint(4).toInt(),
            dryer = Ace2DryerStatus(
                stateCode = dryer.varint(1).toInt(),
                targetTempC = dryer.varint(2).toInt(),
                durationSeconds = dryer.varint(3).toInt(),
                remainSeconds = dryer.varint(4).toInt()
            ),
            slots = slots,
            feedAssistCount = msg.varint(7).toInt()
        )
    }

    /** 烘干状态码 -> 语义（0 停止 / 1,2 烘干 / 3 停止 / 4,5 故障）。 */
    fun dryStateName(code: Int): String = when (code) {
        0 -> "stop"
        1, 2 -> "drying"
        3 -> "stop"
        4, 5 -> "error"
        else -> "unknown"
    }

    /** 烘干详情码 -> 语义。 */
    fun dryStateDetailName(code: Int): String = when (code) {
        0 -> "stop"
        1 -> "starting"
        2 -> "keeping"
        3 -> "stopping"
        4 -> "ptc_error"
        5 -> "ntc_error"
        else -> "unknown"
    }
}

/** DISCOVER_DEVICE 应答里的设备 UID。 */
class Ace2DeviceUid(val uid1: Long, val uid2: Long, val uid3: Long) {
    override fun toString(): String =
        String.format(Locale.US, "%08X-%08X-%08X", uid1, uid2, uid3)
}

/** GET_INFO 应答。 */
class Ace2Info(val version: String, val bootVersion: String, val firstRequest: Boolean)

/** 烘干子状态。 */
class Ace2DryerStatus(
    val stateCode: Int,
    val targetTempC: Int,
    val durationSeconds: Int,
    val remainSeconds: Int
)

/** 单个料槽状态。 */
class Ace2SlotState(val index: Int, val stateCode: Int, val filamentState: Int)

/** GET_STATUS 应答的解析结果。 */
class Ace2Status(
    val workStateCode: Int,
    val tempC: Int,
    val humidity: Int,
    val dryer: Ace2DryerStatus,
    val slots: List<Ace2SlotState>,
    val feedAssistCount: Int
)

/** 一帧解析结果。 */
class Ace2Frame(
    val flags: Int,
    val requestId: Int,
    val commandCode: Int,
    val payload: ByteArray
) {
    val isResponse: Boolean get() = (flags and Ace2Protocol.FLAG_RESPONSE) != 0
    val deviceId: Int get() = flags and Ace2Protocol.DEVICE_ID_MASK
    val commandName: String get() = Ace2Protocol.commandName(commandCode)
}

/**
 * 极简 protobuf 编解码（只覆盖 ACE2 用到的 varint / length-delimited / fixed32）。
 * 字段号与 wire type 与参考实现的 _pb_* 系列保持一致。
 */
class PbMessage private constructor(private val fields: Map<Int, List<PbField>>) {

    private class PbField(val wireType: Int, val varint: Long, val raw: ByteArray?)

    /** 取某个字段的第一个 varint 值（缺省 0）。 */
    fun varint(field: Int, default: Long = 0L): Long {
        val list = fields[field] ?: return default
        for (item in list) {
            if (item.wireType == 0) return item.varint
        }
        return default
    }

    /** 取某个字段的第一个 length-delimited 值。 */
    fun bytes(field: Int): ByteArray? {
        val list = fields[field] ?: return null
        for (item in list) {
            if (item.wireType == 2) return item.raw
        }
        return null
    }

    fun string(field: Int): String = bytes(field)?.toString(Charsets.UTF_8).orEmpty()

    fun repeated(field: Int): List<ByteArray> {
        val list = fields[field] ?: return emptyList()
        val out = ArrayList<ByteArray>(list.size)
        for (item in list) {
            if (item.wireType == 2) out.add(item.raw ?: ByteArray(0))
        }
        return out
    }

    fun subMessage(field: Int): PbMessage? {
        val raw = bytes(field) ?: return null
        return decode(raw)
    }

    companion object {

        fun empty(): PbMessage = PbMessage(emptyMap())

        fun decode(data: ByteArray): PbMessage {
            val map = LinkedHashMap<Int, MutableList<PbField>>()
            var pos = 0
            while (pos < data.size) {
                val (tag, afterTag) = readVarint(data, pos)
                if (afterTag < 0) break
                pos = afterTag
                val fieldNum = (tag ushr 3).toInt()
                val wireType = (tag and 0x07L).toInt()
                if (fieldNum <= 0) break
                when (wireType) {
                    0 -> {
                        val (value, afterValue) = readVarint(data, pos)
                        if (afterValue < 0) break
                        pos = afterValue
                        map.getOrPut(fieldNum) { ArrayList() }.add(PbField(0, value, null))
                    }
                    2 -> {
                        val (len, afterLen) = readVarint(data, pos)
                        if (afterLen < 0) break
                        val n = len.toInt()
                        if (n < 0 || afterLen + n > data.size) break
                        pos = afterLen + n
                        map.getOrPut(fieldNum) { ArrayList() }
                            .add(PbField(2, 0L, data.copyOfRange(afterLen, afterLen + n)))
                    }
                    5 -> {
                        if (pos + 4 > data.size) break
                        map.getOrPut(fieldNum) { ArrayList() }
                            .add(PbField(5, 0L, data.copyOfRange(pos, pos + 4)))
                        pos += 4
                    }
                    else -> break
                }
            }
            return PbMessage(map)
        }

        fun readVarint(data: ByteArray, start: Int): Pair<Long, Int> {
            var result = 0L
            var shift = 0
            var pos = start
            while (pos < data.size) {
                val b = data[pos].toInt() and 0xFF
                pos++
                result = result or ((b and 0x7F).toLong() shl shift)
                if ((b and 0x80) == 0) return Pair(result, pos)
                shift += 7
                if (shift > 63) return Pair(result, -1)
            }
            return Pair(result, -1)
        }

        fun encodeVarint(value: Long): ByteArray {
            require(value >= 0) { "varint 只支持非负值：$value" }
            val out = ByteArrayOutputStream(10)
            var v = value
            while (v > 0x7FL) {
                out.write(((v and 0x7FL) or 0x80L).toInt())
                v = v ushr 7
            }
            out.write((v and 0x7FL).toInt())
            return out.toByteArray()
        }

        fun uint32(field: Int, value: Long): ByteArray =
            encodeVarint((field.toLong() shl 3)) + encodeVarint(value)

        fun bool(field: Int, value: Boolean): ByteArray =
            encodeVarint((field.toLong() shl 3)) + encodeVarint(if (value) 1L else 0L)

        fun bytes(field: Int, value: ByteArray): ByteArray =
            encodeVarint((field.toLong() shl 3) or 2L) +
                encodeVarint(value.size.toLong()) +
                value

        fun string(field: Int, value: String): ByteArray =
            bytes(field, value.toByteArray(Charsets.UTF_8))
    }
}

/**
 * 帧解析器：把串口字节流拼成完整帧（自动重同步、CRC 校验、跨包半个帧头保留）。
 * 移植自参考实现 extract_responses 的 resync 策略。
 */
class Ace2FrameParser {

    private var buffer = ByteArray(0)

    fun reset() {
        buffer = ByteArray(0)
    }

    fun pendingBytes(): Int = buffer.size

    fun feed(chunk: ByteArray): List<Ace2Frame> {
        if (chunk.isEmpty()) return emptyList()
        var working = ByteArray(buffer.size + chunk.size)
        System.arraycopy(buffer, 0, working, 0, buffer.size)
        System.arraycopy(chunk, 0, working, buffer.size, chunk.size)

        val frames = ArrayList<Ace2Frame>(2)
        while (true) {
            if (working.size < Ace2Protocol.MIN_FRAME_LEN) break

            // 1) 对齐帧头 FF AA
            if (working[0] != HEAD_BYTE_0 || working[1] != HEAD_BYTE_1) {
                val idx = indexOfHead(working, 0)
                if (idx < 0) {
                    working = keepSplitHeader(working)
                    break
                }
                working = working.copyOfRange(idx, working.size)
                if (working.size < Ace2Protocol.MIN_FRAME_LEN) break
            }

            // 2) 长度字段
            val payloadLen = working[6].toInt() and 0xFF
            if (payloadLen > Ace2Protocol.MAX_PAYLOAD) {
                // 长度非法：丢 1 字节重新找帧头，避免无限等待
                working = working.copyOfRange(1, working.size)
                continue
            }
            val frameLen = Ace2Protocol.MIN_FRAME_LEN + payloadLen
            if (working.size < frameLen) break

            // 3) 帧尾校验
            if (working[frameLen - 1] != TAIL_BYTE) {
                val idx = indexOfHead(working, 1)
                working = if (idx < 0) keepSplitHeader(working) else working.copyOfRange(idx, working.size)
                continue
            }

            val frameBytes = working.copyOfRange(0, frameLen)
            working = working.copyOfRange(frameLen, working.size)

            // 4) CRC 校验（覆盖 flags..payload 末尾）
            val crcRx = (frameBytes[7 + payloadLen].toInt() and 0xFF) or
                ((frameBytes[8 + payloadLen].toInt() and 0xFF) shl 8)
            val crcCalc = Ace2Protocol.crc16(frameBytes.copyOfRange(2, 7 + payloadLen))
            if (crcRx != crcCalc) continue

            val flags = frameBytes[2].toInt() and 0xFF
            val requestId = (frameBytes[3].toInt() and 0xFF) or
                ((frameBytes[4].toInt() and 0xFF) shl 8)
            val command = frameBytes[5].toInt() and 0xFF
            val payload = frameBytes.copyOfRange(7, 7 + payloadLen)
            frames.add(Ace2Frame(flags, requestId, command, payload))
        }

        // 防止异常数据把缓冲撑大
        buffer = if (working.size > MAX_BUFFER_BYTES) ByteArray(0) else working
        return frames
    }

    private fun indexOfHead(bytes: ByteArray, from: Int): Int {
        var i = from
        while (i + 1 < bytes.size) {
            if (bytes[i] == HEAD_BYTE_0 && bytes[i + 1] == HEAD_BYTE_1) return i
            i++
        }
        return -1
    }

    /** 重同步时不要把跨包的半个帧头（末尾的 0xFF）一起丢掉。 */
    private fun keepSplitHeader(bytes: ByteArray): ByteArray {
        if (bytes.isNotEmpty() && bytes[bytes.size - 1] == HEAD_BYTE_0) {
            return byteArrayOf(HEAD_BYTE_0)
        }
        return ByteArray(0)
    }

    private companion object {
        val HEAD_BYTE_0: Byte = Ace2Protocol.HEAD_0.toByte()
        val HEAD_BYTE_1: Byte = Ace2Protocol.HEAD_1.toByte()
        val TAIL_BYTE: Byte = Ace2Protocol.TAIL.toByte()
        const val MAX_BUFFER_BYTES = 1024
    }
}
