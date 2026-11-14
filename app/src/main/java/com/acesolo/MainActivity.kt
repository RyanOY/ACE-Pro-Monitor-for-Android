package com.acesolo

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
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * AceSolo MVP：连接 USB 串口，输 hex 发送，日志展示 TX/RX 原始字节。
 * 协议命令（查询状态/进料/回抽/停止）留到 v0.2。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var usb: UsbSerialManager
    private lateinit var tvLog: TextView
    private lateinit var tvStatus: TextView
    private lateinit var etHex: EditText
    private lateinit var spinnerBaud: Spinner
    private lateinit var scrollLog: ScrollView
    private lateinit var btnConnect: MaterialButton
    private lateinit var btnSend: MaterialButton
    private lateinit var btnClear: MaterialButton

    private val baudRates = intArrayOf(115200, 230400, 921600, 250000, 57600)
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
    private val logLines = ArrayDeque<String>()
    private val maxLogLines = 800

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvLog = findViewById(R.id.tvLog)
        tvStatus = findViewById(R.id.tvStatus)
        etHex = findViewById(R.id.etHex)
        spinnerBaud = findViewById(R.id.spinnerBaud)
        scrollLog = findViewById(R.id.scrollLog)
        btnConnect = findViewById(R.id.btnConnect)
        btnSend = findViewById(R.id.btnSend)
        btnClear = findViewById(R.id.btnClear)

        setupBaudSpinner()

        usb = UsbSerialManager(
            context = this,
            onLog = { line -> appendLog(line) },
            onConnectionChanged = { refreshStatus() }
        )

        btnConnect.setOnClickListener { toggleConnection() }
        btnSend.setOnClickListener { sendHex() }
        btnClear.setOnClickListener { clearLog() }
        etHex.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendHex()
                true
            } else {
                false
            }
        }

        appendLog("AceSolo v0.1.0 · MVP（连接 USB 串口 + 原始 hex 收发）")
        appendLog("目标设备：Anycubic ACE2 Pro / RS485 · 230400 8N1")
        appendLog("hex 输入支持 \"AA 55 01\" 或 \"AA5501\" 两种写法")

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

    private fun clearLog() {
        logLines.clear()
        tvLog.text = ""
    }

    private fun appendLog(line: String) {
        if (!::tvLog.isInitialized) return
        val stamped = timeFormat.format(Date()) + "  " + line
        logLines.addLast(stamped)
        while (logLines.size > maxLogLines) {
            logLines.removeFirst()
        }
        tvLog.text = logLines.joinToString("\n")
        scrollLog.post { scrollLog.fullScroll(View.FOCUS_DOWN) }
    }

    private fun refreshStatus() {
        if (!::tvStatus.isInitialized || !::usb.isInitialized) return
        val open = usb.isOpen
        btnConnect.text =
            getString(if (open) R.string.btn_disconnect else R.string.btn_connect)
        if (open) {
            tvStatus.text = getString(R.string.status_connected)
        } else {
            val count = usb.devices().size
            tvStatus.text = if (count > 0) {
                getString(R.string.status_idle) + " · 检测到 " + count + " 个 USB 串口设备"
            } else {
                getString(R.string.status_idle)
            }
        }
    }

    private fun handleUsbIntent(intent: Intent?) {
        if (intent == null || !::usb.isInitialized) return
        when (intent.action) {
            UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                val device = UsbSerialManager.deviceFromIntent(intent)
                appendLog("USB 设备接入：" + UsbSerialManager.describeDevice(device))
                appendLog("下一步：点击「连接 USB 串口」发起打开请求")
            }
            UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                val device = UsbSerialManager.deviceFromIntent(intent)
                usb.onDeviceDetached(device)
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
    }

    override fun onDestroy() {
        if (::usb.isInitialized) {
            usb.release()
        }
        super.onDestroy()
    }
}
