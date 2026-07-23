"""
server.py — 业绩预告高增雷达 HTTP 服务
======================================
端口默认 3003 (避开 full-market-funnel 的 3002)。
路由:
  GET /                         → dashboard.html
  GET /api/panorama?min_yoy=50  → 五视图数据 (内存缓存 30min, key 敏感于 min_yoy)
  GET /api/meta                 → KPI + 规则口径
  GET /api/health               → 服务状态
"""
import argparse
import json
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

import aggregate
import announce
import check_coverage
import forecast
import em
import consensus
import express
import q1
import news

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
TTL = 30 * 60  # 30 min

app = Flask(__name__)


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


class Cache:
    """按 min_yoy 分槽的内存缓存 + 磁盘快照。"""
    def __init__(self):
        self.slots = {}   # min_yoy -> (ts, panorama, meta)
        self.lock = threading.Lock()

    def get(self, min_yoy, force=False, fast=False):
        key = round(float(min_yoy), 1)
        with self.lock:
            hit = self.slots.get(key)
            if hit and not force and (time.time() - hit[0]) < TTL:
                # 命中缓存, 按需切掉 slow 字段
                p = self._strip_slow(hit[1]) if fast else hit[1]
                return p, hit[2], True
        # 慢路径: 重新构建 (锁外, 避免阻塞其它请求)
        panorama, meta = aggregate.build(min_yoy=key, with_slow=not fast)
        with self.lock:
            self.slots[key] = (time.time(), panorama, meta)
        self._snapshot(key, panorama, meta)
        if fast:
            panorama = self._strip_slow(panorama)
        return panorama, meta, False

    def _strip_slow(self, panorama):
        """切掉 slow 字段 (ths_eps, reports, ann, tencent, telegraph) 用于首屏快返。"""
        p = dict(panorama)
        p["good_list"] = [
            {k: v for k, v in r.items() if k not in
             ("ths_eps", "reports", "ann", "pb", "turnover", "price", "change_pct")}
            for r in p.get("good_list", [])
        ]
        p["telegraph"] = []
        p["fast_mode"] = True
        return p

    def _snapshot(self, key, panorama, meta):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            stamp = (panorama.get("asof") or "").replace("-", "")
            payload = {"panorama": panorama, "meta": meta}
            with open(os.path.join(DATA_DIR, f"panorama_{stamp}_yoy{int(key)}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            with open(os.path.join(DATA_DIR, f"latest_yoy{int(key)}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass

    def warm_from_disk(self, key=50.0):
        """启动时若有当天快照, 秒加载进缓存 (避免首请求等待构建)。"""
        key = round(float(key), 1)
        path = os.path.join(DATA_DIR, f"latest_yoy{int(key)}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            p, m = d["panorama"], d["meta"]
            # 仅当快照是当天的才用 (避免用隔夜脏数据)
            import datetime
            today = datetime.date.today().strftime("%Y-%m-%d")
            if p.get("asof") == today:
                with self.lock:
                    self.slots[key] = (time.time(), p, m)
                return True
        except Exception:
            pass
        return False


CACHE = Cache()


def _daily_refresher(hour, min_yoy=50.0):
    """后台守护线程: 每天到点(本地 hour 时)重建默认快照。"""
    import datetime
    last_day = None
    while True:
        now = datetime.datetime.now()
        if now.hour == hour and now.date() != last_day:
            try:
                CACHE.get(min_yoy, force=True)
                last_day = now.date()
                print(f"[{now:%Y-%m-%d %H:%M}] 每日自动刷新完成 (min_yoy={min_yoy})")
            except Exception as e:  # noqa
                print("每日刷新失败:", e)
        time.sleep(300)  # 每 5 分钟检查一次


@app.route("/")
def index():
    return send_from_directory(HERE, "dashboard.html")


@app.route("/api/panorama")
def api_panorama():
    min_yoy = request.args.get("min_yoy", "50")
    force = request.args.get("refresh") == "1"
    fast = request.args.get("fast") == "1"
    try:
        panorama, meta, cached = CACHE.get(min_yoy, force=force, fast=fast)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500
    panorama["_cached"] = cached
    return jsonify(panorama)


@app.route("/api/slow_fields")
def api_slow_fields():
    """后台异步补: 同花顺EPS / 研报 / 公告 / 腾讯行情 / 财联社电报。
    不缓存, 每次调用重新拉, dashboard 首屏后调用。"""
    try:
        panorama, _ = aggregate.build(min_yoy=50.0, with_announce=True, with_slow=True)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500
    return jsonify({"good_list": panorama.get("good_list", []),
                    "telegraph": panorama.get("telegraph", [])})


@app.route("/api/meta")
def api_meta():
    min_yoy = request.args.get("min_yoy", "50")
    try:
        _, meta, _ = CACHE.get(min_yoy)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500
    return jsonify(meta)


# ============ 轻量菜单端点 (秒开) ============

@app.route("/api/summary")
def api_summary():
    """菜单页用的轻量摘要: KPI + 财联社电报 + 各模块状态。
    不构建全景 (耗时 30s), 只读 dcache 已缓存数据 (~500ms)。"""
    import datetime, dcache as _dc
    try:
        # 各表最新交易日 / 披露日
        norm = aggregate.forecast.normalize(aggregate.forecast.fetch_universe())
        cmap = consensus.build()
        imap = consensus.build_industry_map()
        exmap = express.fetch_all(aggregate.REPORT_DATE)
        q1map = q1.fetch_q1(aggregate.Q1_DATE)

        # 各模块字段 (轻量)
        good = sum(1 for x in norm if x["cls"] == "good")
        bad = sum(1 for x in norm if x["cls"] == "bad")
        neutral = sum(1 for x in norm if x["cls"] == "neutral")
        asof = max((x["notice_date"] for x in norm if x["notice_date"]), default="")
        # 模块状态
        modules = {
            "forecast": {"count": len(norm), "asof": asof, "ready": True},
            "consensus": {"count": len(cmap), "ready": True},
            "industry": {"count": len(imap), "ready": True},
            "express": {"count": len(exmap), "ready": True},
            "q1": {"count": len(q1map), "ready": True},
        }
        # 财联社电报 (单独模块, 不在全景里)
        tg = news.cls_telegraph(15)
        return jsonify({
            "kpi": {
                "disclosed": len(norm),
                "good": good, "bad": bad, "neutral": neutral,
                "good_rate": round(good / len(norm), 3) if norm else 0,
                "asof": asof,
                "express_n": len(exmap),
                "universe": len(imap),
                "q1_n": len(q1map),
            },
            "telegraph": tg,
            "modules": modules,
            "ts": datetime.datetime.now().isoformat()[:19],
        })
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/module/<name>")
def api_module(name):
    """按需加载各模块数据。
    name: high_growth | redflag | industry | vs_expect | telegraph | reports
    只返回当前模块字段, 不带其它无关数据 (减小 payload)。
    """
    min_yoy = request.args.get("min_yoy", "50")
    top = int(request.args.get("top", "100"))
    try:
        if name == "high_growth":
            panorama, _, _ = CACHE.get(min_yoy)
            return jsonify({
                "good_list": panorama.get("good_list", []),
                "style_dist": panorama.get("style_dist", []),
                "kpi_subset": {k: panorama["kpi"][k] for k in
                                ("disclosed", "good", "bad", "neutral",
                                 "good_rate", "high_growth", "express_n")}
            })
        elif name == "redflag":
            panorama, _, _ = CACHE.get(min_yoy)
            return jsonify(panorama.get("redflag", {}))
        elif name == "industry":
            panorama, _, _ = CACHE.get(min_yoy)
            return jsonify({"industry": panorama.get("industry", [])})
        elif name == "vs_expect":
            panorama, _, _ = CACHE.get(min_yoy)
            return jsonify({"vs_expect": panorama.get("vs_expect", []),
                            "stat": panorama.get("vs_expect_stat", {})})
        elif name == "telegraph":
            return jsonify({"telegraph": news.cls_telegraph(20)})
        elif name == "announcements_for_codes":
            codes = request.args.get("codes", "").split(",")
            codes = [c.strip() for c in codes if c.strip()][:top]
            anns = announce.enrich(codes)
            return jsonify({"announcements": anns})
        else:
            return jsonify({"error": f"unknown module: {name}"}), 400
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    with CACHE.lock:
        slots = {k: {"age_s": round(time.time() - v[0]),
                     "disclosed": v[1]["kpi"]["disclosed"]}
                 for k, v in CACHE.slots.items()}
    return jsonify({"ok": True, "ttl_s": TTL, "slots": slots})


@app.route("/api/announcement")
def api_announcement():
    art = request.args.get("art_code", "").strip()
    if not art:
        return jsonify({"error": "art_code required"}), 400
    try:
        d = announce.fetch_content(art)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500
    return jsonify(d)


def _run_coverage_check():
    """后台自检: 对比 dashboard disclosed vs 全披露源, 差超阈值打 warning。
    异步执行不阻塞看板启动。用 stderr + flush 解决 daemon thread stdout 被 Flask 重定向/缓冲问题。"""
    import sys
    import datetime

    def _log(s):
        sys.stderr.write(s + "\n")
        sys.stderr.flush()

    time.sleep(8)  # 等 dashboard 第一波请求完成后再查
    try:
        import requests
        r = requests.get("http://127.0.0.1:3003/api/panorama",
                         params={"min_yoy": 50}, timeout=10)
        if not r.ok:
            _log("[coverage] dashboard API 不可达, 跳过自检")
            return
        dashboard_n = r.json().get("kpi", {}).get("disclosed", 0)
        if not dashboard_n:
            _log("[coverage] dashboard 无 disclosed 数据, 跳过")
            return
        # 对照三个数据源
        raw = forecast.fetch_universe(aggregate.REPORT_DATE)
        forecast_codes = {r["code"] for r in forecast.normalize(raw)}
        ex_rows = em.paginate("RPT_FCI_PERFORMANCEE",
                              filter_str=f"(REPORT_DATE='{aggregate.REPORT_DATE}')",
                              page_size=500, max_pages=10)
        ex_codes = set(str(r.get("SECURITY_CODE") or "").zfill(6)
                       for r in ex_rows
                       if str(r.get("SECURITY_CODE") or "").zfill(6) != "000000")
        full_rows = em.paginate("RPT_LICO_FN_CPD",
                                filter_str=f"(REPORTDATE='{aggregate.REPORT_DATE}')",
                                page_size=500, max_pages=10)
        full_codes = set(str(r.get("SECURITY_CODE") or "").zfill(6)
                         for r in full_rows
                         if str(r.get("SECURITY_CODE") or "").zfill(6) != "000000")
        full_n = len(forecast_codes | ex_codes | full_codes)
        diff = full_n - dashboard_n
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        msg = (f"[coverage {ts}] disclosed={dashboard_n} | 全披露={full_n} | "
               f"差 {diff:+d} ({diff/dashboard_n*100:+.1f}%)")
        if abs(diff) >= 100:
            _log(f"⚠ {msg}  超阈值, 可能 datacenter 滞后或媒体口径差异")
        else:
            _log(f"✓ {msg}  在容差内")
    except Exception as e:  # noqa
        _log(f"[coverage] 自检异常: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3003)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--warm", action="store_true", help="启动预热 min_yoy=50")
    ap.add_argument("--daily-refresh", type=int, default=None,
                    metavar="HOUR", help="每天 HOUR 时(0-23)自动重建快照, 如 --daily-refresh 18")
    args = ap.parse_args()
    # 启动先尝试从当天磁盘快照秒加载
    if CACHE.warm_from_disk(50):
        print("已从当天磁盘快照秒加载 (min_yoy=50)")
    elif args.warm:
        print("预热中 (min_yoy=50)...")
        CACHE.get(50)
        print("预热完成")
    if args.daily_refresh is not None:
        import threading as _th
        _th.Thread(target=_daily_refresher, args=(args.daily_refresh, 50.0),
                   daemon=True).start()
        print(f"已启用每日自动刷新: 每天 {args.daily_refresh}:00")
    # 启动后异步跑数据覆盖自检 (dashboard disclosed vs 全披露源)
    import threading as _th2
    _th2.Thread(target=_run_coverage_check, daemon=True).start()
    print(f"业绩预告高增雷达 → http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, threaded=True)
