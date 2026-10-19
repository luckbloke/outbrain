#!/usr/bin/env bash
# 外脑 · Cordis Brain —— 在 Linux 容器里构建 Android APK（python-for-android + buildozer）
#
# 用法（在 kivy/buildozer 容器内）：
#   bash /src/android/build_android.sh
#
# 做的事：
#   1) 预置 python-for-android 源码（本机直连 github.com 不通，改走 codeload 镜像）；
#   2) 国内网络适配：把 GitHub/openssl/CPython 的下载地址换成可达且快的镜像，
#      并给所有下载加上超时（默认 urlopen/urlretrieve 无超时，断流会永久卡死）；
#   3) 把项目临时拼装成 buildozer 需要的 source.dir（$WORK/app）；
#   4) 运行 `buildozer android debug`；
#   5) 把产出的 APK 拷回项目 android/dist/，日志拷回 android/buildozer.log。
#
# $WORK 放在持久化卷里（默认 $HOME/work）：已编译好的 python 发行版 / SDK / NDK 都会复用，重跑快很多。
set -euo pipefail

SRC="${SRC:-/src}"
WORK="${WORK:-$HOME/work}"
HERE="$SRC/android"
P4A_DIR="$WORK/.buildozer/android/platform/python-for-android"
P4A_TARBALL_URL="${P4A_TARBALL_URL:-https://codeload.github.com/kivy/python-for-android/tar.gz/refs/heads/master}"
LOG="$WORK/buildozer.log"

# 无论成功失败，都把构建日志回传到 android/buildozer.log，方便在 Windows 侧排查
trap 'cp -f "$LOG" "$HERE/buildozer.log" 2>/dev/null || true' EXIT

echo "=============================================================="
echo " 外脑 · Cordis Brain —— Android APK 构建"
echo "  源码  : $SRC"
echo "  工作区: $WORK"
echo "=============================================================="

# ---------------------------------------------------------------- 1) p4a 源码
seed_p4a() {
    if [ -f "$P4A_DIR/pythonforandroid/toolchain.py" ]; then
        echo "[p4a] 已存在：$P4A_DIR"
        return 0
    fi
    echo "[p4a] 首次运行：下载 python-for-android 源码（github.com 直连不可用，走 codeload）"
    mkdir -p "$P4A_DIR"
    # 容器里没有 curl/wget，用系统 python 下载
    python3 - "$P4A_TARBALL_URL" /tmp/p4a.tar.gz <<'PY'
import sys, urllib.request
url, dest = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "buildozer-seed"})
with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        f.write(chunk)
print("downloaded", dest)
PY
    tar -xzf /tmp/p4a.tar.gz -C "$P4A_DIR" --strip-components=1
    rm -f /tmp/p4a.tar.gz
    # 让 buildozer 的「已是 git 仓库且 url/branch 一致 → 跳过 git clone」检查通过
    (
        cd "$P4A_DIR"
        git init -q -b master
        git remote add origin https://github.com/kivy/python-for-android.git
        git config user.email build@local
        git config user.name build
        git add -A
        git commit -qm "seed p4a source from codeload tarball"
    )
    echo "[p4a] 就绪：$(cd "$P4A_DIR" && git log -1 --format=%h)"
}
seed_p4a

# ---------------------------------------------------------------- 2) 国内网络适配
adapt_network() {
    echo "[net] 国内网络适配（GitHub→codeload/ghproxy、openssl→华为云、Gradle→腾讯云、下载加超时）"
    python3 - "$P4A_DIR" <<'PY'
import glob, os, re, sys

p4a = sys.argv[1]
REC = os.path.join(p4a, "pythonforandroid", "recipes")
MARK = os.path.join(p4a, ".cordis_net_patched")


def rewrite(t):
    # GitHub「归档」→ codeload（字节完全一致、解包目录名不变，只是换 CDN）
    t = re.sub(r"https://github\.com/([\w.\-]+)/([\w.\-]+)/archive/refs/tags/([^\s'\"]+?)\.tar\.gz",
               r"https://codeload.github.com/\1/\2/tar.gz/refs/tags/\3", t)
    t = re.sub(r"https://github\.com/([\w.\-]+)/([\w.\-]+)/archive/refs/heads/([^\s'\"]+?)\.tar\.gz",
               r"https://codeload.github.com/\1/\2/tar.gz/refs/heads/\3", t)
    t = re.sub(r"https://github\.com/([\w.\-]+)/([\w.\-]+)/archive/([^\s'\"]+?)\.tar\.gz",
               r"https://codeload.github.com/\1/\2/tar.gz/\3", t)
    t = re.sub(r"https://github\.com/([\w.\-]+)/([\w.\-]+)/archive/([^\s'\"]+?)\.zip",
               r"https://codeload.github.com/\1/\2/zip/\3", t)
    # GitHub「release 资产」（codeload 不提供）→ ghproxy 代理（负向断言避免重复加前缀）
    t = re.sub(r"(?<!ghproxy\.net/)https://github\.com/([\w.\-]+)/([\w.\-]+)/releases/download/",
               r"https://ghproxy.net/https://github.com/\1/\2/releases/download/", t)
    # openssl.org 不可达。注意：华为云**并没有**镜像 openssl —— /openssl/source/ 返回的
    # 是门户 HTML（下载下来不是 gzip，tar 解包直接报错）。改走 codeload 的 GitHub
    # 标签归档，内容与官方源码包等价（含 Configure / VERSION.dat / config）。
    t = t.replace("https://www.openssl.org/source/openssl-{version}.tar.gz",
                  "https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/openssl-{version}")
    return t


if os.path.exists(MARK):
    print("  已适配过，跳过 URL 重写")
else:
    n = 0
    for p in glob.glob(os.path.join(REC, "*", "__init__.py")):
        s = open(p, encoding="utf-8").read()
        r = rewrite(s)
        if r != s:
            open(p, "w", encoding="utf-8").write(r)
            n += 1
            print("  patched recipe:", os.path.basename(os.path.dirname(p)))
    open(MARK, "w").write("ok\n")
    print("  共重写 %d 个 recipe 的下载地址" % n)

# CPython 源码：github/codeload 到本机被限速到 ~20KB/s，而归档有 2×22MB，
# 会白白多花 ~40 分钟。改用华为云镜像上的**官方** Python-3.14.2.tar.xz
# （内容等价：configure / config.guess / config.sub / Makefile.pre.in / Modules/Setup
#  均在，p4a 用到的补丁全部能干净套用；~815KB/s）。
def rewrite_cpython(t):
    t = t.replace(
        "https://codeload.github.com/python/cpython/tar.gz/refs/tags/v{version}",
        "https://mirrors.huaweicloud.com/python/{version}/Python-{version}.tar.xz")
    t = t.replace(
        "https://github.com/python/cpython/archive/refs/tags/v{version}.tar.gz",
        "https://mirrors.huaweicloud.com/python/{version}/Python-{version}.tar.xz")
    return t


n = 0

# 已适配过的仓库里，openssl 曾被错误地指向华为云（下载到的是 HTML 门户页），
# 现在统一改回 codeload；顺手删掉那份坏的缓存（含 mark），逼它重新下载。
def rewrite_openssl(t):
    t = t.replace(
        "https://mirrors.huaweicloud.com/openssl/source/openssl-{version}.tar.gz",
        "https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/openssl-{version}")
    t = t.replace(
        "https://www.openssl.org/source/openssl-{version}.tar.gz",
        "https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/openssl-{version}")
    return t


for p in glob.glob(os.path.join(REC, "*", "__init__.py")):
    s = open(p, encoding="utf-8").read()
    r = rewrite_openssl(rewrite_cpython(s))
    if r != s:
        open(p, "w", encoding="utf-8").write(r)
        n += 1
if n:
    print("  源码地址修正：CPython→华为云 / openssl→codeload（%d 个 recipe）" % n)

if "mirrors.huaweicloud.com/openssl" not in open(
        os.path.join(REC, "openssl", "__init__.py"), encoding="utf-8").read():
    # 只删「旧 URL 的产物」：新 URL 的文件名是 openssl-3.3.1（无 .tar.gz 后缀），
    # 这两个通配符永远不会命中新文件，所以不会反复触发重新下载。
    for pat in ("openssl-*.tar.gz", ".mark-openssl-*.tar.gz"):
        for bad in glob.glob(os.path.join(p4a, "..", "build-*", "packages",
                                          "openssl", pat)):
            os.remove(bad)
            print("  已删除坏的 openssl 缓存：", os.path.basename(bad))

# Gradle 发行包：services.gradle.org 连不上 → 腾讯云镜像；用 -bin 省 ~90MB
gw = os.path.join(p4a, "pythonforandroid", "bootstraps", "common", "build",
                  "gradle", "wrapper", "gradle-wrapper.properties")
s = open(gw, encoding="utf-8").read()
s = re.sub(r"^distributionUrl=.*$",
           "distributionUrl=https\\://mirrors.cloud.tencent.com/gradle/gradle-8.14.3-bin.zip",
           s, flags=re.M)
open(gw, "w", encoding="utf-8").write(s)
print("  gradle:", [l for l in s.splitlines() if l.startswith("distributionUrl")][0])

# p4a 的 urlretrieve 用全局 socket 默认超时 → 设 120s，断流时抛错交由 p4a 自带的 5 次重试处理
init = os.path.join(p4a, "pythonforandroid", "__init__.py")
s = open(init, encoding="utf-8").read()
if "setdefaulttimeout" not in s:
    if not s.endswith("\n"):
        s += "\n"
    s += "import socket as _socket\n_socket.setdefaulttimeout(120)\n"
    open(init, "w", encoding="utf-8").write(s)
    print("  p4a socket default timeout = 120s")

# p4a 的 unpack() 用「文件名后缀」判断压缩包类型。codeload 的 URL 结尾没有
# .tar.gz/.zip（文件就叫 v3.14.2 之类），会被判成「无法解压」。加一层按内容嗅探的兜底。
rp = os.path.join(p4a, "pythonforandroid", "recipe.py")
s = open(rp, encoding="utf-8").read()
if "tarfile.is_tarfile" not in s:
    s = s.replace("import zipfile\n", "import zipfile\nimport tarfile\n", 1)
    old_zip = "if extraction_filename.endswith(('.zip', '.whl')):"
    new_zip = ("if (extraction_filename.endswith(('.zip', '.whl'))\n"
               "                            or zipfile.is_zipfile(extraction_filename)):")
    old_tar = ("elif extraction_filename.endswith(\n"
               "                            ('.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz')):")
    new_tar = ("elif (extraction_filename.endswith(\n"
               "                            ('.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz'))\n"
               "                            or tarfile.is_tarfile(extraction_filename)):")
    if old_zip not in s or old_tar not in s:
        raise SystemExit("!! p4a unpack() 结构与预期不符，无法打补丁")
    s = s.replace(old_zip, new_zip, 1).replace(old_tar, new_tar, 1)
    open(rp, "w", encoding="utf-8").write(s)
    print("  p4a unpack(): 增加「按内容嗅探压缩包类型」的兜底")

# buildozer 自己的下载（SDK/NDK）也用 urlopen，同样加上超时
try:
    import buildozer
    bo = os.path.join(os.path.dirname(buildozer.__file__), "buildops.py")
    s = open(bo, encoding="utf-8").read()
    if "urlopen(request) as response" in s:
        s = s.replace("with urlopen(request) as response:",
                      "with urlopen(request, timeout=120) as response:")
        open(bo, "w", encoding="utf-8").write(s)
        print("  buildozer download timeout = 120s")
except Exception as e:
    print("  (warn) buildozer patch skipped:", e)

# 跳过 Apache ANT：gradle 构建用不到；archive.apache.org 在国内外网几乎不可达
# （buildozer 只在 apache-ant-1.9.4 目录「存在」时跳过下载，所以建个空目录即可）
ant_dir = os.path.join(os.path.expanduser("~"), ".buildozer", "android", "platform",
                       "apache-ant-1.9.4")
os.makedirs(ant_dir, exist_ok=True)
for n in os.listdir(ant_dir):
    try:
        os.remove(os.path.join(ant_dir, n))
    except OSError:
        pass
print("  Apache ANT 跳过（%s 已存在）" % ant_dir)
PY
}
adapt_network

# ---------------------------------------------------------------- 2.5) 安卓端能力补丁
# p4a 的 webview bootstrap 只装了最基础的 WebView：没有 WebChromeClient，于是
#   · navigator.mediaDevices.getUserMedia() 永远被拒绝  → 语音输入不可用
#   · <input type="file"> 点了没反应                    → 文件捕获不可用
#   · 附件下载（导出 JSON / 备份）静默失败
# 这里给 PythonActivity 补上 WebChromeClient（麦克风授权 + 文件选择器）、
# DownloadListener，并把 p4a 那个英文 Loading 页换成外脑自己的启动页。
#
# 注意：p4a 的 assemble_distribution() 会 rmdir + cp -r 重建 dist，所以补丁要
# 同时写进「bootstrap 源码」（重建时用）和「已存在的 dist」（直接 gradle 编译用）。
patch_android() {
    echo "[android] 注入 WebView 能力补丁（麦克风 / 文件选择 / 下载 / 启动页）"
    DIST="$WORK/.buildozer/android/platform/build-arm64-v8a/dists/cordisbrain"
    export P4A_DIR DIST
    python3 - "$P4A_DIR" "$DIST" <<'PY'
import glob
import os
import sys

p4a, dist = sys.argv[1], sys.argv[2]
BS = os.path.join(p4a, "pythonforandroid", "bootstraps", "webview", "build")
PATCH_TAG = "CORDIS_PATCH_V3"

# ---------------------------------------------------------------- Java 补丁
JAVA_ANCHORS = [
    (
        "import android.os.Bundle;\nimport android.os.PowerManager;",
        "import android.Manifest;\nimport android.media.MediaRecorder;\n"
        "import android.os.Bundle;\n"
        "import android.os.PowerManager;\n"
        "import android.util.Base64;\n"
        "import java.io.ByteArrayOutputStream;\n"
        "import java.io.File;\n"
        "import java.io.FileInputStream;",
    ),
    (
        "import android.webkit.CookieManager;\nimport android.webkit.WebBackForwardList;",
        "import android.webkit.CookieManager;\n"
        "import android.webkit.DownloadListener;\n"
        "import android.webkit.JavascriptInterface;\n"
        "import android.webkit.PermissionRequest;\n"
        "import android.webkit.ValueCallback;\n"
        "import android.webkit.WebBackForwardList;\n"
        "import android.webkit.WebChromeClient;",
    ),
    (
        '            mWebView.getSettings().setDomStorageEnabled(true);\n'
        '            mWebView.loadUrl("file:///android_asset/_load.html");',
        '            mWebView.getSettings().setDomStorageEnabled(true);\n'
        '            mWebView.getSettings().setMediaPlaybackRequiresUserGesture(false);\n'
        '            mWebView.getSettings().setAllowFileAccess(true);\n'
        '            mWebView.setWebChromeClient(new CordisChromeClient());\n'
        '            mWebView.setDownloadListener(new CordisDownloadListener());\n'
        '            mWebView.addJavascriptInterface(new CordisAudioBridge(), "CordisAudio");\n'
        '            // 启动后主动申请一次录音权限：避免首次点「语音」时系统授权框与\n'
        '            // WebView 的 grant() 撞在一起，出现「权限已给但 AudioRecord 起不来」。\n'
        '            mWebView.postDelayed(new Runnable() {\n'
        '                @Override\n'
        '                public void run() {\n'
        '                    cordisRequestAudioPermission();\n'
        '                }\n'
        '            }, 800);\n'
        '            mWebView.loadUrl("file:///android_asset/_load.html");',
    ),
    (
        "    @Override\n    protected void onActivityResult(int requestCode, int resultCode, Intent intent) {\n"
        "        if (this.activityResultListeners == null) return;",
        "    @Override\n    protected void onActivityResult(int requestCode, int resultCode, Intent intent) {\n"
        "        if (requestCode == CORDIS_FILE_CHOOSER) {\n"
        "            if (cordisFilePathCallback != null) {\n"
        "                Uri[] result = null;\n"
        "                if (resultCode == Activity.RESULT_OK && intent != null) {\n"
        "                    if (intent.getClipData() != null) {\n"
        "                        int n = intent.getClipData().getItemCount();\n"
        "                        result = new Uri[n];\n"
        "                        for (int i = 0; i < n; i++) {\n"
        "                            result[i] = intent.getClipData().getItemAt(i).getUri();\n"
        "                        }\n"
        "                    } else if (intent.getData() != null) {\n"
        "                        result = new Uri[] { intent.getData() };\n"
        "                    }\n"
        "                }\n"
        "                cordisFilePathCallback.onReceiveValue(result);\n"
        "                cordisFilePathCallback = null;\n"
        "            }\n"
        "            return;\n"
        "        }\n"
        "        if (this.activityResultListeners == null) return;",
    ),
    (
        "    @Override\n    public void onRequestPermissionsResult(\n"
        "            int requestCode, String[] permissions, int[] grantResults) {\n"
        '        Log.v(TAG, "onRequestPermissionsResult()");',
        "    @Override\n    public void onRequestPermissionsResult(\n"
        "            int requestCode, String[] permissions, int[] grantResults) {\n"
        '        Log.v(TAG, "onRequestPermissionsResult()");\n'
        "        if (requestCode == CORDIS_AUDIO_PERM) {\n"
        "            if (cordisPendingAudio != null) {\n"
        "                boolean granted = grantResults.length > 0\n"
        "                        && grantResults[0] == PackageManager.PERMISSION_GRANTED;\n"
        "                if (granted) {\n"
        "                    cordisPendingAudio.grant(\n"
        "                            new String[] { PermissionRequest.RESOURCE_AUDIO_CAPTURE });\n"
        "                } else {\n"
        "                    cordisPendingAudio.deny();\n"
        "                }\n"
        "                cordisPendingAudio = null;\n"
        "            }\n"
        "            return;\n"
        "        }",
    ),
]

JAVA_BLOCK = '''    // ============ 外脑补丁 CORDIS_PATCH_V3：WebView 能力 ============
    // p4a 的 webview bootstrap 没装 WebChromeClient，导致：
    //   getUserMedia 恒被拒绝、<input type=file> 无响应、附件下载无反应。
    // 下面补上麦克风运行时授权、文件选择器和下载转发。
    private static final int CORDIS_AUDIO_PERM = 0x0C0D;
    private static final int CORDIS_FILE_CHOOSER = 0x0C0E;

    private static PermissionRequest cordisPendingAudio = null;
    private static ValueCallback<Uri[]> cordisFilePathCallback = null;

    // 启动时主动申请录音权限；用户点「语音」时再走 onPermissionRequest 的即时授权。
    private static void cordisRequestAudioPermission() {
        PythonActivity act = PythonActivity.mActivity;
        if (act == null) return;
        try {
            if (act.checkCurrentPermission(Manifest.permission.RECORD_AUDIO)) return;
            Log.v(TAG, "requesting RECORD_AUDIO at startup");
            act.requestPermissionsWithRequestCode(
                    new String[] { Manifest.permission.RECORD_AUDIO }, CORDIS_AUDIO_PERM);
        } catch (Exception e) {
            Log.e(TAG, "request audio permission failed", e);
        }
    }

    // ---- 原生录音桥（Android）：WebView 的 getUserMedia 在部分机型上会被系统的
    // 音频栈挡住（NotReadableError / can't start audio source）。这里用 MediaRecorder
    // 直接录，录完把 base64 交给网页走原有的 /api/capture/upload。
    public static class CordisAudioBridge {
        private MediaRecorder cordisRec = null;
        private File cordisRecFile = null;

        @JavascriptInterface
        public String available() {
            return "1";
        }

        @JavascriptInterface
        public String hasPermission() {
            PythonActivity act = PythonActivity.mActivity;
            return (act != null
                    && act.checkCurrentPermission(Manifest.permission.RECORD_AUDIO)) ? "1" : "0";
        }

        @JavascriptInterface
        public String requestPermission() {
            cordisRequestAudioPermission();
            return "1";
        }

        @JavascriptInterface
        public String openSettings() {
            try {
                PythonActivity act = PythonActivity.mActivity;
                Intent intent = new Intent(
                        android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:" + act.getPackageName()));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                act.startActivity(intent);
                return "1";
            } catch (Exception e) {
                Log.e(TAG, "open app settings failed", e);
                return "ERR";
            }
        }

        @JavascriptInterface
        public synchronized String start() {
            PythonActivity act = PythonActivity.mActivity;
            if (act == null) return "ERR:no-activity";
            if (!act.checkCurrentPermission(Manifest.permission.RECORD_AUDIO)) return "NOPERM";
            try {
                releaseQuietly();
                File f = new File(act.getCacheDir(), "cordis_voice.m4a");
                if (f.exists()) f.delete();
                MediaRecorder rec = new MediaRecorder();
                rec.setAudioSource(MediaRecorder.AudioSource.MIC);
                rec.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
                rec.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
                rec.setAudioEncodingBitRate(64000);
                rec.setAudioSamplingRate(44100);
                rec.setOutputFile(f.getAbsolutePath());
                rec.prepare();
                rec.start();
                cordisRec = rec;
                cordisRecFile = f;
                Log.v(TAG, "native recorder started");
                return "OK";
            } catch (Exception e) {
                Log.e(TAG, "native recorder start failed", e);
                releaseQuietly();
                return "ERR:start:" + e.getClass().getSimpleName();
            }
        }

        @JavascriptInterface
        public synchronized String stop() {
            if (cordisRec == null) return "ERR:not-recording";
            try {
                cordisRec.stop();
            } catch (Exception e) {
                // 录得太短时 stop() 会抛异常，但文件里往往还有有效数据
                Log.e(TAG, "native recorder stop failed", e);
                byte[] partial = null;
                try { partial = readAll(cordisRecFile); } catch (Exception ignore) { }
                releaseQuietly();
                if (partial != null && partial.length > 1024) {
                    return Base64.encodeToString(partial, Base64.NO_WRAP);
                }
                return "ERR:stop:" + e.getClass().getSimpleName();
            }
            releaseQuietly();
            try {
                byte[] data = readAll(cordisRecFile);
                if (data == null || data.length == 0) return "ERR:empty";
                return Base64.encodeToString(data, Base64.NO_WRAP);
            } catch (Exception e) {
                Log.e(TAG, "native recorder read failed", e);
                return "ERR:read:" + e.getClass().getSimpleName();
            }
        }

        private void releaseQuietly() {
            if (cordisRec != null) {
                try { cordisRec.release(); } catch (Exception ignore) { }
                cordisRec = null;
            }
        }

        private byte[] readAll(File f) throws Exception {
            if (f == null) return null;
            FileInputStream in = new FileInputStream(f);
            try {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                return out.toByteArray();
            } finally {
                try { in.close(); } catch (Exception ignore) { }
            }
        }
    }

    private class CordisChromeClient extends WebChromeClient {
        @Override
        public void onPermissionRequest(final PermissionRequest request) {
            Log.v(TAG, "onPermissionRequest " + request.getOrigin());
            boolean wantsAudio = false;
            for (String res : request.getResources()) {
                if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(res)) {
                    wantsAudio = true;
                }
            }
            if (!wantsAudio) {
                request.deny();
                return;
            }
            PythonActivity act = PythonActivity.mActivity;
            if (act != null && act.checkCurrentPermission(Manifest.permission.RECORD_AUDIO)) {
                try {
                    request.grant(new String[] { PermissionRequest.RESOURCE_AUDIO_CAPTURE });
                } catch (Exception e) {
                    Log.e(TAG, "grant audio failed", e);
                    request.deny();
                }
            } else {
                cordisPendingAudio = request;
                if (act != null) {
                    act.requestPermissionsWithRequestCode(
                            new String[] { Manifest.permission.RECORD_AUDIO }, CORDIS_AUDIO_PERM);
                } else {
                    request.deny();
                    cordisPendingAudio = null;
                }
            }
        }

        @Override
        public void onPermissionRequestCanceled(PermissionRequest request) {
            if (cordisPendingAudio == request) {
                cordisPendingAudio = null;
            }
        }

        @Override
        public boolean onShowFileChooser(
                WebView webView,
                ValueCallback<Uri[]> filePathCallback,
                FileChooserParams params) {
            if (cordisFilePathCallback != null) {
                cordisFilePathCallback.onReceiveValue(null);
            }
            cordisFilePathCallback = filePathCallback;
            try {
                Intent intent = params.createIntent();
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                PythonActivity.mActivity.startActivityForResult(intent, CORDIS_FILE_CHOOSER);
                return true;
            } catch (Exception e) {
                Log.e(TAG, "onShowFileChooser failed", e);
                cordisFilePathCallback = null;
                return false;
            }
        }

        @Override
        public boolean onConsoleMessage(android.webkit.ConsoleMessage msg) {
            Log.v(TAG + "/JS", msg.message() + " (line " + msg.lineNumber() + ")");
            return true;
        }
    }

    private class CordisDownloadListener implements DownloadListener {
        @Override
        public void onDownloadStart(
                String url,
                String userAgent,
                String contentDisposition,
                String mimetype,
                long contentLength) {
            Log.v(TAG, "onDownloadStart " + url);
            try {
                if (url == null || url.startsWith("blob:") || url.startsWith("data:")) {
                    Toast.makeText(
                                    PythonActivity.mActivity,
                                    "该文件请在浏览器中打开后下载",
                                    Toast.LENGTH_LONG)
                            .show();
                    return;
                }
                Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                PythonActivity.mActivity.startActivity(intent);
            } catch (Exception e) {
                Log.e(TAG, "download failed", e);
                Toast.makeText(PythonActivity.mActivity, "无法打开下载链接", Toast.LENGTH_LONG)
                        .show();
            }
        }
    }

'''


def patch_java(text):
    if PATCH_TAG in text:
        return text, "already"
    for old, new in JAVA_ANCHORS:
        if old not in text:
            raise SystemExit("!! PythonActivity.java 结构不符，无法打补丁:\n" + old[:120])
        text = text.replace(old, new, 1)
    anchor = "    public static void loadUrl(String url) {"
    if anchor not in text:
        raise SystemExit("!! 找不到 loadUrl()，无法插入补丁类")
    text = text.replace(anchor, JAVA_BLOCK + anchor, 1)
    return text, "patched"


def write_targets(relpath, content, mode=None, dist_extra=None):
    """把内容写到 bootstrap 源码 + bootstrap_builds + dist（存在哪个写哪个）。

    dist 额外可指定 dist_extra：make_package 每次会清空 src/main/assets 再从
    webview_includes/ 拷过去，所以 dist 里两处都要写。
    """
    written = []
    roots = [BS]
    roots += glob.glob(os.path.join(p4a, "..", "build-*", "bootstrap_builds", "*"))
    if os.path.isdir(dist):
        roots.append(dist)
    for root in roots:
        rels = [relpath]
        if root is dist and dist_extra:
            rels += dist_extra
        for rel in rels:
            target = os.path.join(root, rel)
            if not os.path.exists(os.path.dirname(target)):
                continue
            if root is not BS and not os.path.exists(target):
                # bootstrap_builds / dist 里没有这个文件就跳过（只更新已有副本）
                continue
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            written.append(os.path.relpath(target, p4a))
            if mode:
                os.chmod(target, mode)
    return written


src = os.path.join(BS, "src", "main", "java", "org", "kivy", "android",
                   "PythonActivity.java")


def read_source():
    """读取 p4a 的 PythonActivity；若残留旧版补丁（没有版本标记），先 git 回滚再打。"""
    text = open(src, encoding="utf-8").read()
    if PATCH_TAG not in text and "CordisChromeClient" in text:
        import subprocess
        print("  检测到旧版补丁，回滚到 p4a 原始 PythonActivity.java 后重新打补丁")
        subprocess.check_call(["git", "-C", p4a, "checkout", "--",
                               os.path.relpath(src, p4a)])
        text = open(src, encoding="utf-8").read()
    return text


java = read_source()
java, status = patch_java(java)
files = write_targets("src/main/java/org/kivy/android/PythonActivity.java", java)
print("  PythonActivity.java: %s → %d 处" % (status, len(files)))
for f in files:
    print("    -", f)

# ---------------------------------------------------------------- 启动页
LOAD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="_loading_style.css" rel="stylesheet">
<title>外脑</title>
</head>
<body>
  <div class="splash">
    <div class="mark">\u7f9e</div>
    <div class="name">\u5916\u8111</div>
    <div class="en">CORDIS BRAIN</div>
    <div class="dots"><i></i><i></i><i></i></div>
    <div class="tip">\u6b63\u5728\u542f\u52a8\u672c\u5730\u670d\u52a1\u2026</div>
  </div>
</body>
</html>
"""
# 用真实字符（上面用转义是为了避免 shell 编码问题，这里替换回来）
LOAD_HTML = LOAD_HTML.replace("\u7f9e", "\U0001f9e0")

LOAD_CSS = """:root{color-scheme:dark}
html,body{height:100%;margin:0}
body{
  display:flex;align-items:center;justify-content:center;
  background:radial-gradient(1200px 600px at 50% -10%,#16233a,transparent),#0b0f14;
  color:#e8eef6;font-family:-apple-system,"Segoe UI","PingFang SC",sans-serif;
  -webkit-user-select:none;user-select:none;
}
.splash{text-align:center;animation:fade .5s ease}
.mark{
  width:76px;height:76px;margin:0 auto 16px;border-radius:22px;font-size:38px;
  display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#4c8dff,#8b5cff);
  box-shadow:0 14px 40px rgba(76,141,255,.35);
}
.name{font-size:24px;font-weight:700;letter-spacing:2px}
.en{margin-top:4px;font-size:10.5px;letter-spacing:3px;color:#6b7889}
.dots{display:flex;gap:6px;justify-content:center;margin-top:26px}
.dots i{
  width:7px;height:7px;border-radius:50%;background:#4c8dff;opacity:.25;
  animation:bounce 1.1s infinite ease-in-out;
}
.dots i:nth-child(2){animation-delay:.15s}
.dots i:nth-child(3){animation-delay:.3s}
.tip{margin-top:18px;font-size:12px;color:#6b7889}
@keyframes bounce{0%,80%,100%{opacity:.25;transform:scale(.85)}40%{opacity:1;transform:scale(1.15)}}
@keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
"""

files = write_targets("webview_includes/_load.html", LOAD_HTML,
                      dist_extra=["src/main/assets/_load.html"])
files += write_targets("webview_includes/_loading_style.css", LOAD_CSS,
                       dist_extra=["src/main/assets/_loading_style.css"])
print("  启动页: %d 处" % len(files))
for f in files:
    print("    -", f)
PY
}
patch_android

# ---------------------------------------------------------------- 3) 拼装 source.dir
rm -rf "$WORK/app"
mkdir -p "$WORK/app" "$WORK/bin"

cp    "$HERE/main.py"        "$WORK/app/main.py"
cp    "$HERE/config.toml"    "$WORK/app/config.toml"
cp -r "$SRC/brain"           "$WORK/app/brain"
cp -r "$SRC/cordis"          "$WORK/app/cordis"
cp -r "$SRC/web"             "$WORK/app/web"
cp    "$HERE/buildozer.spec" "$WORK/buildozer.spec"

find "$WORK/app" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$WORK/app" -type f -name '*.pyc' -delete 2>/dev/null || true

echo "---- source.dir 内容 ----"
(cd "$WORK/app" && find . -not -path '*/__pycache__*' | sort | sed 's/^/  /')

# ---------------------------------------------------------------- 4) 构建
rm -f "$WORK"/bin/*.apk
cd "$WORK"
buildozer android debug 2>&1 | tee "$LOG"

# ---------------------------------------------------------------- 5) 收集 APK
mkdir -p "$HERE/dist"
shopt -s nullglob
apks=( "$WORK"/bin/*.apk )
shopt -u nullglob

if [ "${#apks[@]}" -eq 0 ]; then
    echo "!! 没有找到 APK，请检查 $LOG" >&2
    exit 1
fi

for f in "${apks[@]}"; do
    cp -f "$f" "$HERE/dist/"
done

echo "=============================================================="
echo " 构建完成，APK 在 android/dist/ ："
ls -lh "$HERE/dist" | sed 's/^/   /'
echo "=============================================================="
