# -*- coding: utf-8 -*-
"""构建 testory-assistant.apk 并复制到 config/plugin_bundles/。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APK_PROJECT = ROOT / "mobile_assistant_apk_v2"  # v2 Kotlin/Compose project
OUTPUT_APK = ROOT / "config" / "plugin_bundles" / "testory-assistant.apk"
GRADLE_VERSION = "8.4"
CMDTOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _default_sdk_root() -> Path:
    if _is_windows():
        for drive in ("C:", "D:"):
            candidate = Path(f"{drive}/testory-android-sdk")
            if _sdk_ready(candidate):
                return candidate
        return Path("C:/testory-android-sdk")
    return (Path.home() / "testory-android-sdk").resolve()


def _sdk_candidates() -> list[Path]:
    out: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            out.append(Path(raw))
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        out.append(Path(local) / "Android" / "Sdk")
    user = (os.environ.get("USERPROFILE") or "").strip()
    if user:
        out.append(Path(user) / "AppData" / "Local" / "Android" / "Sdk")
    out.append(_default_sdk_root())
    out.append(ROOT / "AndroidSDK")
    return out


def _sdk_ready(sdk_root: Path) -> bool:
    platforms = sdk_root / "platforms" / "android-34"
    build_tools = sdk_root / "build-tools" / "34.0.0"
    return platforms.is_dir() and build_tools.is_dir()


def _run(cmd: list[str], *, env: dict | None = None, cwd: Path | None = None, input_text: str | None = None) -> None:
    print("+", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        text=True,
        input=input_text,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败 ({proc.returncode}): {' '.join(cmd)}")


def _accept_sdk_licenses(sdkmanager: Path, sdk_root: Path, env: dict) -> None:
    yes = "\n".join(["y"] * 40) + "\n"
    _run(
        [str(sdkmanager), "--sdk_root=" + str(sdk_root), "--licenses"],
        env=env,
        input_text=yes,
    )


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def _ensure_cmdline_tools(sdk_root: Path) -> Path:
    latest = sdk_root / "cmdline-tools" / "latest" / "bin"
    sdkmanager = latest / ("sdkmanager.bat" if _is_windows() else "sdkmanager")
    if sdkmanager.is_file():
        return sdkmanager

    sdk_root.mkdir(parents=True, exist_ok=True)
    zip_path = sdk_root / "cmdline-tools.zip"
    _download(CMDTOOLS_URL, zip_path)
    tmp = Path("C:/testory-sdk-tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    zip_path.unlink(missing_ok=True)

    src = tmp / "cmdline-tools"
    if not src.is_dir():
        children = [p for p in tmp.iterdir() if p.is_dir()]
        src = children[0] if children else src
    dest = sdk_root / "cmdline-tools" / "latest"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    if not sdkmanager.is_file():
        raise RuntimeError(f"sdkmanager 未找到: {sdkmanager}")
    return sdkmanager


def _ensure_android_sdk() -> Path:
    for candidate in _sdk_candidates():
        try:
            if _sdk_ready(candidate.resolve()):
                print(f"使用已有 SDK: {candidate}")
                return candidate.resolve()
        except OSError:
            continue

    sdk_root = _default_sdk_root()
    if " " in str(sdk_root) or re.search(r"[\u4e00-\u9fff]", str(sdk_root)):
        raise RuntimeError(
            f"默认 SDK 路径不可用（含空格或中文）：{sdk_root}，请设置 ANDROID_HOME 到纯英文无空格目录"
        )

    sdkmanager = _ensure_cmdline_tools(sdk_root)
    env = os.environ.copy()
    env["ANDROID_HOME"] = str(sdk_root)
    env["ANDROID_SDK_ROOT"] = str(sdk_root)
    env["JAVA_HOME"] = env.get("JAVA_HOME") or _detect_java_home()
    _accept_sdk_licenses(sdkmanager, sdk_root, env)

    pkgs = [
        "platform-tools",
        "platforms;android-34",
        "build-tools;34.0.0",
    ]
    for pkg in pkgs:
        _run(
            [
                str(sdkmanager),
                "--sdk_root=" + str(sdk_root),
                "--install",
                pkg,
            ],
            env=env,
        )
    if not _sdk_ready(sdk_root):
        raise RuntimeError("Android SDK 组件安装后仍不完整")
    print(f"SDK 就绪: {sdk_root}")
    return sdk_root


def _detect_java_home() -> str:
    proc = subprocess.run(["java", "-XshowSettings:properties", "-version"], capture_output=True, text=True)
    text = (proc.stderr or "") + (proc.stdout or "")
    for line in text.splitlines():
        if "java.home" in line:
            return line.split("=", 1)[1].strip()
    return ""


def _ensure_gradle_wrapper() -> Path:
    wrapper_dir = APK_PROJECT / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    props = wrapper_dir / "gradle-wrapper.properties"
    if not props.is_file():
        props.write_text(
            f"distributionBase=GRADLE_USER_HOME\n"
            f"distributionPath=wrapper/dists\n"
            f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{GRADLE_VERSION}-bin.zip\n"
            f"networkTimeout=10000\n"
            f"zipStoreBase=GRADLE_USER_HOME\n"
            f"zipStorePath=wrapper/dists\n",
            encoding="utf-8",
        )
    jar = wrapper_dir / "gradle-wrapper.jar"
    if not jar.is_file():
        # Gradle 官方 wrapper jar（固定版本，供 bootstrap 使用）
        _download(
            "https://raw.githubusercontent.com/gradle/gradle/v8.4.0/gradle/wrapper/gradle-wrapper.jar",
            jar,
        )
    gradlew = APK_PROJECT / ("gradlew.bat" if _is_windows() else "gradlew")
    if not gradlew.is_file():
        if _is_windows():
            gradlew.write_text(
                "@if \"%DEBUG%\"==\"\" @echo off\r\n"
                "set DIR=%~dp0\r\n"
                "java -classpath \"%DIR%gradle\\wrapper\\gradle-wrapper.jar\" "
                "org.gradle.wrapper.GradleWrapperMain %*\r\n",
                encoding="utf-8",
            )
        else:
            gradlew.write_text(
                "#!/bin/sh\n"
                "DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
                "exec java -classpath \"$DIR/gradle/wrapper/gradle-wrapper.jar\" "
                "org.gradle.wrapper.GradleWrapperMain \"$@\"\n",
                encoding="utf-8",
            )
            gradlew.chmod(0o755)
    return gradlew


def build_apk() -> Path:
    sdk_root = _ensure_android_sdk()
    gradlew = _ensure_gradle_wrapper()
    env = os.environ.copy()
    env["ANDROID_HOME"] = str(sdk_root)
    env["ANDROID_SDK_ROOT"] = str(sdk_root)
    if not env.get("JAVA_HOME"):
        jh = _detect_java_home()
        if jh:
            env["JAVA_HOME"] = jh

    _run([str(gradlew), "assembleDebug", "--no-daemon", "-q"], env=env, cwd=APK_PROJECT)
    built = APK_PROJECT / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if not built.is_file():
        raise FileNotFoundError(f"未找到构建产物: {built}")

    OUTPUT_APK.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, OUTPUT_APK)
    print(f"已生成: {OUTPUT_APK} ({OUTPUT_APK.stat().st_size} bytes)")
    return OUTPUT_APK


def main() -> int:
    try:
        build_apk()
        return 0
    except Exception as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
