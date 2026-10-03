"""autopay launcher. Installed as the `autopay` command.

usage:
  uv run autopay              serve backend + pay page + merchant console (seeds db first if missing)
  uv run autopay test         run pytest
  uv run autopay mcp          launch mcp server (built last per spec)
  uv run autopay help         show help
"""
import argparse
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
backend_cmd = [sys.executable, "-m", "uvicorn", "app.api:app",
               "--host", "127.0.0.1", "--port", "8000"]


def ensure_db():
    from app.tools import ingest_customers_data
    ingest_customers_data()


def serve():
    ensure_db()
    print("api + pay page + merchant console: http://127.0.0.1:8000/console")
    subprocess.run(backend_cmd, cwd=root)


def test():
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=root)
    raise SystemExit(proc.returncode)


def mcp():
    try:
        import app.mcp_server  # noqa: F401  (presence check only)
    except ImportError:
        print("mcp server not built yet (planned last per agent spec).")
        raise SystemExit(1)
    proc = subprocess.run([sys.executable, "-m", "app.mcp_server"], cwd=root)
    raise SystemExit(proc.returncode)


def build_parser():
    parser = argparse.ArgumentParser(prog="autopay", description="autopay_voice launcher (synthetic demo).")
    parser.add_argument("target", nargs="?", default="serve",
                        choices=["serve", "test", "tests", "mcp", "help"],
                        help="what to run (default: serve).")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target == "help":
        parser.print_help()
    elif args.target in ("test", "tests"):
        test()
    elif args.target == "mcp":
        mcp()
    else:
        serve()


if __name__ == "__main__":
    main()
