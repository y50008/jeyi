"""tools/verify_fish_bridge.py — 一键复现「大肥鱼 × jeyi」的对接验证

它做的事：
  1. 找到（或接收）一个已安装/已下载的 dsh-identity 包；
  2. 在临时目录里搭一个沙箱：把它的 lib/{memory,store,config,moodv2,diaglog}.js
     换成 memory.orig.js 等，再把我们的 memory.bridge.js 装成 memory.js；
  3. 起一个本地 sidecar（默认纯词法；--dense 则带稠密）；
  4. 用沙箱里的真实 memory.js 跑 integration/dsh-identity/verify.mjs 做对照。

为什么值得跑：这不是模拟接口，跑的是大肥鱼**自己的模块和存储**，
只有"哪几条进上下文"这一层被换掉。

用法：
    python tools/verify_fish_bridge.py                       # 自动找已安装的包
    python tools/verify_fish_bridge.py --fish D:/dsh-identity
    python tools/verify_fish_bridge.py --dense               # 带稠密（需先 setup_dense）
    python tools/verify_fish_bridge.py --keep                # 保留沙箱便于排查
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from jeyi.console import ensure_utf8

ensure_utf8()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTEG = os.path.join(ROOT, "integration", "dsh-identity")

# 大肥鱼 memory.js 的依赖链（导入这几个就能独立跑起来）
NEED = ["memory", "store", "config", "moodv2", "diaglog"]


def find_fish(explicit: str | None) -> str:
    cands = []
    if explicit:
        cands.append(explicit)
    home = os.path.expanduser("~")
    for base in (os.path.join(home, ".dsh", "profiles"), os.path.join(home, ".dsh"),
                 os.path.join(home, "AppData", "Roaming", "npm", "node_modules")):
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, _files in os.walk(base):
            if os.path.basename(dirpath) == "dsh-identity":
                cands.append(dirpath)
            if dirpath.count(os.sep) - base.count(os.sep) > 6:
                dirnames[:] = []
    for c in cands:
        if os.path.exists(os.path.join(c, "lib", "memory.js")):
            return os.path.abspath(c)
    raise SystemExit(
        "没找到 dsh-identity。请用 --fish 指向它的包目录（含 lib/memory.js），或先安装该插件。\n"
        "临时获取：git clone https://github.com/Anarrogantcat/dsh-identity")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_health(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=1) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def find_node() -> str:
    for c in (shutil.which("node"),
              os.path.join(os.path.expanduser("~"), ".dsh", "dsh-runtimes"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                           "DeepSeek Harness", "resources", "runtime")):
        if not c:
            continue
        if os.path.isfile(c):
            return c
        for dirpath, _d, files in os.walk(c):
            if "node.exe" in files or "node" in files:
                p = os.path.join(dirpath, "node.exe" if "node.exe" in files else "node")
                return p
    raise SystemExit("找不到 Node（>=20）。请把 node 加进 PATH。")


def build_sandbox(fish: str, work: str) -> str:
    lib = os.path.join(work, "lib")
    os.makedirs(lib, exist_ok=True)
    name_map = {"memory": "memory.orig.js", "store": "store.js",
                "config": "config.js", "moodv2": "moodv2.js", "diaglog": "diaglog.js"}
    for stem, dst in name_map.items():
        src = os.path.join(fish, "lib", f"{stem}.js")
        if not os.path.exists(src):
            raise SystemExit(f"缺少 {src}（大肥鱼版本可能变了）")
        shutil.copy2(src, os.path.join(lib, dst))
    shutil.copy2(os.path.join(INTEG, "memory.bridge.js"), os.path.join(lib, "memory.js"))
    shutil.copy2(os.path.join(INTEG, "memory.fetch-worker.mjs"), lib)
    shutil.copy2(os.path.join(INTEG, "verify.mjs"), work)
    return work


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fish", default=None, help="dsh-identity 包目录")
    ap.add_argument("--dense", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    fish = find_fish(a.fish)
    node = find_node()
    print(f"大肥鱼: {fish}")
    print(f"node:   {node}")

    work = tempfile.mkdtemp(prefix="fishbridge-")
    build_sandbox(fish, work)
    port = free_port()
    env = dict(os.environ)
    env["DSH_HOME"] = os.path.join(work, "dshhome")
    env["JEYI_URL"] = f"http://127.0.0.1:{port}"
    os.makedirs(env["DSH_HOME"], exist_ok=True)

    py = sys.executable
    cmd = [py, "-m", "jeyi.serve", "--port", str(port),
           "--store", os.path.join(work, "peers")]
    if a.dense:
        cmd.append("--dense")
    side = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        if not wait_health(port):
            out = side.stdout.read().decode("utf-8", "replace") if side.stdout else ""
            raise SystemExit(f"sidecar 没起来：\n{out}")
        print(f"sidecar: 127.0.0.1:{port}（{'稠密' if a.dense else '纯词法'}）\n")
        rc = subprocess.call([node, "verify.mjs"], cwd=work, env=env)
        return rc
    finally:
        side.terminate()
        try:
            side.wait(timeout=5)
        except subprocess.TimeoutExpired:
            side.kill()
        if a.keep:
            print(f"\n沙箱保留在：{work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
