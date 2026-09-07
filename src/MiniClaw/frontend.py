from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def architecture_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "architecture"


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    directory = architecture_directory()
    if not (directory / "index.html").is_file():
        raise FileNotFoundError(f"Architecture frontend is missing: {directory}")
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"MiniClaw architecture viewer: http://{host}:{port}/")
    print(f"Serving: {directory}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the MiniClaw SVG architecture viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
