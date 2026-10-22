# cordis/__init__.py
from .context import Context
from .fiber import Fiber
from .service import Service
from .events import EventEmitter
from .loader import PluginLoader

__all__ = ["Context", "Fiber", "Service", "EventEmitter", "PluginLoader"]

# ============ 全局 ctx 单例（简化跨模块访问） ============

_GLOBAL_CTX = None

def set_global_ctx(ctx):
    """由 main.py 启动时调用"""
    global _GLOBAL_CTX
    _GLOBAL_CTX = ctx

def get_global_ctx():
    """其他模块读取全局 ctx"""
    return _GLOBAL_CTX
