from typing import Any, Dict, List
from collections import defaultdict, deque

from .service import Service

class PluginLoader:
    def __init__(self, ctx):
        self.ctx = ctx

    def _extract_meta(self, plugin: Any) -> dict:
        """从插件对象中提取 name, inject, provide 元数据。"""
        if isinstance(plugin, type) and issubclass(plugin, Service):
            name = getattr(plugin, 'name', plugin.__name__)
            return {'name': name, 'inject': set(), 'provide': getattr(plugin, 'provide', None)}
        if callable(plugin):
            return {'name': getattr(plugin, 'name', plugin.__name__),
                    'inject': set(getattr(plugin, 'inject', [])),
                    'provide': getattr(plugin, 'provide', None)}
        raise TypeError(f"Unsupported plugin type: {type(plugin)}")

    def topological_sort(self, plugins: Dict[str, Any]) -> List[str]:
        """
        输入：{plugin_name: plugin_object}
        输出：按依赖顺序排列的 plugin_name 列表。
        """
        graph = {name: [] for name in plugins}
        in_degree = {name: 0 for name in plugins}
        provides = {}  # service_name -> plugin_name

        # 先建立 provide 索引
        for name, plugin in plugins.items():
            meta = self._extract_meta(plugin)
            if meta['provide']:
                provides[meta['provide']] = name

        # 再构建依赖边
        for name, plugin in plugins.items():
            meta = self._extract_meta(plugin)
            for dep in meta['inject']:
                if dep in provides:
                    # 如果该服务由某个插件提供，则依赖它
                    provider = provides[dep]
                    graph[provider].append(name)
                    in_degree[name] += 1

        # Kahn 算法
        queue = deque([n for n in in_degree if in_degree[n] == 0])
        sorted_order = []
        while queue:
            node = queue.popleft()
            sorted_order.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_order) != len(plugins):
            raise ValueError("Circular dependency detected among plugins")

        return sorted_order