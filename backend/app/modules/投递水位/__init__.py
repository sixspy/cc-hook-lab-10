"""空桩接线：投递水位（hook-10）。

现行水位只读正式投递集；回放旁路不抬水位。实现主体 app/services/水位.py。
"""
NAME = "投递水位"

from app.services.水位 import 读水位, 水位, 现行水位, 骨签, 工程形态  # noqa: E402,F401
