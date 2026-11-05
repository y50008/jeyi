"""demo.py — v4.0 端到端演示：写入(三通道) → 建图 → 检索(含多跳/弃答) → 召回 → 改口 → 结算 → 指标

跑了就知道哪里变了：
  · 每轮注入有硬上限，且会话内不重复注入（省 token 的结构性保证）
  · 单关键词能召回（v3 会因为分数饱和被 0.20 地板切掉）
  · 多跳能召回（问题里没有"海鲜"，靠图扩散过去）
  · 越界问题弃答（记忆里没有就不硬凑）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from jeyi import MemoryHooks


def show(t):
    print("\n" + "═" * 3 + " " + t + " " + "═" * 3)


ag = MemoryHooks()

show("① 写入：三通道 + 问题过滤（疑问/假设/玩笑不入卡）")
for line in ("我养了一只猫叫团子，它最近老掉毛。",
             "团子对海鲜过敏，不能乱喂。",
             "我对花生过敏，吃了会起疹子。",
             "我下周三要去杭州出差。",
             "我喜欢吃什么？",                 # 问句，应被跳过
             "如果我明天中彩票呢？",           # 假设，应被跳过
             "我喜欢喝铁观音，只喝浓香型。"):
    ids = ag.on_message(line)
    print(f"  {line:<28} → 入卡 {len(ids)} 条")

n = next(x for x in ag.brain.nodes.values() if "团子" in x.gist)
print("\n  三通道抽样：")
print("    gist     :", n.gist)
print("    verbatim :", n.verbatim)
print("    feeling  :", n.feeling)
print("    entities :", n.entities)

show("② 图：边真的建出来了")
edges = sum(len(v) for v in ag.brain.adj.values()) // 2
kinds = sorted({et for v in ag.brain.adj.values() for _n, et, _w in v})
print(f"  节点 {len(ag.brain.nodes)} / 边 {edges} / 类型 {kinds}")

show("③ 检索：单关键词 / 多跳 / 越界弃答")
for q, note in (("团子", "单关键词（v3 会被地板切掉）"),
                ("我家猫身体有什么问题", "多跳：问题里没有'海鲜'"),
                ("我护照什么时候到期", "越界：记忆里没有，应弃答")):
    # 每个问题当作一次独立对话（否则会话级去重会把上一轮已注入的记忆压掉——这是特性，不是 bug）
    ag.session_seen.clear()
    ag.brain.turn = 0
    r = ag.before_reply(q)
    print(f"\n  问：{q}   〔{note}〕")
    print("  注入：" + (r["pack"].replace("\n", "\n        ") or "(空 —— 绝不虚构)"))
    if r["ranked"]:
        top = r["ranked"][0]
        print(f"  分解：{top['decomp']}")

show("④ [[recall: 词]] 主动召回协议（v3 只返回 id，没有人用）")
ag.on_session_end()                     # 换一个会话，清空"已注入"去重表
ids = ag.handle_turn("让我想想… [[recall: 团子]]")
r = ag.before_reply("今天天气怎么样")
print("  上一轮点名召回：", ids)
print("  下一轮注入：" + r["pack"].replace("\n", "\n              "))
print("  → 召回的 id 确实出现在注入包里：", bool(set(ids) & set(r["cites"])))

show("⑤ 改口：唯一匹配 + 事件溯源（重启不复活）")
import tempfile
d = tempfile.mkdtemp()                      # 用临时目录，保证 demo 可重复运行
ag2 = MemoryHooks(store_dir=d)
n_old = ag2.brain.add_node(label="用户对花生不过敏", gist="用户对花生不过敏",
                           origin="user", about_person=True)
new, msg = ag2.brain.correct("用户对花生不过敏", "用户对花生过敏，会起疹子")
print("  改口：", msg, "→ 新卡", new.id if new else None)
ag2.on_session_end()
ag3 = MemoryHooks(store_dir=d)
old = [x for x in ag3.brain.nodes.values() if x.id == n_old.id]
print(f"  重启后旧卡状态：{old[0].status if old else '丢失'}"
      f"（v3 会复活成 live，导致两条冲突记忆同时可注入）")

show("⑥ 五步结算")
for line in ag.on_session_end():
    print("  " + line)

show("⑦ 会话作用域与跨会话同一性")
ag4 = MemoryHooks(session="S1")
ag4.remember("先做登录页，下周再排期", scope="session")   # 会话私有
ag4.on_message("我对花生过敏")                            # 跨会话长期
ag4.set_session("S2")                                     # 换会话
r4 = ag4.before_reply("登录页 排期 花生 过敏")
print("  S2 注入：" + (r4["pack"].replace("\n", " / ") or "(空)"))
print("  → 花生（长期）在，登录页（S1 私有）不在：",
      ("花生" in r4["pack"]) and ("登录页" not in r4["pack"]))
ag4.on_message("我对花生过敏")                            # 同一事实，第二个会话又说一次
st4 = ag4.stats()
print(f"  跨会话同一性：nodes={st4['nodes']}（同一事实仍是 1 张卡） "
      f"cross_session={st4['cross_session']}（在 ≥2 个会话被确认过）")

show("⑧ 指标（全部是实测值，不再有 ''working*120 + active*45'' 这种拍出来的数）")
for k, v in ag.stats().items():
    print(f"  {k}: {v}")

print("\n提示：python tools/bench.py 看检索质量基准；python tools/export_map.py 导出脑图。")
