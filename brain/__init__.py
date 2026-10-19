"""Cordis Brain —— 个人外脑。

基于 cordis「一切皆插件」内核构建：
  - 每个能力都是一个 cordis 插件（Service 或函数插件）
  - ctx.config / ctx.db / ctx.ai / ctx.capture / ctx.parse / ctx.search /
    ctx.reminder / ctx.notify / ctx.review / ctx.entities / ctx.http
  - 事件流：capture.received -> item.parsed -> item.stored -> reminder.due

设计目标：捕获无摩擦，整理自动化，检索语义化，提醒情境化，回顾周期化。
人只负责输入、决策、执行、反馈；系统负责记忆、连接、检索、提醒、复盘。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
