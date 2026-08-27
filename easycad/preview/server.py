"""Tiny local preview server for the EasyCAD IR + GLB demo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = Path(__file__).resolve().parent
MODELS = ROOT / "models"
RUNTIME = ROOT / "easycad" / "runtime"
CLI = RUNTIME / "cad_cli.py"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from params import extract_params, load_ir  # noqa: E402


def _python() -> str:
    return os.environ.get("EASYCAD_PYTHON") or sys.executable


def _cli(args: list[str], stdin: str | None = None) -> dict:
    proc = subprocess.run(
        [_python(), str(CLI), *args],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        raise RuntimeError((proc.stderr or "").strip() or "cad_cli produced no output")
    return json.loads(line[-1])


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        print("[preview]", self.address_string(), format % args)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"service":"easycad-preview"}\n')
            return
        if path == "/" or path == "/index.html":
            self._send_file(PREVIEW / "index.html", "text/html; charset=utf-8")
            return
        if path == "/params":
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0] or "loop_demo"
            if "/" in name or "\\" in name or ".." in name:
                self._send_json({"ok": False, "error": "invalid name"}, status=400)
                return
            ir = load_ir(MODELS / f"{name}.ir.json")
            script = MODELS / f"{name}.step.py"
            source = script.read_text(encoding="utf-8") if script.is_file() else ""
            extracted = extract_params(ir, source)
            self._send_json({"ok": True, "name": name, **extracted})
            return
        if path == "/latest":
            latest = MODELS / ".easycad-latest.json"
            if not latest.is_file():
                self._send_json({"name": None})
                return
            self._send_file(latest, "application/json; charset=utf-8")
            return
        if path == "/parts":
            names: set[str] = set()
            for path_item in MODELS.iterdir():
                if not path_item.is_file() or path_item.name.startswith("."):
                    continue
                if path_item.name.endswith(".step.py"):
                    names.add(path_item.name[: -len(".step.py")])
                elif path_item.name.endswith(".ir.json"):
                    names.add(path_item.name[: -len(".ir.json")])
                elif path_item.suffix in {".glb", ".step"}:
                    names.add(path_item.stem)
            self._send_json({"names": sorted(names)})
            return
        if path.startswith("/models/"):
            rel = path[len("/models/") :]
            target = (MODELS / rel).resolve()
            try:
                target.relative_to(MODELS.resolve())
            except ValueError:
                self.send_error(403)
                return
            if not target.is_file():
                self.send_error(404)
                return
            ctype = "model/gltf-binary" if target.suffix == ".glb" else "application/json"
            if target.suffix == ".py":
                ctype = "text/plain; charset=utf-8"
            if target.suffix == ".step":
                ctype = "application/octet-stream"
            self._send_file(target, ctype)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/apply":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = _cli(["apply", "--json-stdin"], raw)
            self._send_json(payload, status=200 if payload.get("ok") else 400)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3246)
    args = parser.parse_args()
    MODELS.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"ok": True, "url": f"http://{args.host}:{args.port}"}, ensure_ascii=False), flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
