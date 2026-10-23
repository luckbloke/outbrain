# 外脑 · Cordis Brain —— Buildozer 配置（Android / python-for-android）
#
# 说明：
#   本 spec 的 source.dir = app，app/ 由 build_android.sh 在构建时从项目里
#   「临时拼装」出来（main.py + config.toml + brain/ + cordis/ + web/），
#   这样仓库里不需要保留一份重复源码。
#
# 构建入口（推荐）：
#   Windows:  powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
#   容器内:   bash android/build_android.sh
#
# 关键点：
#   · p4a.bootstrap = webview —— p4a 自带的 WebView 外壳：后台跑 Python，
#     并轮询 http://127.0.0.1:<p4a.port>/ ，通了就把 WebView 指过来。
#   · p4a.port = 8787 必须与 android/main.py 里的 PORT 一致。
#   · requirements 只要 python3：webview bootstrap 会自动带上
#     python3 / android / genericndkbuild / pyjnius / sqlite3 / openssl / libffi。
#     项目本身零依赖（jieba/requests 都是可选增强，缺了自动降级）。

[app]
title = 外脑
package.name = cordisbrain
package.domain = org.cordis

source.dir = app
# pem/crt：随包 CA（certs/cacert.pem），少了它手机端 HTTPS 一律证书校验失败
source.include_exts = py,html,htm,css,js,json,toml,txt,md,png,jpg,ico,pem,crt
source.exclude_dirs = __pycache__,.git,data,build,dist

version = 0.1.0

requirements = python3

# ---------- python-for-android ----------
p4a.bootstrap = webview
p4a.port = 8787

# ---------- Android ----------
orientation = portrait
android.archs = arm64-v8a
android.minapi = 24
# INTERNET：WebView 访问 127.0.0.1 上的本地服务
# RECORD_AUDIO：语音捕获（WebView getUserMedia，运行时弹窗授权）
android.permissions = INTERNET, RECORD_AUDIO
android.allow_backup = True
android.accept_sdk_license = True
# 非全屏：保留系统状态栏，避免顶部内容被刘海/状态栏遮挡（配合 CSS 的 safe-area）
fullscreen = 0
android.apptheme = @android:style/Theme.NoTitleBar

[buildozer]
log_level = 2
# 容器里以 root 构建（沙箱），关掉 root 交互确认，避免卡在 input()
warn_on_root = 0
