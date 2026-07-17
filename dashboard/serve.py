"""
Serve the dashboard with Supabase credentials injected from the repo
.env file.  No hardcoded keys in HTML.

Usage:
    python dashboard/serve.py [--port 8080]
"""

import argparse
import http.server
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"


def _load_env() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            if key.strip() not in os.environ:
                os.environ[key.strip()] = val.strip()


class _Handler(http.server.SimpleHTTPRequestHandler):
    _CACHEABLE = {".js", ".css", ".png", ".svg", ".woff2"}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/explorer", "/explorer.html"):
            self._serve_injected()
        else:
            super().do_GET()

    def end_headers(self):
        ext = os.path.splitext(self.path.split("?")[0])[1]
        if ext in self._CACHEABLE:
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def translate_path(self, path):
        """Serve all files from the dashboard directory."""
        rel = path.lstrip("/") or "index.html"
        return str(_DASHBOARD_DIR / rel)

    def _serve_injected(self):
        path = self.path.split("?")[0]
        fname = "explorer.html" if "explorer" in path else "index.html"
        html = (_DASHBOARD_DIR / fname).read_text(encoding="utf-8")
        html = html.replace("__SUPABASE_URL__", os.environ.get("SUPABASE_URL", ""))
        anon = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        html = html.replace("__SUPABASE_ANON_KEY__", anon)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[dashboard] {args[0]}")


def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Serve the drift dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        return 1

    print(f"Dashboard → http://localhost:{args.port}")
    httpd = http.server.HTTPServer(("0.0.0.0", args.port), _Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
