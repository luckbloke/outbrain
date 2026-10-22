class Service:
    """所有 Service 类插件的基类。子类通过 super().__init__(ctx, 'service_name') 自动注册。"""
    provide = None
    inject = []   # ✅ 默认空列表

    def __init__(self, ctx, provide: str):
        self.ctx = ctx
        self._provide_name = provide
        # 将自身注册到上下文中，并记录可逆副作用
        old = ctx._services.get(provide)
        ctx._services[provide] = self
        fiber = ctx._current_fiber  # 当前正在加载的 Fiber
        if fiber:
            fiber.effect(lambda: ctx._services.update({provide: old}) if old is not None else ctx._services.pop(provide, None))