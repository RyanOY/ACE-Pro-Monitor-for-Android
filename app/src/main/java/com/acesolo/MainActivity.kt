package com.acesolo

import android.app.AlertDialog
import android.content.Intent
import android.hardware.usb.UsbManager
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * AceSolo v0.2：USB 串口 → ACE2 Pro 协议（RS-485 230400 8N1）。
 *
 * 主功能：耗材烘干（启动 / 停止 / 状态轮询），保留原始 hex 收发与串口日志。
 * 安全：温度限制 30–65 ℃、时长 10–1440 分钟，启动前二次确认。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var usb: UsbSerialManager
    private lateinit var client: Ace2Client

    private lateinit var tvLog: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvAceStatus: TextView
    private lateinit var tvDryerInfo: TextView
    private lateinit var tvDryHint: TextView
    private lateinit var etHex: EditText
    private lateinit var etDryTemp: EditText
    private lateinit var etDryDuration: EditText
    private lateinit var spinnerBaud: Spinner
    private lateinit var scrollLog: ScrollView
    private lateinit var btnConnect: MaterialButton
    private lateinit var btnSend: MaterialButton
    private lateinit var btnClear: MaterialButton
    private lateinit var btnQueryStatus: MaterialButton
    private lateinit var btnStartDry: MaterialButton
    private lateinit var btnStopDry: MaterialButton

    private val baudRates = intArrayOf(115200, 230400, 921600, 250000, 57600)
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
    private val logLines = ArrayDeque<String>()
    private val maxLogLines = 800

    /** 增量追加计数：每 200 条才做一次超限裁剪，避免每帧全量重排（230400 下会 ANR）。 */
    private var appendCount = 0

    /** 客户端会话是否已随串口打开而启动（避免重复握手）。 */
    private var clientSessionOpen = false

    private var firmwareVersion = ""

    // 烘干安全边界（与参考实现 Klipper 宏 ACE_START_DRYING 对齐，上限取配置 max_dryer_temperature=65）
    private val minDryTemp = 30
    private val maxDryTemp = 65
    private val minDryMinutes = 10
    private val maxDryMinutes = 1440

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvLog = findViewById(R.id.tvLog)
        tvStatus = findViewById(R.id.tvStatus)
        tvAceStatus = findViewById(R.id.tvAceStatus)
        tvDryerInfo = findViewById(R.id.tvDryerInfo)
        tvDryHint = findViewById(R.id.tvDryHint)
        etHex = findViewById(R.id.etHex)
        etDryTemp = findViewById(R.id.etDryTemp)
        etDryDuration = findViewById(R.id.etDryDuration)
        spinnerBaud = findViewById(R.id.spinnerBaud)
        scrollLog = findViewById(R.id.scrollLog)
        btnConnect = findViewById(R.id.btnConnect)
        btnSend = findViewById(R.id.btnSend)
        btnClear = findViewById(R.id.btnClear)
        btnQueryStatus = findViewById(R.id.btnQueryStatus)
        btnStartDry = findViewById(R.id.btnStartDry)
        btnStopDry = findViewById(R.id.btnStopDry)

        setupBaudSpinner()
        if (etDryTemp.text.isNullOrBlank()) etDryTemp.setText("50")
        if (etDryDuration.text.isNullOrBlank()) etDryDuration.setText("240")

        usb = UsbSerialManager(
            context = this,
            onLog = { line -> appendLog(line) },
            onConnectionChanged = {
                refreshStatus()
                syncClientWithSerial()
            }
        )
        usb.onDataReceived = { data -> if (::client.isInitialized) client.onSerialData(data) }

        client = Ace2Client(
            writeBytes = { bytes ->
                if (usb.isOpen) {
                    usb.send(bytes)
                    true
                } else {
                    false
                }
            },
            log = { line -> appendLog(line) }
        )
        client.listener = object : Ace2Client.Listener {
            override fun onStateChanged(state: Ace2Client.State, detail: String) {
                renderAceState(state, detail)
                refreshStatus()
                updateControls()
            }

            override fun onInfo(info: Ace2Info) {
                firmwareVersion = info.version
                renderAceState(client.state, "固件 ${info.version}")
            }

            override fun onStatus(status: Ace2Status) {
                renderStatus(status)
            }
        }

        btnConnect.setOnClickListener { toggleConnection() }
        btnSend.setOnClickListener { sendHex() }
        btnClear.setOnClickListener { clearLog() }
        btnQueryStatus.setOnClickListener { queryStatus() }
        btnStartDry.setOnClickListener { startDrying() }
        btnStopDry.setOnClickListener { stopDrying() }
        etHex.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendHex()
                true
            } else {
                false
            }
        }

        appendLog("AceSolo v0.2.0 · ACE2 Pro 烘干控制（RS-485 230400 8N1）")
        appendLog("连接后自动流程：DISCOVER_DEVICE → ASSIGN_DEVICE_ID(1) → GET_INFO → GET_STATUS（每 2s）")
        appendLog("烘干安全范围：${minDryTemp}–${maxDryTemp} ℃ / ${minDryMinutes}–${maxDryMinutes} 分钟，启动前会二次确认")
        appendLog("原始 hex 发送仍可用：\"AA 55 01\" 或 \"AA5501\"")

        updateControls()
        handleUsbIntent(intent)
        refreshStatus()
    }

    private fun setupBaudSpinner() {
        val items = baudRates.map { it.toString() }
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, items)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spinnerBaud.adapter = adapter
        val defaultIndex = baudRates.indexOf(UsbSerialManager.DEFAULT_BAUD)
        if (defaultIndex >= 0) {
            spinnerBaud.setSelection(defaultIndex)
        }
    }

    /** 串口开关状态变化时，同步启动/结束 ACE2 会话。 */
    private fun syncClientWithSerial() {
        if (!::client.isInitialized) return
        val open = usb.isOpen
        if (open && !clientSessionOpen) {
            clientSessionOpen = true
            client.onSerialConnected()
        } else if (!open && clientSessionOpen) {
            clientSessionOpen = false
            client.onSerialDisconnected()
        }
    }

    private fun toggleConnection() {
        if (!::usb.isInitialized) return
        if (usb.isOpen) {
            usb.disconnect()
            refreshStatus()
        } else {
            val baud = baudRates[spinnerBaud.selectedItemPosition.coerceIn(0, baudRates.lastIndex)]
            appendLog("尝试连接串口，波特率 $baud …")
            usb.connect(baud)
        }
    }

    private fun sendHex() {
        if (!::usb.isInitialized) return
        val text = etHex.text?.toString().orEmpty()
        if (text.isBlank()) {
            appendLog("!! 发送内容为空")
            return
        }
        val bytes = UsbSerialManager.parseHex(text)
        if (bytes == null || bytes.isEmpty()) {
            appendLog("!! 十六进制格式不正确：$text")
            return
        }
        usb.send(bytes)
    }

    private fun queryStatus() {
        if (!::client.isInitialized) return
        if (!usb.isOpen) {
            toast("请先连接 USB 串口")
            return
        }
        if (!client.isReady) {
            toast(getString(R.string.toast_not_ready))
            return
        }
        client.requestStatus()
        appendLog("手动查询状态 …")
    }

    private fun startDrying() {
        if (!::client.isInitialized) return
        if (!client.isReady) {
            toast(getString(R.string.toast_not_ready))
            return
        }
        val tempC = etDryTemp.text?.toString()?.trim()?.toIntOrNull()
        if (tempC == null || tempC !in minDryTemp..maxDryTemp) {
            toast(getString(R.string.toast_temp_invalid))
            return
        }
        val minutes = etDryDuration.text?.toString()?.trim()?.toIntOrNull()
        if (minutes == null || minutes !in minDryMinutes..maxDryMinutes) {
            toast(getString(R.string.toast_duration_invalid))
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_start_title)
            .setMessage(getString(R.string.dialog_start_msg, tempC, minutes))
            .setPositiveButton(R.string.dialog_confirm) { _, _ ->
                appendLog("用户确认：启动烘干 ${tempC}℃ / ${minutes} 分钟")
                client.startDrying(tempC, minutes) { ok, msg -> onDryingResult(ok, msg) }
            }
            .setNegativeButton(R.string.dialog_cancel, null)
            .show()
    }

    private fun stopDrying() {
        if (!::client.isInitialized) return
        if (!client.isReady) {
            toast(getString(R.string.toast_not_ready))
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_stop_title)
            .setMessage(R.string.dialog_stop_msg)
            .setPositiveButton(R.string.dialog_confirm) { _, _ ->
                appendLog("用户确认：停止烘干")
                client.stopDrying { ok, msg -> onDryingResult(ok, msg) }
            }
            .setNegativeButton(R.string.dialog_cancel, null)
            .show()
    }

    private fun onDryingResult(ok: Boolean, msg: String) {
        if (ok) {
            toast(msg)
            appendLog("✔ $msg")
        } else {
            toast(getString(R.string.toast_dry_failed, msg))
            appendLog("!! 烘干指令失败：$msg")
        }
    }

    private fun clearLog() {
        logLines.clear()
        appendCount = 0
        tvLog.text = ""
    }

    /**
     * 日志追加：只 append 增量文本，不每次 join 整块（最高 800 行 × 60 字符）。
     * 超限裁剪走「每 200 条一次全量重建」，把重排成本摊薄。
     */
    private fun appendLog(line: String) {
        if (!::tvLog.isInitialized || isFinishing || isDestroyed) return
        val stamped = timeFormat.format(Date()) + "  " + line + "\n"
        logLines.addLast(stamped)
        while (logLines.size > maxLogLines) {
            logLines.removeFirst()
        }
        tvLog.append(stamped)
        appendCount++
        if (appendCount >= 200) {
            appendCount = 0
            if (tvLog.lineCount > maxLogLines) {
                tvLog.text = logLines.joinToString("")
            }
        }
        scrollLog.post { scrollLog.fullScroll(View.FOCUS_DOWN) }
    }

    private fun renderAceState(state: Ace2Client.State, detail: String) {
        if (!::tvAceStatus.isInitialized) return
        tvAceStatus.text = when (state) {
            Ace2Client.State.IDLE -> "ACE2：未连接"
            Ace2Client.State.DISCOVERING -> "ACE2：正在发现设备…"
            Ace2Client.State.ASSIGNING -> "ACE2：已发现，分配总线地址…"
            Ace2Client.State.READY -> {
                val fw = if (firmwareVersion.isBlank()) "—" else firmwareVersion
                "ACE2：就绪 · 固件 $fw"
            }
            Ace2Client.State.ERROR -> "ACE2：错误 — $detail"
        }
    }

    private fun renderStatus(status: Ace2Status) {
        if (!::tvAceStatus.isInitialized) return
        val workNames = resources.getStringArray(R.array.work_state_names)
        val dryNames = resources.getStringArray(R.array.dry_state_names)
        val workName = if (status.workStateCode in workNames.indices) {
            workNames[status.workStateCode]
        } else {
            workNames.last()
        }
        val dryName = if (status.dryer.stateCode in dryNames.indices) {
            dryNames[status.dryer.stateCode]
        } else {
            dryNames.last()
        }
        val fw = if (firmwareVersion.isBlank()) "—" else firmwareVersion
        tvAceStatus.text =
            getString(R.string.ace_status_format, workName, fw, status.tempC, status.humidity)

        val remainSeconds = status.dryer.remainSeconds
        val remainText =
            getString(R.string.remain_format, remainSeconds / 60, remainSeconds % 60)
        val target = if (status.dryer.targetTempC > 0) {
            status.dryer.targetTempC
        } else {
            client.lastDryingTempC
        }
        tvDryerInfo.text = getString(
            R.string.dryer_info_format,
            dryName,
            target,
            status.tempC,
            remainText
        )
        tvDryerInfo.setTextColor(
            if (status.dryer.stateCode >= 4) 0xFFFF6B6B.toInt() else 0xFF9AA4B2.toInt()
        )
    }

    private fun updateControls() {
        if (!::btnStartDry.isInitialized || !::client.isInitialized) return
        val ready = client.isReady
        btnStartDry.isEnabled = ready
        btnStopDry.isEnabled = ready
        btnQueryStatus.isEnabled = ready
    }

    private fun refreshStatus() {
        if (!::tvStatus.isInitialized || !::usb.isInitialized) return
        val open = usb.isOpen
        btnConnect.text =
            getString(if (open) R.string.btn_disconnect else R.string.btn_connect)
        if (open) {
            val aceState = if (::client.isInitialized) client.state.name else "IDLE"
            tvStatus.text = getString(R.string.status_connected) + " · ACE2 $aceState"
        } else {
            val count = usb.devices().size
            tvStatus.text = if (count > 0) {
                getString(R.string.status_idle) + " · 检测到 " + count + " 个 USB 串口设备"
            } else {
                getString(R.string.status_idle)
            }
        }
    }

    private fun toast(message: String) {
        if (isFinishing || isDestroyed) return
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun handleUsbIntent(intent: Intent?) {
        if (intent == null || !::usb.isInitialized) return
        when (intent.action) {
            UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                val device = UsbSerialManager.deviceFromIntent(intent)
                appendLog("USB 设备接入：" + UsbSerialManager.describeDevice(device))
                appendLog("下一步：点击「连接 USB 串口」发起打开请求")
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleUsbIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        updateControls()
    }

    override fun onDestroy() {
        if (::client.isInitialized) {
            client.shutdown()
        }
        if (::usb.isInitialized) {
            usb.release()
        }
        super.onDestroy()
    }
}
