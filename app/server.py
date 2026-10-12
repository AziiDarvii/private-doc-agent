"""Tiny stdlib web server: serves the UI and a JSON API on top of the agent."""
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Tuple

from . import agent, llm, metrics, privacy

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5050"))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
MAX_BODY_BYTES = 300_000
MAX_QUESTION_CHARS = 500
MAX_RUSH_USERS = 128
RUSH_MAX_TOKENS = 300
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".txt": "text/plain"}
RUSH_PROMPT = "Write a short paragraph explaining why a tenant should read a lease carefully."

log = logging.getLogger("private-doc-agent")
_rush_lock = threading.Lock()
_rush_state: Dict[str, int] = {"active": 0, "done": 0, "failed": 0}


class BadRequest(Exception):
    pass


def _require_text(body: dict, key: str, limit: int) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(f"'{key}' is required.")
    if len(value) > limit:
        raise BadRequest(f"'{key}' is too long (max {limit} characters).")
    return value


def _set_rush(**deltas: int) -> None:
    global _rush_state
    with _rush_lock:
        _rush_state = {k: v + deltas.get(k, 0) for k, v in _rush_state.items()}


def _one_rush_request(_: int) -> None:
    try:
        llm.chat([{"role": "user", "content": RUSH_PROMPT}], max_tokens=RUSH_MAX_TOKENS, temperature=0.8)
        _set_rush(active=-1, done=1)
    except Exception:
        log.exception("rush-hour request failed")
        _set_rush(active=-1, failed=1)


def _run_rush(users: int) -> None:
    with ThreadPoolExecutor(max_workers=users) as pool:
        list(pool.map(_one_rush_request, range(users)))


# ---- API handlers: body -> (status, payload) ---------------------------------

def api_analyze(body: dict) -> Tuple[int, dict]:
    document = _require_text(body, "document", agent.MAX_DOC_CHARS)
    return 200, {**agent.analyze(document), "pii": privacy.find_pii(document)}


def api_ask(body: dict) -> Tuple[int, dict]:
    document = _require_text(body, "document", agent.MAX_DOC_CHARS)
    question = _require_text(body, "question", MAX_QUESTION_CHARS)
    return 200, agent.answer(document, question)


def api_rush(body: dict) -> Tuple[int, dict]:
    users = body.get("users", 32)
    if not isinstance(users, int) or isinstance(users, bool) or not 1 <= users <= MAX_RUSH_USERS:
        raise BadRequest(f"'users' must be a number from 1 to {MAX_RUSH_USERS}.")
    _set_rush(active=users)
    threading.Thread(target=_run_rush, args=(users,), daemon=True).start()
    return 202, {"started": users}


def api_leak_test(_: dict) -> Tuple[int, dict]:
    """Simulates a buggy/compromised tool trying to send the document to a hosted AI API."""
    try:
        llm.guarded_post("https://api.openai.com/v1/chat/completions", {"messages": "[private document]"})
    except llm.EgressBlocked as exc:
        return 200, {"blocked": True, "message": str(exc)}
    return 200, {"blocked": False, "message": "WARNING: request was not blocked"}


def api_status(_: dict) -> Tuple[int, dict]:
    with _rush_lock:
        rush = dict(_rush_state)
    return 200, {"gpu": metrics.snapshot(), "privacy": llm.stats(), "rush": rush}


POST_ROUTES: Dict[str, Callable[[dict], Tuple[int, dict]]] = {
    "/api/analyze": api_analyze,
    "/api/ask": api_ask,
    "/api/rush": api_rush,
    "/api/leak-test": api_leak_test,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        if "/api/status" not in self.path:
            log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _static(self, base: Path, name: str) -> None:
        path = (base / name).resolve()
        if base not in path.parents or not path.is_file():  # blocks ../ path traversal
            self._json(404, {"error": "Not found"})
            return
        self._send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/api/status":
            self._json(*api_status({}))
        elif route.startswith("/samples/"):
            self._static(SAMPLES_DIR, route[len("/samples/"):])
        else:
            self._static(STATIC_DIR, "index.html" if route == "/" else route.lstrip("/"))

    def do_POST(self) -> None:
        handler = POST_ROUTES.get(self.path)
        if handler is None:
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise BadRequest("Document is too large for this demo (max ~300 KB of text).")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise BadRequest("Expected a JSON object.")
            self._json(*handler(body))
        except (BadRequest, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except llm.EgressBlocked as exc:
            self._json(403, {"error": str(exc)})
        except Exception:
            log.exception("request to %s failed", self.path)
            self._json(502, {"error": "The model server did not respond. Check that the GPU connection is running."})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("Private Doc Agent on http://%s:%d  (model %s at %s)", HOST, PORT, llm.MODEL, llm.VLLM_BASE_URL)
    server.serve_forever()


if __name__ == "__main__":
    main()
