"""tools/setup_dense.py — 一键启用稠密检索（这是"准确率最高 + 最实惠"的落地方式）

它做四件事：
  1. 检查 numpy / onnxruntime / tokenizers（缺就告诉你要装什么，不自动装系统包）；
  2. 从 hf-mirror 下载 bge-small-zh-v1.5 的 int8 ONNX（约 24MB，CPU 跑）；
  3. 自检：拿几组中文句子算余弦，确认模型真的在做语义（而不是坏掉返回常数）；
  4. 打印磁盘占用与单句延迟，让你自己判断这笔账划不划算。

用法：
    python tools/setup_dense.py
    python tools/setup_dense.py --fp32      # 用 95MB 的 fp32 版本（精度略高一点）
"""
from __future__ import annotations
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def main():
    fp32 = "--fp32" in sys.argv
    from jeyi import dense

    print("① 依赖检查")
    if not dense.available():
        print("   ✗ 缺少 numpy / onnxruntime / tokenizers")
        print("     安装：pip install numpy onnxruntime tokenizers")
        return 1
    import numpy
    import onnxruntime
    import tokenizers
    print(f"   ✓ numpy {numpy.__version__} / onnxruntime {onnxruntime.__version__}"
          f" / tokenizers {tokenizers.__version__}")

    print("\n② 模型")
    t0 = time.time()
    try:
        path = dense.download(quantized=not fp32)
    except Exception as e:
        print(f"   ✗ 下载失败：{type(e).__name__} {e}")
        return 1
    size = sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path)
               if os.path.isfile(os.path.join(path, f)))
    print(f"   ✓ {path}")
    print(f"   ✓ {size / 1e6:.1f} MB，耗时 {time.time() - t0:.1f}s")

    print("\n③ 自检（语义相似度排序）")
    emb = dense.make_embedder(path)
    if emb is None:
        print("   ✗ 模型加载失败")
        return 1

    pairs = [
        ("我养了一只猫叫团子", "宠物", True),
        ("我养了一只猫叫团子", "房贷还款日是每月五号", False),
        ("我对花生过敏", "吃东西要看配料表", True),
        ("我对花生过敏", "下周有个设计评审", False),
        ("我平时喝什么饮料", "我每天喝一杯黑咖啡", True),
        ("我平时喝什么饮料", "我的工位在十二楼", False),
    ]
    ok = 0
    for a, b, expect in pairs:
        # 检索场景：查询与文档
        va, vb = emb.embed(a), emb.embed(b)
        sim = sum(x * y for x, y in zip(va, vb))
        flag = "✓" if (sim > 0.5) == expect else "✗"
        ok += flag == "✓"
        print(f"   {flag} cos={sim:.3f}  期望{'相关' if expect else '无关'}  {a[:12]} ↔ {b[:12]}")
    if ok < len(pairs):
        print("   ! 有不符合预期的组合——模型可能不是中文语义模型，或做的是 token embedding。")

    print("\n④ 代价")
    texts = ["我对花生过敏，吃了会起疹子"] * 32
    t = time.time()
    emb.embed_batch(texts)
    ms = (time.time() - t) * 1000 / len(texts)
    print(f"   单句推理 {ms:.1f} ms（CPU，批 32）")
    print(f"   1 万条记忆建索引约 {ms * 10000 / 1000:.0f}s（一次性），向量占盘约 20MB")

    print("\n⑤ 启用")
    print("   代码：from brain import dense; ag = MemoryHooks(embedding=dense.make_embedder())")
    print("   基准：python tools/bench.py --dense && python tools/bench.py --dense --holdout")
    return 0 if ok == len(pairs) else 1


if __name__ == "__main__":
    sys.exit(main())
