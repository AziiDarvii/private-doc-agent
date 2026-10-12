"""Client for the self-hosted vLLM endpoint, behind an egress guard.

Every outbound HTTP call goes through `guarded_post` / `guarded_get`, which refuse
any host that is not on the allowlist *before* any bytes leave the machine.
"""
import json
import os
import re
import threading
import urllib.request
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://localhost:8000")
MODEL = os.environ.get("VLLM_MODEL", "Qwen/Qwen3-4B")
ALLOWED_HOSTS = frozenset(
    h.strip() for h in os.environ.get("EGRESS_ALLOWLIST", "localhost,127.0.0.1").split(",") if h.strip()
)
REQUEST_TIMEOUT_S = 180
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class EgressBlocked(Exception):
    """Raised when code tries to send data to a host outside the allowlist."""


_lock = threading.Lock()
_stats: Dict[str, float] = {
    "calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "bytes_to_own_gpu": 0,
    "bytes_to_third_parties": 0,
    "blocked_attempts": 0,
}
_blocked_log: Tuple[Dict[str, str], ...] = ()


def _bump(**deltas: float) -> None:
    global _stats
    with _lock:
        _stats = {k: v + deltas.get(k, 0) for k, v in _stats.items()}


def stats() -> Dict[str, object]:
    with _lock:
        return {**_stats, "blocked_log": list(_blocked_log[-5:]), "allowlist": sorted(ALLOWED_HOSTS)}


def _check_host(url: str) -> None:
    global _blocked_log
    host = urlparse(url).hostname or ""
    if host in ALLOWED_HOSTS:
        return
    with _lock:
        _blocked_log = _blocked_log + ({"host": host, "url": url},)
    _bump(blocked_attempts=1)
    raise EgressBlocked(f"Blocked: {host} is not on the egress allowlist. 0 bytes sent.")


def guarded_post(url: str, payload: dict) -> dict:
    _check_host(url)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        data = json.loads(resp.read())
    _bump(bytes_to_own_gpu=len(body))
    return data


def guarded_get(url: str, timeout: float = 5) -> str:
    _check_host(url)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode()


def chat(
    messages: List[Dict[str, str]],
    max_tokens: int = 512,
    temperature: float = 0.2,
    json_mode: bool = False,
) -> Tuple[str, Dict[str, int]]:
    """Send a chat completion to our own GPU. Returns (text, usage)."""
    payload: Dict[str, object] = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Qwen3 "thinking" adds latency and tokens we don't need here.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = guarded_post(f"{VLLM_BASE_URL}/v1/chat/completions", payload)
    usage = data.get("usage") or {}
    _bump(
        calls=1,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
    )
    text = THINK_RE.sub("", data["choices"][0]["message"]["content"] or "").strip()
    return text, usage


def parse_json(text: str) -> Optional[dict]:
    """Best-effort extraction of the first JSON object in a model reply."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
