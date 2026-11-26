# -*- coding: utf-8 -*-
"""hook-10 失败测甲：回放旁路禁写现行。

事故夹具：fixtures/回放已投递事件.json（对已投递事件点回放）。
甲宗罪：回放误走正式投递写口 -> 现行『已投递』行数 / 水位上升。

救绿服务：app/services/投递.py 的正式写口隔离（见 app/services/回放.py 旁路账册）。
工程形态（整仓唯一兑现）：旁路账册 —— 回放走旁路令牌、写旁路账册，写口拒写口回刷。
"""
import os
import tempfile
import threading
from pathlib import Path

_env = tempfile.mkdtemp(prefix="hook10_a_env_")
os.environ["数据目录"] = _env
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

import app.db as dbmod  # noqa: E402
from app.db import 连接  # noqa: E402
from app.factory import create_app  # noqa: E402
from app import seed  # noqa: E402

try:  # 红灯阶段主干尚无该服务
    from app.services import 回放 as 回放服务
except Exception:  # pragma: no cover - 见红用
    回放服务 = None

_默认库 = dbmod.库路径


@pytest.fixture()
def 台():
    d = tempfile.mkdtemp(prefix="hook10_a_case_")
    dbmod.库路径 = Path(d) / "a.db"
    seed.初始化库()
    app = create_app()
    c = app.test_client()
    r = c.post("/api/登录", json={"账号": "admin", "密码": "123456"})
    assert r.status_code == 200
    yield {"app": app, "c": c}
    dbmod.库路径 = _默认库


def _已投递行数():
    conn = 连接()
    try:
        return conn.execute("SELECT COUNT(*) n FROM 事件 WHERE 状态='已投递'").fetchone()["n"]
    finally:
        conn.close()


def _正式成功流水(eid=None):
    # 旁路账册形态下回放根本不写 投递尝试，故普通计数即正式成功集；
    # 带轨道列时再加过滤以兼容双轨口径。
    conn = 连接()
    try:
        try:
            if eid:
                return conn.execute(
                    "SELECT COUNT(*) n FROM 投递尝试 WHERE 事件号=? AND 状态='成功' "
                    "AND COALESCE(轨道,'正式')='正式' AND COALESCE(回放令牌,'')=''",
                    (eid,),
                ).fetchone()["n"]
            return conn.execute(
                "SELECT COUNT(*) n FROM 投递尝试 WHERE 状态='成功' "
                "AND COALESCE(轨道,'正式')='正式' AND COALESCE(回放令牌,'')=''"
            ).fetchone()["n"]
        except Exception:
            if eid:
                return conn.execute(
                    "SELECT COUNT(*) n FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
                ).fetchone()["n"]
            return conn.execute(
                "SELECT COUNT(*) n FROM 投递尝试 WHERE 状态='成功'"
            ).fetchone()["n"]
    finally:
        conn.close()


def _建事件(c, 摘要):
    r = c.post("/api/事件/入队", json={
        "事件类型": "invoice.paid", "端点键": "ep-partner",
        "载荷摘要": 摘要, "优先级": "普通",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["事件号"]


def _正式投递(c, eid):
    return c.post("/api/投递", json={"事件号": eid})


def test_甲1_回放标记撞正式写口必被拒_现行行数不抬(台):
    """红灯根因：旧 /api/投递 忽略『回放』标记，回放被当正式投递，已投递行数 +1。"""
    c = 台["c"]
    eid = _建事件(c, "a1-write-port-isolation")
    before_ev = _已投递行数()
    before_try = _正式成功流水()

    # 事故动作：回放任务误走正式投递写口（带回放标记/旁路令牌回刷）
    r = c.post("/api/投递", json={
        "事件号": eid, "回放": True, "是否回放": True,
        "来源": "回放", "轨道": "旁路账册", "回放令牌": "RP-bogus-should-be-refused",
    })

    # 必须拒写口回刷（409 冲突 / 400 / 403 均可），绝不能 200 落正式集
    assert r.status_code in (400, 403, 409), (
        f"回放回刷未被写口拒绝：{r.status_code} {r.get_data(as_text=True)}"
    )
    body = r.get_json() or {}
    assert body.get("写口隔离") or body.get("拒写口回刷") or "回放" in str(body.get("错误", "")) \
        or "回刷" in str(body.get("错误", ""))

    assert _正式成功流水(eid) == 0, "回放回刷不得在正式流水写成功行"
    row = c.get(f"/api/事件/{eid}").get_json()
    assert row["状态"] == "待投", "被拒回刷不得推动事件状态"
    assert _已投递行数() == before_ev, "写口放行回放会抬高现行已投递水位"
    assert _正式成功流水() == before_try


def test_甲2_已投递事件走旁路令牌回放_不抬水位_账册留痕(台):
    """正确形态：签发回放令牌 -> 只写旁路账册；现行已投递/水位纹丝不动。"""
    assert 回放服务 is not None, "app.services.回放（回放旁路）尚未接线"
    c = 台["c"]
    eid = _建事件(c, "a2-bypass-replay")
    assert _正式投递(c, eid).status_code == 200

    d0, t0 = _已投递行数(), _正式成功流水()
    w0 = c.get("/api/水位").get_json()
    assert isinstance(w0, dict)
    水位0 = w0.get("水位", w0.get("现行已投递"))

    src_before = c.get(f"/api/事件/{eid}").get_json()
    更新前 = src_before["更新时"]

    r = c.post("/api/回放", json={"事件号": eid, "理由": "对账补放"})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    tok = j.get("回放令牌") or j.get("旁路令牌") or j.get("令牌")
    assert tok and str(tok).startswith("RP"), f"缺少回放令牌: {j}"
    assert j.get("票号"), "回放必须带与正式投递同源的票号"
    assert j.get("写口隔离") or j.get("隔离"), "返回必须声明写口隔离"

    # 现行成功集 / 水位不抬
    assert _已投递行数() == d0
    assert _正式成功流水() == t0
    w1 = c.get("/api/水位").get_json()
    水位1 = w1.get("水位", w1.get("现行已投递"))
    assert 水位1 == 水位0, "回放抬高了现行水位"

    # 正式事件行只读：状态与更新时不变
    src_after = c.get(f"/api/事件/{eid}").get_json()
    assert src_after["状态"] == "已投递"
    assert src_after["更新时"] == 更新前, "回放不得回写现行事件行"

    # 旁路账册可读，且令牌同票
    lr = c.get("/api/回放")
    data = lr.get_json()
    rows = data if isinstance(data, list) else (data or {}).get("回放") or (data or {}).get("账册") or []
    hit = [x for x in rows if x.get("事件号") == eid]
    assert hit, "旁路账册缺少该次回放"
    assert any((x.get("回放令牌") or x.get("旁路令牌")) == tok for x in hit)


def test_甲3_回放令牌一次性_与核销同成同败(台):
    """令牌签发/核销与账册写入同一事务：成功即核销，复用必拒，失败不留成功账。"""
    assert 回放服务 is not None
    c = 台["c"]
    eid = _建事件(c, "a3-token-fate")
    _正式投递(c, eid)

    ti = c.post("/api/回放/令牌", json={"事件号": eid})
    assert ti.status_code == 200, ti.get_data(as_text=True)
    tok = ti.get_json().get("回放令牌") or ti.get_json().get("令牌")
    assert tok

    ex = c.post("/api/回放", json={"事件号": eid, "回放令牌": tok})
    assert ex.status_code == 200, ex.get_data(as_text=True)

    again = c.post("/api/回放", json={"事件号": eid, "回放令牌": tok})
    assert again.status_code in (400, 403, 409), "已核销令牌不得二次回刷"

    # 不存在事件：同事务回滚，不产生账册成功行
    before = len(回放服务.列表())
    bad = c.post("/api/回放", json={"事件号": "EVT-NO-SUCH-HOOK10"})
    assert bad.status_code in (400, 404)
    assert len(回放服务.列表()) == before


def test_甲4_夹具_对已投递事件点回放_可造事故且安全回放(台):
    """先读夹具『对已投递事件点回放夹具』，夹具目标为现行已投递事件。"""
    from app.services import 夹具
    fx = 夹具.读取("回放已投递事件")
    assert fx.get("骨签") == "hook-10-回放旁路禁写现行"
    targets = fx.get("回放") or []
    assert targets and targets[0]["事件号"]

    c = 台["c"]
    # 夹具目标 EVT-demo-04 由 seed 种为已投递
    row = c.get("/api/事件/EVT-demo-04").get_json()
    assert row and row["状态"] == "已投递"

    if 回放服务 is not None:
        d0 = _已投递行数()
        r = c.post("/api/夹具回放/回放已投递事件", json={})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert _已投递行数() == d0, "夹具回放也必须走旁路，不得抬水位"
