# -*- coding: utf-8 -*-
"""外脑 · Cordis Brain — Windows 桌面客户端（内嵌 Chromium 壳，加载后端网页 UI）。

用法：
  · 双击 外脑.exe
  · 指定服务器：  外脑.exe --url=http://127.0.0.1:8787
  · 开发者工具：  设环境变量 CORDIS_DESKTOP_DEBUG=1 后再启动（F12）

服务器地址优先级：  --url  >  环境变量 CORDIS_DESKTOP_URL  >  同目录 desktop_url.txt  >  默认 127.0.0.1:8787
（程序内点菜单「外脑 → 设置服务器地址…」也会写入同目录 desktop_url.txt，下次记住。）
渲染后端：优先 Qt WebEngine（自包含 Chromium，最稳）；无 Qt 时回退系统 WebView2(Edge)。
注意：本客户端只负责打开界面，需先有外脑服务在跑（本机 python server.py，或服务器上的同款服务）。
"""
import os
import sys
import json
import tempfile

import webview

# 静态导入 Qt 后端：既在运行时选中 Qt WebEngine，也让 PyInstaller 打包时带上 Qt 组件。
_GUI = None
try:
    import webview.platforms.qt  # noqa: F401
    _GUI = "qt"
except Exception:
    _GUI = None  # 回退默认（WebView2/Edge）

from webview.menu import Menu, MenuAction, MenuSeparator

APP_TITLE = "外脑 · Cordis Brain"
DEFAULT_URL = "http://127.0.0.1:8787"
SELFTEST = "--selftest" in sys.argv[1:]


def _base_dir():
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


URL_FILE = os.path.join(_base_dir(), "desktop_url.txt")


def _profile_dir():
    try:
        base = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "cordis-desktop")
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return tempfile.gettempdir()


def _icon_path():
    """窗口/任务栏图标：优先用 exe 同目录的 cordis.ico（方便自定义），
    再回退到打进去的副本（PyInstaller onedir 放在 _internal/ 里）。"""
    cands = [os.path.join(_base_dir(), "cordis.ico")]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "cordis.ico"))
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(_base_dir(), "_internal", "cordis.ico"))
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


def _set_app_id():
    """让 Windows 任务栏用我们自己的图标/分组，而不是归到 python.exe。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("cordis.brain.desktop")
    except Exception:
        pass


def _norm(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return DEFAULT_URL
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    return u


def _load_url() -> str:
    for a in sys.argv[1:]:
        if a.startswith("--url="):
            return _norm(a[len("--url="):])
    env = os.environ.get("CORDIS_DESKTOP_URL", "").strip()
    if env:
        return _norm(env)
    if os.path.exists(URL_FILE):
        try:
            v = open(URL_FILE, encoding="utf-8").read().strip()
            if v:
                return _norm(v)
        except Exception:
            pass
    return DEFAULT_URL


def _save_url(u: str) -> None:
    try:
        with open(URL_FILE, "w", encoding="utf-8") as f:
            f.write(_norm(u))
    except Exception:
        pass


def _eval(w, js, timeout=20):
    """pywebview 不同后端下 evaluate_js 返回值或 Future，统一处理。"""
    r = w.evaluate_js(js)
    try:
        return r.result(timeout)
    except AttributeError:
        return r


_state = {"window": None, "url": DEFAULT_URL}


def _reload():
    w = _state.get("window")
    if not w:
        return
    try:
        cur = w.get_current_url()
        w.load_url(cur or _state.get("url") or DEFAULT_URL)
    except Exception:
        pass


def _set_url():
    w = _state.get("window")
    if not w:
        return
    try:
        cur = w.get_current_url() or ""
        val = _eval(w,
                    "window.prompt('外脑 · 服务器地址（如 127.0.0.1:8787 或 http://<主机>:8787）', "
                    + json.dumps(cur) + ")")
        if val:
            _save_url(val)
            _state["url"] = _norm(val)
            w.load_url(_norm(val))
    except Exception:
        pass


def _quit():
    w = _state.get("window")
    if w:
        try:
            w.destroy()
        except Exception:
            pass


def _on_ready():
    """窗口首次就绪后回调。--selftest 下打印页面标题/地址再自动退出（用于验证加载）。"""
    if not SELFTEST:
        return
    w = _state.get("window")
    if not w:
        print("SELFTEST FAIL: no window", flush=True)
        return
    try:
        info = _eval(w,
                     "JSON.stringify({t:document.title,u:location.href,"
                     "h:(document.getElementById('chipHealth')||{}).textContent})")
        print("SELFTEST info=%s" % info, flush=True)
        ok = ("8787" in str(info)) or str(info).startswith(_state.get("url", "")[:7])
        print("SELFTEST_OK" if ok else "SELFTEST_FAIL", flush=True)
    except Exception as e:
        print("SELFTEST err=%s: %s" % (type(e).__name__, e), flush=True)
        print("SELFTEST_FAIL", flush=True)
    finally:
        try:
            w.destroy()
        except Exception:
            pass


def main() -> None:
    _set_app_id()
    url = _load_url()
    _state["url"] = url
    win = webview.create_window(
        APP_TITLE, url,
        width=1280, height=860, min_size=(920, 600),
        background_color="#0d1117",
    )
    _state["window"] = win or (webview.windows[-1] if webview.windows else None)
    menu = [
        Menu("外脑", [
            MenuAction("刷新", _reload),
            MenuAction("设置服务器地址…", _set_url),
            MenuSeparator(),
            MenuAction("退出", _quit),
        ]),
    ]
    debug = os.environ.get("CORDIS_DESKTOP_DEBUG", "") in ("1", "true", "True")
    webview.start(_on_ready if SELFTEST else None,
                  gui=_GUI, debug=debug, menu=menu,
                  storage_path=_profile_dir(), icon=_icon_path())


if __name__ == "__main__":
    main()
