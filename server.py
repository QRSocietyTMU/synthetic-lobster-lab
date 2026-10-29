"""Serve the workshop dashboard locally: python server.py, then open localhost:8765."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import asdict
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
from urllib.parse import parse_qs, urlparse
import uuid
import zipfile

from generate_lobster import Config, CONFIG_LIMITS, export_result, simulate


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
RUNS: OrderedDict[str, dict] = OrderedDict()
RUN_LOCK = threading.Lock()
SIMULATION_LOCK = threading.Lock()
MAX_SAVED_RUNS = 3


class DashboardHandler(BaseHTTPRequestHandler):
    """Small same-origin API, bound to this computer only."""

    def log_message(self, format, *args):
        if args and str(args[1] if len(args) > 1 else "").startswith("5"):
            super().log_message(format, *args)

    def send_bytes(self, body, content_type, status=200, filename=None):
        compressed = (
            len(body) > 2048
            and "gzip" in self.headers.get("Accept-Encoding", "")
            and content_type != "application/zip"
        )
        if compressed:
            body = gzip.compress(body, compresslevel=3)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A new slider input can supersede a response already in flight.

    def send_json(self, value, status=200):
        self.send_bytes(
            json.dumps(value, separators=(",", ":"), allow_nan=False).encode(),
            "application/json; charset=utf-8", status,
        )

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/api/health":
            return self.send_json({"status": "ready", "hardware_connected": False})
        if route.path == "/api/config":
            return self.send_json({"defaults": asdict(Config()), "limits": CONFIG_LIMITS})
        if route.path == "/api/export":
            return self.download_run(parse_qs(route.query))
        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if route.path in static_files:
            filename, kind = static_files[route.path]
            path = WEB / filename
            if path.is_file():
                return self.send_bytes(path.read_bytes(), kind)
        return self.send_json({"error": "This page does not exist."}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/simulate":
            return self.send_json({"error": "This endpoint does not exist."}, 404)
        origin = self.headers.get("Origin")
        if origin and origin not in {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }:
            return self.send_json({"error": "Use the dashboard on this computer."}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError("Send a settings object smaller than 8 KB.")
            settings = json.loads(self.rfile.read(size))
            if not isinstance(settings, dict):
                raise ValueError("Settings must be a JSON object.")
            config = Config(**settings)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)

        # One simulation at a time keeps dragging several sliders from saturating a laptop.
        if not SIMULATION_LOCK.acquire(timeout=20):
            return self.send_json({"error": "Another run is still generating. Try again shortly."}, 503)
        try:
            result = simulate(config)
            if not result.get("validation", {}).get("passed"):
                raise RuntimeError("The generated run did not pass its consistency checks.")
            run_id = uuid.uuid4().hex
            with RUN_LOCK:
                RUNS[run_id] = result
                while len(RUNS) > MAX_SAVED_RUNS:
                    RUNS.popitem(last=False)
            payload = {
                key: result[key]
                for key in ("config", "events", "candles", "summary", "validation", "initial_orders")
            }
            payload.update(
                run_id=run_id,
                final_book=result["orderbook"][-1],
                book_frames=result["orderbook"],
            )
            self.send_json(payload)
        except (TypeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            print(f"Simulation failed: {type(exc).__name__}: {exc}", flush=True)
            self.send_json({"error": "The simulation could not finish. Details are in the server terminal."}, 500)
        finally:
            SIMULATION_LOCK.release()

    def download_run(self, query):
        if query.get("format", ["zip"])[0] != "zip":
            return self.send_json({"error": "Use format=zip."}, 400)
        run_id = query.get("run_id", [""])[0]
        with RUN_LOCK:
            result = RUNS.get(run_id)
        if result is None:
            return self.send_json({"error": "This run has expired. Generate it again, then export."}, 404)
        try:
            with tempfile.TemporaryDirectory(prefix="lobster-export-") as folder:
                export_result(result, folder, include_xlsx=True)
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                    for path in sorted(Path(folder).iterdir()):
                        if path.is_file():
                            output.write(path, path.name)
                self.send_bytes(archive.getvalue(), "application/zip", filename="synthetic-market.zip")
        except Exception as exc:
            print(f"Export failed: {type(exc).__name__}: {exc}", flush=True)
            self.send_json({"error": "Export failed. Check the terminal and confirm openpyxl is installed."}, 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Synthetic Market Lab is ready at http://127.0.0.1:{server.server_port}", flush=True)
    print("Keep this terminal open. Ctrl+C stops the server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMarket Lab stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
