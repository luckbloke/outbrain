import asyncio
from typing import Callable, Any, List

class EventEmitter:
    def __init__(self):
        self._listeners: dict[str, List[Callable]] = {}

    def on(self, event: str, fn: Callable):
        """注册监听器，返回一个清理函数（可逆副作用）。"""
        self._listeners.setdefault(event, []).append(fn)
        def disposer():
            if fn in self._listeners.get(event, []):
                self._listeners[event].remove(fn)
        return disposer

    # 1. emit：广播，所有监听器按注册顺序执行，不关心返回值
    def emit(self, event: str, *args, **kwargs):
        for fn in list(self._listeners.get(event, [])):
            fn(*args, **kwargs)

    # 2. parallel：并行执行，收集所有结果
    async def parallel(self, event: str, *args, **kwargs) -> List[Any]:
        tasks = [asyncio.create_task(self._call_async(fn, *args, **kwargs))
                 for fn in list(self._listeners.get(event, []))]
        return await asyncio.gather(*tasks) if tasks else []

    # 3. serial：串行执行，前一个返回值作为后一个输入
    async def serial(self, event: str, initial_value: Any = None) -> Any:
        value = initial_value
        for fn in list(self._listeners.get(event, [])):
            value = await self._call_async(fn, value)
        return value

    # 4. bail：执行到第一个返回非 None 的监听器就停止，并返回该值
    async def bail(self, event: str, *args, **kwargs) -> Any:
        for fn in list(self._listeners.get(event, [])):
            result = await self._call_async(fn, *args, **kwargs)
            if result is not None:
                return result
        return None

    # 5. waterfall：洋葱模型，最核心的模式
    async def waterfall(self, event: str, *args, **kwargs) -> Any:
        """
        监听器签名：fn(*args, next_fn, **kwargs) -> result
        必须调用 next_fn() 才能将控制权传递给下一个监听器。
        """
        chain = list(self._listeners.get(event, []))
        async def _dispatch(index: int) -> Any:
            if index >= len(chain):
                return None  # 链条末端
            fn = chain[index]
            async def next_fn():
                return await _dispatch(index + 1)
            return await self._call_async(fn, *args, next_fn=next_fn, **kwargs)
        return await _dispatch(0)

    async def _call_async(self, fn, *args, **kwargs):
        if asyncio.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return fn(*args, **kwargs)