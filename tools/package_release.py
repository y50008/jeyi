"""package_release.py — 打一个可直接分发的 zip

只打包"该进仓库的东西"：
  · 排除 .venv / data / __pycache__ / .git / dist / *.pyc / *.log / node_modules
  · 附 MANIFEST.txt（文件清单 + 大小 + 行数）与 SHA256
  · 产物在 dist/，同名覆盖

用法：
    python tools/package_release.py
    python tools/package_release.py --out D:\\release
"""
from __future__ import annotations
import argparse
import hashlib
import os
import sys
import time
import zipfile

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from jeyi.console import ensure_utf8

ensure_utf8()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "venv", "data", "dist", "__pycache__", "node_modules",
             ".idea", ".vscode", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm", ".part"}
KEEP_EXT = {".py", ".js", ".mjs", ".md", ".html", ".toml", ".yml", ".yaml",
            ".txt", ".json", ".png", ".gitignore", ".gitattributes"}
KEEP_NAMES = {"LICENSE", "Dockerfile", ".gitignore", ".gitattributes"}


def version() -> str:
    try:
        sys.path.insert(0, ROOT)
        from jeyi import __version__
        return __version__
    except Exception:
        return "0.0.0"


def collect() -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(tuple(SKIP_EXT)):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in KEEP_EXT and fn not in KEEP_NAMES:
                continue
            full = os.path.join(dirpath, fn)
            out.append(os.path.relpath(full, ROOT))
    return out


def count_lines(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ver = version()
    name = f"jeyi-v{ver}"
    zip_path = os.path.join(a.out, name + ".zip")

    files = collect()
    manifest = [f"# {name}  ({time.strftime('%Y-%m-%d %H:%M')})", ""]
    total_bytes = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            full = os.path.join(ROOT, rel)
            size = os.path.getsize(full)
            total_bytes += size
            z.write(full, os.path.join(name, rel))
            manifest.append(f"{size:>9}  {count_lines(full):>6} 行  {rel}")
        z.writestr(os.path.join(name, "MANIFEST.txt"), "\n".join(manifest) + "\n")

    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    print(f"已打包 {len(files)} 个文件（源 {total_bytes/1024:.0f} KB）")
    print(f"产物   {zip_path}  {os.path.getsize(zip_path)/1024:.0f} KB")
    print(f"SHA256 {h.hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
