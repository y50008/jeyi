"""serve_daemon.py — 侧车守护进程（开机自启跑的就是它）

`brain.serve` 只是一个 HTTP 服务：进程一死，大肥鱼就退回原实现（不会崩，但也没了增强）。
这个守护进程负责三件事：
  1. **单实例**：端口已被占用就不再起第二个；
  2. **挂了自动重启**：子进程退出 → 3 秒后重来；连续快速崩溃则指数退避，不刷屏；
  3. **日志滚动**：写到 ~/.jeyi/logs/sidecar.log，超过 5MB 轮转一次。

配置（~/.jeyi/config.json，由 tools/install_autostart.py 生成）：
    {"port": 8765, "store": "...", "dense": true, "budget": 300, "token": ""}

手动跑：
    python tools/serve_daemon.py            # 前台
    python tools/serve_daemon.py --once     # 只跑一次子进程，不重启（调试用）
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from jeyi.console import ensure_utf8

ensure_utf8()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(os.path.expanduser("~"), ".jeyi")
CONFIG = os.path.join(HOME, "config.json")
LOGS = os.path.join(HOME, "logs")
LOG_MAX = 5 * 1024 * 1024
DEFAULTS = {"port": 8765, "store": os.path.join(ROOT, "data", "peers"),
            "dense": True, "budget": 300, "token": ""}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def rotate(path: str):
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + ".1")
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()

    cfg = load_config()
    port = a.port or int(cfg["port"])
    os.makedirs(LOGS, exist_ok=True)
    log_path = os.path.join(LOGS, "sidecar.log")

    if port_open(port):
        print(f"[daemon] 端口 {port} 已在服务，不再启动第二个", flush=True)
        return 0

    py = sys.executable
    cmd = [py, "-m", "jeyi.serve", "--port", str(port),
           "--store", cfg["store"], "--budget", str(cfg.get("budget", 300))]
    if cfg.get("dense"):
        cmd.append("--dense")
    if cfg.get("token"):
        cmd += ["--token", cfg["token"]]

    child = None
    stopping = {"flag": False}

    def stop(*_a):
        stopping["flag"] = True
        if child and child.poll() is None:
            try:
                child.terminate()
            except OSError:
                pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop)
        except (AttributeError, ValueError, OSError):
            pass

    backoff, started = 2.0, time.time()
    while not stopping["flag"]:
        rotate(log_path)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 {port} ===\n")
            log.write(" ".join(cmd) + "\n")
            log.flush()
            child = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            rc = child.wait()
        except KeyboardInterrupt:
            stop()
            rc = 0
        if a.once or stopping["flag"]:
            return rc
        # 跑够 30 秒才算"稳定"，否则指数退避
        if time.time() - started > 30:
            backoff, started = 2.0, time.time()
        else:
            backoff = min(60.0, backoff * 1.7)
        print(f"[daemon] 子进程退出 rc={rc}，{backoff:.0f}s 后重启", flush=True)
        time.sleep(backoff)
        started = time.time()
    return 0


if __name__ == "__main__":
    sys.exit(main())
