package com.acesolo

import android.os.Handler
import android.os.Looper

/**
 * ACE2 会话层：发现设备 → 分配总线地址 → 查询固件 → 周期轮询状态，并提供烘干启停。
 *
 * 设计约束（RS-485 半双工单主站）：
 *  - 同一时刻只允许一个在途请求（inFlight），其余排队；应答按 id 匹配
 *  - 每次写帧后启动超时计时，超时按 maxAttempts 重试
 *  - 所有状态/回调都在主线程（Handler(Looper.getMainLooper())）触发
 *
 * 注意：地址分配在设备上电后不持久，每次串口连接都要重新发现 + 分配（device_id 固定为 1）。
 */
class Ace2Client(
    private val writeBytes: (ByteArray) -> Boolean,
    private val log: (String) -> Unit
) {

    enum class State { IDLE, DISCOVERING, ASSIGNING, READY, ERROR }

    interface Listener {
        fun onStateChanged(state: State, detail: String)
        fun onInfo(info: Ace2Info)
        fun onStatus(status: Ace2Status)
    }

    var listener: Listener? = null

    var state: State = State.IDLE
        private set

    var firmwareVersion: String = ""
        private set

    var deviceUid: Ace2DeviceUid? = null
        private set

    val isReady: Boolean
        get() = state == State.READY

    private class Request(
        val command: Int,
        val label: String,
        val payload: ByteArray,
        val maxAttempts: Int,
        val timeoutMs: Long,
        val onResult: (Ace2Frame?) -> Unit
    ) {
        var attempts = 0
    }

    private val handler = Handler(Looper.getMainLooper())
    private val parser = Ace2FrameParser()
    private val queue = ArrayDeque<Request>()

    private var inFlight: Request? = null
    private var inFlightId = 0
    private var nextRequestId = 1
    private var shutDown = false
    private var statusMisses = 0
    private var pollScheduled = false

    /** 最近一次实际写入总线的帧：用于识别半双工转换器把 TX 回显到 RX 的情况。 */
    private var lastTxBytes: ByteArray? = null

    /** 最近一次成功下发的烘干参数，用于状态里目标温度缺失时兜底显示。 */
    var lastDryingTempC: Int = 0
        private set
    var lastDryingMinutes: Int = 0
        private set

    private val timeoutRunnable = Runnable { onRequestTimeout() }

    private val pollRunnable = object : Runnable {
        override fun run() {
            pollScheduled = false
            if (shutDown || !isReady) return
            enqueueStatusRequest(force = false)
            schedulePoll()
        }
    }

    // ------------------------------------------------------------------
    // 生命周期
    // ------------------------------------------------------------------

    fun onSerialConnected() {
        if (shutDown) return
        handler.post {
            resetSession()
            setState(State.DISCOVERING, "开始发现 ACE2 设备（DISCOVER_DEVICE）")
            enqueue(
                Request(
                    Ace2Protocol.Cmd.DISCOVER_DEVICE,
                    "DISCOVER_DEVICE",
                    Ace2Protocol.discoverPayload(),
                    DISCOVER_ATTEMPTS,
                    REQUEST_TIMEOUT_MS
                ) { frame -> handleDiscoverResponse(frame) }
            )
        }
    }

    fun onSerialDisconnected() {
        handler.post {
            stopPolling()
            resetSession()
            setState(State.IDLE, "串口已断开")
        }
    }

    fun shutdown() {
        shutDown = true
        handler.post {
            stopPolling()
            queue.clear()
            inFlight = null
            inFlightId = 0
            handler.removeCallbacksAndMessages(null)
            parser.reset()
        }
    }

    /** 串口线程回调：喂字节流给帧解析器（内部切主线程处理）。 */
    fun onSerialData(data: ByteArray) {
        if (shutDown || data.isEmpty()) return
        handler.post {
            val frames: List<Ace2Frame> = try {
                parser.feed(data)
            } catch (t: Throwable) {
                log("!! 帧解析异常：${t.message}")
                emptyList()
            }
            for (frame in frames) handleFrame(frame)
        }
    }

    // ------------------------------------------------------------------
    // 对外命令
    // ------------------------------------------------------------------

    fun requestStatus() {
        handler.post {
            if (!isReady) {
                log("… 尚未就绪，忽略状态查询（当前 ${state.name}）")
                return@post
            }
            enqueueStatusRequest(force = true)
        }
    }

    fun startDrying(
        tempC: Int,
        durationMinutes: Int,
        onDone: (Boolean, String) -> Unit = { _, _ -> }
    ) {
        handler.post {
            if (!isReady) {
                onDone(false, "ACE2 尚未就绪")
                return@post
            }
            lastDryingTempC = tempC
            lastDryingMinutes = durationMinutes
            log("→ 启动烘干：${tempC}℃ / ${durationMinutes} 分钟")
            enqueue(
                Request(
                    Ace2Protocol.Cmd.DRYING,
                    "DRYING(start ${tempC}C ${durationMinutes}min)",
                    Ace2Protocol.dryingPayload(tempC, durationMinutes),
                    DRYING_ATTEMPTS,
                    DRYING_TIMEOUT_MS
                ) { frame ->
                    if (frame == null) {
                        onDone(false, "设备无应答（超时）")
                    } else {
                        val code = Ace2Protocol.parseGenericCode(frame.payload)
                        if (code == 0) {
                            onDone(true, "已启动烘干")
                        } else {
                            onDone(false, "code=$code ${Ace2Protocol.describeCode(code)}")
                        }
                        scheduleStatusSoon()
                    }
                }
            )
        }
    }

    fun stopDrying(onDone: (Boolean, String) -> Unit = { _, _ -> }) {
        handler.post {
            if (!isReady) {
                onDone(false, "ACE2 尚未就绪")
                return@post
            }
            log("→ 停止烘干")
            enqueue(
                Request(
                    Ace2Protocol.Cmd.DRYING,
                    "DRYING(stop)",
                    Ace2Protocol.stopDryingPayload(),
                    DRYING_ATTEMPTS,
                    DRYING_TIMEOUT_MS
                ) { frame ->
                    if (frame == null) {
                        onDone(false, "设备无应答（超时）")
                    } else {
                        val code = Ace2Protocol.parseGenericCode(frame.payload)
                        if (code == 0) {
                            onDone(true, "已停止烘干")
                            lastDryingTempC = 0
                            lastDryingMinutes = 0
                        } else {
                            onDone(false, "code=$code ${Ace2Protocol.describeCode(code)}")
                        }
                        scheduleStatusSoon()
                    }
                }
            )
        }
    }

    // ------------------------------------------------------------------
    // 内部：请求排队与超时
    // ------------------------------------------------------------------

    private fun enqueue(request: Request) {
        queue.addLast(request)
        pump()
    }

    private fun pump() {
        if (shutDown || inFlight != null) return
        if (queue.isEmpty()) return
        val request = queue.removeFirst()
        request.attempts++

        val id = nextRequestId
        nextRequestId = if (nextRequestId >= 0xFFFF) 1 else nextRequestId + 1
        inFlight = request
        inFlightId = id

        val bytes = Ace2Protocol.buildRequest(id, request.command, request.payload)
        lastTxBytes = bytes
        log("→ ${request.label} #$id (${request.attempts}/${request.maxAttempts}) ${UsbSerialManager.toHex(bytes)}")

        val ok = try {
            writeBytes(bytes)
        } catch (t: Throwable) {
            log("!! 写串口异常：${t.message}")
            false
        }
        if (!ok) {
            inFlight = null
            inFlightId = 0
            log("!! ${request.label} 发送失败（串口未打开）")
            request.onResult(null)
            pump()
            return
        }
        handler.removeCallbacks(timeoutRunnable)
        handler.postDelayed(timeoutRunnable, request.timeoutMs)
    }

    private fun onRequestTimeout() {
        val request = inFlight ?: return
        inFlight = null
        inFlightId = 0
        if (request.attempts < request.maxAttempts) {
            log("… ${request.label} 超时，重试（${request.attempts}/${request.maxAttempts}）")
            queue.addFirst(request)
            pump()
        } else {
            log("!! ${request.label} 超时，放弃（已尝试 ${request.maxAttempts} 次）")
            request.onResult(null)
            pump()
        }
    }

    private fun handleFrame(frame: Ace2Frame) {
        log("← ${frame.commandName} #${frame.requestId} flags=0x${hex2(frame.flags)} ${UsbSerialManager.toHex(frame.payload)}")

        val request = inFlight
        if (request != null && frame.requestId == inFlightId && frame.commandCode == request.command) {
            if (!frame.isResponse && isEchoOfLastTx(frame)) {
                // 半双工转换器把本机刚发出的请求回显到 RX：不是应答，继续等真正的响应
                log("… 忽略串口回显帧（flags 无 0x80 且与 TX 完全一致）")
                return
            }
            inFlight = null
            inFlightId = 0
            handler.removeCallbacks(timeoutRunnable)
            try {
                request.onResult(frame)
            } catch (t: Throwable) {
                log("!! 处理应答异常：${t.message}")
            }
            pump()
        }
        // 未匹配的帧（迟到应答/广播应答/命令不符）忽略：单设备场景下重试会拿到新的 id 应答
    }

    /** 判断一帧是否就是把 [lastTxBytes] 原样回显回来的请求帧。 */
    private fun isEchoOfLastTx(frame: Ace2Frame): Boolean {
        val tx = lastTxBytes ?: return false
        val rebuilt = Ace2Protocol.buildFrame(frame.flags, frame.requestId, frame.commandCode, frame.payload)
        return rebuilt.contentEquals(tx)
    }

    private fun resetSession() {
        parser.reset()
        queue.clear()
        inFlight = null
        inFlightId = 0
        handler.removeCallbacks(timeoutRunnable)
        statusMisses = 0
        deviceUid = null
        firmwareVersion = ""
        lastDryingTempC = 0
        lastDryingMinutes = 0
        lastTxBytes = null
    }

    private fun setState(newState: State, detail: String) {
        state = newState
        log("状态：${newState.name} · $detail")
        listener?.onStateChanged(newState, detail)
        if (newState != State.READY) stopPolling()
    }

    // ------------------------------------------------------------------
    // 内部：握手
    // ------------------------------------------------------------------

    private fun handleDiscoverResponse(frame: Ace2Frame?) {
        if (frame == null) {
            setState(
                State.ERROR,
                "发现失败：设备无应答（检查 A/B 线序、波特率 230400、是否已被其他主机占用）"
            )
            return
        }
        val uid = try {
            Ace2Protocol.parseDiscover(frame.payload)
        } catch (t: Throwable) {
            log("!! 发现应答解析失败：${t.message}")
            null
        }
        if (uid == null) {
            setState(State.ERROR, "发现应答解析失败")
            return
        }
        deviceUid = uid
        setState(State.ASSIGNING, "发现设备 UID=$uid，分配总线地址 ${Ace2Protocol.DEFAULT_DEVICE_ID}")
        enqueue(
            Request(
                Ace2Protocol.Cmd.ASSIGN_DEVICE_ID,
                "ASSIGN_DEVICE_ID",
                Ace2Protocol.assignIdPayload(uid, Ace2Protocol.DEFAULT_DEVICE_ID),
                ASSIGN_ATTEMPTS,
                REQUEST_TIMEOUT_MS
            ) { assignFrame -> handleAssignResponse(assignFrame) }
        )
    }

    private fun handleAssignResponse(frame: Ace2Frame?) {
        if (frame == null) {
            setState(State.ERROR, "分配设备地址失败：设备无应答")
            return
        }
        val code = Ace2Protocol.parseGenericCode(frame.payload)
        if (code != 0) {
            setState(
                State.ERROR,
                "分配设备地址被拒绝：code=$code ${Ace2Protocol.describeCode(code)}"
            )
            return
        }
        log("设备地址分配成功：device_id=${Ace2Protocol.DEFAULT_DEVICE_ID}")
        requestInfoInternal()
    }

    private fun requestInfoInternal() {
        enqueue(
            Request(
                Ace2Protocol.Cmd.GET_INFO,
                "GET_INFO",
                ByteArray(0),
                INFO_ATTEMPTS,
                REQUEST_TIMEOUT_MS
            ) { frame ->
                if (frame != null) {
                    val info = try {
                        Ace2Protocol.parseInfo(frame.payload)
                    } catch (t: Throwable) {
                        log("!! GET_INFO 解析失败：${t.message}")
                        null
                    }
                    if (info != null) {
                        firmwareVersion = info.version
                        listener?.onInfo(info)
                        log("固件版本：${info.version}（boot ${info.bootVersion}）")
                    }
                } else {
                    log("… GET_INFO 无应答，继续进入就绪状态")
                }
                setState(State.READY, "ACE2 已就绪，开始周期查询状态")
                startPolling()
                scheduleStatusSoon()
            }
        )
    }

    // ------------------------------------------------------------------
    // 内部：状态轮询
    // ------------------------------------------------------------------

    private fun startPolling() {
        pollScheduled = false
        schedulePoll()
    }

    private fun schedulePoll() {
        if (shutDown || pollScheduled) return
        pollScheduled = true
        handler.postDelayed(pollRunnable, POLL_INTERVAL_MS)
    }

    private fun stopPolling() {
        pollScheduled = false
        handler.removeCallbacks(pollRunnable)
    }

    private fun scheduleStatusSoon() {
        handler.postDelayed({
            if (!shutDown && isReady) enqueueStatusRequest(force = true)
        }, STATUS_SOON_DELAY_MS)
    }

    private fun enqueueStatusRequest(force: Boolean) {
        if (!isReady) return
        if (!force && (inFlight != null || queue.isNotEmpty())) return
        enqueue(
            Request(
                Ace2Protocol.Cmd.GET_STATUS,
                "GET_STATUS",
                ByteArray(0),
                if (force) STATUS_ATTEMPTS_FORCED else STATUS_ATTEMPTS_POLL,
                REQUEST_TIMEOUT_MS
            ) { frame ->
                if (frame == null) {
                    statusMisses++
                    if (statusMisses == 3) {
                        log("… 连续 $statusMisses 次状态查询无应答，检查连线与设备供电")
                    } else if (statusMisses >= 6) {
                        setState(State.ERROR, "状态查询连续无应答，请检查串口连接")
                    }
                } else {
                    statusMisses = 0
                    val status = try {
                        Ace2Protocol.parseStatus(frame.payload)
                    } catch (t: Throwable) {
                        log("!! 状态解析失败：${t.message}")
                        null
                    }
                    if (status != null) {
                        listener?.onStatus(status)
                    }
                }
            }
        )
    }

    private fun hex2(value: Int): String =
        String.format(java.util.Locale.US, "%02X", value and 0xFF)

    private companion object {
        const val REQUEST_TIMEOUT_MS = 1200L
        const val DRYING_TIMEOUT_MS = 2000L
        const val POLL_INTERVAL_MS = 2000L
        const val STATUS_SOON_DELAY_MS = 500L
        const val DISCOVER_ATTEMPTS = 3
        const val ASSIGN_ATTEMPTS = 3
        const val INFO_ATTEMPTS = 2
        const val DRYING_ATTEMPTS = 3
        const val STATUS_ATTEMPTS_FORCED = 2
        const val STATUS_ATTEMPTS_POLL = 1
    }
}
