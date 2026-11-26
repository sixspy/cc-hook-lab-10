"""空桩接线：互证全扫（hook-10）。

全扫口径把正式投递集与回放旁路账册分开，并做同票核对。
实现主体 app/services/互证.py。
"""
NAME = "互证全扫"

from app.services.互证 import 跑一次, 同票核对  # noqa: E402,F401


def 全扫():
    return 跑一次()
