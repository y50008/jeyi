"""tools/fetch_bench.py — 下载公开记忆基准（放到 ~/.cache/jeyi/bench/）

LoCoMo（2.8MB，推荐先跑）：10 段长对话 / ~2000 条 QA / 多跳·时序·对抗
LongMemEval-S（278MB）：500 问，每问带一整套干扰会话，检索难度最高

国内可用性：GitHub raw 直连不通，走 ghproxy；HuggingFace 走 hf-mirror。

用法：
    python tools/fetch_bench.py --dataset locomo
    python tools/fetch_bench.py --dataset longmemeval
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import urllib.request

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from jeyi.console import ensure_utf8

ensure_utf8()

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "jeyi", "bench")
UA = {"User-Agent": "Mozilla/5.0 jeyi/bench"}

SOURCES = {
    "locomo": [
        "https://ghproxy.net/https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
        "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
    ],
    "longmemeval": [
        "https://hf-mirror.com/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s",
        "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s",
    ],
}
TARGET = {"locomo": "locomo10.json", "longmemeval": "longmemeval_s.json"}
MIN_SIZE = {"locomo": 1_000_000, "longmemeval": 100_000_000}


def fetch(kind: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, TARGET[kind])
    if os.path.exists(out) and os.path.getsize(out) > MIN_SIZE[kind]:
        print(f"已存在：{out}（{os.path.getsize(out)/1e6:.1f} MB）")
        return out
    last = None
    for url in SOURCES[kind]:
        try:
            print(f"下载 {url}")
            req = urllib.request.Request(url, headers=UA)
            tmp = out + ".part"
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total and done % (32 << 20) < (1 << 20):
                        print(f"  {done*100//total}%  {done/1e6:.0f}MB  "
                              f"{done/1e6/max(0.1,time.time()-t0):.2f}MB/s", flush=True)
            if os.path.getsize(tmp) < MIN_SIZE[kind]:
                raise OSError(f"文件太小：{os.path.getsize(tmp)}")
            os.replace(tmp, out)
            print(f"完成：{out}（{os.path.getsize(out)/1e6:.1f} MB）")
            return out
        except Exception as e:                      # 换下一条源
            last = e
            print(f"  失败：{type(e).__name__} {e}")
    raise SystemExit(f"下载失败：{last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["locomo", "longmemeval"], required=True)
    args = ap.parse_args()
    fetch(args.dataset)


if __name__ == "__main__":
    main()
