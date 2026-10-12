"""Reads vLLM's Prometheus /metrics and turns it into a small live snapshot."""
import threading
import time
from typing import Dict, Optional

from . import llm

_lock = threading.Lock()
_prev: Optional[Dict[str, float]] = None


def parse_prometheus(text: str) -> Dict[str, float]:
    """Sum every sample by metric name (labels dropped). Skips comments."""
    totals: Dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name_part, _, value = line.rpartition(" ")
        name = name_part.split("{", 1)[0]
        try:
            totals = {**totals, name: totals.get(name, 0.0) + float(value)}
        except ValueError:
            continue
    return totals


def _avg(m: Dict[str, float], base: str) -> Optional[float]:
    count = m.get(f"vllm:{base}_count", 0)
    return round(m.get(f"vllm:{base}_sum", 0) / count, 3) if count else None


def build_snapshot(m: Dict[str, float], prev: Optional[Dict[str, float]], now: float) -> Dict[str, object]:
    gen = m.get("vllm:generation_tokens_total", 0)
    tokens_per_s = 0.0
    if prev and now > prev["t"]:
        tokens_per_s = max(0.0, (gen - prev["gen"]) / (now - prev["t"]))
    return {
        "ok": True,
        "model": llm.MODEL,
        "running": m.get("vllm:num_requests_running", 0),
        "waiting": m.get("vllm:num_requests_waiting", 0),
        "kv_cache_pct": round(100 * m.get("vllm:kv_cache_usage_perc", 0), 1),
        "tokens_per_s": round(tokens_per_s, 1),
        "generation_tokens_total": gen,
        "prompt_tokens_total": m.get("vllm:prompt_tokens_total", 0),
        "avg_ttft_s": _avg(m, "time_to_first_token_seconds"),
        "avg_e2e_s": _avg(m, "e2e_request_latency_seconds"),
        "preemptions": m.get("vllm:num_preemptions_total", 0),
    }


def snapshot() -> Dict[str, object]:
    global _prev
    try:
        raw = llm.guarded_get(f"{llm.VLLM_BASE_URL}/metrics")
    except Exception as exc:  # tunnel down, server restarting, etc.
        return {"ok": False, "error": f"Cannot reach the model server ({exc}). Is the port-forward running?"}
    m = parse_prometheus(raw)
    now = time.time()
    with _lock:
        snap = build_snapshot(m, _prev, now)
        _prev = {"t": now, "gen": m.get("vllm:generation_tokens_total", 0)}
    return snap
