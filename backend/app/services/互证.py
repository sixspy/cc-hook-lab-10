from __future__ import annotations
from app.db import 连接
from app.services.时间 import 此刻


def 同票核对(conn) -> dict:
    """影子旁路账册 与 正式投递流水 按票号 PIAO-<事件号> 联查对账。"""
    try:
        shadows = conn.execute(
            "SELECT 事件号, 票号, COUNT(*) AS n FROM 回放账册 GROUP BY 事件号"
        ).fetchall()
    except Exception:
        return {"对齐": True, "票数": 0, "明细": [], "说明": "旁路账册尚未建立"}

    明细 = []
    全对齐 = True
    for s in shadows:
        eid = s["事件号"]
        期望票 = f"PIAO-{eid}"
        formal = conn.execute(
            "SELECT COUNT(*) AS n FROM 投递尝试 "
            "WHERE 事件号=? AND 状态='成功' "
            "AND COALESCE(轨道,'正式')='正式' AND COALESCE(回放令牌,'')=''",
            (eid,),
        ).fetchone()["n"]
        票一致 = (s["票号"] == 期望票)
        # 回放一般针对已正式投递事件：同票且至少一条正式成功流水
        对齐 = bool(票一致 and formal >= 1)
        全对齐 = 全对齐 and 对齐
        明细.append({
            "事件号": eid,
            "票号": s["票号"],
            "期望票号": 期望票,
            "回放笔数": s["n"],
            "正式成功流水": formal,
            "对齐": 对齐,
        })
    return {
        "对齐": 全对齐,
        "是否对齐": 全对齐,
        "票数": len(明细),
        "明细": 明细,
        "说明": "影子回放账册与正式流水按同一票号核对" if 明细 else "尚无回放账册，同票核对空过",
    }


def 跑一次() -> dict:
    t = 此刻()
    conn = 连接()
    try:
        总 = conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"]
        已 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='已投递'").fetchone()["c"]
        死 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='死信'").fetchone()["c"]
        待 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态 IN ('待投','投递中')").fetchone()["c"]
        匣 = conn.execute("SELECT COUNT(*) AS c FROM 死信 WHERE 状态='待处置'").fetchone()["c"]

        # hook-10：正式流水口径与回放旁路口径严格分开
        正式成功 = conn.execute(
            "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 状态='成功' "
            "AND COALESCE(轨道,'正式')='正式' AND COALESCE(回放令牌,'')=''"
        ).fetchone()["c"]
        try:
            回放数 = conn.execute("SELECT COUNT(*) AS c FROM 回放账册").fetchone()["c"]
        except Exception:
            回放数 = 0
        try:
            拒刷数 = conn.execute(
                "SELECT COUNT(*) AS c FROM 操作留痕 WHERE 操作='写口拒刷'"
            ).fetchone()["c"]
        except Exception:
            拒刷数 = 0
        同票 = 同票核对(conn)

        对齐 = 1 if (死 == 匣 and 已 + 死 + 待 <= 总) else 0
        说明 = "事件/投递/死信口径对齐" if 对齐 else "死信匣与事件死信数不一致或水位异常"
        if 回放数:
            说明 += f"；回放旁路 {回放数} 笔未计入正式已投递"
        if not 同票["对齐"]:
            对齐 = 0
            说明 += "；回放影子与正式流水同票核对撕裂"

        conn.execute(
            "INSERT INTO 互证批次(时刻,事件总数,已投递数,死信数,待投数,是否对齐,说明) VALUES (?,?,?,?,?,?,?)",
            (t, 总, 已, 死, 待, 对齐, 说明),
        )
        conn.commit()
        return {
            "时刻": t,
            "事件总数": 总,
            "已投递数": 已,
            "死信数": 死,
            "待投数": 待,
            "死信匣待处置": 匣,
            "是否对齐": bool(对齐),
            "是否全部对齐": bool(对齐),
            "说明": 说明,
            "明细": {"总": 总, "已": 已, "死": 死, "待": 待, "匣": 匣, "正式成功流水": 正式成功},
            # hook-10 新增口径
            "正式成功流水": 正式成功,
            "回放旁路数": 回放数,
            "回放数": 回放数,
            "拒写口回刷数": 拒刷数,
            "同票核对": 同票,
            "同票": 同票,
            "水位": {"水位": 已, "现行已投递": 已, "正式成功流水": 正式成功},
            "骨签": "hook-10-回放旁路禁写现行",
            "工程形态": "旁路账册",
        }
    finally:
        conn.close()
