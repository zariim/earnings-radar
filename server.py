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

    def get(self, min_yoy, force=False):
        key = round(float(min_yoy), 1)
        with self.lock:
            hit = self.slots.get(key)
            if hit and not force and (time.time() - hit[0]) < TTL:
                return hit[1], hit[2], True
        # 慢路径: 重新构建 (锁外, 避免阻塞其它请求)
        panorama, meta = aggregate.build(min_yoy=key)
        with self.lock:
            self.slots[key] = (time.time(), panorama, meta)
        self._snapshot(key, panorama, meta)
        return panorama, meta, False

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
    try:
        panorama, meta, cached = CACHE.get(min_yoy, force=force)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500
    panorama["_cached"] = cached
    return jsonify(panorama)


@app.route("/api/meta")
def api_meta():
    min_yoy = request.args.get("min_yoy", "50")
    try:
        _, meta, _ = CACHE.get(min_yoy)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500
    return jsonify(meta)


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
    print(f"业绩预告高增雷达 → http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, threaded=True)
