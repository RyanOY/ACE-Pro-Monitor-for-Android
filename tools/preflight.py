#!/usr/bin/env python3
"""AceSolo 本地预检（不依赖 JDK / Android SDK / Gradle）。

用途：在没有 Android 构建环境的机器（例如运行 dsh 的手机）上，提前发现
「资源缺失、ID 拼错、manifest 引用悬空、CI 版本不一致」这类只会在
GitHub Actions 里才暴露的问题。

用法：
    python3 tools/preflight.py            # 在仓库根目录执行

退出码：0 = 全部通过；1 = 存在失败项。
"""

import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
ANDROID = "{http://schemas.android.com/apk/res/android}"

failures = []
warnings = []


def ok(msg):
    print("  \033[32mOK\033[0m   " + msg)


def fail(msg):
    failures.append(msg)
    print("  \033[31mFAIL\033[0m " + msg)


def warn(msg):
    warnings.append(msg)
    print("  \033[33mWARN\033[0m " + msg)


def check_required_files():
    print("[1/7] 必需文件")
    required = [
        "settings.gradle.kts",
        "build.gradle.kts",
        "gradle.properties",
        ".gitignore",
        ".github/workflows/build.yml",
        "app/build.gradle.kts",
        "app/proguard-rules.pro",
        "app/src/main/AndroidManifest.xml",
        "app/src/main/java/com/acesolo/MainActivity.kt",
        "app/src/main/java/com/acesolo/UsbSerialManager.kt",
        "app/src/main/res/layout/activity_main.xml",
        "app/src/main/res/values/strings.xml",
        "app/src/main/res/xml/device_filter.xml",
    ]
    for rel in required:
        path = os.path.join(ROOT, rel)
        if os.path.isfile(path):
            ok(rel)
        else:
            fail("缺少 " + rel)


def check_xml():
    print("[2/7] XML 合法性")
    paths = glob.glob(os.path.join(ROOT, "app/src/main/res/**/*.xml"), recursive=True)
    paths.append(os.path.join(ROOT, "app/src/main/AndroidManifest.xml"))
    for path in sorted(paths):
        rel = os.path.relpath(path, ROOT)
        try:
            ET.parse(path)
            ok(rel)
        except Exception as exc:  # noqa: BLE001
            fail("%s 解析失败: %s" % (rel, exc))


def load_strings():
    print("[3/7] strings.xml 与引用")
    path = os.path.join(ROOT, "app/src/main/res/values/strings.xml")
    keys = set()
    if os.path.isfile(path):
        root = ET.parse(path).getroot()
        for child in root:
            name = child.get("name")
            if name:
                keys.add(name)

    # Kotlin 里用到的 R.string.*
    used = set()
    for kt in glob.glob(os.path.join(ROOT, "app/src/main/java/**/*.kt"), recursive=True):
        used |= set(re.findall(r"R\.string\.(\w+)", open(kt, encoding="utf-8").read()))
    missing = sorted(used - keys)
    if missing:
        fail("Kotlin 引用了不存在的 string: %s" % ", ".join(missing))
    else:
        ok("Kotlin R.string 引用 %d 个，全部存在" % len(used))

    # layout / manifest 里的 @string/...
    for path in glob.glob(os.path.join(ROOT, "app/src/main/res/layout/*.xml")) + [
        os.path.join(ROOT, "app/src/main/AndroidManifest.xml")
    ]:
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        refs = set(re.findall(r"@string/(\w+)", text))
        missing = sorted(refs - keys)
        if missing:
            fail("%s 引用不存在的 string: %s" % (os.path.basename(path), ", ".join(missing)))
    ok("layout/manifest 的 @string 引用检查完成")


def check_ids():
    print("[4/7] layout ID 与 Kotlin findViewById 一致性")
    layout = os.path.join(ROOT, "app/src/main/res/layout/activity_main.xml")
    if not os.path.isfile(layout):
        fail("缺少 activity_main.xml，跳过 ID 检查")
        return
    tree = ET.parse(layout)
    ids = set()
    for el in tree.iter():
        value = el.get(ANDROID + "id")
        if value and value.startswith("@+id/"):
            ids.add(value[len("@+id/"):])
    used = set()
    for kt in glob.glob(os.path.join(ROOT, "app/src/main/java/**/*.kt"), recursive=True):
        used |= set(re.findall(r"R\.id\.(\w+)", open(kt, encoding="utf-8").read()))
    missing = sorted(used - ids)
    unused = sorted(ids - used)
    if missing:
        fail("Kotlin findViewById 了 layout 中不存在的 id: %s" % ", ".join(missing))
    else:
        ok("Kotlin 使用 %d 个 id，layout 中全部存在" % len(used))
    if unused:
        warn("layout 中有未使用的 id: %s" % ", ".join(unused))


def check_manifest():
    print("[5/7] AndroidManifest 关键项")
    path = os.path.join(ROOT, "app/src/main/AndroidManifest.xml")
    if not os.path.isfile(path):
        fail("缺少 AndroidManifest.xml")
        return
    text = open(path, encoding="utf-8").read()
    root = ET.parse(path).getroot()
    package = root.get("package")
    if package:
        warn("manifest 声明了 package=%s（AGP 8 建议用 namespace，当前 build.gradle.kts 已有 namespace）" % package)

    app = root.find("application")
    if app is None:
        fail("manifest 缺少 <application>")
        return
    activity = app.find("activity")
    if activity is None:
        fail("manifest 缺少 <activity>")
        return
    name = activity.get(ANDROID + "name")
    if name not in (".MainActivity", "com.acesolo.MainActivity"):
        fail("activity name 异常: %s（应为 .MainActivity）" % name)
    else:
        ok("activity = %s" % name)
    if activity.get(ANDROID + "exported") != "true":
        fail("targetSdk 34 下带 intent-filter 的 activity 必须 android:exported=\"true\"")
    else:
        ok("activity exported=true")

    actions = {el.get(ANDROID + "name") for el in activity.iter("action")}
    for want in (
        "android.intent.action.MAIN",
        "android.hardware.usb.action.USB_DEVICE_ATTACHED",
    ):
        if want in actions:
            ok("intent-filter action: " + want)
        else:
            fail("缺少 intent-filter action: " + want)

    meta = activity.find("meta-data")
    if meta is None or meta.get(ANDROID + "resource") != "@xml/device_filter":
        fail("缺少 USB 设备过滤 meta-data（android:resource=\"@xml/device_filter\"）")
    else:
        ok("USB device_filter meta-data")

    if "android.hardware.usb.host" in text:
        ok("uses-feature usb.host")
    else:
        warn("未见 uses-feature android.hardware.usb.host")

    for theme in re.findall(r'android:theme="([^"]+)"', text):
        if theme.startswith("@style/Theme.Material3"):
            ok("theme = %s（由 material 库提供）" % theme)
        else:
            warn("theme = %s，预检无法校验，请确认资源存在" % theme)


def check_device_filter():
    print("[6/7] device_filter VID/PID")
    path = os.path.join(ROOT, "app/src/main/res/xml/device_filter.xml")
    if not os.path.isfile(path):
        fail("缺少 device_filter.xml")
        return
    root = ET.parse(path).getroot()
    entries = []
    for el in root.findall("usb-device"):
        vid = el.get("vendor-id")
        pid = el.get("product-id")
        if vid is None or pid is None:
            fail("<usb-device> 缺少 vendor-id/product-id")
            continue
        try:
            vid_i, pid_i = int(vid), int(pid)
        except ValueError:
            fail("VID/PID 必须是十进制整数: vendor-id=%s product-id=%s" % (vid, pid))
            continue
        if vid_i > 0xFFFF or pid_i > 0xFFFF:
            fail("VID/PID 超出 0xFFFF: %04X:%04X" % (vid_i, pid_i))
        entries.append((vid_i, pid_i))
    if not entries:
        fail("device_filter 没有有效条目")
    else:
        ok("%d 个 USB 设备过滤条目" % len(entries))
        for vid_i, pid_i in entries:
            print("        0x%04X:0x%04X" % (vid_i, pid_i))
    if not re.search(r"<!--", open(path, encoding="utf-8").read()):
        warn("建议在 device_filter 里用注释标注芯片型号")


def check_ci_consistency():
    print("[7/7] CI 与 Gradle 配置一致性")
    workflow_path = os.path.join(ROOT, ".github/workflows/build.yml")
    app_gradle_path = os.path.join(ROOT, "app/build.gradle.kts")
    root_gradle_path = os.path.join(ROOT, "build.gradle.kts")
    for path in (workflow_path, app_gradle_path, root_gradle_path):
        if not os.path.isfile(path):
            fail("缺少 " + os.path.relpath(path, ROOT))
            return
    workflow = open(workflow_path, encoding="utf-8").read()
    app_gradle = open(app_gradle_path, encoding="utf-8").read()
    root_gradle = open(root_gradle_path, encoding="utf-8").read()

    if "\t" in workflow:
        fail("workflow 含制表符（YAML 不允许 tab 缩进）")
    else:
        ok("workflow 无 tab 缩进")

    for key in ("on:", "jobs:", "runs-on:", "steps:"):
        if key not in workflow:
            fail("workflow 缺少 %s" % key)

    compile_sdk = re.search(r"compileSdk\s*=\s*(\d+)", app_gradle)
    if compile_sdk:
        sdk = compile_sdk.group(1)
        if "platforms;android-%s" % sdk in workflow:
            ok("compileSdk %s 与 CI 安装的 platforms;android-%s 一致" % (sdk, sdk))
        else:
            fail("compileSdk %s，但 CI 未安装 platforms;android-%s" % (sdk, sdk))
    build_tools = re.search(r'build-tools;([\d.]+)', workflow)
    if build_tools:
        ok("CI build-tools %s" % build_tools.group(1))

    agp = re.search(r'id\("com\.android\.application"\)\s*version\s*"([\d.]+)"', root_gradle)
    if agp:
        ok("AGP %s" % agp.group(1))
    gradle_ver = re.search(r"gradle-version:\s*'?([\d.]+)'?", workflow)
    if gradle_ver:
        ok("CI Gradle %s" % gradle_ver.group(1))

    if "assembleDebug" in workflow:
        ok("CI 执行 assembleDebug")
    else:
        fail("CI 未执行 assembleDebug")
    if "app/build/outputs/apk/debug/app-debug.apk" not in workflow:
        fail("CI artifact 路径不是 app/build/outputs/apk/debug/app-debug.apk")
    else:
        ok("CI artifact 路径正确")

    # 未使用 Gradle Wrapper 时必须由 setup-gradle 提供 gradle 可执行文件
    has_wrapper = os.path.isfile(os.path.join(ROOT, "gradle/wrapper/gradle-wrapper.properties"))
    if not has_wrapper and "gradle/actions/setup-gradle" not in workflow:
        fail("仓库没有 Gradle Wrapper，CI 也没用 setup-gradle，构建会找不到 gradle 命令")
    else:
        ok("Gradle 获取方式一致（wrapper=%s, setup-gradle=%s）" % (
            "有" if has_wrapper else "无",
            "有" if "gradle/actions/setup-gradle" in workflow else "无"))


def main():
    print("AceSolo preflight @ %s\n" % ROOT)
    check_required_files()
    check_xml()
    load_strings()
    check_ids()
    check_manifest()
    check_device_filter()
    check_ci_consistency()
    print("\n结果：%d 个失败，%d 个警告" % (len(failures), len(warnings)))
    if failures:
        print("\n失败项：")
        for item in failures:
            print(" - " + item)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
