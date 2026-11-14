# GitHub Actions 云构建操作手册

本工程**不在本机安装 Android SDK / JDK**。所有编译都在 GitHub 托管的 runner 上完成，产物作为 artifact 下载。

> 相关文件：[.github/workflows/build.yml](../.github/workflows/build.yml) · 项目说明见 [README.md](../README.md)

---

## 0. 前置条件

| 项 | 要求 |
| --- | --- |
| GitHub 账号 | 能创建仓库即可（免费账号可用） |
| 仓库 | 名称随意，本文以 `AceSolo` 为例 |
| 本机工具 | 只需 `git` 和网络（手机 Termux、电脑均可） |
| 不需要 | JDK、Android SDK、Gradle、Android Studio |

仓库里已包含 workflow，**不要再自己新建或修改 `.github/workflows/build.yml`**。

## 1. 在 GitHub 创建空仓库

1. 打开 GitHub → 右上角 `+` → **New repository**；
2. Repository name 填 `AceSolo`；
3. 可见性按需选 Public / Private；
4. **不要勾选** `Add a README file`、`Add .gitignore`、`Choose a license` —— 保持**完全空白**，否则首次 push 会被拒绝（远端已有历史，需要先合并）；
5. 点 **Create repository**，记下页面给出的 HTTPS 地址：
   `https://github.com/<你的用户名>/AceSolo.git`

> 如果已经误勾了 README，先用 `git pull --rebase origin main` 合并，或删库重建（更省事）。

## 2. 用 HTTPS 推送代码

在工程根目录（含 `settings.gradle.kts` 的那一层）执行：

```bash
cd AceSolo

git init -b main
git add -A
git commit -m "feat: AceSolo MVP (USB serial + hex log)"

git remote add origin https://github.com/<你的用户名>/AceSolo.git
git push -u origin main
```

注意：

- GitHub 已**不支持账号密码**推送：`git push` 提示输入密码时，要填 **PAT（Personal Access Token）**；
- 账号名填 GitHub 用户名，密码填 PAT；
- 首次 push 会一次性上传 `.github/workflows/build.yml`，**PAT 必须带 workflow 权限**，否则会被拒绝；
- `.gitignore` 已忽略 `build/`、`.gradle/`、`local.properties`、`*.apk`，不要把构建产物提交上去；
- 推送成功后，`main` 分支上应能看到 `.github/`、`app/`、`docs/` 等目录。

## 3. 创建 PAT 并授权

### 方式 A：Classic token（推荐，最不容易踩坑）

1. GitHub → 右上角头像 → **Settings** → 左侧最下方 **Developer settings**
   → **Personal access tokens** → **Tokens (classic)**；
2. **Generate new token (classic)**；
3. Note 填 `AceSolo push`，Expiration 建议 30 天；
4. 勾选 scope：

   | Scope | 为什么需要 |
   | --- | --- |
   | `repo` | 读写仓库内容（push 代码） |
   | `workflow` | 允许创建/更新 `.github/workflows/*` 文件 |

5. Generate，**立刻复制** token（只显示一次）。

### 方式 B：Fine-grained token

1. Settings → Developer settings → **Personal access tokens** → **Fine-grained tokens** → Generate new token；
2. Repository access 选 **Only select repositories** → `AceSolo`；
3. Permissions 至少给：

   | Permission | 权限 |
   | --- | --- |
   | Contents | Read and write |
   | Actions | Read and write |
   | Metadata | Read（强制，自动带上） |

4. 若本次 push 包含 `.github/workflows/build.yml`，还需要 **Workflows: Read and write**；
5. 生成后复制 token。

### 保存 token（可选）

避免每次 push 都粘贴：

```bash
git config --global credential.helper store
# 下次 push 输入一次用户名 + token，之后自动记住
```

> token 等同于密码，不要写进代码、README、issue 或提交历史。泄露后立刻到设置页 Revoke。

## 4. 触发构建

workflow 名称为 **`Build APK`**，触发条件：

| 触发方式 | 说明 |
| --- | --- |
| push 到 `main` | 最常用，推送后自动开始 |
| pull_request 到 `main` | PR 也会跑一次，产物同样可下载 |
| `workflow_dispatch` | 手动触发，适合改完配置想立刻重跑 |

### 手动触发步骤

1. 打开仓库页 → **Actions** 标签；
2. 左侧列表选 **Build APK**；
3. 右侧 **Run workflow** 下拉 → 分支选 `main` → **Run workflow**；
4. 刷新页面，出现新的 run（黄色圆点 = 运行中，绿色 = 成功，红色 = 失败）。

### 运行时长

- 首次运行约 **3–8 分钟**（要下载 Gradle 与 SDK 依赖，会慢一些）；
- 后续有缓存时通常 **2–4 分钟**；
- run 页面点开 job `assembleDebug (cloud)` 可看每个 step 的实时日志。

### 成功长什么样

所有 step 变绿，最后一个 step 是 `Upload APK artifact`。关键 step 顺序：

`Checkout` → `Set up JDK 17` → `Set up Android SDK` → `Install Android SDK packages`
→ `Set up Gradle` → `Show toolchain versions` → `Build debug APK` → `Upload APK artifact`

## 5. 下载 artifact 并安装到手机

1. 打开成功的 run 页面，滚到底部 **Artifacts** 区域；
2. 点 **`AceSolo-debug`** 下载（得到 `AceSolo-debug.zip`）；
3. 解压，里面就是 `app-debug.apk`；
4. 把 APK 传到手机（微信文件传输 / 网盘 / 数据线均可），点击安装：
   - 首次安装需在系统里允许「安装未知来源应用」；
   - 该 APK 由 CI 用 **debug keystore** 签名，可直接安装；
5. 若在电脑上且有 adb：

   ```bash
   adb install -r app-debug.apk
   ```

> artifact 默认保留 **90 天**（可在仓库 Settings → Actions → Artifacts and logs retention 调整）。过期后重新跑一次 workflow 即可。
>
> 覆盖安装：`adb install -r` 或手机上直接点新 APK 安装；debug 包同签名可覆盖。

## 6. 构建失败排查表

先点开失败 run → 找到红色 step，看末尾 20 行报错。常见情况：

| 症状（日志关键字） | 原因 | 处理 |
| --- | --- | --- |
| `Unsupported class file major version 65/61`、`Could not determine java version` | JDK 版本不对（runner 默认 JDK 不是 17，或 setup-java 没生效） | 确认 `Set up JDK 17` 在构建之前；workflow 固定 `java-version: '17'` + `temurin`；本工程 Kotlin/Java target 都是 17，不要混用 JDK 21 |
| `Failed to find target with hash string 'android-34'`、`compileSdk 34 not found` | Android SDK 平台包没装 | 看 `Verify Android SDK` 步骤输出；缺包时工作流会用 `$ANDROID_HOME/cmdline-tools/*/bin/sdkmanager` 补装。**不要**再用 `android-actions/setup-android@v3`：它会 `sdkmanager --install "tools"`，该包已下线，action 直接失败 |
| `SDK location not found`、`ANDROID_HOME` 相关 | SDK 环境变量缺失 | `ubuntu-latest` runner 自带 `ANDROID_HOME`；本工程不需要 `local.properties`，自定义 runner 才需自行设置 |
| `Minimum supported Gradle version is 8.7`、`AGP 8.5.2 requires Gradle 8.7` | Gradle 版本不匹配 | workflow 已用 `gradle/actions/setup-gradle@v3` 固定 `gradle-version: '8.7'`；**不要提交 gradle-wrapper**，也不要本地改成 8.6 |
| Kotlin 编译错误：`Unresolved reference: ...`、`e: file:...` | 代码/资源问题 | 按日志里的 `文件:行号` 定位；`Unresolved reference: R` 通常是 `res/` 资源缺失或布局 id 与代码不一致；`jvmTarget` 不一致则检查是否被改成 11/21 |
| `Could not resolve com.github.mik3y:usb-serial-for-android:3.8.1`、`Could not GET ...` | 依赖下载失败（网络抖动/仓库源缺失） | 确认 `settings.gradle.kts` 里有 `mavenCentral()` 与 `maven { url = uri("https://jitpack.io") }`（usb-serial-for-android **只发布在 JitPack**）；直接 **Re-run all jobs** 重试 |
| `upload-artifact` 报 `No files were found with the provided path` | APK 没生成（前一步已失败）或路径改了 | 本工程路径固定为 `app/build/outputs/apk/debug/app-debug.apk`；先确认 `Build debug APK` 是绿的；`if-no-files-found: error` 会主动让这一步失败，属于保护 |
| 明明改了代码但报错没变、日志像旧的 | Gradle 缓存失效/陈旧 | Actions → **Re-run all jobs**（必要时在 Actions → Caches 删除对应缓存）；`--no-daemon` 已禁用常驻进程 |
| `refusing to allow a personal access token to create or update workflow` | PAT 缺 workflow 权限 | classic token 补勾 `workflow`；fine-grained 加 **Workflows: Read and write** |
| `remote: Invalid username or password` | 密码位置填了账号密码 | 密码处填 PAT；确认 token 未过期/未被 Revoke |
| Actions 页面没有 `Build APK` | workflow 不在默认分支，或 YAML 缩进错误 | 确认 `.github/workflows/build.yml` 已 push 到 `main`；用编辑器检查 YAML（两个空格缩进、无 Tab） |

### 重跑方式

- 单次失败：run 页面右上 **Re-run all jobs**；
- 想清缓存：**Re-run all jobs** 时勾选 debug 选项，或到仓库 Settings → Actions → Caches 删除对应缓存；
- 修改代码后：直接再 push 一次 `main` 即可。

### 排查顺序建议

1. 看失败的 **step 名称**，先区分「环境问题」还是「代码问题」；
2. 环境问题对照上表第 1–4 行；
3. 代码问题看 `e: file:line` 具体行；
4. 都没头绪时，在本地检查 `app/src/main/AndroidManifest.xml` 引用的资源（`@string/app_name`、`@xml/device_filter`、主题）与 `MainActivity` 的 `R.id.*` 是否都存在。

## 7. 验收清单（CI 侧）

- [ ] GitHub 上仓库为**空仓库**创建，未带 README / .gitignore / License；
- [ ] 首次 `git push -u origin main` 成功（PAT 含 workflow 权限）；
- [ ] Actions 中出现 `Build APK` 并跑完，job `assembleDebug (cloud)` 全绿；
- [ ] 能下载 artifact `AceSolo-debug`，解压得到 `app-debug.apk`；
- [ ] APK 可安装到手机并启动（见 [README 第 6 节](../README.md#6-安装与真机验证)）；
- [ ] 任意一次改代码 push 后，无需本机任何 Android 工具即可再次产出 APK。
