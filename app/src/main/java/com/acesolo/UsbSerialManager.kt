package com.acesolo

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import com.hoho.android.usbserial.util.SerialInputOutputManager
import java.io.IOException
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException

/**
 * USB 串口管理器（MVP：打开端口 + 原始字节收发）。
 *
 * 依赖 usb-serial-for-android 3.8.1：CH340/CH341A、CP210x、FTDI、PL2303 有专用驱动；
 * CH343/CH9102 在 3.8.1 中没有专用驱动（CH9102 若以 CDC-ACM 枚举则可能被通用驱动接住）。
 * 不涉及 ACE2 Pro 业务协议，收到的数据原样以 hex 抛给 UI。
 */
class UsbSerialManager(
    private val context: Context,
    private val onLog: (String) -> Unit,
    private val onConnectionChanged: (() -> Unit)? = null
) : SerialInputOutputManager.Listener {

    companion object {
        private const val TAG = "AceSolo"
        private const val WRITE_TIMEOUT_MS = 2000

        const val ACTION_USB_PERMISSION = "com.acesolo.action.USB_PERMISSION"
        const val DEFAULT_BAUD = 230400

        /** 从 USB 相关 Intent 里取设备（兼容 API 33 前后的 Parcelable 读取方式）。 */
        fun deviceFromIntent(intent: Intent?): UsbDevice? {
            if (intent == null) return null
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
            }
        }

        fun describeDevice(device: UsbDevice?): String {
            if (device == null) return "未知设备"
            val name = device.productName ?: device.deviceName
            return String.format(
                Locale.US,
                "VID=0x%04X PID=0x%04X (%s)",
                device.vendorId,
                device.productId,
                name
            )
        }

        /**
         * 解析十六进制输入，兼容 "AA 55 01"、"AA5501"、"0xAA,0x55" 等写法。
         * 返回 null 表示格式非法（空输入同样返回 null）。
         */
        fun parseHex(input: String): ByteArray? {
            val tokens = input.trim()
                .split(Regex("[\\s,;:_+\\-]+"))
                .filter { it.isNotEmpty() }
            if (tokens.isEmpty()) return null
            val hex = StringBuilder()
            for (token in tokens) {
                val raw = token.removePrefix("0x").removePrefix("0X")
                if (raw.isEmpty()) return null
                if (!raw.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' }) return null
                hex.append(raw)
            }
            if (hex.length % 2 != 0) return null
            val out = ByteArray(hex.length / 2)
            for (i in out.indices) {
                out[i] = hex.substring(i * 2, i * 2 + 2).toInt(16).toByte()
            }
            return out
        }

        fun toHex(bytes: ByteArray): String {
            if (bytes.isEmpty()) return ""
            val sb = StringBuilder(bytes.size * 3)
            for (b in bytes) {
                sb.append(String.format(Locale.US, "%02X", b.toInt() and 0xFF)).append(' ')
            }
            return sb.toString().trimEnd()
        }
    }

    private val usbManager: UsbManager =
        context.getSystemService(Context.USB_SERVICE) as UsbManager

    private val mainHandler = Handler(Looper.getMainLooper())
    private val writer = Executors.newSingleThreadExecutor()

    private var port: UsbSerialPort? = null
    private var connection: UsbDeviceConnection? = null
    private var ioManager: SerialInputOutputManager? = null
    private var receiverRegistered = false
    private var pendingBaud = DEFAULT_BAUD
    private var released = false

    val isOpen: Boolean
        get() = port?.isOpen == true

    /**
     * 运行时广播接收器：
     * - ACTION_USB_PERMISSION：USB 授权结果（PendingIntent 回调）
     * - ACTION_USB_DEVICE_DETACHED：拔线。Activity 的 manifest intent-filter 收不到拔线广播，
     *   必须动态注册，否则拔线后 isOpen 一直是 true。
     */
    private val usbEventReceiver = object : BroadcastReceiver() {
        override fun onReceive(receiverContext: Context?, intent: Intent?) {
            when (intent?.action) {
                ACTION_USB_PERMISSION -> handlePermissionResult(intent)
                UsbManager.ACTION_USB_DEVICE_DETACHED ->
                    onDeviceDetached(deviceFromIntent(intent))
                else -> Unit
            }
        }
    }

    private fun handlePermissionResult(intent: Intent) {
        val device = deviceFromIntent(intent)
        val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
        if (device == null) {
            log("!! USB 权限回调里没有设备信息")
            return
        }
        if (!granted) {
            log("!! USB 权限被拒绝：${describeDevice(device)}")
            notifyConnectionChanged()
            return
        }
        log("USB 权限已授予：${describeDevice(device)}")
        openPort(device, pendingBaud)
    }

    private fun log(message: String) {
        Log.i(TAG, message)
        mainHandler.post { onLog(message) }
    }

    private fun notifyConnectionChanged() {
        mainHandler.post { onConnectionChanged?.invoke() }
    }

    /** 枚举当前已插入的 USB 串口设备。 */
    fun devices(): List<UsbSerialDriver> = try {
        UsbSerialProber.getDefaultProber().findAllDrivers(usbManager)
    } catch (e: Exception) {
        log("!! 枚举 USB 设备失败：${e.message}")
        emptyList()
    }

    /** 连接第一个可识别的 USB 串口设备（必要时先申请 USB 权限）。 */
    fun connect(baud: Int) {
        if (released) return
        ensureReceiver()
        pendingBaud = baud
        if (isOpen) {
            log("串口已打开，如需修改波特率请先断开")
            return
        }
        val drivers = devices()
        if (drivers.isEmpty()) {
            log("!! 未发现 USB 串口设备")
            log("   检查：OTG 已开启 / 转换器已插紧 / 驱动是否被系统占用 / 是否已授予 USB 权限")
            notifyConnectionChanged()
            return
        }
        for (driver in drivers) {
            val ports = driver.ports.size
            log("发现设备：${describeDevice(driver.device)} 驱动=${driver.javaClass.simpleName} 端口数=$ports")
        }
        val device = drivers.first().device
        if (usbManager.hasPermission(device)) {
            openPort(device, baud)
        } else {
            requestPermission(device)
        }
    }

    private fun requestPermission(device: UsbDevice) {
        ensureReceiver()
        try {
            val flags =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
            val intent = Intent(ACTION_USB_PERMISSION).setPackage(context.packageName)
            val pendingIntent = PendingIntent.getBroadcast(context, 0, intent, flags)
            log("请求 USB 权限：${describeDevice(device)}")
            usbManager.requestPermission(device, pendingIntent)
        } catch (e: Exception) {
            log("!! 请求 USB 权限失败：${e.message}")
        }
    }

    private fun ensureReceiver() {
        if (receiverRegistered) return
        try {
            val filter = IntentFilter(ACTION_USB_PERMISSION).apply {
                addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            }
            ContextCompat.registerReceiver(
                context,
                usbEventReceiver,
                filter,
                ContextCompat.RECEIVER_NOT_EXPORTED
            )
            receiverRegistered = true
        } catch (e: Exception) {
            log("!! 注册 USB 广播接收器失败：${e.message}")
        }
    }

    private fun openPort(device: UsbDevice, baud: Int) {
        if (isOpen) closePort()

        val driver = UsbSerialProber.getDefaultProber().probeDevice(device)
        if (driver == null) {
            log("!! 未识别该设备的串口驱动（可换 CH340/CP210x 转换器）")
            notifyConnectionChanged()
            return
        }
        val conn = usbManager.openDevice(device)
        if (conn == null) {
            log("!! openDevice 返回 null（权限不足或被其他 App 占用）")
            notifyConnectionChanged()
            return
        }
        val serialPort = driver.ports.firstOrNull()
        if (serialPort == null) {
            log("!! 该设备没有可用串口端点")
            conn.close()
            notifyConnectionChanged()
            return
        }

        try {
            serialPort.open(conn)
            serialPort.setParameters(
                baud,
                8,
                UsbSerialPort.STOPBITS_1,
                UsbSerialPort.PARITY_NONE
            )
            try {
                serialPort.setDTR(true)
                serialPort.setRTS(true)
            } catch (e: IOException) {
                // 部分芯片/接线不支持 DTR/RTS，忽略即可
                Log.d(TAG, "DTR/RTS 不支持：${e.message}")
            }
            port = serialPort
            connection = conn
            ioManager = SerialInputOutputManager(serialPort, this).also { it.start() }
            log("串口已打开 @ $baud 8N1 [${driver.javaClass.simpleName}]")
        } catch (e: Exception) {
            log("!! 打开串口失败：${e.message}")
            try {
                serialPort.close()
            } catch (_: Exception) {
            }
            try {
                conn.close()
            } catch (_: Exception) {
            }
            port = null
            connection = null
        }
        notifyConnectionChanged()
    }

    /** 发送原始字节（hex 由 UI 解析后传入）。 */
    fun send(data: ByteArray) {
        if (released) return
        val serialPort = port
        if (serialPort == null || !serialPort.isOpen) {
            log("!! 串口未打开，无法发送")
            return
        }
        log("TX (${data.size}B) ${toHex(data)}")
        try {
            writer.execute {
                try {
                    serialPort.write(data, WRITE_TIMEOUT_MS)
                } catch (e: Exception) {
                    log("!! 写入失败：${e.message}")
                }
            }
        } catch (_: RejectedExecutionException) {
            log("!! 发送线程已关闭，忽略本次发送")
        }
    }

    fun disconnect() {
        val wasOpen = isOpen
        closePort()
        if (wasOpen) log("串口已断开")
        notifyConnectionChanged()
    }

    private fun closePort() {
        try {
            ioManager?.stop()
        } catch (_: Exception) {
        }
        ioManager = null
        try {
            port?.close()
        } catch (_: Exception) {
        }
        port = null
        try {
            connection?.close()
        } catch (_: Exception) {
        }
        connection = null
    }

    /** USB 设备被拔出：自动关闭端口。 */
    fun onDeviceDetached(device: UsbDevice?) {
        log("USB 设备移除：${describeDevice(device)}")
        if (isOpen) {
            closePort()
            log("串口已自动关闭")
            notifyConnectionChanged()
        }
    }

    /** Activity 销毁时释放资源。 */
    fun release() {
        released = true
        closePort()
        if (receiverRegistered) {
            try {
                context.unregisterReceiver(usbEventReceiver)
            } catch (_: Exception) {
            }
            receiverRegistered = false
        }
        try {
            writer.shutdown()
        } catch (_: Exception) {
        }
        mainHandler.removeCallbacksAndMessages(null)
    }

    // ---- SerialInputOutputManager.Listener ----

    override fun onNewData(data: ByteArray) {
        log("RX (${data.size}B) ${toHex(data)}")
    }

    override fun onRunError(e: Exception) {
        log("!! 串口读取异常，已关闭端口：${e.message}")
        mainHandler.post {
            closePort()
            notifyConnectionChanged()
        }
    }
}
