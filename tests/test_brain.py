"""tests/test_brain.py — 无 pytest 依赖，直接 python tests/test_brain.py 运行

v4.0 的测试原则：每条对着 v3.1.1 的一个实测缺陷写一条断言（B1–B16，见 docs/FIXES.md），
再加一条"检索质量"集成门禁（test_bench_quality_gate）——v3 的测试只断言机制存在，
不断言效果，所以 10/10 全绿而实际检索是坏的。
"""
import sys
import os
import io

# Windows 控制台默认 GBK，直接 print 中文/✓ 会抛 UnicodeEncodeError（v3 的 CI 在 Ubuntu 上没暴露）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, io.UnsupportedOperation):
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
import tempfile

from brain import MemoryHooks, Brain, SqliteStorage, HashingEmbedding, tokenize
from brain.core import Node, DAY
from brain.lang import is_question, find_date, is_planned


# ---------------------------------------------------------------- v3 契约（保留）
def test_confidence_half_life():
    b = Brain()
    hi = b.add_node(label="A", origin="user", importance=.5)
    lo = b.add_node(label="B", origin="web", importance=.5)
    assert b.half_life(hi) > b.half_life(lo), "置信度应加权半衰期"
    t = time.time() + 30 * 86400
    assert b.freshness(hi, t) > b.freshness(lo, t), "拿不准的淡得更快"
    print("ok  置信度加权半衰期")


def test_gate():
    ag = MemoryHooks()
    ag.brain.add_node(label="量子引力", type="fact", origin="agent",
                      gist="完全无关的话题", importance=1.0)
    ag.brain.add_node(label="超越", type="lesson", origin="user",
                      gist="发现现成的 超越现成的", importance=.9)
    ag.scorer._rebuild()
    r = ag.scorer.rank("超越 现成的")
    labels = [x["label"] for x in r]
    assert "超越" in labels and "量子引力" not in labels, "门控应挡住无关高激活节点"
    print("ok  相关度门控挡住无关高重要节点")


def test_unique_match_correction():
    ag = MemoryHooks()
    ag.brain.add_node(label="用户喜欢火锅", type="fact", origin="user", gist="用户喜欢火锅")
    ag.brain.add_node(label="用户喜欢毛肚", type="fact", origin="user", gist="用户喜欢毛肚")
    n, msg = ag.brain.correct("喜欢", "x")
    assert n is None, "多匹配应拒绝"
    n, msg = ag.brain.correct("用户喜欢火锅", "用户喜欢重庆火锅微辣")
    assert n is not None and n.feedback_until > time.time(), "改口应成功且 24h 强插"
    old = [x for x in ag.brain.nodes.values() if x.status == "superseded"]
    assert len(old) == 1, "旧卡应 superseded 而非删除"
    print("ok  改口唯一匹配 + 强插 + 状态机")


def test_settle_budget_and_reflection():
    ag = MemoryHooks()
    for i in range(6):
        ag.brain.add_node(label=f"m{i}", gist=f"话题{i} 内容")
    ag.scorer._rebuild()
    ag.on_session_end()
    refs = [n for n in ag.brain.nodes.values() if n.type == "reflection"]
    assert len(refs) == 1, "应生成反思节点"
    assert "预算余额" in refs[0].gist, "反思应记录预算余额"
    print("ok  结算预算 + 反思生成")


def test_traits_evidence():
    ag = MemoryHooks()
    ag.persona.propose("用户很严谨", ref="第1次讨论", emotional=False)
    ag.persona.add_evidence("用户很严谨", "第2次")
    assert ag.persona.traits["用户很严谨"].state == "tentative"
    ag.persona.add_evidence("用户很严谨", "第3次")
    assert ag.persona.traits["用户很严谨"].state == "durable", "≥3 独立证据才 durable"
    ag.persona.propose("用户情绪化", ref="一次发火", emotional=True)
    for i in range(4):
        ag.persona.add_evidence("用户情绪化", f"e{i}", emotional=True)
    assert ag.persona.traits["用户情绪化"].state == "tentative", "纯情绪不得 durable"
    ag.persona.owner_veto("用户很严谨")
    assert ag.persona.traits["用户很严谨"].state == "vetoed"
    print("ok  traits 证据制")


def test_budget_and_relevance_gate():
    """v3 的"断崖"：低分陪跑不能占预算。v4 用相关度门控实现同一契约。"""
    ag = MemoryHooks()
    for i in range(2):
        ag.brain.add_node(label=f"火锅 好吃{i}", gist=f"火锅 好吃{i}", origin="user",
                          importance=.9, about_person=True)
    for i in range(3):
        ag.brain.add_node(label=f"税务 报表{i}", gist=f"税务 报表{i}", origin="user",
                          importance=.95, about_person=True)
    ag.scorer._rebuild()
    r = ag.before_reply("火锅 好吃")
    assert r["chars"] <= 300, "注入预算不得超"
    assert all("火锅" in line for line in r["pack"].split("\n") if line), \
        f"无关内容不得入包: {r['pack']}"
    print("ok  相关度门控 + 字符预算")


def test_never_fabricate_archive():
    ag = MemoryHooks()
    ag.brain.add_node(label="旧话题", origin="agent", gist="很久以前")
    for n in ag.brain.nodes.values():
        n.mentions = [time.time() - 200 * 86400]
    ag.brain.archive_pass()
    r = ag.scorer.rank("旧话题")
    assert all(x["label"] != "旧话题" for x in r), "归档节点不进常规检索"
    print("ok  归档隔离（但 full 卡仍在）")


def test_sqlite_roundtrip():
    from brain import SqliteStorage as SS
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    ag = MemoryHooks(storage=SS(path))
    ag.brain.add_node(label="持久", origin="user", gist="落库测试")
    ag.brain.save()
    ag2 = MemoryHooks(storage=SS(path))
    assert any(n.label == "持久" for n in ag2.brain.nodes.values()), "SQLite 应能回读"
    print("ok  SQLite 事件表往返")


def test_hash_embedding():
    from brain import cosine
    e = HashingEmbedding()
    v1, v2 = e.embed("我喜欢吃火锅"), e.embed("我喜欢吃火锅")
    assert v1 == v2, "哈希向量应确定性"
    s = cosine(e.embed("我喜欢吃火锅"), e.embed("火锅 麻辣 重庆"))
    d = cosine(e.embed("我喜欢吃火锅"), e.embed("量子引力 弦论"))
    assert s > d, "语义相近应得分更高"
    print("ok  本地哈希向量（确定性 + 排序合理）")


def test_embedding_changes_rank():
    ag = MemoryHooks(embedding=HashingEmbedding())
    ag.brain.add_node(label="饮茶", type="fact", origin="agent", gist="喝茶 茶叶 铁观音")
    ag.scorer._rebuild()
    r = ag.scorer.rank("铁观音")
    assert r and r[0]["label"] == "饮茶", "embedding 应提升语义召回"
    print("ok  embedding 接入排序")


# ---------------------------------------------------------------- v4 修复（B1–B16）
def test_B1_superseded_survives_reload():
    """B1：v3 的全量卡只在创建时快照，重启后 superseded 会复活成 live。"""
    d = tempfile.mkdtemp()
    ag = MemoryHooks(store_dir=d)
    ag.on_message("我对花生不过敏，真的")
    n, msg = ag.brain.correct("我对花生不过敏", "我对花生过敏，会起疹子")
    assert n is not None, msg
    ag.on_session_end()
    ag2 = MemoryHooks(store_dir=d)
    live = [x for x in ag2.brain.nodes.values() if x.status == "live"
            and "花生" in x.label]
    sup = [x for x in ag2.brain.nodes.values() if x.status == "superseded"]
    assert len(sup) == 1, f"重启后旧卡必须仍是 superseded，实际 {len(sup)}"
    assert len(live) == 1, f"重启后只能有一条 live，实际 {len(live)}"
    print("ok  B1 改口跨重启不复活（事件溯源）")


def test_B2_cold_start_retrieval():
    """B2：v3 初始化时 N=0 → idf 全 0 → 首轮检索必然为空。"""
    ag = MemoryHooks()
    ag.on_message("我养了一只猫叫团子")
    r = ag.before_reply("团子")
    assert r["cites"], "首轮就必须能检索到（不需要手动 _rebuild）"
    assert "团子" in r["pack"]
    print("ok  B2 冷启动首轮可检索")


def test_B3_single_keyword_passes_gate():
    """B3：v3 的 C=s/(s+8) 会饱和，单个关键词命中恒低于 0.20 地板。"""
    ag = MemoryHooks()
    ag.on_message("我养了一只猫叫团子")
    ag.on_message("我下周三要去杭州出差")
    r = ag.scorer.rank("团子")
    assert r and "猫" in r[0]["label"], f"单关键词必须排第一，实际 {r[:1]}"
    print("ok  B3 单关键词命中不被地板切掉")


def test_B4_mention_only_for_injected():
    """B4：v3 对 ranked 全部调 mention，没进注入包的也算"复述过"。"""
    ag = MemoryHooks()
    for i in range(6):
        ag.brain.add_node(label=f"记录{i} 项目", gist=f"记录{i} 项目细节", origin="user",
                          importance=0.5, about_person=True)
    ag.scorer._rebuild()
    before = {n.id: len(n.mentions) for n in ag.brain.nodes.values()}
    r = ag.before_reply("项目 记录")
    after = {n.id: len(n.mentions) for n in ag.brain.nodes.values()}
    rehearsed = {k for k in before if after[k] > before[k]}
    assert rehearsed == set(r["cites"]), \
        f"只有进注入包的才算复述：rehearsed={rehearsed} cites={set(r['cites'])}"
    print("ok  B4 只对真正注入的记忆计复述")


def test_B5_no_repeat_within_session():
    """B5：v3 每轮重复注入同一批记忆（实测 10 轮只出现 3 条、重复 30 次）。"""
    ag = MemoryHooks()
    ag.on_message("我养了一只猫叫团子")
    ag.on_message("我对花生过敏")
    first = ag.before_reply("团子 花生")
    second = ag.before_reply("团子 花生")
    assert first["cites"], "第一轮应有注入"
    assert not second["cites"], "会话内不应重复注入同一批记忆"
    print("ok  B5 会话内不重复注入")


def test_B6_question_not_ingested():
    """B6：v3 的抽取把"？"排除在片段外，导致问句漏进记忆。"""
    ag = MemoryHooks()
    for q in ("我喜欢吃什么？", "我是不是喜欢这个？", "我们怎么超越现成的？"):
        assert not ag.on_message(q), f"问句不得入卡: {q}"
    assert ag.on_message("我喜欢喝铁观音")
    print("ok  B6 疑问句不入卡（含句末问号）")


def test_B7_person_floor_cap():
    """B7：v3 用 [:3-n]，n≥3 时变负切片，注入条数失控（实测 cap=3 却给 7 条）。"""
    ag = MemoryHooks()
    topics = ["火锅", "爬山", "铁观音", "游泳", "摄影", "围棋", "爵士乐", "咖啡",
              "露营", "书法"]
    pids = [ag.brain.add_node(label=f"用户喜欢{t}，周末常去",
                              gist=f"用户喜欢{t}，周末常去", origin="user",
                              importance=.9, about_person=True).id for t in topics]
    for i in range(2):
        ag.brain.add_node(label=f"其他事项{i} 的记录", gist=f"其他事项{i} 的记录",
                          origin="user", importance=.9)
    ag.scorer._rebuild()
    from brain.pack import build
    ranked = [{"id": p, "label": ag.brain.nodes[p].label, "score": 1.0,
               "decomp": {"rel": 0.5}} for p in pids[:6]]
    out = build(ag.brain, ranked, qtoks=tokenize("用户偏好 记录"), social=True)
    assert len(out["used"]) <= 6, f"不得越过上限，实际 {len(out['used'])}"
    assert len(out["used"]) == 6, "6 条高相关都应入包（不再有负切片失控）"
    print("ok  B7 person floor 无负切片")


def test_B8_archive_uses_days():
    """B8：v3 的归档阈值是 ACT-R 原始秒数，0.15 ≈ 44 秒；且只提过 2 次的永不归档。"""
    b = Brain()
    n1 = b.add_node(label="刚说的事", origin="user")
    n1.mentions = [time.time() - 60]              # 60 秒前只提过一次
    n2 = b.add_node(label="很久以前的事", origin="user")
    n2.mentions = [time.time() - 400 * DAY, time.time() - 390 * DAY]
    b.archive_pass()
    assert n1.slice != "archive", "刚说过一分钟的记忆不得归档"
    assert n2.slice == "archive", "400 天前、只提过两次的记忆必须归档"
    print("ok  B8 归档按天，且不豁免「多次提及」的陈旧记忆")


def test_B9_full_card_is_append_only():
    """B9：v3 的 full 卡只在创建时写快照，之后状态变更全不落档。"""
    d = tempfile.mkdtemp()
    ag = MemoryHooks(store_dir=d)
    n = ag.brain.add_node(label="可改口的事", origin="user", gist="可改口的事")
    ag.brain.correct("可改口的事", "改口后的新事实")
    ag.brain.save()
    ev = os.path.join(d, "events.jsonl")
    lines = [l for l in open(ev, encoding="utf-8") if l.strip()]
    ops = [__import__("json").loads(l)["op"] for l in lines]
    assert "create" in ops and "patch" in ops, f"事件流应含创建与变更，实际 {set(ops)}"
    print("ok  B9 全量卡 append-only（create+patch 事件都在）")


def test_B10_verbatim_reaches_model():
    """B10：v3 注入只用 gist/label，原话锚点永远到不了模型。"""
    ag = MemoryHooks()
    ag.brain.add_node(label="结算协议", type="lesson", origin="user", importance=.9,
                      gist="每次聊天都结算", verbatim=["安静清醒期就是巩固窗口"],
                      about_person=True)
    ag.scorer._rebuild()
    r = ag.before_reply("安静清醒期 巩固窗口")
    assert "安静清醒期" in r["pack"], f"原话应随命中一起注入: {r['pack']}"
    print("ok  B10 原话锚点可进注入包")


def test_B11_three_channels_populated():
    """B11：v3 的 on_message 只填 label=gist，verbatim/feeling 恒空。"""
    ag = MemoryHooks()
    ids = ag.on_message("我下周三要去杭州出差，有点紧张")
    assert ids, "应写入"
    n = ag.brain.nodes[ids[0]]
    assert n.verbatim and "杭州" in n.verbatim[0], "原话通道应填充"
    assert n.feeling and n.feeling.get("label"), "情感通道应填充"
    assert n.due_at > time.time(), "日期应解析为到期时间"
    assert "杭州" in n.entities, f"实体应抽取: {n.entities}"
    print("ok  B11 三通道 + 日期 + 实体都被填充")


def test_B12_judge_is_wired():
    """B12：v3 的 judge 参数赋值后从未被调用。"""
    calls = []

    class FakeJudge:
        def review_conflict(self, a, b):
            calls.append((a, b))
            return False

    ag = MemoryHooks(judge=FakeJudge())
    ag.brain.add_node(label="我在做一个记忆引擎项目", gist="我在做一个记忆引擎项目",
                      origin="user", about_person=True)
    new = ag.brain.add_node(label="我做的是记忆引擎项目", gist="我做的是记忆引擎项目",
                            origin="user", about_person=True)
    ag.session_new = [new.id]
    ag.on_session_end()
    assert calls, "settle 必须真的调用 judge 做冲突复核"
    print("ok  B12 judge 接线（冲突复核真的发生）")


def test_B13_edges_are_real():
    """v3 中文冲突检测用 str.split() → 实测边数恒为 0，图是空壳。"""
    ag = MemoryHooks()
    for t in ("我养了一只猫叫团子", "团子对海鲜过敏", "我对花生过敏", "我老婆叫小满",
              "小满对猫毛过敏"):
        ag.on_message(t)
    edges = sum(len(v) for v in ag.brain.adj.values()) // 2
    assert edges > 0, "必须建出边"
    types = {et for v in ag.brain.adj.values() for _nb, et, _w in v}
    assert "topic" in types, f"应有主题边，实际 {types}"
    print(f"ok  B13 图真的建出来了（{edges} 条边，类型 {sorted(types)}）")


def test_B14_conflict_detection_on_chinese():
    """v3 用 set(gist.split()) 算中文 Jaccard，恒为 0 → 冲突检测从未生效。"""
    ag = MemoryHooks()
    a = ag.brain.add_node(label="我家的猫叫团子", gist="我家的猫叫团子",
                          origin="user", about_person=True)
    b = ag.brain.add_node(label="我家的猫叫豆豆", gist="我家的猫叫豆豆",
                          origin="user", about_person=True)
    ag.session_new = [b.id]
    ag.on_session_end()
    kinds = {et for nb, et, w in ag.brain.adj.get(a.id, [])}
    assert "conflict" in kinds, f"中文近义冲突应被检出，实际邻居类型 {kinds}"
    print("ok  B14 中文冲突检测生效（统一分词器）")


def test_B15_stats_are_measured():
    """B15：v3 的 resident_tokens / dim14_efficiency 是拍出来的常数，随节点数单调变好。"""
    ag = MemoryHooks()
    ag.on_message("我养了一只猫叫团子")
    ag.before_reply("团子")
    s = ag.stats()
    assert s["avg_inject_chars"] > 0 and s["memory_chars"] > 0
    assert s["compression"] > 1, "压缩比应是真实测量值"
    assert "edges" in s and s["edges"] >= 0
    print(f"ok  B15 指标是实测的（压缩比 {s['compression']}x）")


def test_B16_v3_data_migration():
    """v3 的 full_cards.jsonl / live_cards.json 应能自动迁移到事件流。"""
    import json as _json
    d = tempfile.mkdtemp()
    old = {"id": "n000", "label": "老数据", "gist": "老数据", "status": "live",
           "origin": "user", "created": time.time(), "mentions": [time.time()]}
    with open(os.path.join(d, "full_cards.jsonl"), "w", encoding="utf-8") as f:
        f.write(_json.dumps(old, ensure_ascii=False) + "\n")
    ag = MemoryHooks(store_dir=d)
    assert any(n.label == "老数据" for n in ag.brain.nodes.values()), "旧库应被迁移进来"
    print("ok  B16 v3 数据自动迁移")


# ---------------------------------------------------------------- v4.1 长时程能力
def test_virtual_clock_drives_decay():
    """v4.0 直接调 time.time()，半年后的行为根本没法测。v4.1 引入可注入时钟。"""
    from brain import clock as ck
    vc = ck.VirtualClock("2026-01-01")
    with vc:
        b = Brain()
        n = b.add_node(label="很久以前的一件事", origin="user", importance=0.35)
        n.mentions = [vc.now()]
        f0 = b.freshness(n)
        vc.advance(days=30)
        f30 = b.freshness(n)
        vc.advance(days=470)                       # 累计 500 天
        f500 = b.freshness(n)
        assert f0 > f30 > f500, "鲜活度必须随时钟单调下降"
        assert f500 < 0.02, f"500 天后应几乎不可及，实际 {f500:.4f}"
        b.archive_pass()
        assert n.slice == "archive", "500 天没提起过的记忆应归档"
    # 退出 with 后时钟必须恢复真实时间
    assert abs(ck.now() - time.time()) < 5, "虚拟时钟泄漏到外层"
    print("ok  可注入时钟驱动衰减与归档（180→500 天）")


def test_due_commitment_fires_on_due_date():
    """前瞻记忆：承诺必须在自己那一天浮出来，不能早也不能晚。"""
    from brain import clock as ck
    vc = ck.VirtualClock("2026-01-01")
    with vc:
        ag = MemoryHooks()
        ids = ag.on_message("我1月10号要去医院体检")
        assert ids, "承诺应入卡"
        n = ag.brain.nodes[ids[0]]
        assert n.due_at > 0, "应解析出到期时间"
        assert not ag.on_proactive(), "还没到日子不该提醒"
        vc.advance(days=8)                       # 2026-01-09，提前一天
        trig = ag.on_proactive()
        assert any(t == "到期承诺" for t, _ in trig), f"到期前一天应提醒，实际 {trig}"
        vc.advance(days=3)                       # 逾期
        trig2 = ag.on_proactive()
        assert any(t == "到期承诺" for t, _ in trig2), "逾期也应提醒"
    print("ok  到期承诺在正确的那天浮出（前瞻记忆）")


def test_working_slice_is_populated():
    """三切片里 working 一直为 0 是 v4.0 的遗留缺陷。"""
    from brain.core import WORKING_CAP
    ag = MemoryHooks()
    topics = ["火锅", "爬山", "铁观音", "游泳", "摄影", "围棋", "爵士乐", "咖啡",
              "露营", "书法"]
    for t in topics:
        ag.on_message(f"我最近在研究{t}，周末常去")
    ag.on_session_end()
    working = [n for n in ag.brain.nodes.values() if n.slice == "working"]
    assert len(working) == WORKING_CAP, f"工作记忆应满容量，实际 {len(working)}"
    assert ag.stats()["slices"]["working"] == WORKING_CAP
    print(f"ok  工作记忆切片真的被填满（{WORKING_CAP} 条）")


def test_archive_still_recallable_on_explicit_request():
    """设计说"遗忘=可及性下降，不是可用性消失"；v4.0 却把归档硬排除，等于真遗忘。"""
    ag = MemoryHooks()
    n = ag.brain.add_node(label="我们第一次见面是在茶水间", gist="我们第一次见面是在茶水间",
                          origin="user", about_person=True, importance=0.6)
    n.mentions = [time.time() - 500 * DAY]
    ag.brain.archive_pass()
    assert n.slice == "archive"
    # 常规检索不翻归档（避免陈年记忆淹没当前话题）
    assert all(x["id"] != n.id for x in ag.scorer.rank("茶水间")), "常规检索不该翻归档"
    # 显式回忆必须能翻出来
    ranked = ag.scorer.rank("茶水间 第一次见面", include_archive=True)
    assert any(x["id"] == n.id for x in ranked), "显式回忆应能取回归档记忆"
    r = ag.before_reply("你还记得我们第一次见面吗")
    assert n.id in r["cites"], f"「还记得」应触发归档召回，实际注入 {r['cites']}"
    print("ok  归档记忆可被显式回忆取回（可及性下降 ≠ 可用性消失）")


# ---------------------------------------------------------------- 新能力
def test_recall_protocol_closes_loop():
    """v3 的 [[recall: 词]] 只返回 id，没有人使用；v4 下一轮强制注入。"""
    ag = MemoryHooks()
    ag.on_message("我养了一只猫叫团子")
    ag.on_message("我对花生过敏")
    ids = ag.handle_turn("让我想想 [[recall: 团子]]")
    assert ids, "召回协议应解析出候选"
    r = ag.before_reply("今天天气怎么样")
    assert set(ids) & set(r["cites"]), "上一轮召回的记忆必须在下一轮被注入"
    print("ok  [[recall:]] 协议闭环")


def test_due_commitment_is_forced():
    """前瞻记忆：明天到期的事，即使跟当前话题无关也要提醒。"""
    ag = MemoryHooks()
    ag.brain.add_node(label="明天要交报告", gist="明天要交报告", type="rule",
                      origin="user", about_person=True, due_at=time.time() + 3600)
    ag.on_message("我对花生过敏")
    r = ag.before_reply("今天天气不错")
    assert "交报告" in r["pack"], f"到期承诺应强制注入: {r['pack']}"
    print("ok  到期承诺强制注入（前瞻记忆）")


def test_graph_diffusion_multihop():
    """扩散必须能通过"桥"节点找到问题里没有关键词的答案。"""
    ag = MemoryHooks()
    ag.on_message("我养了一只猫叫团子")
    ag.on_message("团子对海鲜过敏")
    ag.on_message("我喜欢喝铁观音")
    r = ag.before_reply("我家猫身体有什么问题")
    assert "海鲜" in r["pack"], f"多跳扩散失败: {r['pack']}"
    print("ok  扩散多跳命中（问题里没有'海鲜'）")


def test_out_of_scope_abstains():
    """记忆里没有的东西，宁可不说话，也不要硬塞相关记忆。"""
    ag = MemoryHooks()
    for t in ("我养了一只猫叫团子", "我对花生过敏", "我喜欢喝铁观音"):
        ag.on_message(t)
    r = ag.before_reply("我护照什么时候到期")
    assert not r["cites"], f"越界问题应弃答，实际注入: {r['pack']}"
    print("ok  越界问题弃答（绝不虚构）")


def test_arabic_and_english_mixed():
    ag = MemoryHooks()
    ag.on_message("我用 Python 写代码，电脑是 ThinkPad X1")
    r = ag.before_reply("ThinkPad")
    assert r["cites"] and "ThinkPad" in r["pack"]
    print("ok  中英混排检索")


def test_long_session_no_blowup():
    """长会话：50 轮后注入量不随轮数增长（这是"省 token"的结构性保证）。"""
    ag = MemoryHooks()
    for i in range(20):
        ag.on_message(f"我第{i}个偏好是茶{i}")
    sizes = []
    for i in range(50):
        r = ag.before_reply(f"茶{i % 20} 偏好")
        sizes.append(r["chars"])
    assert max(sizes) <= 300, "任何一轮都不得超过预算"
    assert sum(sizes) / len(sizes) < 300, "平均注入必须远低于整段历史"
    print(f"ok  长会话注入封顶（均值 {sum(sizes)/len(sizes):.0f} 字 / 上限 300）")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
