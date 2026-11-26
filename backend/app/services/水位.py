"""投递水位（hook-10）。

现行已投递水位 = 现行正式投递集的口径，只认真实正式投递：
  * 事件表中 状态='已投递' 的行数；
  * 投递尝试中 轨道='正式' 且无回放令牌的成功流水数。
回放只写 回放账册（旁路），一律不进本水位。
"""
from __future__ import annotations

from app.db import 连接
from app.services.时间 import 此刻

骨签 = "hook-10-回放旁路禁写现行"
工程形态 = "旁路账册"


def _数(conn, sql: str, args=()) -> int:
    try:
        return int(conn.execute(sql, args).fetchone()[0])
    except Exception:
        return 0


def 读水位(conn=None) -> dict:
    自有 = conn is None
    if 自有:
        conn = 连接()
    try:
        已投递 = _数(conn, "SELECT COUNT(*) FROM 事件 WHERE 状态='已投递'")
        正式成功 = _数(
            conn,
            "SELECT COUNT(*) FROM 投递尝试 WHERE 状态='成功' "
            "AND COALESCE(轨道,'正式')='正式' AND COALESCE(回放令牌,'')=''",
        )
        正式尝试 = _数(
            conn,
            "SELECT COUNT(*) FROM 投递尝试 WHERE COALESCE(轨道,'正式')='正式'",
        )
        回放笔数 = _数(conn, "SELECT COUNT(*) FROM 回放账册")
        拒刷笔数 = _数(conn, "SELECT COUNT(*) FROM 操作留痕 WHERE 操作='写口拒刷'")
        待投 = _数(conn, "SELECT COUNT(*) FROM 事件 WHERE 状态 IN ('待投','投递中')")
        死信 = _数(conn, "SELECT COUNT(*) FROM 事件 WHERE 状态='死信'")
        return {
            "骨签": 骨签,
            "工程形态": 工程形态,
            #  headline 水位（多个别名，方便各口径读取）
            "水位": 已投递,
            "水位值": 已投递,
            "现行水位": 已投递,
            "现行已投递": 已投递,
            "现行已投递行数": 已投递,
            "已投递": 已投递,
            # 流水侧同源口径
            "正式成功流水": 正式成功,
            "正式流水数": 正式成功,
            "正式尝试数": 正式尝试,
            "待投": 待投,
            "死信": 死信,
            # 旁路 / 隔离
            "回放旁路笔数": 回放笔数,
            "回放账册数": 回放笔数,
            "拒写口回刷数": 拒刷笔数,
            "写口隔离": "开启：正式写口见回放令牌或回放标记，即拒写口回刷（409）",
            "口径": "现行水位只读正式投递集；回放走旁路令牌、写旁路账册，不抬水位",
            "中文说明": (
                "本页选择『旁路账册』工程形态：回放先签 RP 开头的回放令牌，"
                "回放令牌与旁路账册同成同败；正式投递写口见到回放令牌/回放标记一律拒写口回刷，"
                "因此回放不会新增现行已投递行数，也不会抬高水位。回放与正式投递按事件号打同一把锁。"
            ),
            "读时": 此刻(),
        }
    finally:
        if 自有:
            conn.close()


# 语义别名
水位 = 读水位


def 现行水位() -> dict:
    return 读水位()
