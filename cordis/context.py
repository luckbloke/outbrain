from typing import Any, Callable, Dict, List, Optional

from .events import EventEmitter
from .fiber import Fiber
from .service import Service 

class Context:
    def __init__(self, parent=None):
        self.parent = parent
        self._services: Dict[str, Any] = {}
        self._events = EventEmitter()
        self._current_fiber = None
        self._fibers = []

    def __getattr__(self, name: str):
        # 1. 本地服务查找
        if name in self._services:
            return self._services[name]
        # 2. 沿 parent 链上溯
        if self.parent:
            return getattr(self.parent, name)
        raise AttributeError(f"Service '{name}' not found")

    def get(self, name: str, default=None):
        try:
            return getattr(self, name)
        except AttributeError:
            return default

    def effect(self, disposer: Callable):
        """在当前 Fiber 上注册可逆副作用。"""
        if self._current_fiber:
            self._current_fiber.effect(disposer)

    def on(self, event: str, listener: Callable):
        """注册事件监听器，自动绑定为可逆副作用。"""
        disposer = self._events.on(event, listener)
        self.effect(disposer)

    # 便捷事件分发方法
    def emit(self, event: str, *args, **kwargs):
        return self._events.emit(event, *args, **kwargs)

    async def parallel(self, event: str, *args, **kwargs):
        return await self._events.parallel(event, *args, **kwargs)

    async def serial(self, event: str, init_value=None):
        return await self._events.serial(event, init_value)

    async def bail(self, event: str, *args, **kwargs):
        return await self._events.bail(event, *args, **kwargs)

    async def waterfall(self, event: str, *args, **kwargs):
        return await self._events.waterfall(event, *args, **kwargs)

    async def plugin(self, plugin_obj, config=None):
        """加载单个插件。"""
        name = getattr(plugin_obj, 'name', getattr(plugin_obj, '__name__', str(plugin_obj)))
        # 简化：实际应用中应使用 loader 处理依赖
        fiber = Fiber(self, plugin_obj)
        self._fibers.append(fiber)
        self._current_fiber = fiber
        try:
            if isinstance(plugin_obj, type):
                # Service 类插件
                plugin_obj(self)
            elif callable(plugin_obj):
                # 函数插件，apply(ctx, config)
                plugin_obj(self, config or {})
            fiber.status = "ACTIVE"
        except Exception as e:
            fiber.status = "ERROR"
            raise
        finally:
            self._current_fiber = None
        return fiber