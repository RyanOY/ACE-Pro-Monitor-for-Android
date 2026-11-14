# AceSolo

> 用手机 + 一根 OTG 线直连 **Anycubic ACE2 Pro** 的 RS-485 串口：启动/停止烘干、盯住温湿度与剩余时间，排障时还能收发原始 hex 帧。
> **本机不需要 Android SDK / JDK**：代码推到 GitHub，由 GitHub Actions 云端编译出 APK。

`v0.2.0 · ACE2 Pro 烘干控制` · `Kotlin 2.0.21` · `minSdk 24 / targetSdk 34` · `AGP 8.5.2 / Gradle 8.7` · `CI: GitHub Actions`

---

## ⚠️ 安全须知（先读这一节）

- **烘干 = 真实加热。** 启动后设备里的 PTC 会升温到设定温度（上限 65 ℃），旁边就是线材、纸盒和塑料件。**烘干全程必须有人值守**，离开前先停止。
- **协议未在真机验证。** 帧格式、CRC、命令号、状态字段都来自社区逆向工程，可能有固件差异；第一次跑请按 [docs/DRYING.md](docs/DRYING.md) 的「首次试机」步骤来。
- **断线不会停止加热。** USB 松脱 / App 退出 / 手机没电后，App 无法再发停止命令，设备会继续按设定时长加热；停不下来时**直接断设备电源**。
- 温度硬限制 **30–65 ℃**、时长 **10–1440 分钟**；启动前有二次确认；故障码出现时立即停止。
- 完整安全说明、线材温度参考与故障处理见 **[docs/DRYING.md](docs/DRYING.md)**。

---

## 1. 项目定位

AceSolo 是一个**实验性 / 自用**的 Android 工具：把手机变成 Anycubic ACE2 Pro 多色供料器的控制终端。

- **v0.1.0** 只做链路：USB OTG → RS-485 230400 8N1，原始 hex 收发 + 日志，先把「能连上、能看见字节」做确定。
- **v0.2.0** 在链路上叠了烘干：自动发现设备并分配地址，读固件版本，按 `DRYING` 启动/停止烘干，每 2 s 轮询状态并显示当前温度、湿度、目标温度与剩余时间；原始帧日志和手动 hex 发送保留，方便对照协议排障。

## 2. 功能范围

| 能力 | v0.1.0 | v0.2.0 | 说明 |
|---|---|---|---|
| 连接 USB 串口 | ✅ | ✅ | 枚举 → 申请 USB 权限 → 打开端口；230400 8N1 无流控 |
| 波特率选择 | ✅ | ✅ | 115200 / **230400（默认）** / 921600 / 250000 / 57600 |
| 原始 hex 发送 + TX/RX 日志 | ✅ | ✅ | 带毫秒时间戳、自动滚动、最多 800 行；支持 `AA 55 01` / `AA5501` / `0xAA,0x55` |
| 热插拔处理 | ✅ | ✅ | 插入可唤起 App，拔出自动关闭端口 |
| ACE2 自动握手 | ❌ | ✅ | `DISCOVER_DEVICE` → `ASSIGN_DEVICE_ID(1)` → `GET_INFO` |
| 启动烘干 | ❌ | ✅ | 30–65 ℃ / 10–1440 分钟，`auto_roll` 关闭，二次确认 |
| 停止烘干 | ❌ | ✅ | 全零 `DRYING`；就绪后「停止烘干」按钮常驻 |
| 状态轮询与状态卡 | ❌ | ✅ | 每 2 s `GET_STATUS`：工作状态、温度、湿度、烘干状态、目标温度、剩余时间 |
| 进料 / 回抽 / 风扇 / 阀门 | ❌ | ❌ | 协议已调研，界面未接 |
| RFID 读写 / 固件升级（IAP） | ❌ | ❌ | 不实现，避免误写标签或刷砖 |
| 多设备总线寻址 | ❌ | ❌ | 固定 `device_id=1`，只支持单台 |

## 3. 快速开始

### 3.1 安装 APK

APK 由 GitHub Actions 构建，不在仓库里存二进制：

1. 打开 [Actions 页面](https://github.com/RyanOY/ACE-Pro-Monitor-for-Android/actions)，选工作流 **Build APK** 最近一次成功运行（绿色 ✓）；
2. 拉到底部 **Artifacts**，下载 **`AceSolo-debug`**（zip，按仓库设置保留，默认 90 天）；
3. 解压得到 `app-debug.apk`，传到手机安装（需要允许「安装未知应用」）；
4. 这是 **debug 签名**，仅自用，不要分发。

> 想自己构建、或构建失败要看排查手册，见 [docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md)。

### 3.2 接线

```text
手机（USB Host / OTG）
   └── OTG 转接 → USB 转 RS-485 模块（CH340 / CH341A / CP210x / FTDI / PL2303）
          └── A / B / GND → Anycubic ACE2 Pro 的 RS-485 通讯口
```

- 只用 3 根线：**A、B、GND**；**不接设备的 5V**，转换器由手机供电即可；
- A/B 接反的表现是「完全没回包」，断电后对调试一次；
- 官方 ACE2 线缆是 CH343（`1a86:55d3`），`usb-serial-for-android 3.8.1` 没有该芯片驱动，插上通常识别不到 → 用上面的转换器；
- 手机 OTG 要打开（部分机型在设置里有开关）；不要用只能充电的线；
- **单主站**：不要把手机和打印机主机同时接到同一条 RS-485 总线。

### 3.3 连接设备

1. 打开 AceSolo，标题是 `ACE2 Pro · 烘干控制台`；
2. 插入转换器，点「连接 USB 串口」→ 系统弹 USB 授权 → 允许；
3. 日志出现 `串口已打开 @ 230400 8N1 [...]`，随后自动握手：

   ```text
   → DISCOVER_DEVICE #1 ...
   状态：ASSIGNING · 发现设备 UID=...，分配总线地址 1
   设备地址分配成功：device_id=1
   固件版本：V1.x.y（boot V1.x.y）
   状态：READY · ACE2 已就绪，开始周期查询状态
   ```

4. 状态卡出现 `ACE2：就绪 · 固件 … · 温度 … ℃ · 湿度 …%`，日志每 2 s 有一条 `← GET_STATUS`。

### 3.4 启动 / 停止烘干

1. 输入目标温度（30–65）与时长（10–1440 分钟），点「启动烘干」；
2. 在确认框核对参数和安全提示，点「确认」；
3. 日志出现 `✔ 已启动烘干`，状态卡显示 `烘干：烘干中 · 目标 55 ℃ · 当前 xx ℃ · 剩余 239分58秒`；
4. 要停就点「停止烘干」→ 确认；状态回到「已停止」，温度逐步回落（PTC 有余温）；
5. **断开串口不会停止加热**：先停烘干，再断开。

更细的步骤、协议帧逐字节解释与 CRC 自校验脚本见 **[docs/DRYING.md](docs/DRYING.md)**。

## 4. 技术栈

| 项 | 选型 | 备注 |
|---|---|---|
| 语言 | Kotlin 2.0.21 | `kotlin.code.style=official` |
| UI | AndroidX + Material 3 | 单 Activity + XML：控制台 + 状态卡 + 烘干面板 |
| USB 串口 | `com.github.mik3y:usb-serial-for-android:3.8.1`（JitPack） | 自带 CH340/CH341A、CP210x、FTDI、PL2303；CH343/CH9102 无专用驱动 |
| 协议 | 自研 `Ace2Protocol` / `Ace2Client` | CRC16/MCRF4XX、protobuf 极简编解码、半双工单在途请求队列 |
| 构建 | AGP 8.5.2 + Gradle 8.7 | 仓库**不提交** Gradle Wrapper，版本由 CI 指定 |
| JDK | 17（Temurin） | `compileOptions` / `kotlinOptions` 均为 17 |
| SDK | `compileSdk 34` · `targetSdk 34` · `minSdk 24` | Android 7.0 及以上 |
| 包名 / 版本 | `com.acesolo` | `versionName 0.2.0`（`versionCode 2`） |
| CI | GitHub Actions · `ubuntu-latest` | 工作流 `Build APK`，产物 artifact：`AceSolo-debug` |

## 5. 目录结构

```text
AceSolo/
├── .github/workflows/build.yml   # 云端构建：JDK 17 + Android SDK 34 + Gradle 8.7 → assembleDebug
├── app/
│   ├── build.gradle.kts
│   ├── proguard-rules.pro
│   └── src/main/
│       ├── AndroidManifest.xml   # USB Host、插入唤起、单 Activity
│       ├── java/com/acesolo/
│       │   ├── MainActivity.kt        # UI：连接、状态卡、烘干输入与确认框、日志、手动 hex
│       │   ├── UsbSerialManager.kt    # 枚举 / 权限 / 打开 / 读写 / 热插拔
│       │   ├── Ace2Protocol.kt        # 帧编解码、CRC16/MCRF4XX、protobuf、状态解析
│       │   └── Ace2Client.kt          # 握手、请求队列与超时重试、2 s 轮询、烘干启停
│       └── res/
│           ├── layout/activity_main.xml
│           ├── values/strings.xml
│           └── xml/device_filter.xml  # USB 转串口芯片的插入唤起白名单（十进制 VID/PID）
├── docs/
│   ├── DRYING.md                  # 烘干操作、安全、真实请求帧与 CRC 自校验
│   ├── ace2-protocol-notes.md     # ACE2 Pro 协议调研笔记（来源等级、命令表、状态字段）
│   └── GITHUB_ACTIONS.md          # 云构建操作手册（建仓库 / PAT / 触发 / 下载 / 排查）
├── tools/
│   ├── ace2_vectors.py            # 由社区参考实现生成黄金向量 ace2_vectors.json
│   ├── ace2_vectors.json          # 帧 / payload / CRC 的逐字节期望值
│   ├── verify_ace2.py             # 校验 Kotlin 实现对黄金向量（120 项）
│   └── preflight.py               # 无 SDK 本地预检：资源 ID、manifest、CI 版本一致性
├── build.gradle.kts
├── settings.gradle.kts            # google + mavenCentral + jitpack，include(":app")
├── gradle.properties
├── .gitignore
└── README.md
```

核心类分工：

- **`MainActivity`**：纯 UI 层。连接开关、状态卡渲染、烘干参数校验（30–65 / 10–1440）与二次确认、日志。
- **`UsbSerialManager`**：串口层。枚举 → 权限 → `setParameters(baud, 8, STOPBITS_1, PARITY_NONE)` → 异步读写；收到数据转给会话层。
- **`Ace2Protocol`**：协议层。组帧/解帧、CRC、protobuf 编解码、`GET_STATUS` 解析。
- **`Ace2Client`**：会话层。连接后自动握手（DISCOVER → ASSIGN → GET_INFO）、单在途请求 + 超时重试、2 s 状态轮询、烘干启停，全部回调切主线程。

## 6. 协议速查

RS-485 半双工，单主站，**230400 8N1 无流控**。请求与应答同格式：

```text
FF AA | flags(1) | id(2, 小端) | cmd(1) | len(1) | payload(len) | crc16(2, 小端) | FE
```

- `flags`：请求低 7 位是总线地址（本 App 用 1）；发现/分配用 0 广播；应答为 `0x80 | 地址`（`0x81`）。
- `crc16`：CRC-16/MCRF4XX（poly `0x8408` 反射，init `0xFFFF`，无最终异或），范围 `frame[2 .. 7+len)`；黄金值 `CRC16("123456789") = 0x6F91`。
- `payload`：protobuf；`len` 上限 100，最小帧长 10 字节。

v0.2 使用的命令：`0 DISCOVER_DEVICE`、`1 ASSIGN_DEVICE_ID`、`6 GET_STATUS`、`7 GET_INFO`、`11 DRYING`。其中 `DRYING` 的 payload 是 `{1: 温度℃, 2: 时长**分钟**, 3: auto_roll}`，全零即停止；状态里的 `duration` / `remain_time` 是**秒**。

启动烘干 55 ℃ / 240 分钟的真实请求帧（请求 id = 5）：

```text
FF AA 01 05 00 0B 07 08 37 10 F0 01 18 00 78 E3 FE
```

逐字节解释与 python3 CRC 自校验见 [docs/DRYING.md](docs/DRYING.md#64-真实请求帧启动烘干-55---240-分钟)；完整命令表、状态字段与「哪些是推断」见 [docs/ace2-protocol-notes.md](docs/ace2-protocol-notes.md)。

## 7. 构建方式：不依赖本机 Android SDK

设计前提：**主力设备是手机，不装 JDK、不装 Android SDK、不装 Android Studio。**

代码仓库：[RyanOY/ACE-Pro-Monitor-for-Android](https://github.com/RyanOY/ACE-Pro-Monitor-for-Android)。

仓库里没有 Gradle Wrapper，也没有 `local.properties`；JDK、Android SDK、Gradle 全部由 CI runner 提供。

1. 修改代码，提交并 **push 到 `main` / `master`**（或 Actions 页面手动 `Run workflow`）；
2. `.github/workflows/build.yml` 自动运行；
3. 等约 3–8 分钟，构建通过后在该次 run 页面下载 artifact **`AceSolo-debug`**；
4. 解压得到 `app-debug.apk`，装到手机。

workflow 关键步骤：

| 步骤 | 内容 |
|---|---|
| Checkout | `actions/checkout@v4` |
| Set up JDK 17 | `actions/setup-java@v4`，`temurin` |
| 校验 Android SDK | 直接用 runner 预装的 `ANDROID_HOME`；缺 `platforms;android-34` / `build-tools;34.0.0` 时用 `sdkmanager` 补装（**不用** `setup-android@v3`，它会装已下线的 `tools` 包并失败） |
| Set up Gradle | `gradle/actions/setup-gradle@v3`，`gradle-version: 8.7` |
| 编译 | `gradle assembleDebug --no-daemon --stacktrace` |
| 上传产物 | `actions/upload-artifact@v4`，`name: AceSolo-debug`，`app/build/outputs/apk/debug/*.apk` |

> 完整操作手册（建空仓库、PAT 权限、触发方式、下载安装、失败排查）见 **[docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md)**。本机只改文档时可以跑 `python3 tools/preflight.py` 做静态预检。

## 8. 已知限制与未验证项

- **未在真机 ACE2 Pro 上验证。** 协议来自社区逆向（`acepro-any-klipper`、multiACE、Simon-CR 等实现互相印证），帧示例是按协议合成的，不是实测抓包；`tools/verify_ace2.py` 的 120 项通过只说明「实现与参考实现一致」，不等于硬件可用。
- 只支持单台设备（`device_id=1`），地址不持久，每次连接重新握手。
- 不读/不写 RFID；不支持固件升级（IAP）；进料/回抽/风扇/阀门/闪灯未接界面。
- 官方 CH343 线缆在 `usb-serial-for-android 3.8.1` 下无驱动，需要 CH340/CH341A/CP210x/FTDI/PL2303 转换器。
- 半双工总线无仲裁：App 与打印机主机不能同时接管；断线后设备不会自动停止加热。
- `GET_STATUS` 的烘干状态字段若带 bit6/bit7 标记，App 会显示「未知」（按裸值查表，真机确认后再改）。
- 状态卡不显示槽位/RFID 信息。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| 日志「未发现 USB 串口设备」/「未识别该设备的串口驱动」 | 确认 OTG、换数据线；换 CH340 / CP210x 转换器（CH343 无驱动） |
| 「openDevice 返回 null」 | 重新插拔并授权；关掉其他串口 App |
| 连接后「发现失败：设备无应答」 | A/B 对调、共地、确认 230400、设备唤醒、断开打印机主机 |
| 启动烘干 `code=1/2` | 参数超范围或设备忙；等就绪后用 40–60 ℃ / 30–120 分钟先试 |
| 构建失败 | 看 run 页面哪个 step 失败，按 [docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md) 排查 |

烘干相关的更多问题（不升温、剩余时间不刷新、故障码 4/5 等）见 [docs/DRYING.md](docs/DRYING.md#7-常见问题)。

## 10. 路线图 / 不做的部分

- **v0.2**：烘干启停 + 状态轮询（当前版本）。
- 后续可能做：进料/回抽面板、多设备总线寻址、连接稳定性（自动重连）。
- **明确不做**：RFID 写入、固件升级（IAP）——写入类操作风险高且未验证，不放进近期路线。

---

## 免责声明

- 本项目为个人自用调试工具，**debug 签名**，请勿用于分发或正式环境；
- 烘干会真实加热，存在火灾、烫伤与设备损坏风险，使用后果自负；第三方接入可能影响厂商保修；
- 向设备发送未知 hex 帧存在风险，写类命令前请先确认协议与后果；建议在设备空闲、不打印时调试。
