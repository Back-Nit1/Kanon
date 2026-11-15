"""Localhost dashboard: where does an eval (or a single `kanon ask`) spend its time?

    uv run kanon-dashboard            # http://127.0.0.1:8791, refreshes every 3 s while a run is going

Reads traces/timing.jsonl (written by kanon.timing spans) and, as a fallback for runs made before
timing existed, the modification times of traces/eval/<mode>/<qid>/*.json. Read-only: it never
touches the index, the store, or a model.
Rejected: Streamlit/Grafana/Prometheus — a dependency or a service for one page. stdlib http.server
bound to 127.0.0.1 is enough, and nothing here is reachable from the network.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kanon import timing
from kanon.config import ROOT

PAGE = Path(__file__).with_name("dashboard.html")
MAX_RECORDS = 50_000


def load_timing(log: Path) -> list[dict]:
    if not log.exists():
        return []
    out = []
    for line in log.read_text().splitlines()[-MAX_RECORDS:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue   # a half-written last line while a run is appending
    return out


def trace_times(trace_dir: Path) -> list[dict]:
    """Fallback: completion time of each eval trace. Gaps between consecutive traces in one mode
    approximate per-question wall time for runs that predate span timing."""
    rows = []
    for f in (trace_dir / "eval").glob("*/*/*.json"):
        rows.append({"mode": f.parents[1].name, "qid": f.parent.name, "done": f.stat().st_mtime})
    return sorted(rows, key=lambda r: r["done"])


class Handler(BaseHTTPRequestHandler):
    log: Path = timing.LOG
    trace_dir: Path = ROOT / "traces"

    def do_GET(self):
        if self.path == "/api/timing":
            body = json.dumps({"records": load_timing(self.log),
                               "traces": trace_times(self.trace_dir),
                               "log": str(self.log)}).encode()
            self._send(200, "application/json", body)
        elif self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a) -> None:   # keep the terminal quiet; the page polls every 3 s
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kanon-dashboard")
    p.add_argument("--port", type=int, default=8791)
    args = p.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Kanon dashboard: http://127.0.0.1:{args.port}  (reading {Handler.log})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
