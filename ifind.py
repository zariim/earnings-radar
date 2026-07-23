"""
ifind.py — 同花顺 iFinD 接入层 (通过本地 bridge, 优雅降级)
=============================================================
iFinD 当前账号下稳定可用的是"实时行情"(THS_RealtimeQuotes): PB / 换手率 / 成交额 / 现价。
bridge 未启动或未登录时, 所有函数返回空 (不报错), 上层自动回落东财。

启动 bridge:
  python "C:/Users/ASUS/finance-mcp-server/python-bridge/ifind_bridge.py" --port 5001
"""
import os
import time
import requests

BRIDGE = os.environ.get("IFIND_BRIDGE_URL", "http://127.0.0.1:5001")
_TIMEOUT = 8

# 节流: 健康检查与数据拉取都加最小间隔, 避免被 iFinD 收费
_HEALTH_TTL = 600      # 健康探测结果缓存 10 分钟 (关键省钱)
_QUOTES_TTL = 1800     # 行情结果缓存 30 分钟 (与 panorama 缓存一致)
_LAST_HEALTH_TS = 0
_LAST_HEALTH_OK = False
_QUOTES_CACHE = {}     # code -> (ts, payload)


def available():
    """bridge 是否在线且已登录 iFinD。带 TTL 节流避免频繁探测扣费。"""
    global _LAST_HEALTH_TS, _LAST_HEALTH_OK
    now = time.time()
    if now - _LAST_HEALTH_TS < _HEALTH_TTL:
        return _LAST_HEALTH_OK
    ok = False
    try:
        r = requests.get(f"{BRIDGE}/health", timeout=4)
        d = r.json()
        ok = bool(d.get("logged_in") and d.get("ifind_available"))
    except Exception:
        ok = False
    _LAST_HEALTH_TS = now
    _LAST_HEALTH_OK = ok
    return ok


def quotes(codes, batch=40):
    """
    批量实时行情。返回 {code -> {pb, turnover, amount_yi, price, change_pct}}。
    bridge 不通 → 返回 {} (优雅降级)。带 30 分钟缓存节流。
    """
    codes = [str(c).zfill(6) for c in codes if c]
    if not codes:
        return {}
    now = time.time()
    out, missing = {}, []
    for c in codes:
        hit = _QUOTES_CACHE.get(c)
        if hit and now - hit[0] < _QUOTES_TTL:
            out[c] = hit[1]
        else:
            missing.append(c)
    if not missing:
        return out
    for i in range(0, len(missing), batch):
        chunk = missing[i:i + batch]
        try:
            r = requests.get(f"{BRIDGE}/realtime",
                             params={"codes": ",".join(chunk)}, timeout=_TIMEOUT)
            d = r.json()
            if not d.get("success"):
                continue
            for x in d.get("data", []):
                code = str(x.get("code") or "").zfill(6)
                if not code:
                    continue
                amt = x.get("amount")
                payload = {
                    "pb": round(x["pb"], 2) if x.get("pb") is not None else None,
                    "turnover": round(x["turnoverRate"], 2) if x.get("turnoverRate") is not None else None,
                    "amount_yi": round(amt / 1e8, 2) if amt else None,
                    "price": x.get("price"),
                    "change_pct": round(x["changePercent"], 2) if x.get("changePercent") is not None else None,
                }
                out[code] = payload
                _QUOTES_CACHE[code] = (now, payload)
        except Exception:
            continue
    return out


def disable_for_session():
    """紧急停用: 让 available() 永远返回 False, 直到下次进程重启。
    用于用户收到收费通知想立刻停止 iFinD 调用时。"""
    global _LAST_HEALTH_TS, _LAST_HEALTH_OK
    _LAST_HEALTH_TS = time.time()
    _LAST_HEALTH_OK = False


if __name__ == "__main__":
    print("iFinD bridge available:", available())
    q = quotes(["603986", "300750"])
    for c, v in q.items():
        print(c, v)
