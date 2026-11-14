# AceSolo

> 用手机 + 一根 OTG 线直连 **Anycubic ACE2 Pro** 的 RS485 串口，在手机上收发原始 hex 帧。
> **本机不需要 Android SDK / JDK**：代码推到 GitHub，由 GitHub Actions 云端编译出 APK。

`v0.1.0 · MVP` · `Kotlin 2.0.21` · `minSdk 24 / targetSdk 34` · `AGP 8.5.2 / Gradle 8.7` · `CI: GitHub Actions`

---

## 1. 项目定位

AceSolo 是一个**实验性 / 自用**的 Android 工具：把手机变成 Anycubic ACE2 Pro 多色供料器的调试终端。

它刻意把范围压到最小 —— **只做两件事**：

1. 通过 USB OTG（USB 转 RS485，常见为 CH340 方案）打开一条 230400 8N1 的串口；
2. 把收发的**原始字节**以 hex 形式实时打印在日志区，方便对照协议文档逐帧试验。

先拿到「能连上、能看见字节」这个确定性，再往上叠业务协议。这是 v0.1.0 的全部野心。

## 2. MVP 范围

| 能力 | v0.1.0 | 说明 |
| --- | --- | --- |
| 连接 USB 串口 | ✅ | 枚举 USB 串口设备 → 申请 USB 权限 → 打开端口 |
| 波特率选择 | ✅ | 115200 / **230400（默认）** / 921600 / 250000 / 57600 |
| 原始 hex 发送 | ✅ | 支持 `AA 55 01`、`AA5501`、`0xAA,0x55` 等写法 |
| TX / RX 日志 | ✅ | 带毫秒时间戳，自动滚动，最多保留 800 行 |
| 云端构建 APK | ✅ | push 即触发 GitHub Actions，不依赖本机 SDK |
| 热插拔处理 | ✅ | 插入可唤起 App，拔出自动关闭端口 |
| 查询状态 / 进料 / 回抽 / 停止 | ❌ 留到 v0.2 | 需要先确定 ACE2 Pro 的帧格式与校验 |
| 烘干 / RFID / 固件升级 | ❌ 暂不做 | 不在 MVP 内，避免半成品协议实现 |

## 3. 技术栈

| 项 | 选型 | 备注 |
| --- | --- | --- |
| 语言 | Kotlin 2.0.21 | `kotlin.code.style=official` |
| UI | AndroidX + Material 3 | `core-ktx 1.13.1` / `appcompat 1.7.0` / `material 1.12.0`，单 Activity + XML 布局 |
| USB 串口 | `com.github.mik3y:usb-serial-for-android:3.8.1` | 内置 CH340/CH341/CH343/CH9102、CP210x、FTDI、PL2303 等驱动 |
| 构建 | AGP 8.5.2 + Gradle 8.7 | 仓库**不提交** Gradle Wrapper，由 CI 指定版本 |
| JDK | 17（Temurin） | `compileOptions` / `kotlinOptions` 均为 17 |
| SDK | `compileSdk 34` · `targetSdk 34` · `minSdk 24` | Android 7.0 及以上 |
| 包名 | `com.acesolo` | `applicationId` 与 `namespace` 一致 |
| 版本 | `0.1.0`（`versionCode 1`） | debug 构建 |
| CI | GitHub Actions · `ubuntu-latest` | 产物 artifact：`AceSolo-debug` |

## 4. 目录结构

```text
AceSolo/
├── .github/
│   └── workflows/
│       └── build.yml              # 云端构建：JDK 17 + Android SDK 34 + Gradle 8.7 → assembleDebug
├── app/
│   ├── build.gradle.kts           # 应用模块：SDK 版本、依赖、Kotlin/JVM 17
│   ├── proguard-rules.pro
│   └── src/main/
│       ├── AndroidManifest.xml    # USB Host 特性、USB 插拔过滤器、单 Activity
│       ├── java/com/acesolo/
│       │   ├── MainActivity.kt        # UI 编排：连接/发送/清空、波特率、日志
│       │   └── UsbSerialManager.kt    # 串口枚举、权限、打开、读写、热插拔
│       └── res/
│           ├── layout/activity_main.xml   # 控制台界面：标题 / 状态 / 波特率 / hex 输入 / 日志
│           ├── values/strings.xml         # 全部中文文案与波特率选项
│           └── xml/device_filter.xml      # 7 款 USB 转串口芯片的插入唤起过滤
├── docs/
│   └── GITHUB_ACTIONS.md          # 云构建操作手册（建仓库 / PAT / 触发 / 下载 / 排查）
├── build.gradle.kts               # 根脚本：仅声明插件版本，全部 apply false
├── settings.gradle.kts            # 仓库源（google + mavenCentral）、include(":app")
├── gradle.properties              # JVM 内存、并行、缓存、AndroidX
├── .gitignore                     # 忽略 build/、.gradle/、local.properties、*.apk
└── README.md
```

核心类分工：

- **`MainActivity`**：纯 UI 层。负责把日志渲染到 `TextView`、解析 hex 输入、把用户操作转成 `UsbSerialManager` 调用；不碰任何 USB 细节。
- **`UsbSerialManager`**：串口层。封装枚举 → 权限 → `open` → `setParameters(baud, 8, STOPBITS_1, PARITY_NONE)` → `SerialInputOutputManager` 异步读写；收到数据原样以 hex 回调给 UI。

## 5. 构建方式：不依赖本机 Android SDK

设计前提：**主力设备是手机，不装 JDK、不装 Android SDK、不装 Android Studio。**

因此仓库里没有 Gradle Wrapper，也没有 `local.properties`；`gradle build` 需要的 JDK、Android SDK、Gradle 全部由 CI runner 提供：

1. 修改代码，提交并 **push 到 `main`**（或手动 `workflow_dispatch`）；
2. GitHub Actions 自动运行 `.github/workflows/build.yml`；
3. 等待约 3–8 分钟，构建通过后在本次 run 页面下载 artifact **`AceSolo-debug`**；
4. 解压得到 `app-debug.apk`，装到手机。

workflow 关键步骤：

| 步骤 | 内容 |
| --- | --- |
| Checkout | `actions/checkout@v4` |
| Set up JDK 17 | `actions/setup-java@v4`，`temurin` |
| Set up Android SDK | `android-actions/setup-android@v3` |
| 安装 SDK 包 | `sdkmanager --install "platform-tools" "platforms;android-34" "build-tools;34.0.0"` |
| Set up Gradle | `gradle/actions/setup-gradle@v3`，`gradle-version: 8.7` |
| 编译 | `gradle assembleDebug --no-daemon --stacktrace` |
| 上传产物 | `actions/upload-artifact@v4`，`name: AceSolo-debug`，路径 `app/build/outputs/apk/debug/app-debug.apk` |

> 完整操作手册（建空仓库、PAT 权限、触发方式、下载安装、失败排查）见 **[docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md)**。

## 6. 安装与真机验证

### 硬件链路

```text
手机（OTG Host）
   └── USB-C OTG 转接 → USB 转 RS485 模块（常见 CH340）
          └── RS485 A/B（+ GND）→ Anycubic ACE2 Pro 通讯口
```

注意：

- 手机 OTG 必须开启（部分机型在「设置 → 其他设置 → OTG」里开关）；
- RS485 的 **A/B 不要接反**，模块与 ACE2 Pro 共地；
- 手机作为 Host 会给模块供电，CH340 模块一般够用；
- 不要用只能充电的线材。

### 验证步骤

1. 安装并打开 **AceSolo**，标题显示 `ACE2 Pro · 串口控制台`，日志区显示版本与目标设备提示：
   `AceSolo v0.1.0 · MVP（连接 USB 串口 + 原始 hex 收发）`
   `目标设备：Anycubic ACE2 Pro / RS485 · 230400 8N1`
2. 通过 OTG 插入 USB 转 RS485 模块：匹配到设备时 App 可能被系统唤起；日志出现
   `USB 设备接入：VID=0x1A86 PID=0x7523 (...)`（VID/PID 以实际模块为准）。
3. 波特率保持默认 **230400**，点「连接 USB 串口」：
   - 首次连接系统会弹 USB 权限框，选「允许」；
   - 日志依次出现 `请求 USB 权限：…` → `USB 权限已授予：…` → `发现设备：…`；
   - 成功标志（关键验收点）：
     `串口已打开 @ 230400 8N1 [Ch34xSerialDriver]`
4. 在 hex 输入框输入一帧，例如 `AA 55 01`，点「发送」或键盘发送键：
   - 格式错误会提示 `!! 十六进制格式不正确：…`；
   - 发送成功日志：`TX (3B) AA 55 01`。
5. 观察设备回包：日志出现 `RX (nB) …` 即链路双向可用；点「清空日志」可重置日志。
6. 拔掉模块：日志出现 `USB 设备移除：…` 与 `串口已自动关闭`；重新插入可再次连接。

> **提示**：v0.1.0 不会主动查询或改写 ACE2 Pro 状态。发送的字节完全由你决定，请对照官方/社区协议资料逐条试帧，切勿盲发批量写命令。

## 7. 验收标准

- [ ] `main` 分支 push 后，GitHub Actions 的 `Build APK` 成功，artifact `AceSolo-debug` 可下载；
- [ ] 下载解压出的 `app-debug.apk` 能在 Android 7.0+ 真机安装并启动；
- [ ] 启动后日志区显示名称、版本、目标设备信息；
- [ ] 插入 USB 串口模块后，状态行（标题下方）能显示「检测到 N 个 USB 串口设备」；
- [ ] 授权后日志出现 `串口已打开 @ 230400 8N1`；
- [ ] hex 发送产生 `TX (nB) …` 日志，且字节与输入一致；
- [ ] 设备回包产生 `RX (nB) …` 日志（若设备按协议应答）；
- [ ] 非法 hex（如 `AA5`、`zz`）被拦截并给出错误提示；
- [ ] 「清空日志」按钮可清空日志，「断开串口」关闭端口，拔出模块自动关闭端口；
- [ ] 整个过程**不要求**本机安装 JDK / Android SDK / Gradle。

## 8. 常见问题

### 找不到设备

- 确认 OTG 已开启、转接模块插紧，换一根确定支持数据的 OTG 线；
- 日志里若一直「检测到 0 个 USB 串口设备」：换一个 **CH340 / CP210x** 模块试；
- 若日志有 `USB 设备接入` 但枚举结果始终为 0：说明该 VID/PID 未被驱动库识别，见下一条。

### USB 权限被拒

- 日志出现 `!! USB 权限被拒绝：…`：再次点「连接 USB 串口」重新弹窗并选「允许」；
- 若系统不再弹窗：拔插模块重试；某些定制 ROM 需要在「开发者选项 / 权限」里放行 USB；
- 拒绝后无需重启 App。

### 驱动未识别

- 现象：`!! 未识别该设备的串口驱动（可换 CH340/CP210x 转换器）`；
- `usb-serial-for-android 3.8.1` 覆盖 CH340/CH341/CH343/CH9102、CP210x、FTDI、PL2303 等常见芯片；
- 若你的模块是别的芯片（如部分 CDC-ACM），需要后续自行扩展 prober；
- 模块被其他串口 App 占用时会报 `!! openDevice 返回 null（权限不足或被其他 App 占用）`，先关掉其他 App。

### 230400 不支持 / 通信异常

- 换 115200 或 57600 试收（ACE2 Pro 侧若固定 230400，则本端也必须是 230400）；
- 波特率只在**端口未打开时**生效；要改先「断开」再改下拉框再连接；
- 921600 对部分 CH340 模块不稳，出现乱码/丢帧时先降速把链路跑通；
- 出现 `!! 打开串口失败：…` 多为参数不被芯片支持，换速率或换模块。

### Android 14+ 广播限制

- 代码用 `ContextCompat.registerReceiver(..., RECEIVER_NOT_EXPORTED)` 注册 USB 权限回调，并向本包 `setPackage(context.packageName)`；
- Android 13+ 用 `Intent.getParcelableExtra(name, Class)` 读取设备，S+ 上 `PendingIntent` 显式 `FLAG_MUTABLE` —— 这些是 targetSdk 34 的硬要求，改动时请保留；
- 若日志出现 `!! 注册 USB 权限广播失败`，基本是注册标志被改坏或 ROM 限制，按上面三点核对。

### 构建失败

- 先看 run 页面失败的具体 step：`Install Android SDK packages` / `Build debug APK` / `Upload APK artifact`；
- 本机零 SDK 的前提下，唯一依赖是 workflow 本身，见 [docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md) 的排查表。

## 9. 下一步计划

- **v0.2**：接入 ACE2 Pro 业务协议 —— 查询状态、进料、回抽、停止；把命令封装成面板按钮，并把应答解析成结构化状态；
- 帧收发可视化：hex ↔ ASCII 双栏、常用帧收藏、历史记录导出；
- 连接稳定性：自动重连、串口异常提示与重试；
- **暂不做**：烘干、RFID 识别、固件升级（需要更多协议逆向与安全评估，不放进近期路线）。

---

## 安全与使用说明

- 本项目为个人调试工具，**debug 签名**，请勿用于分发或正式环境；
- 直接向设备发送未知 hex 帧存在风险，修改写类命令前请先确认协议与后果；
- 建议在 ACE2 Pro 空闲、不打印时调试。
