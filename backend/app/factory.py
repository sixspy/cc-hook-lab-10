from __future__ import annotations
import os
from pathlib import Path
from flask import Flask, jsonify, redirect, request, send_from_directory

from app import seed
from app.services import 会话, 入队, 投递, 死信, 重试, 互证, 端点, 夹具, 说明, 签名目录, 回放, 水位

_STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC), static_url_path="")
    app.secret_key = os.environ.get("密钥", "hook-lab-dev")

    @app.before_request
    def _gate():
        if request.path.startswith("/api/") and request.path not in ("/api/登录",):
            if not 会话.已登录():
                return jsonify({"错误": "未登录"}), 401
        return None

    @app.route("/login")
    def 登录页():
        return send_from_directory(_STATIC, "login.html")

    @app.route("/")
    def 首页():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/<path:name>")
    def 静态页(name):
        p = _STATIC / name
        if p.is_file():
            return send_from_directory(_STATIC, name)
        return redirect("/")

    @app.post("/api/登录")
    def api_登录():
        body = request.get_json(force=True, silent=True) or {}
        账号 = str(body.get("账号") or body.get("user") or "")
        密码 = str(body.get("密码") or body.get("password") or "")
        if 会话.校验登录(账号, 密码):
            会话.写入会话(账号)
            return jsonify({"ok": True, "用户": 账号})
        return jsonify({"ok": False, "错误": "账号或密码不对"}), 401

    @app.post("/api/登出")
    def api_登出():
        会话.登出()
        return jsonify({"ok": True})

    @app.get("/api/概览")
    def api_概览():
        return jsonify(签名目录.概览())

    @app.get("/api/仓情")
    def api_仓情():
        return jsonify(说明.仓情())

    @app.get("/api/签名规")
    def api_签名规_get():
        return jsonify(签名目录.签名规())

    @app.post("/api/签名规")
    def api_签名规_post():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(签名目录.改签名规(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/事件")
    def api_事件():
        return jsonify(签名目录.事件列表())

    @app.get("/api/事件/<eid>")
    def api_事件一条(eid):
        row = 签名目录.事件一条(eid)
        if not row:
            return jsonify({"错误": "无此事件"}), 404
        return jsonify(row)

    @app.post("/api/事件/入队")
    def api_入队():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(入队.建事件(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/投递")
    def api_投递列表():
        return jsonify(签名目录.投递列表())

    @app.post("/api/投递")
    def api_投递():
        body = request.get_json(force=True, silent=True) or {}
        try:
            eid = body["事件号"]
            强制 = bool(body.get("强制失败"))
            # 其余字段（回放标记 / 回放令牌 / 来源 / 轨道 …）原样透传给写口隔离闸
            额外 = {k: v for k, v in body.items() if k not in ("事件号", "强制失败")}
            return jsonify(投递.投递一次(eid, 强制, **额外))
        except 回放.写口隔离错误 as e:
            键 = getattr(e, "锁键", None)
            if not 键 and body.get("事件号"):
                键 = 回放.锁键(str(body["事件号"]))
            return jsonify({
                "错误": str(e), "写口隔离": True, "拒写口回刷": True,
                "工程形态": 回放.工程形态, "骨签": 回放.骨签, "锁键": 键,
            }), getattr(e, "状态码", 409)
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/水位")
    def api_水位():
        return jsonify(水位.读水位())

    @app.get("/api/回放")
    def api_回放列表():
        # 与其它集合口一致：根路径返回账册数组；聚合态走 /api/回放/概览
        return jsonify(回放.列表())

    @app.get("/api/回放/概览")
    @app.get("/api/回放台")
    @app.get("/api/回放/状态")
    def api_回放概览():
        return jsonify(回放.概览())

    @app.get("/api/回放/账册")
    @app.get("/api/回放/列表")
    def api_回放账册():
        return jsonify(回放.列表())

    @app.get("/api/回放/令牌")
    def api_回放令牌列表():
        return jsonify(回放.令牌列表())

    @app.post("/api/回放/令牌")
    def api_回放令牌签发():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(回放.签发令牌(body.get("事件号"), body.get("理由", "")))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/回放")
    def api_回放执行():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(回放.发起回放(body))
        except 回放.写口隔离错误 as e:
            键 = getattr(e, "锁键", None)
            if not 键 and body.get("事件号"):
                键 = 回放.锁键(str(body["事件号"]))
            return jsonify({"错误": str(e), "写口隔离": True, "拒写口回刷": True,
                            "工程形态": 回放.工程形态, "骨签": 回放.骨签, "锁键": 键}), getattr(e, "状态码", 409)
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/夹具回放/<name>")
    def api_夹具回放(name):
        try:
            体 = 夹具.读取(name)
            items = 体.get("回放") or []
            out = [回放.发起回放(it) for it in items]
            return jsonify({"夹具": name, "骨签": 体.get("骨签"), "回放": out, "条数": len(out)})
        except FileNotFoundError:
            return jsonify({"错误": name}), 404
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/投递/批量重试")
    def api_批量重试():
        return jsonify(重试.批量重试待投())

    @app.get("/api/死信")
    def api_死信():
        return jsonify(签名目录.死信列表())

    @app.post("/api/死信/重投")
    def api_死信重投():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(死信.重投(body["事件号"]))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/死信/作废")
    def api_死信作废():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(死信.作废(body["事件号"], body.get("原因", "人工作废")))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/端点")
    def api_端点():
        return jsonify(签名目录.端点列表())

    @app.get("/api/端点/<key>")
    def api_端点详情(key):
        d = 端点.详情(key)
        if not d:
            return jsonify({"错误": "无此端点"}), 404
        return jsonify(d)

    @app.post("/api/端点")
    def api_端点登记():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(端点.登记(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/端点/启用")
    def api_端点启用():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(端点.改启用(body["端点键"], bool(body.get("启用"))))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/互证")
    def api_互证列表():
        return jsonify(签名目录.互证列表())

    @app.post("/api/互证/跑")
    def api_互证跑():
        return jsonify(互证.跑一次())

    @app.get("/api/夹具")
    def api_夹具():
        return jsonify(夹具.列表())

    @app.post("/api/夹具入队/<name>")
    def api_夹具入队(name):
        try:
            体 = 夹具.读取(name)
            out = [入队.建事件(item) for item in 体.get("事件", [])]
            return jsonify({"入队": out})
        except FileNotFoundError:
            return jsonify({"错误": name}), 404
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/空桩")
    def api_空桩():
        root = Path(__file__).resolve().parent / "modules"
        out = []
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir() and (p / "__init__.py").is_file():
                    out.append(p.name)
        return jsonify({"空桩": out, "条数": len(out)})

    with app.app_context():
        seed.初始化库()
    return app
