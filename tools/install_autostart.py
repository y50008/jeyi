"""install_autostart.py — 一键把记忆侧车设成开机自启（Windows 登录启动）

做三件事：
  1. 写配置 `~/.jeyi/config.json`（端口、库路径、是否开稠密）；
  2. 在「启动」文件夹放一个**不弹窗**的 `jeyi.vbs`，登录即静默启动守护进程；
  3. 立刻后台拉起来并自检 `/v1/health`。

为什么用 VBS 而不是快捷方式：快捷方式会闪一个黑框；VBS 用
`WScript.Shell.Run(..., 0, False)` 以隐藏窗口启动，用户完全无感。
（任务计划程序也能做，但要管理员 + 更繁琐，这里优先"普通用户双击就能装"。）

用法：
    python tools/install_autostart.py --install
    python tools/install_autostart.py --status
    python tools/install_autostart.py --uninstall
    python tools/install_autostart.py --install --port 8765 --no-dense
    python tools/install_autostart.py --install --store D:\\mem\\peers
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from jeyi.console import ensure_utf8

ensure_utf8()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(os.path.expanduser("~"), ".jeyi")
CONFIG = os.path.join(HOME, "config.json")
DAEMON = os.path.join(ROOT, "tools", "serve_daemon.py")
DEFAULTS = {"port": 8765, "store": os.path.join(ROOT, "data", "peers"),
            "dense": True, "budget": 300, "token": ""}


def startup_dir() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")


def vbs_path() -> str:
    return os.path.join(startup_dir(), "jeyi.vbs")


def python_exe() -> str:
    """优先用项目 venv 里的 python（稠密依赖装在那边）。"""
    for c in (os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe"),
              os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
              os.path.join(ROOT, ".venv", "bin", "python"),
              sys.executable):
        if os.path.exists(c):
            return c
    return sys.executable


def write_config(cfg: dict):
    os.makedirs(HOME, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def read_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def write_vbs():
    os.makedirs(startup_dir(), exist_ok=True)
    py = python_exe()
    # VBScript 字符串里嵌入引号要写成两个引号：sh.Run """C:\py.exe"" ""x.py"""
    # （旧实现少写了外层引号，WScript 会报"语句未结束 800A0401"——v5.0 实测过。）
    cmd = f'"""{py}"" ""{DAEMON}"""'
    content = (
        "' jeyi 记忆侧车：登录时静默启动（删掉本文件即可取消自启）\r\n"
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{ROOT}"\r\n'
        f'sh.Run {cmd}, 0, False\r\n'
    )
    # Windows Script Host 只认 ANSI 或 UTF-16LE(BOM)。项目路径含中文时，
    # UTF-8 无 BOM 会被按 GBK 解码成乱码，自启静默失败（v5.0 实测过）。
    with open(vbs_path(), "w", encoding="utf-16", newline="") as f:
        f.write(content)


def health(port: int, timeout=2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def start_now(cfg: dict):
    """立刻拉起来（不等下次登录）。"""
    if health(cfg["port"]):
        return True
    py = python_exe()
    exe = py.replace("pythonw.exe", "python.exe") if py.endswith("pythonw.exe") else py
    creation = 0x00000008 if os.name == "nt" else 0          # DETACHED_PROCESS
    subprocess.Popen([exe, DAEMON], cwd=ROOT,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=creation)
    for _ in range(40):
        time.sleep(0.4)
        if health(cfg["port"]):
            return True
    return False


def cmd_install(args):
    cfg = read_config()
    port = args.port or int(cfg["port"])
    cfg.update({"port": port,
                "store": args.store or cfg["store"],
                "dense": (not args.no_dense),
                "budget": args.budget or int(cfg.get("budget", 300))})
    write_config(cfg)
    write_vbs()
    ok = start_now(cfg)
    h = health(port) or {}
    print(f"配置文件   {CONFIG}")
    print(f"开机自启   {vbs_path()}  （删除该文件即可取消）")
    print(f"当前状态   {'已运行' if ok else '未起来（看 logs）'}  port={port} "
          f"{'v' + str(h.get('version')) if h else ''}")
    print(f"面板       http://127.0.0.1:{port}/")
    if not ok:
        print(f"排查：{os.path.join(HOME, 'logs', 'sidecar.log')}")
        return 1
    return 0


def cmd_uninstall(_args):
    p = vbs_path()
    if os.path.exists(p):
        os.remove(p)
        print(f"已移除自启项 {p}")
    else:
        print("自启项本来就不存在")
    print("（守护进程可能仍在跑；要立刻停掉可执行：")
    print("  Get-CimInstance Win32_Process | Where-Object CommandLine -match 'serve_daemon' "
          "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }）")
    return 0


def cmd_status(_args):
    cfg = read_config()
    h = health(cfg["port"])
    print(f"配置     {CONFIG}")
    print(f"  port={cfg['port']}  dense={cfg['dense']}  store={cfg['store']}")
    print(f"自启项   {'已安装' if os.path.exists(vbs_path()) else '未安装'}  {vbs_path()}")
    print(f"服务     {'运行中 ' + json.dumps(h, ensure_ascii=False) if h else '未运行'}")
    print(f"面板     http://127.0.0.1:{cfg['port']}/")
    return 0 if h else 1


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--store", default=None)
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--no-dense", action="store_true")
    a = ap.parse_args()
    if os.name != "nt" and a.install:
        print("这个脚本目前只处理 Windows 的登录自启。")
        print("Linux/macOS 请把这条命令加进 systemd / launchd：")
        print(f"  {sys.executable} {DAEMON}")
        return 2
    if a.install:
        return cmd_install(a)
    if a.uninstall:
        return cmd_uninstall(a)
    return cmd_status(a)


if __name__ == "__main__":
    sys.exit(main())
