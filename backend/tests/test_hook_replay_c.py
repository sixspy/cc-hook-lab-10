# -*- coding: utf-8 -*-
"""hook-10 争用失败测：回放与正式投递并发打同一把锁，禁止脏写。

红灯根因：旧 投递.投递一次 无事件级同锁、无原子状态占用，N 线程同投一事件 -> N 条成功流水。
救绿服务：app/services/并发锁.py（事件级同锁）+ 投递写口临界区内复检/原子占用。
验收：并发双请求（一回放、一正式）打同锁；正式成功恰一条，回放只入旁路账册，水位恒定。
"""
import os
import tempfile
import threading
from pathlib import Path

_env = tempfile.mkdtemp(prefix="hook10_c_env_")
os.environ["数据目录"] = _env
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

import app.db as dbmod  # noqa: E402
from app.db import 连接  # noqa: E402
from app.factory import create_app  # noqa: E402
from app import seed  # noqa: E402
from app.services import 入队, 投递  # noqa: E402

try:
    from app.services import 回放 as 回放服务
except Exception:  # pragma: no cover
    回放服务 = None
try:
    from app.services import 并发锁
except Exception:  # pragma: no cover
    并发锁 = None

_默认库 = dbmod.库路径


@pytest.fixture()
def 台():
    d = tempfile.mkdtemp(prefix="hook10_c_case_")
    dbmod.库路径 = Path(d) / "c.db"
    seed.初始化库()
    app = create_app()
    c = app.test_client()
    assert c.post("/api/登录", json={"账号": "admin", "密码": "123456"}).status_code == 200
    yield {"app": app, "c": c}
    dbmod.库路径 = _默认库


def _成功流水数(eid):
    conn = 连接()
    try:
        return conn.execute(
            "SELECT COUNT(*) n FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
        ).fetchone()["n"]
    finally:
        conn.close()


def _新待投事件():
    ev = 入队.建事件({"事件类型": "invoice.paid", "端点键": "ep-partner", "载荷摘要": "race"})
    return ev["事件号"]


def test_丙1_同一事件八线程并发正式投递_仅一条成功(台):
    """无锁时 8 线程全部读到『待投』并各写一条成功 -> 脏写；修复后恰一条成功。"""
    eid = _新待投事件()
    N = 8
    bar = threading.Barrier(N)
    结果 = []
    锁 = threading.Lock()

    def worker():
        try:
            bar.wait()
            r = 投递.投递一次(eid)
            with 锁:
                结果.append(("ok", r))
        except Exception as e:  # 输家应被干净拒绝
            with 锁:
                结果.append(("err", type(e).__name__))

    ts = [threading.Thread(target=worker) for _ in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)

    assert _成功流水数(eid) == 1, (
        f"并发脏写：同一事件出现 {_成功流水数(eid)} 条成功流水（应为 1）"
    )
    assert sum(1 for k, _ in 结果 if k == "ok") == 1


def test_丙2_回放回刷与正式投递并发_打同锁_不脏写(台):
    """4 个回放回刷（带标记撞写口）+ 4 个正式投递并发：

    红灯：旧写口忽略回放标记且无同锁 -> 8 条成功脏写。
    绿灯：回放回刷在同一把事件锁内被拒（409），正式投递临界区串行 -> 恰 1 条成功。
    """
    app = 台["app"]
    eid = _新待投事件()
    N_R, N_F = 4, 4
    bar = threading.Barrier(N_R + N_F)
    codes = []
    锁 = threading.Lock()

    def client():
        cc = app.test_client()
        assert cc.post("/api/登录", json={"账号": "admin", "密码": "123456"}).status_code == 200
        return cc

    def replay_worker():
        cc = client()
        bar.wait()
        r = cc.post("/api/投递", json={"事件号": eid, "回放": True, "回放令牌": "RP-race"})
        with 锁:
            codes.append(("replay", r.status_code))

    def formal_worker():
        cc = client()
        bar.wait()
        r = cc.post("/api/投递", json={"事件号": eid})
        with 锁:
            codes.append(("formal", r.status_code))

    ts = [threading.Thread(target=replay_worker) for _ in range(N_R)]
    ts += [threading.Thread(target=formal_worker) for _ in range(N_F)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)

    n_succ = _成功流水数(eid)
    assert n_succ == 1, f"回放/正式并发脏写：成功流水 {n_succ} 条（应恰 1）"

    replay_codes = [code for kind, code in codes if kind == "replay"]
    formal_codes = [code for kind, code in codes if kind == "formal"]
    # 修复后回放回刷必须全部被写口隔离
    assert replay_codes and all(code in (400, 403, 409) for code in replay_codes), replay_codes
    # 正式投递恰一个 200，其余被状态/锁干净拒绝（4xx），不得 5xx
    assert formal_codes.count(200) == 1, formal_codes
    assert all(code < 500 for code in formal_codes), formal_codes


def test_丙3_回放与正式共用同一把事件锁_锁原语互斥(台):
    """确定性锁契约：同事件同锁键；持锁期间他线程非阻塞获取失败。"""
    assert 并发锁 is not None, "app.services.并发锁 尚未接线"
    eid = _新待投事件()
    assert 并发锁.锁键(eid).endswith(eid)

    got = {}

    def try_acquire():
        got["acquired"] = 并发锁.尝试获锁(eid, 超时=0.1)
        if got["acquired"]:
            并发锁.释放(eid)

    with 并发锁.同锁(eid):
        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=5)
        assert got.get("acquired") is False, "持锁期间他线程竟能进入同一临界区"

    # 释放后可再获取
    assert 并发锁.尝试获锁(eid, 超时=0.5) is True
    并发锁.释放(eid)


def test_丙4_已投递事件并发旁路回放_与正式读不脏写_水位恒定(台):
    """已投递事件：N 个旁路回放 + 并发正式写口（应拒），账册 N 条、正式仍 1 条、水位不变。"""
    assert 回放服务 is not None and 并发锁 is not None
    c = 台["c"]
    eid = _新待投事件()
    assert 投递.投递一次(eid)["状态"] == "成功"

    def 水位():
        conn = 连接()
        try:
            return conn.execute("SELECT COUNT(*) n FROM 事件 WHERE 状态='已投递'").fetchone()["n"]
        finally:
            conn.close()

    w0 = 水位()
    N = 6
    bar = threading.Barrier(N + 2)
    outs, errs = [], []
    lk = threading.Lock()

    def rp():
        try:
            bar.wait()
            r = 回放服务.发起回放(eid, 理由="race-replay")
            with lk:
                outs.append(r)
        except Exception as e:
            with lk:
                errs.append(("rp", type(e).__name__, str(e)))

    def fm():
        try:
            bar.wait()
            投递.投递一次(eid)
        except Exception as e:
            with lk:
                errs.append(("fm", type(e).__name__))

    ts = [threading.Thread(target=rp) for _ in range(N)]
    ts += [threading.Thread(target=fm) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)

    assert not [e for e in errs if e[0] == "rp"], f"旁路回放不应失败: {errs}"
    assert _成功流水数(eid) == 1
    conn = 连接()
    try:
        ledger_n = conn.execute("SELECT COUNT(*) n FROM 回放账册 WHERE 事件号=?", (eid,)).fetchone()["n"]
    finally:
        conn.close()
    assert ledger_n == N, f"旁路账册应有 {N} 条，实际 {ledger_n}"
    assert 水位() == w0, "并发回放抬高了水位"
    keys = {r.get("锁键") for r in outs}
    assert keys == {并发锁.锁键(eid)}, f"回放未打同一把锁: {keys}"
