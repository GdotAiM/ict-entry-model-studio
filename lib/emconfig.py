"""Shared config loader: config/instruments.json, overridden key-by-key by config/instruments.local.json (git-ignored).

Stop buffers (spread + normal 1m wick) live in each instrument's "stop_buffer". They are DEFAULT PLACEHOLDERS until the
trader enters their Capital.com spreads; validate_setup.py reads them from here and reports which values it used.
"""
from __future__ import annotations
import json, os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG = os.path.join(ROOT, "config", "instruments.json")
LOCAL = os.path.join(ROOT, "config", "instruments.local.json")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str = CONFIG, local: str | None = LOCAL) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    if local and os.path.exists(local):
        with open(local) as f:
            cfg = _merge(cfg, json.load(f))
    return cfg


def instrument(cfg: dict, name: str) -> dict:
    key = name.upper()
    for n, spec in cfg["instruments"].items():
        if key in (n.upper(), spec["tv"].upper()):
            return {"name": n, **spec}
    raise KeyError(f"unknown instrument {name!r}; known: {', '.join(cfg['instruments'])}")


def watchlist(cfg: dict) -> list[str]:
    """Instruments that are switched on (no "enabled": false), in config order, including context-only ones (DXY)."""
    return [n for n, spec in cfg["instruments"].items() if spec.get("enabled", True) is not False]


def tradeable(cfg: dict) -> list[str]:
    """Switched-on instruments that get setups and alerts (watchlist minus confluence_only, i.e. minus DXY)."""
    return [n for n in watchlist(cfg) if not cfg["instruments"][n].get("confluence_only")]


def corr_group(cfg: dict, name: str) -> str | None:
    """Correlation family used by the 1% group cap ("US-indices", "USD"); None = stands alone."""
    try:
        return instrument(cfg, name).get("corr_group")
    except KeyError:
        return None


def stop_buffer(cfg: dict, name: str) -> dict:
    """{'spread', 'wick_1m', 'total', 'status'} for an instrument."""
    sb = instrument(cfg, name).get("stop_buffer") or {}
    spread, wick = float(sb.get("spread", 0.0)), float(sb.get("wick_1m", 0.0))
    return {"spread": spread, "wick_1m": wick, "total": spread + wick, "status": sb.get("status", "unspecified"),
            "is_default": "DEFAULT" in str(sb.get("status", "")).upper()}
