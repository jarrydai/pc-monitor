# -*- coding: utf-8 -*-
"""
不用 Gradle 直接打包 APK:
  aapt2 编译资源/合并清单 -> javac 编译 -> d8 生成 dex -> 打包 -> zipalign -> apksigner 签名
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.join(ROOT, "sdk")
JDK = os.path.join(ROOT, "jdk", "jdk-17.0.20.1+1")
BT = os.path.join(SDK, "build-tools", "33.0.2")
AJAR = os.path.join(SDK, "platforms", "android-33", "android.jar")
APP = os.path.join(ROOT, "app")
OUT = os.path.join(ROOT, "out")
KEYSTORE = os.path.join(ROOT, "debug.keystore")
TARGET_APK = os.environ.get("PCM_APK") or os.path.join(
    ROOT, "..", "pc-monitor", "static", "pc-monitor.apk")

ENV = dict(os.environ)
ENV["JAVA_HOME"] = JDK
ENV["PATH"] = os.path.join(JDK, "bin") + os.pathsep + ENV.get("PATH", "")
ENV["PYTHONIOENCODING"] = "utf-8"


def run(cmd, **kw):
    print("  $ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    p = subprocess.run(cmd, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        print(out[-4000:])
        raise SystemExit("命令失败 (exit %d): %s" % (p.returncode, cmd[0]))
    if out.strip():
        for line in out.strip().splitlines()[-6:]:
            print("    " + line)
    return out


def main():
    for path, name in [(AJAR, "android.jar"), (BT, "build-tools")]:
        if not os.path.exists(path):
            raise SystemExit("缺少 %s: %s" % (name, path))

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    for d in ("gen", "classes", "dex"):
        os.makedirs(os.path.join(OUT, d))

    aapt2 = os.path.join(BT, "aapt2.exe")
    javac = os.path.join(JDK, "bin", "javac.exe")
    keytool = os.path.join(JDK, "bin", "keytool.exe")
    d8jar = os.path.join(BT, "lib", "d8.jar")
    zipalign = os.path.join(BT, "zipalign.exe")
    apksigner = os.path.join(BT, "lib", "apksigner.jar")
    java = os.path.join(JDK, "bin", "java.exe")

    print("[1/6] 编译资源")
    res_zip = os.path.join(OUT, "res.zip")
    run([aapt2, "compile", "--dir", os.path.join(APP, "res"), "-o", res_zip])

    print("[2/6] 链接资源与清单")
    base_apk = os.path.join(OUT, "base.apk")
    assets = os.path.join(APP, "assets")
    extra = ["-A", assets] if os.path.isdir(assets) else []
    run([aapt2, "link", "-o", base_apk, "-I", AJAR,
         "--manifest", os.path.join(APP, "AndroidManifest.xml"),
         "-R", res_zip, "--java", os.path.join(OUT, "gen"),
         "--auto-add-overlay", "--min-sdk-version", "21",
         "--target-sdk-version", "33"] + extra)

    print("[3/6] 编译 Java")
    srcs = []
    for root, _, files in os.walk(os.path.join(APP, "src")):
        for f in files:
            if f.endswith(".java"):
                srcs.append(os.path.join(root, f))
    gen_src = os.path.join(OUT, "gen")
    for root, _, files in os.walk(gen_src):
        for f in files:
            if f.endswith(".java"):
                srcs.append(os.path.join(root, f))
    run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8",
         "-bootclasspath", AJAR, "-classpath", AJAR,
         "-nowarn", "-d", os.path.join(OUT, "classes")] + srcs)

    print("[4/6] 生成 dex")
    class_files = []
    for root, _, files in os.walk(os.path.join(OUT, "classes")):
        for f in files:
            if f.endswith(".class"):
                class_files.append(os.path.join(root, f))
    run([java, "-cp", d8jar, "com.android.tools.r8.D8",
         "--lib", AJAR, "--min-api", "21",
         "--output", os.path.join(OUT, "dex")] + class_files)

    print("[5/6] 打包 + 对齐")
    unaligned = os.path.join(OUT, "app-unaligned.apk")
    shutil.copyfile(base_apk, unaligned)
    with zipfile.ZipFile(unaligned, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(OUT, "dex", "classes.dex"), "classes.dex")
    aligned = os.path.join(OUT, "app-aligned.apk")
    run([zipalign, "-f", "4", unaligned, aligned])

    print("[6/6] 签名")
    if not os.path.exists(KEYSTORE):
        run([keytool, "-genkeypair", "-keystore", KEYSTORE,
             "-storepass", "android", "-keypass", "android",
             "-alias", "androiddebugkey", "-keyalg", "RSA",
             "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=Android Debug,O=Android,C=US"])
    # 先签名到 out 目录，再复制，避免旧的 v2 签名块残留
    signed = os.path.join(OUT, "pc-monitor.apk")
    run([java, "-jar", apksigner, "sign",
         "--ks", KEYSTORE, "--ks-pass", "pass:android",
         "--key-pass", "pass:android", "--ks-key-alias", "androiddebugkey",
         "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
         "--out", signed, aligned])

    os.makedirs(os.path.dirname(TARGET_APK), exist_ok=True)
    if os.path.exists(TARGET_APK):
        os.remove(TARGET_APK)
    shutil.copyfile(signed, TARGET_APK)

    size = os.path.getsize(TARGET_APK) / 1024.0
    print("\n完成: %s  (%.1f KB)" % (TARGET_APK, size))


if __name__ == "__main__":
    main()
