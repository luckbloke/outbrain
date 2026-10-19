import logging
from typing import Callable

logger = logging.getLogger(__name__)


class Fiber:
    def __init__(self, ctx, plugin):
        self.ctx = ctx
        self.plugin = plugin
        self.status = "PENDING"
        self._disposables = []

    def effect(self, disposer: Callable):
        """注册一个可逆副作用。"""
        self._disposables.append(disposer)

    def unload(self):
        """按逆序清理所有副作用。"""
        if self.status != "ACTIVE":
            return
        self.status = "DISPOSED"
        while self._disposables:
            disposer = self._disposables.pop()
            try:
                disposer()
            except Exception as e:
                logger.error(f"[Fiber] teardown error: {e}")