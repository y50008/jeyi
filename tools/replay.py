"""tools/replay.py — 多轮会话回放：看注入包怎么随话题变、以及会话去重省了多少字

与 demo.py 的分工：demo 展示单点能力，replay 展示"连续多轮"下的行为——
这才是 v3 最难看的地方（每轮重复注入同一批记忆，10 轮只出现 3 条、重复 30 次）。

用法：python tools/replay.py [--sqlite]
"""
from __future__ import annotations
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from jeyi import MemoryHooks, SqliteStorage, HashingEmbedding      # noqa: E402

from jeyi.console import ensure_utf8

ensure_utf8()

# 一段真实感的连续对话（本项目自身的历史 + 几条个人事实）
TURNS = [
    ("细节在末梢，向上一次轮廓，再向上是事件。没有真正的遗忘，也没有不遗忘。", "encode"),
    ("我们的意义就是发现现成的，超越现成的。", "encode"),
    ("我养了一只猫叫团子，它最近老掉毛。", "encode"),
    ("我对花生过敏，吃东西要看配料表。", "encode"),
    ("我下周三要去杭州出差。", "encode"),
    ("我喜欢喝铁观音，只喝浓香型。", "encode"),
    ("我最近在做一个记忆脑图引擎。", "encode"),
    ("我感觉扩散检索那块还是不稳。", "feedback"),
    ("我准备在年底前把项目开源。", "encode"),
    ("团子今天又吐毛球了，有点担心。", "encode"),
    ("我是开玩笑的吗？如果明天再聊呢？哈哈", "skip-check"),
    ("我喜欢喝铁观音，不假。", "dedup-check"),
    ("帮我看看检索这块怎么改？", "query"),
    ("我家猫身体有什么问题？", "query"),
    ("我护照什么时候到期？", "query"),
]


def main():
    use_sqlite = "--sqlite" in sys.argv
    storage = SqliteStorage(":memory:") if use_sqlite else None
    ag = MemoryHooks(storage=storage, embedding=HashingEmbedding())

    print(f"存储后端：{'sqlite' if use_sqlite else '内存'}")
    print(f"{'轮':<4}{'类型':<12}{'入卡':<5}{'注入字':<7}{'注入包'}")
    print("-" * 96)
    chars, full, seen_hist = 0, 0, {}
    for i, (text, tag) in enumerate(TURNS, 1):
        ids = ag.on_message(text)
        r = ag.before_reply(text)
        pack = r["pack"].replace("\n", " / ") or "(空)"
        chars += r["chars"]
        full += len(text)
        print(f"T{i:02d}  {tag:<12}{len(ids):<5}{r['chars']:<7}{pack[:58]}")
        for nid in r["cites"]:
            seen_hist[nid] = seen_hist.get(nid, 0) + 1

    print("\n结算：")
    for line in ag.on_session_end():
        print("  " + line)

    st = ag.stats()
    n_turns = len(TURNS)
    full_replay = st["memory_chars"] * n_turns      # 朴素做法：每轮把全部记忆重放一遍
    print("\n指标：")
    print(f"  节点 {st['nodes']} / 边 {st['edges']} / 活跃 {st['slices'].get('active', 0)}"
          f" / 归档 {st['slices'].get('archive', 0)}")
    print(f"  记忆总量 {st['memory_chars']} 字；若每轮整段重放 = {full_replay} 字")
    print(f"  实际累计注入 {chars} 字 → 是全量重放的 {chars / max(1, full_replay):.1%}"
          f"（平均每轮 {st['avg_inject_chars']} 字，压缩比 {st['compression']}x）")
    dup = {k: v for k, v in seen_hist.items() if v > 1}
    print(f"  同一记忆被重复注入：{len(dup)} 条"
          f"（v3 是每轮无脑重复，10 轮只出现 3 条、重复 30 次）")
    if dup:
        print("    " + "，".join(ag.brain.nodes[k].label[:12] for k in dup))

    # 召回协议演示
    ag.on_session_end()                    # 换会话，重置"已注入"去重表
    ids = ag.handle_turn("让我想想… [[recall: 团子]]")
    r = ag.before_reply("最近怎么样")
    print(f"\n召回协议：点名 {ids} → 下一轮注入 {r['cites']}")
    print("\n注：T13–T15 返回空是『会话级去重』在起作用——那些记忆本会话已经注入过，"
          "\n    模型上下文里还在，不再重复烧 token（这是 v4 相对 v3 最主要的省法）。")
    print("\n提示：python tools/bench.py 看质量基准；python tools/export_map.py 导出脑图。")


if __name__ == "__main__":
    main()
