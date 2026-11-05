"""tools/ci_gate.py — 质量门禁：把 bench 的关键指标变成会失败的 CI 检查

用法：python tools/bench.py --json > bench.json && python tools/ci_gate.py bench.json
这是 v3 最缺的东西：测试只证明"机制在"，门禁证明"效果没退步"。
"""
from __future__ import annotations
import json
import sys

MIN = {
    "rank@5": 95.0,
    "pack_hit": 94.0,
    "abstain": 95.0,
    "cat_multihop": 90.0,   # 以 [命中率, 样本数] 形式返回
}
MAX = {
    "avg_chars": 60.0,      # 每轮注入字符上限（预算 300，实际应远低于它）
}


def _val(v):
    """分类指标是 [rate, n]，取 rate。"""
    return v[0] if isinstance(v, (list, tuple)) else v


def _load(path):
    """容忍编码差异：bash 重定向是 UTF-8，PowerShell 重定向默认是 UTF-16 LE。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("无法解析 bench.json（尝试 utf-8/utf-16/gbk 均失败）")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "bench.json"
    try:
        s = _load(path)
    except (OSError, ValueError) as e:
        print(f"[gate] 读不到基准结果（{e}），先跑 python tools/bench.py --json > bench.json")
        return 1
    v4 = s["v4"]
    bad = []
    for k, floor in MIN.items():
        got = _val(v4.get(k))
        if got is None:
            bad.append(f"{k} 缺失")
        elif got < floor:
            bad.append(f"{k}={got} < {floor}")
    for k, ceil in MAX.items():
        got = _val(v4.get(k))
        if got is not None and got > ceil:
            bad.append(f"{k}={got} > {ceil}")
    print("[gate] v4:", json.dumps(v4, ensure_ascii=False))
    print("[gate] v3 基线:", json.dumps(s["naive_v3"], ensure_ascii=False))
    if bad:
        print("[gate] 未通过：" + "；".join(bad))
        return 1
    print("[gate] 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
