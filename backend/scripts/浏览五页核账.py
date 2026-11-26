#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook-10 收工：依次浏览五页，核账实同源。

顺序（题目指定）：
  1) 投递互证列表   2) 端点名册详情   3) 投递流水卡片
  4) 签名规目录     5) 事件队列策略

每页校验 200 + 必读中文/骨签标记，并打其后端 API；
最后做跨页同源核对：
  /api/概览.已投递 == /api/水位.现行已投递(水位值) == /api/互证/跑.已投递数
且回放笔数只在旁路集合里，不进正式口径。

纯标准库：
  python scripts/浏览五页核账.py [--base http://127.0.0.1:8510]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

骨签 = "hook-10-回放旁路禁写现行"


class 浏览器:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def _open(self, method, path, body=None):
        data = None
        headers = {"Accept": "application/json,text/html"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        safe = urllib.parse.quote(path, safe="/?&=%:+#")
        req = urllib.request.Request(self.base + safe, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as r:
                raw = r.read()
                return r.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def 登录(self, u, p):
        code, txt = self._open("POST", "/api/登录", {"账号": u, "密码": p})
        try:
            j = json.loads(txt)
        except json.JSONDecodeError:
            j = {}
        if code != 200 or not j.get("ok"):
            raise SystemExit(f"登录失败 {code}: {txt[:200]}")

    def 页(self, path):
        return self._open("GET", path)

    def api(self, method, path, body=None):
        code, txt = self._open(method, path, body)
        try:
            return code, json.loads(txt)
        except json.JSONDecodeError:
            return code, {"_raw": txt}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8510")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="123456")
    args = ap.parse_args()
    b = 浏览器(args.base)
    b.登录(args.user, args.password)

    失败 = []

    def 核(cond, msg):
        print(("    ✅ " if cond else "    ❌ ") + msg)
        if not cond:
            失败.append(msg)

    def 含全部(html, tokens, 页名):
        for t in tokens:
            核(t in html, f"{页名} 页面可读「{t}」")

    # 先取一次跨页同源基准
    c_ov, ov = b.api("GET", "/api/概览")
    c_w, w0 = b.api("GET", "/api/水位")
    c_rc, rc = b.api("POST", "/api/互证/跑", {})
    总_ov = ov.get("已投递")
    总_w = w0.get("现行已投递", w0.get("水位值"))
    总_rc = rc.get("已投递数")
    print(f"[同源基准] 概览.已投递={总_ov}  水位.现行已投递={总_w}  互证.已投递数={总_rc}")

    # ---------- 第 1 页：投递互证列表 ----------
    print("\n① 投递互证列表")
    code, html = b.页("/投递互证.html")
    核(code == 200, f"GET /投递互证.html -> {code}")
    含全部(html, ["投递互证", "同票", "回放旁路", "PIAO", "已投递"], "投递互证")
    code, rows = b.api("GET", "/api/互证")
    核(code == 200 and isinstance(rows, list), f"GET /api/互证 -> {code}，批次 {len(rows) if isinstance(rows,list) else '?'} 行")
    核(c_rc == 200 and "已投递数" in rc, f"POST /api/互证/跑 -> {c_rc}，已投递数={总_rc}")
    核("回放旁路数" in rc, f"互证单列 回放旁路数={rc.get('回放旁路数')}（不计入已投递）")
    tk = rc.get("同票核对") or {}
    核(bool(tk.get("对齐", rc.get("对齐", False))), f"同票核对对齐：{tk if tk else rc.get('说明','')}")

    # ---------- 第 2 页：端点名册详情 ----------
    print("\n② 端点名册详情（联查）")
    code, html = b.页("/端点名册.html")
    核(code == 200, f"GET /端点名册.html -> {code}")
    含全部(html, ["端点名册详情", "看详情", "/api/端点/"], "端点名册")
    code, eps = b.api("GET", "/api/端点")
    核(code == 200 and isinstance(eps, list) and eps, f"GET /api/端点 -> {code}，{len(eps) if isinstance(eps,list) else 0} 个端点")
    key = "ep-billing" if any(e.get("端点键") == "ep-billing" for e in eps) else eps[0]["端点键"]
    code, d = b.api("GET", f"/api/端点/{key}")
    核(code == 200 and d.get("端点键") == key, f"GET /api/端点/{key} -> {code}")
    cnt = d.get("计数") or {}
    核(all(k in cnt for k in ("事件", "已投递", "成功流水", "死信")), f"详情含联查计数 {cnt}")
    流水 = d.get("投递流水") or []
    核(all((r.get("轨道", "正式") == "正式") and not r.get("回放令牌") for r in 流水),
       f"端点详情流水 {len(流水)} 条全部为正式轨（回放不混入）")
    code404, _ = b.api("GET", "/api/端点/__不存在__")
    核(code404 == 404, f"不存在端点详情 -> {code404}")

    # ---------- 第 3 页：投递流水卡片 ----------
    print("\n③ 投递流水卡片（hook-10 主舞台）")
    code, html = b.页("/投递流水.html")
    核(code == 200, f"GET /投递流水.html -> {code}")
    含全部(html, ["回放令牌", "写口隔离", "水位", 骨签, "旁路账册", "同一把锁", "中文说明", "PIAO"], "投递流水")
    核(c_w == 200 and all(k in w0 for k in ("水位值", "正式成功流水", "回放旁路笔数", "中文说明")),
       f"GET /api/水位 -> {c_w}，水位值={w0.get('水位值')}，正式成功流水={w0.get('正式成功流水')}，回放旁路笔数={w0.get('回放旁路笔数')}")
    核(骨签 in str(w0.get("骨签")) and w0.get("工程形态") == "旁路账册",
       f"水位回执带骨签与形态：{w0.get('骨签')} / {w0.get('工程形态')}")
    code, ledger = b.api("GET", "/api/回放")
    核(code == 200 and isinstance(ledger, list), f"GET /api/回放（旁路账册）-> {code}，{len(ledger)} 笔")
    code, formal = b.api("GET", "/api/投递")
    核(code == 200 and isinstance(formal, list), f"GET /api/投递（正式流水）-> {code}，{len(formal)} 行")
    # 账实同源：账册里的票号都应是 PIAO-<事件号>
    核(all(r.get("票号") == "PIAO-" + r.get("事件号", "") for r in ledger),
       "旁路账册笔笔同票 PIAO-<事件号>")

    # ---------- 第 4 页：签名规目录 ----------
    print("\n④ 签名规目录")
    code, html = b.页("/签名规.html")
    核(code == 200, f"GET /签名规.html -> {code}")
    含全部(html, ["签名规目录", "回放工程形态", "写口策略", "现行水位口径", "中文策略解读"], "签名规")
    code, rules = b.api("GET", "/api/签名规")
    核(code == 200 and isinstance(rules, dict), f"GET /api/签名规 -> {code}，{len(rules)} 条")
    核(rules.get("骨签") == 骨签, f"签名规骨签={rules.get('骨签')}")
    核(rules.get("回放工程形态") == "旁路账册", f"回放工程形态={rules.get('回放工程形态')}")
    核(bool(rules.get("写口策略")), f"写口策略={rules.get('写口策略')}")
    核(bool(rules.get("现行水位口径")), f"现行水位口径={rules.get('现行水位口径')}")

    # ---------- 第 5 页：事件队列策略 ----------
    print("\n⑤ 事件队列策略（中文命运）")
    code, html = b.页("/事件队列.html")
    核(code == 200, f"GET /事件队列.html -> {code}")
    含全部(html, ["事件队列策略", "中文命运", "详情/策略", "回放"], "事件队列")
    code, events = b.api("GET", "/api/事件")
    核(code == 200 and isinstance(events, list) and events, f"GET /api/事件 -> {code}，{len(events)} 个事件")
    命运集 = {r.get("状态") for r in events}
    核(bool(命运集), f"队列含中文命运状态：{sorted(x for x in 命运集 if x)}")

    # ---------- 跨页账实同源总核对 ----------
    print("\n=== 跨页账实同源总核对 ===")
    核(总_ov == 总_w == 总_rc, f"已投递三处一致：概览={总_ov} / 水位={总_w} / 互证={总_rc}")
    正式计数 = w0.get("现行已投递行数")
    核(正式计数 in (None, 总_w) or 正式计数 == 总_w,
       f"水位现行已投递行数={正式计数} 与水位值自洽")
    回放数 = w0.get("回放旁路笔数")
    核(isinstance(回放数, int) and 回放数 == len(ledger),
       f"水位回放旁路笔数={回放数} == /api/回放 账册 {len(ledger)} 笔（回放单列，不进正式）")

    print("\n" + "=" * 64)
    if 失败:
        print(f"❌ 五页核账有 {len(失败)} 项未过：")
        for m in 失败:
            print("  - " + m)
        return 1
    print(f"✅ 五页依次浏览完毕，页页 200、标记可读、账实同源。骨签 {骨签}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
