"""rename_to_jeyi.py — 一次性改名脚本（memory-brain-agent → jeyi）

只改"项目名"，不动领域词：
  · `self.brain` / `.brain.`（Brain 实例属性，124 处）**保持**
  · `Brain` 类名保持
  · `topic/` 下的历史笔记保持（里面记录的是当时的项目名与外部案例，改了就失真）
  · `《鱼的记忆脑图》`（别人的项目）保持

用法：
    python tools/rename_to_jeyi.py --dry     # 只看会改什么
    python tools/rename_to_jeyi.py           # 真改
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "data", "dist", "__pycache__", "node_modules", "topic"}

# 顺序敏感：先长后短
LITERALS = [
    ("memory-brain-agent", "jeyi"),
    ("MEMORY_BRAIN_TIMEOUT", "JEYI_TIMEOUT"),
    ("MEMORY_BRAIN_TOKEN", "JEYI_TOKEN"),
    ("MEMORY_BRAIN_URL", "JEYI_URL"),
    ("MEMORY_BRAIN", "JEYI"),
    ("memory-brain", "jeyi"),
    ("tests/test_brain.py", "tests/test_jeyi.py"),
    ("test_brain.py", "test_jeyi.py"),
    ("brain_map_live.html", "map_live.html"),
    ("brain_map.html", "map.html"),
    ("brain_map.png", "map.png"),
    ("ui/brain_map_live.html", "ui/map_live.html"),
    ("ui/brain_map.html", "ui/map.html"),
]

# 模块路径：只改 import / -m 这类语法位置
REGEXES = [
    (re.compile(r"(^|\n)(\s*)from brain import\b"), r"\1\2from jeyi import"),
    (re.compile(r"(^|\n)(\s*)from brain\.(\w+) import\b"), r"\1\2from jeyi.\3 import"),
    (re.compile(r"(^|\n)(\s*)import brain\b"), r"\1\2import jeyi"),
    (re.compile(r"-m brain\.(\w+)"), r"-m jeyi.\1"),
    (re.compile(r"python -m brain\b"), "python -m jeyi"),
    (re.compile(r'"brain/(\w+)\.py"'), r'"jeyi/\1.py"'),
    (re.compile(r"'brain/(\w+)\.py'"), r"'jeyi/\1.py'"),
    (re.compile(r"brain/"), "jeyi/"),
]

# 只在这些位置上，把中文名换成 jeyi（其余"记忆脑图"是描述词，保留）
TITLE_FIXES = [
    ("# memory-brain-agent · v4.6 记忆脑图引擎", "# jeyi · 记忆脑图引擎"),
    ("<title>记忆脑图 · v4.1 全图底座</title>", "<title>jeyi · 记忆脑图</title>"),
    ("<h1>记忆脑图</h1>", "<h1>jeyi</h1>"),
    ("- 全称：memory-brain-agent 记忆脑图引擎", "- 全称：jeyi（记忆脑图引擎）"),
]

EXTS = {".py", ".md", ".toml", ".yml", ".yaml", ".js", ".mjs", ".html",
        ".json", ".txt", ".gitignore", ".gitattributes"}


def walk():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn == "rename_to_jeyi.py":
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in EXTS and fn not in (".gitignore", ".gitattributes", "LICENSE"):
                continue
            yield os.path.join(dirpath, fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    changed = {}
    for path in walk():
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        out = src
        for a_, b_ in LITERALS:
            out = out.replace(a_, b_)
        for rx, rep in REGEXES:
            out = rx.sub(rep, out)
        for a_, b_ in TITLE_FIXES:
            out = out.replace(a_, b_)
        if out != src:
            n = sum(1 for x, y in zip(src.splitlines(), out.splitlines()) if x != y)
            changed[os.path.relpath(path, ROOT)] = max(1, abs(len(out) - len(src)) + n)
            if not a.dry:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(out)
    for k in sorted(changed):
        print(f"  {k}")
    print(f"\n{'将修改' if a.dry else '已修改'} {len(changed)} 个文件")
    # 目录与文件重命名
    moves = [("brain", "jeyi"),
             ("tests/test_brain.py", "tests/test_jeyi.py"),
             ("ui/brain_map.html", "ui/map.html"),
             ("docs/brain_map.png", "docs/map.png")]
    for old, new in moves:
        o, n = os.path.join(ROOT, old), os.path.join(ROOT, new)
        if os.path.exists(o):
            print(f"  mv {old} -> {new}")
            if not a.dry:
                subprocess.run(["git", "mv", old, new], cwd=ROOT,
                               capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
