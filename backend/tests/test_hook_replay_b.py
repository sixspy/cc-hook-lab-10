# -*- coding: utf-8 -*-
"""hook-10 失败测乙：投递互证对账不得把回放副作用当成正式投递。

救绿服务：app/services/互证.py（对账只认正式投递集），并与旁路账册做同票核对。
跨表联查、影子（旁路账册）与正式对账同票。
"""
import os
import tempfile
from pathlib import Path

_env = tempfile.mkdtemp(prefix="hook10_b_env_")
os.environ["数据目录"] = _env
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

import app.db as dbmod  # noqa: E402
from app.factory import create_app  # noqa: E402
from app import seed  # noqa: E402
from app.services import 互证  # noqa: E402

_默认库 = dbmod.库路径


@pytest.fixture()
def 台():
    d = tempfile.mkdtemp(prefix="hook10_b_case_")
    dbmod.库路径 = Path(d) / "b.db"
    seed.初始化库()
    app = create_app()
    c = app.test_client()
    assert c.post("/api/登录", json={"账号": "admin", "密码": "123456"}).status_code == 200
    yield {"app": app, "c": c}
    dbmod.库路径 = _默认库


def _建并投(c, 摘要):
    eid = c.post("/api/事件/入队", json={
        "事件类型": "invoice.paid", "端点键": "ep-partner", "载荷摘要": 摘要,
    }).get_json()["事件号"]
    assert c.post("/api/投递", json={"事件号": eid}).status_code == 200
    return eid


def test_乙1_互证已投递数不含回放副作用(台):
    """红灯根因：回放误走写口后，互证把该 +1 当成正式已投递。"""
    c = 台["c"]
    _建并投(c, "b-formal-1")
    base = 互证.跑一次()
    基线已 = base["已投递数"]
    assert base["是否全部对齐"] is True

    # 再造一个事件，用“回放回刷”标记撞正式写口
    eid2 = c.post("/api/事件/入队", json={
        "事件类型": "invoice.paid", "端点键": "ep-partner", "载荷摘要": "b-replay-sideeffect",
    }).get_json()["事件号"]
    wr = c.post("/api/投递", json={"事件号": eid2, "回放": True, "来源": "回放"})
    # 修复后这里应被写口隔离；红灯阶段是 200（副作用落正式集）
    if wr.status_code < 400:
        # 若主干尚未隔离，正是事故现场：继续验证互证是否误算（本断言必须红）
        pass

    rep = 互证.跑一次()
    assert rep["已投递数"] == 基线已, (
        f"互证把回放副作用算成正式投递：基线 {基线已} -> {rep['已投递数']}"
    )
    assert rep["是否全部对齐"] is True


def test_乙2_旁路回放后互证仍对齐_且单列回放账(台):
    """正确形态：旁路回放若干次，正式口径不变；互证单列回放旁路数并做同票核对。"""
    c = 台["c"]
    eid = _建并投(c, "b-bypass-reconcile")
    base = 互证.跑一次()

    try:
        from app.services import 回放
    except Exception:
        回放 = None
    assert 回放 is not None, "回放旁路服务未接线"

    for i in range(2):
        r = c.post("/api/回放", json={"事件号": eid, "理由": f"b{i}"})
        assert r.status_code == 200, r.get_data(as_text=True)

    rep = 互证.跑一次()
    assert rep["已投递数"] == base["已投递数"], "旁路回放不得进互证正式已投递口径"
    assert rep["是否全部对齐"] is True
    assert int(rep.get("回放旁路数", rep.get("回放数", 0))) >= 2

    同票 = rep.get("同票核对") or rep.get("同票") or {}
    assert 同票, "互证必须给出影子账册与正式流水的同票核对"
    assert 同票.get("对齐", 同票.get("是否对齐")) in (True, 1), f"同票核对未对齐: {同票}"


def test_乙3_现行流水只读正式投递集(台):
    """/api/投递 现行流水不得混入回放旁路行；旁路行只在 /api/回放 账册。"""
    c = 台["c"]
    eid = _建并投(c, "b-formal-feed")
    try:
        from app.services import 回放
    except Exception:
        回放 = None
    assert 回放 is not None
    assert c.post("/api/回放", json={"事件号": eid}).status_code == 200

    rows = c.get("/api/投递").get_json()
    assert isinstance(rows, list)
    for r in rows:
        assert not r.get("回放令牌"), "现行流水混入了回放令牌行"
        assert (r.get("轨道") or "正式") == "正式"
    assert any(r["事件号"] == eid and r["状态"] == "成功" for r in rows)

    ledger = c.get("/api/回放").get_json()
    lrows = ledger if isinstance(ledger, list) else (ledger or {}).get("回放") or []
    assert any(x["事件号"] == eid for x in lrows)
