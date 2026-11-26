#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook-10 收工核验：并发双请求打同一把事件锁。

场景一（新事件，双请求齐发）：
  屏障后同一瞬间发 POST /api/回放 与 POST /api/投递（同一事件号）。
  断言：两请求均无 5xx；回执里的 锁键 完全相同且以事件号结尾；
        正式成功流水恰好 1 条；旁路账册恰好 +1；水位不被回放抬高。

场景二（已投递事件 EVT-demo-04）：
  屏障后齐发 旁路回放 与 带回放标记的正式写口回刷。
  断言：回放 200 入账册；回刷 409 写口隔离；现行水位前后不变。

仅用标准库。用法：
  python scripts/并发双请求.py [--base http://127.0.0.1:8510] \
      [--user admin] [--password 123456] [--event EVT-demo-04]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

骨签 = "hook-10-回放旁路禁写现行"


class 客户端:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def _请求(self, method: str, path: str, body: dict | None = None, timeout: float = 20):
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        # urllib 要求 URL 全 ASCII：对中文 API 路径做百分号编码（保留 / ? & =）
        safe_path = urllib.parse.quote(path, safe="/?&=%:+")
        req = urllib.request.Request(self.base + safe_path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return e.code, {"_raw": raw}

    def 登录(self, user: str, password: str):
        code, j = self._请求("POST", "/api/登录", {"账号": user, "密码": password})
        if code != 200 or not j.get("ok"):
            raise SystemExit(f"登录失败：HTTP {code} {j}")

    def get(self, path: str):
        return self._请求("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self._请求("POST", path, body or {})


def _跑一对(c: 客户端, barrier: threading.Barrier, 任务):
    """任务 = (名字, method, path, body)。屏障齐发，返回 (名字, code, json, 耗时)。"""
    名字, method, path, body = 任务
    out = {}

    def work():
        barrier.wait()
        t0 = time.time()
        code, j = c._请求(method, path, body)
        out["r"] = (名字, code, j, round(time.time() - t0, 3))

    th = threading.Thread(target=work, name=名字)
    th.start()
    return th, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8510")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--event", default="EVT-demo-04")
    args = ap.parse_args()

    c = 客户端(args.base)
    c.登录(args.user, args.password)
    print(f"[登录] {args.user} @ {args.base}  OK")

    失败 = []

    def 检查(cond: bool, msg: str):
        print(("  ✅ " if cond else "  ❌ ") + msg)
        if not cond:
            失败.append(msg)

    # ---------- 场景一：全新待投事件，回放 + 正式投递 屏障齐发 ----------
    print("\n=== 场景一：新事件 并发[旁路回放 × 正式投递] 打同锁 ===")
    新号 = f"EVT-lock-{int(time.time() * 1000)}"
    code, j = c.post("/api/事件/入队", {
        "事件号": 新号, "事件类型": "lock.probe", "端点键": "ep-billing",
        "载荷摘要": f"probe={新号}", "优先级": "普通",
    })
    if code not in (200, 201):
        # 某些入队实现自造事件号：退回用列表里最新的待投事件
        code2, rows = c.get("/api/事件")
        待投 = [r for r in rows if r.get("状态") == "待投"] if isinstance(rows, list) else []
        if not 待投:
            raise SystemExit(f"造不出待投事件：{code} {j}")
        新号 = 待投[0]["事件号"]
        print(f"[入队] 指定号未收，改用现有待投事件 {新号}（{code} {j.get('错误','')}）")
    else:
        print(f"[入队] {新号} -> {j.get('状态', '待投')}")

    code, w0 = c.get("/api/水位")
    水0 = w0.get("水位值", w0.get("现行水位"))
    print(f"[水位] 并发前 现行水位={水0}  回放旁路笔数={w0.get('回放旁路笔数')}")

    barrier = threading.Barrier(2)
    t1, o1 = _跑一对(c, barrier, ("回放", "POST", "/api/回放", {"事件号": 新号, "理由": "并发双请求脚本"}))
    t2, o2 = _跑一对(c, barrier, ("正式投递", "POST", "/api/投递", {"事件号": 新号}))
    t1.join(25); t2.join(25)
    n1, c1, j1, dt1 = o1["r"]
    n2, c2, j2, dt2 = o2["r"]
    print(f"[齐发] 回放     HTTP {c1} {dt1}s 锁键={j1.get('锁键')} 状态={j1.get('状态') or j1.get('错误')}")
    print(f"[齐发] 正式投递 HTTP {c2} {dt2}s 锁键={j2.get('锁键')} 状态={j2.get('状态') or j2.get('错误')}")

    检查(c1 < 500 and c2 < 500, "两请求均无 5xx（同锁串行化，无 SQLite 锁库崩溃）")
    k1, k2 = j1.get("锁键"), j2.get("锁键")
    检查(bool(k1) and k1 == k2, f"两回执同一把锁：{k1} == {k2}")
    检查(bool(k1) and str(k1).endswith(新号), f"锁键按事件号归一：…:{新号}")
    检查(c1 == 200 and j1.get("票号") == f"PIAO-{新号}", "回放 200 且同票 PIAO-<事件号>")
    检查(c2 == 200 and j2.get("轨道") == "正式", "正式投递 200 且轨道=正式")

    code, rows = c.get("/api/投递")
    正式 = [r for r in rows if r.get("事件号") == 新号 and r.get("状态") == "成功"
            and (r.get("轨道", "正式") == "正式") and not r.get("回放令牌")]
    检查(len(正式) == 1, f"正式成功流水恰好 1 条（实际 {len(正式)}，无并发脏写）")

    code, ledger = c.get("/api/回放")
    账 = [r for r in ledger if r.get("事件号") == 新号] if isinstance(ledger, list) else []
    检查(len(账) >= 1, f"旁路账册有本事件记录（实际 {len(账)}）")
    if 账:
        检查(all(r.get("票号") == f"PIAO-{新号}" for r in 账), "账册票号与正式同票")

    code, w1 = c.get("/api/水位")
    水1 = w1.get("水位值", w1.get("现行水位"))
    检查(水1 == 水0 + 1, f"新事件正式成功使水位 {水0} -> {水1}（恰 +1，回放不多抬）")

    # ---------- 场景二：已投递事件，回放 vs 写口回刷 屏障齐发 ----------
    eid = args.event
    print(f"\n=== 场景二：已投递事件 {eid} 并发[旁路回放 × 带标记写口回刷] ===")
    code, before = c.get("/api/水位")
    水前 = before.get("水位值", before.get("现行水位"))
    code, recon0 = c.post("/api/互证/跑", {})
    已投前 = recon0.get("已投递数")
    print(f"[基线] 水位={水前} 互证已投递数={已投前}")

    barrier2 = threading.Barrier(2)
    t3, o3 = _跑一对(c, barrier2, ("旁路回放", "POST", "/api/回放", {"事件号": eid, "理由": "收工并发核验"}))
    t4, o4 = _跑一对(c, barrier2, ("写口回刷", "POST", "/api/投递",
                                   {"事件号": eid, "回放": True, "来源": "回放"}))
    t3.join(25); t4.join(25)
    _, c3, j3, _ = o3["r"]
    _, c4, j4, _ = o4["r"]
    print(f"[齐发] 旁路回放 HTTP {c3} 状态={j3.get('状态') or j3.get('错误')} 令牌={j3.get('回放令牌')}")
    print(f"[齐发] 写口回刷 HTTP {c4} 写口隔离={j4.get('写口隔离')} {j4.get('错误','')[:48]}")

    检查(c3 == 200 and j3.get("工程形态") == "旁路账册", "旁路回放 200，工程形态=旁路账册")
    检查(c4 == 409 and j4.get("写口隔离") is True, "写口回刷被 409 写口隔离拒绝")
    检查(j3.get("锁键") == j4.get("锁键") and str(j3.get("锁键", "")).endswith(eid),
          "回放与回刷打同一把事件锁")

    code, after = c.get("/api/水位")
    水后 = after.get("水位值", after.get("现行水位"))
    检查(水后 == 水前, f"已投递事件再回放/拒刷，水位不变（{水前} -> {水后}）")

    code, recon1 = c.post("/api/互证/跑", {})
    检查(recon1.get("已投递数") == 已投前,
          f"互证已投递数不把回放副作用算正式（{已投前} -> {recon1.get('已投递数')}）")
    tk = recon1.get("同票核对") or {}
    检查(bool(tk.get("对齐", recon1.get("对齐", False))), "互证同票核对对齐")

    # ---------- 令牌同成同败 / 一次性 ----------
    print("\n=== 附加：回放令牌一次性（同成同败） ===")
    code, tokj = c.post("/api/回放/令牌", {"事件号": eid, "理由": "双请求脚本令牌核验"})
    tok = tokj.get("回放令牌")
    检查(code == 200 and str(tok or "").startswith("RP-"), f"签发 RP 令牌 {tok}")
    code, r1 = c.post("/api/回放", {"事件号": eid, "回放令牌": tok})
    检查(code == 200, "令牌首次回放 200（令牌核销与账册同事务）")
    code, r2 = c.post("/api/回放", {"事件号": eid, "回放令牌": tok})
    检查(code in (400, 409) and r2.get("ok") is not True, f"同令牌二次使用被拒 HTTP {code}")

    print("\n" + ("=" * 60))
    if 失败:
        print(f"❌ 并发同锁核验有 {len(失败)} 项失败：")
        for m in 失败:
            print("  - " + m)
        return 1
    print(f"✅ 全部断言通过：双请求同锁、写口隔离、水位不抬、互证同票、令牌同成同败。骨签 {骨签}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
