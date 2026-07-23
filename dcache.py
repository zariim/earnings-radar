"""
dcache.py — 磁盘缓存层 (按 (report_name, filter_key) 维度)
========================================================
针对东财 datacenter 大批量拉取(每次 5-10s), 加磁盘缓存减少日内重复拉取。
TTL 默认 6 小时 (数据中心数据日内基本不变, 7/15 强制披露截止日会触发更新)。
"""
import json
import os
import time
import hashlib

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "dcache")
_TTL_DEFAULT = 6 * 3600  # 6 hours


def _key(report, params):
    raw = f"{report}|{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _path(report, key):
    return os.path.join(CACHE_DIR, f"{report}__{key}.json")


def get(report, params, ttl=_TTL_DEFAULT):
    """读缓存, None 表示 miss。"""
    path = _path(report, _key(report, params))
    try:
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        if time.time() - mtime > ttl:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_or_load(report, params, loader, ttl=_TTL_DEFAULT):
    """缓存优先; miss 时调 loader() 加载并写回。"""
    hit = get(report, params, ttl=ttl)
    if hit is not None:
        return hit
    data = loader()
    put(report, params, data)
    return data


def put(report, params, data):
    """写缓存 (失败不影响主流程)。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_path(report, _key(report, params)), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def memo(report_name, ttl=_TTL_DEFAULT):
    """
    装饰器: 自动 memoize datacenter 拉取。
    用法:
        @memo("RPT_WEB_RESPREDICT")
        def build():
            return [...rows...]
    """
    def deco(fn):
        params = {"_fn": fn.__name__}

        def wrapped(*args, **kwargs):
            cache_params = dict(params)
            cache_params.update({f"a{i}": a for i, a in enumerate(args)})
            cache_params.update({f"k{k}": v for k, v in kwargs.items()})
            hit = get(report_name, cache_params, ttl=ttl)
            if hit is not None:
                return hit
            data = fn(*args, **kwargs)
            put(report_name, cache_params, data)
            return data
        return wrapped
    return deco


def clear():
    """清空所有磁盘缓存 (调试用)。"""
    import shutil
    try:
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        pass