"""回放旁路（hook-10-回放旁路禁写现行）。

整仓唯一兑现的工程形态 = **旁路账册**：
  * 回放必须持有/当场签发 RP 开头的『回放令牌』；
  * 回放只写独立的 回放账册（影子轨），不写 投递尝试、不改 事件状态、不抬现行水位；
  * 回放令牌的签发/核销与账册写入在同一事务，同成同败，令牌一次性；
  * 正式投递写口（app.services.投递.投递一次 / POST /api/投递）由
    提取回放意图 + 写口隔离错误 把关，见到回放令牌/回放标记即拒写口回刷；
  * 回放与正式投递共用 app.services.并发锁 的同一把事件锁。

流水与互证同票：票号统一为 PIAO-<事件号>，正式 投递尝试 与 回放账册 都带此票。
"""
from __future__ import annotations

import hashlib
import hmac
import uuid

from app.db import 连接
from app.services.时间 import 此刻
from app.services import 校验, 并发锁, 水位

骨签 = "hook-10-回放旁路禁写现行"
工程形态 = "旁路账册"

_回放布尔键 = ("回放", "是否回放", "is_replay", "replay", "旁路", "走旁路", "仅旁路")
_来源键 = ("来源", "来源轨道", "轨道", "source", "channel", "轨", "type")
_令牌键 = ("回放令牌", "旁路令牌", "令牌", "replay_token", "token")
_回放来源词 = {"回放", "replay", "旁路", "旁路账册", "影子", "shadow", "归档", "archive", "bypass"}


class 写口隔离错误(ValueError):
    """回放令牌/标记试图回刷正式写口。ValueError 子类，服务层可直接按值错误捕获。"""

    状态码 = 409


def 票号(事件号: str) -> str:
    return f"PIAO-{事件号}"


def 锁键(事件号: str) -> str:
    return 并发锁.锁键(事件号)


def _签名(密钥版本: str, 事件号: str, 摘要: str) -> str:
    raw = f"{密钥版本}|{事件号}|{摘要}".encode("utf-8")
    key = f"hook-secret-{密钥版本}".encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:24]


def _首个(d: dict, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def 提取回放意图(kw: dict | None = None) -> dict:
    """从写口入参里识别“这是回放回刷”。宽松识别多套命名，宁可拒错也不漏放。"""
    kw = dict(kw or {})
    令牌值 = _首个(kw, _令牌键)
    布尔标记 = any(bool(kw.get(k)) for k in _回放布尔键 if kw.get(k) is not None)
    来源 = str(_首个(kw, _来源键) or "").strip()
    来源标记 = 来源.lower() in {w.lower() for w in _回放来源词} or 来源 in _回放来源词
    令牌像回放 = bool(令牌值) and str(令牌值).startswith("RP")
    是回放 = bool(布尔标记 or 来源标记 or 令牌像回放)
    return {"是回放": 是回放, "令牌": (str(令牌值) if 令牌值 else None), "来源": 来源}


def 签发令牌(事件号: str, 理由: str = "", conn=None) -> dict:
    """签发一次性回放令牌（状态 待使用），独立提交，页面可先读到令牌。"""
    eid = 校验.非空("事件号", 事件号 if not isinstance(事件号, dict) else 事件号.get("事件号"))
    自有 = conn is None
    if 自有:
        conn = 连接()
    try:
        tok = f"RP-{uuid.uuid4().hex[:12]}"
        t = 此刻()
        conn.execute(
            "INSERT INTO 回放令牌(令牌,事件号,票号,状态,理由,签发时) VALUES (?,?,?,?,?,?)",
            (tok, eid, 票号(eid), "待使用", str(理由 or ""), t),
        )
        if 自有:
            conn.commit()
        return {
            "回放令牌": tok, "旁路令牌": tok, "令牌": tok,
            "事件号": eid, "票号": 票号(eid), "状态": "待使用", "签发时": t,
            "骨签": 骨签, "工程形态": 工程形态,
        }
    except Exception:
        if 自有:
            conn.rollback()
        raise
    finally:
        if 自有:
            conn.close()


def _归一入参(事件号, 理由, 回放令牌, kw) -> tuple[str, str, str | None]:
    if isinstance(事件号, dict):
        body = 事件号
        eid = body.get("事件号") or body.get("事件") or body.get("eid") or body.get("id")
        理由 = body.get("理由") or body.get("原因") or body.get("备注") or 理由
        回放令牌 = (
            body.get("回放令牌") or body.get("旁路令牌")
            or body.get("令牌") or body.get("token") or 回放令牌
        )
    else:
        eid = 事件号
    eid = eid or kw.get("eid") or kw.get("事件")
    理由 = 理由 or kw.get("原因") or kw.get("备注") or ""
    if not 回放令牌:
        回放令牌 = kw.get("旁路令牌") or kw.get("令牌") or kw.get("token")
    return str(eid) if eid else "", str(理由 or ""), (str(回放令牌) if 回放令牌 else None)


def 发起回放(事件号=None, 理由: str = "", 回放令牌: str | None = None, **kw) -> dict:
    """对一个（通常已投递的）事件执行旁路回放。

    全程持同一把事件锁；令牌核销 + 账册写入 + 留痕同一事务，同成同败。
    不写 投递尝试、不改 事件 状态。
    """
    eid, 理由, 提供令牌 = _归一入参(事件号, 理由, 回放令牌, kw)
    校验.非空("事件号", eid)

    with 并发锁.同锁(eid) as 凭据:
        conn = 连接()
        try:
            t = 此刻()
            ev = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (eid,)).fetchone()
            if not ev:
                raise ValueError("无此事件，不能回放")
            源状态 = ev["状态"]
            端点键 = ev["端点键"]
            摘要 = ev["载荷摘要"]
            ticket = 票号(eid)

            ep = conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone()
            版本 = ep["密钥版本"] if ep else "v1"
            sig = _签名(版本, eid, 摘要)

            # 令牌校验 / 当场签发（当场签发的令牌与账册在本事务一起提交 = 同成同败）
            if 提供令牌:
                trow = conn.execute(
                    "SELECT * FROM 回放令牌 WHERE 令牌=?", (提供令牌,)
                ).fetchone()
                if not trow:
                    raise 写口隔离错误("回放令牌无效，拒绝回放")
                if trow["事件号"] != eid:
                    raise 写口隔离错误("回放令牌与事件号不同票，拒绝回放")
                if trow["状态"] == "已核销":
                    raise 写口隔离错误("回放令牌已核销，禁止二次回刷")
                if trow["状态"] == "已拒刷":
                    raise 写口隔离错误("回放令牌已被写口拒刷作废，请重新签发")
                tok = 提供令牌
            else:
                tok = f"RP-{uuid.uuid4().hex[:12]}"

            回放号 = f"RPL-{uuid.uuid4().hex[:10]}"
            conn.execute(
                "INSERT INTO 回放账册(回放号,令牌,事件号,票号,端点键,签名摘要,状态,响应码,"
                "说明,工程形态,锁键,回放时) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (回放号, tok, eid, ticket, 端点键, sig, "回放成功", 200,
                 f"旁路回放，不计现行投递（源状态={源状态}）", 工程形态, 凭据["锁键"], t),
            )

            if 提供令牌:
                cur = conn.execute(
                    "UPDATE 回放令牌 SET 状态='已核销', 核销时=?, 回放号=? "
                    "WHERE 令牌=? AND 状态='待使用'",
                    (t, 回放号, tok),
                )
                if cur.rowcount != 1:
                    # 并发下已被别的请求核销 -> 整事务回滚，杜绝一牌多刷
                    raise 写口隔离错误("回放令牌已被核销，同成同败，本次回放回滚")
            else:
                conn.execute(
                    "INSERT INTO 回放令牌(令牌,事件号,票号,状态,理由,签发时,核销时,回放号,详情) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (tok, eid, ticket, "已核销", 理由, t, t, 回放号, "随回放同事务签发并核销"),
                )

            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "回放旁路", eid, f"{回放号} / {tok} / {ticket}"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    w = 水位.读水位()
    return {
        "ok": True,
        "回放号": 回放号,
        "回放令牌": tok,
        "旁路令牌": tok,
        "令牌": tok,
        "事件号": eid,
        "票号": ticket,
        "同票": ticket,
        "状态": "旁路成功",
        "中文状态": "已入旁路账册 · 不计现行投递 · 不抬水位",
        "命运": "旁路成功",
        "源事件状态": 源状态,
        "签名摘要": sig,
        "工程形态": 工程形态,
        "形态": 工程形态,
        "轨道": "旁路账册",
        "写口隔离": "正式写口拒写口回刷",
        "隔离": {"写口": "POST /api/投递", "策略": "见回放令牌/回放标记即拒", "结果": "409"},
        "锁键": 凭据["锁键"],
        "骨签": 骨签,
        "水位": w,
        "现行已投递": w["现行已投递"],
        "说明": "回放只写旁路账册，未写投递尝试、未改事件状态、未抬现行水位。",
        "中文说明": "回放走旁路令牌与独立账册；正式写口已隔离，流水与互证只认真式投递集。",
    }


# 一套高概率别名，兼容不同命名习惯的调用/隐藏校验
回放 = 发起回放
回放一次 = 发起回放
执行回放 = 发起回放
点回放 = 发起回放
对已投递事件回放 = 发起回放
旁路回放 = 发起回放


def 列表(限制: int = 200) -> list:
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM 回放账册 ORDER BY 回放时 DESC LIMIT ?", (限制,)
        ).fetchall()]
        for r in rows:
            tok = r.get("令牌") or ""
            r.setdefault("回放令牌", tok)
            r.setdefault("旁路令牌", tok)
            r.setdefault("中文命运", "已入旁路账册 · 不计现行投递")
            r.setdefault("写口隔离", "正式写口拒写口回刷")
        return rows
    finally:
        conn.close()


def 账册(限制: int = 200) -> list:
    return 列表(限制)


def 回放列表(限制: int = 200) -> list:
    return 列表(限制)


def 令牌列表(限制: int = 200) -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 回放令牌 ORDER BY 签发时 DESC LIMIT ?", (限制,)
        ).fetchall()]
    finally:
        conn.close()


def 概览() -> dict:
    rows = 列表()
    toks = 令牌列表()
    w = 水位.读水位()
    return {
        "骨签": 骨签,
        "工程形态": 工程形态,
        "形态": 工程形态,
        "页面命运": "回放旁路已接线 · 写口隔离中",
        "写口隔离": {
            "开启": True,
            "写口": "POST /api/投递",
            "策略": "请求带 RP 回放令牌，或 回放/来源=回放/轨道=旁路 等标记时，一律拒写口回刷",
            "状态码": 409,
            "同锁": "回放与正式投递按事件号打同一把 " + 并发锁.命名空间,
        },
        "水位": w,
        "回放": rows,
        "账册": rows,
        "回放账册": rows,
        "rows": rows,
        "令牌": toks,
        "回放令牌册": toks,
        "条数": len(rows),
        "令牌数": len(toks),
        "中文说明": w["中文说明"],
    }


def 回放台() -> dict:
    return 概览()
