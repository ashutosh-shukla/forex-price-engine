"""Control script for the Price Engine (works on Windows, Mac and Linux).

python engine_ctl.py init      create .env (if missing) and generate the secrets
python engine_ctl.py check-db  test the Oracle connection
python engine_ctl.py initdb    create the tables in Oracle
python engine_ctl.py start     start in the background
python engine_ctl.py stop      graceful stop (saves in-progress candles)
python engine_ctl.py status    is it running? is the feed healthy?
python engine_ctl.py run       run in this window (Ctrl+C to stop)
python engine_ctl.py restart
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
PID = ROOT / "data" / "engine.pid"
CONSOLE = ROOT / "logs" / "console.log"
SECRETS = ("ADMIN_API_KEY", "API_KEY_PEPPER", "WS_TOKEN_SECRET")


def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2)
                return out


def base_url() -> str:
    e = read_env()
    return f"http://{e.get('API_HOST', '127.0.0.1')}:{e.get('API_PORT', '8000')}"


def python_exe() -> str:
    for cand in (
        ROOT / "venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "bin" / "python",
    ):
        if cand.exists():
            return str(cand)
        return sys.executable


def alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid() -> int | None:
    try:
        return int(PID.read_text().strip())
    except Exception:
        return None


def http(method: str, path: str, headers: dict | None = None, timeout: float = 5):
    req = urllib.request.Request(
        base_url() + path,
        method=method,
        headers=headers or {},
        data=b"" if method == "POST" else None,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def cmd_init() -> None:
    if not ENV.exists():
        shutil.copy(ROOT / ".env.example", ENV)
        print("Created .env from .env.example")
        text = ENV.read_text(encoding="utf-8")
        changed = []
        for k in SECRETS:
            if not re.search(rf"^{k}=.+$", text, flags=re.M):
                text = re.sub(
                    rf"^{k}=.*$", f"{k}={secrets.token_hex(32)}", text, flags=re.M
                )
                changed.append(k)
                ENV.write_text(text, encoding="utf-8")
                print(
                    "Generated secrets: "
                    + (", ".join(changed) if changed else "none needed (already set)")
                )
                e = read_env()
                todo = [
                    k
                    for k in (
                        "ORACLE_ADB_USER",
                        "ORACLE_ADB_PASSWORD",
                        "ORACLE_ADB_DSN",
                        "ORACLE_ADB_WALLET_PASSWORD",
                        "OANDA_API_KEY",
                        "OANDA_ACCOUNT_ID",
                    )
                    if not e.get(k)
                ]
                if todo:
                    print("\nNow open .env and fill in:", ", ".join(todo))
                    (ROOT / "logs").mkdir(exist_ok=True)
                    (ROOT / "data").mkdir(exist_ok=True)


def run_module(module: str, *args: str) -> int:
    return subprocess.call([python_exe(), "-m", module, *args], cwd=ROOT)


def cmd_start() -> None:
    pid = read_pid()
    if pid and alive(pid):
        print(f"Already running (PID {pid}). UI: {base_url()}/ui/")
        return
    missing = [
        k
        for k in ("ORACLE_ADB_USER", "ORACLE_ADB_PASSWORD", "ORACLE_ADB_DSN", *SECRETS)
        if not read_env().get(k)
    ]
    if missing:
        sys.exit(
            f"Fill these in .env first: {', '.join(missing)}  (run: python engine_ctl.py init)"
        )
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "data").mkdir(exist_ok=True)
        out = open(CONSOLE, "ab")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (
                0x00000008 | 0x00000200
            )  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                [python_exe(), "-m", "app.run"],
                cwd=ROOT,
                stdout=out,
                stderr=out,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
            PID.write_text(str(proc.pid))
            print(f"Starting (PID {proc.pid}) ...", end="", flush=True)
            for _ in range(60):
                time.sleep(1)
                if proc.poll() is not None:
                    PID.unlink(missing_ok=True)
                    print(
                        "\nIt stopped right away. Last lines of logs/console.log and logs/error.log:\n"
                    )
                    for f in (CONSOLE, ROOT / "logs" / "error.log"):
                        if f.exists():
                            print(
                                "\n".join(
                                    f.read_text(errors="replace").splitlines()[-15:]
                                )
                            )
                            sys.exit(1)
                            try:
                                if http("GET", "/health/live", timeout=2)[0] == 200:
                                    print(
                                        f" up.\n\nAPI : {base_url()}\nUI  : {base_url()}/ui/\nDocs: {base_url()}/docs\nLogs: logs\\main.log (milestones), activity.log, error.log"
                                    )
                                    return
                            except Exception:
                                print(".", end="", flush=True)
                                print(
                                    "\nStill starting after 60 s. Check logs/main.log and logs/error.log."
                                )


def cmd_stop() -> None:
    pid = read_pid()
    if not pid or not alive(pid):
        PID.unlink(missing_ok=True)
        print("Not running.")
        return
    print(f"Stopping PID {pid} gracefully ...", end="", flush=True)
    try:
        http(
            "POST",
            "/v1/admin/shutdown",
            {"X-Admin-Key": read_env().get("ADMIN_API_KEY", "")},
            timeout=5,
        )
    except Exception:
        pass
    for _ in range(30):
        if not alive(pid):
            break
        time.sleep(1)
        print(".", end="", flush=True)
        if alive(pid):
            print("\nDid not stop in time; forcing.")
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True
                )
            else:
                os.kill(pid, 9)
                PID.unlink(missing_ok=True)
                print("\nStopped.")


def cmd_status() -> None:
    pid = read_pid()
    running = bool(pid and alive(pid))
    print(f"Process : {'running (PID %d)' % pid if running else 'not running'}")
    if not running:
        return
    try:
        code, body = http("GET", "/health/ready")
        print(f"Health  : {body}")
    except urllib.error.HTTPError as e:
        print(f"Health  : {e.read().decode()}")
    except Exception as e:
        print(f"Health  : no answer ({e})")
        print(f"UI      : {base_url()}/ui/")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init":
        cmd_init()
    elif cmd == "check-db":
        sys.exit(run_module("scripts.check_db"))
    elif cmd == "initdb":
        sys.exit(run_module("scripts.init_db"))
    elif cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "restart":
        cmd_stop()
        cmd_start()
    elif cmd == "status":
        cmd_status()
    elif cmd == "run":
        sys.exit(run_module("app.run"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
