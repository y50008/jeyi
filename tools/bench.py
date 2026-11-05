"""tools/bench.py — 检索质量基准（v4.0 新增，这是 v3 最缺的那场"检验"）

不测"机制在不在"，测"问对了能不能拿回来、代价多大"。

三个系统对照：
  full   把所有记忆整段塞进上下文（长上下文的代表：召回满分、token 无底）
  naive  v3.1.1 的算法复刻（二元组分词 + s/(s+8) + 0.20 地板 + 最早三条 person floor）
  v4     当前引擎（倒排索引 + tf-idf 余弦门控 + 图扩散 + MMR 选包 + 会话去重）

指标：rank@5 / 投递命中(注入包里有没有) / 无关行数 / 注入字符 / 弃答正确率 / 延迟
语料：主题词表 + 多轮事实，人工构造（**合成语料，不是公开基准**，见 docs/VALIDATION.md）。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jeyi import MemoryHooks, tokenize            # noqa: E402
from jeyi.core import DAY                        # noqa: E402

# ------------------------------------------------------------------ 语料
# (口语原句, 金标标签)。金标标签用于在注入包里认出"这条是不是该命中的记忆"。
MEMORIES = [
    ("我对花生过敏，吃了会起疹子", "allergy"),
    ("我养了一只猫叫团子", "cat"),
    ("我喜欢喝铁观音，只喝浓香型", "tea"),
    ("我下周三要去杭州出差", "trip"),
    ("我每天早上七点起床", "wake"),
    ("我讨厌开会，尤其是长会", "meeting"),
    ("我在做一个记忆引擎项目", "project"),
    ("我最近睡眠不好", "sleep"),
    ("我准备换工作了", "job"),
    ("我家住在上海徐汇区", "home"),
    ("我老婆叫小满", "wife"),
    ("我女儿今年五岁", "daughter"),
    ("我周末喜欢去爬山", "hike"),
    ("我学的是计算机视觉", "cv"),
    ("我以前在字节跳动工作过三年", "bytedance"),
    ("我喝咖啡会心悸", "coffee"),
    ("我不吃香菜", "cilantro"),
    ("我习惯用 Python 写代码", "python"),
    ("我的电脑是 ThinkPad X1", "laptop"),
    ("我下周要交一份季度报告", "report"),
    ("我对猫毛也过敏", "catallergy"),
    ("我最近在读《人类简史》", "book"),
    ("我每天早上会喝一杯温水", "water"),
    ("我爸爸有高血压", "dad"),
    ("我讨厌吃辣", "spicy"),
    ("我开车上班，大概四十分钟", "commute"),
    ("我今年三十五岁", "age"),
    ("我租的房子在六楼，没有电梯", "floor"),
    ("我手机是 iPhone 15", "phone"),
    ("我喜欢看科幻电影", "scifi"),
    ("我讨厌打电话，喜欢微信", "wechat"),
    ("我每年体检一次", "checkup"),
    ("我不抽烟，也不喝酒", "nosmoke"),
    ("我最近开始健身", "gym"),
    ("我最喜欢的城市是杭州", "city"),
    ("我大学在武汉读的", "wuhan"),
    ("我怕冷，冬天必须开暖气", "cold"),
    ("我养过一只狗叫大福，去年走了", "dog"),
    ("我周三下午一般有例会", "weekmeeting"),
    ("我的工位在十二楼靠窗", "desk"),
    ("我讨厌写周报", "weekly"),
    ("我准备明年考一个证", "cert"),
    ("我妈是老师", "mom"),
    ("我喜欢喝冰美式，不加糖", "americano"),
    ("我睡觉必须完全黑", "dark"),
    ("我下雨天会偏头痛", "headache"),
    ("我在学日语，每天背单词", "japanese"),
    ("我最怕迟到", "late"),
    ("我习惯把待办写在纸上", "todo"),
    ("我下周要去看牙医", "dentist"),
    ("团子对海鲜过敏", "catseafood"),
    ("小满对猫毛过敏", "wifeallergy"),
]

# (问句, 金标标签集合, 类别)。空金标 = 记忆里没有，正确行为是弃答。
QUESTIONS = [
    ("我对什么过敏", {"allergy", "catallergy"}, "direct"),
    ("我吃花生会怎么样", {"allergy"}, "direct"),
    ("我家猫叫什么名字", {"cat"}, "entity"),
    ("宠物叫什么", {"cat"}, "synonym"),
    ("我下周三去哪", {"trip"}, "temporal"),
    ("我下周有什么安排", {"trip", "report", "dentist"}, "temporal"),
    ("我喜欢喝什么茶", {"tea"}, "direct"),
    ("我平时喝什么饮料", {"tea", "americano", "water"}, "synonym"),
    ("我几点起床", {"wake"}, "direct"),
    ("我对开会什么态度", {"meeting"}, "direct"),
    ("我最近在忙什么", {"project", "sleep"}, "aggregate"),
    ("我住哪", {"home"}, "direct"),
    ("我老婆叫什么", {"wife"}, "direct"),
    ("我女儿多大", {"daughter"}, "direct"),
    ("我周末喜欢干什么", {"hike"}, "direct"),
    ("我学什么专业", {"cv"}, "direct"),
    ("我以前在哪工作", {"bytedance"}, "direct"),
    ("喝咖啡会怎样", {"coffee"}, "direct"),
    ("我不吃什么", {"cilantro", "spicy"}, "direct"),
    ("我写代码用什么语言", {"python"}, "direct"),
    ("我用什么电脑", {"laptop"}, "direct"),
    ("我在读什么书", {"book"}, "direct"),
    ("我爸身体怎么样", {"dad"}, "direct"),
    ("我怎么上班", {"commute"}, "direct"),
    ("我多大年纪", {"age"}, "direct"),
    ("我住的房子有什么不方便", {"floor"}, "infer"),
    ("我用什么手机", {"phone"}, "direct"),
    ("我喜欢看什么电影", {"scifi"}, "direct"),
    ("我喜欢打电话还是微信", {"wechat"}, "direct"),
    ("我体检频率", {"checkup"}, "direct"),
    ("我抽烟喝酒吗", {"nosmoke"}, "direct"),
    ("我最近在健身吗", {"gym"}, "direct"),
    ("我最喜欢哪个城市", {"city"}, "direct"),
    ("我大学在哪读的", {"wuhan"}, "direct"),
    ("我怕冷吗", {"cold"}, "direct"),
    ("我养过狗吗", {"dog"}, "direct"),
    ("我周几有例会", {"weekmeeting"}, "temporal"),
    ("我的工位在哪", {"desk"}, "direct"),
    ("我讨厌写什么", {"weekly"}, "direct"),
    ("我妈做什么工作", {"mom"}, "direct"),
    ("我喝美式加糖吗", {"americano"}, "direct"),
    ("我睡觉有什么要求", {"dark"}, "direct"),
    ("下雨天我会怎样", {"headache"}, "direct"),
    ("我在学什么外语", {"japanese"}, "direct"),
    ("我最怕什么", {"late"}, "direct"),
    ("我的待办记在哪", {"todo"}, "direct"),
    ("我护照什么时候到期", set(), "abstain"),
    ("我爷爷叫什么名字", set(), "abstain"),
    ("我银行卡尾号多少", set(), "abstain"),
    ("我保险什么时候到期", set(), "abstain"),
    ("我奶奶叫什么名字", set(), "abstain"),
    # 多跳：问题里没有答案记忆的任何词，只能靠图从"桥"节点扩散过去
    ("我家猫身体有什么问题", {"catseafood"}, "multihop"),
    ("我老婆不能接触什么", {"wifeallergy"}, "multihop"),
]

# ------------------------------------------------------------------ 留出集
# 完全不同的人设、领域与问法。**这份语料在参数定稿后才写、只跑一次**：
# 主集与留出集的差距就是过拟合的度量。任何"照着留出集调参"都让它失去意义。
HOLDOUT_MEMORIES = [
    ("我在杭州做建筑设计，主要做住宅", "designer"),
    ("我妈今年六十八，住在绍兴", "mom"),
    ("我弟弟在准备考研，压力很大", "brother"),
    ("我对芒果过敏，嘴会肿", "mango"),
    ("我每天早上要喝一杯黑咖啡，不加糖", "coffee"),
    ("我下周五要去甲方那边过方案", "client"),
    ("我最近在赶一个幼儿园的项目", "project"),
    ("我加班到十一点是常事", "overtime"),
    ("我颈椎不好，坐久了会疼", "neck"),
    ("我养了两盆绿萝，快养死了", "plant"),
    ("我租的房子在拱墅区，两室一厅", "rent"),
    ("我周末一般会去运河边跑步", "run"),
    ("我最喜欢的建筑师是路易斯康", "kahn"),
    ("我不会游泳，一直想学", "swim"),
    ("我讨厌改图，尤其是甲方半夜提需求", "revision"),
    ("我女朋友叫阿禾，是做展览的", "girlfriend"),
    ("我家猫叫年糕，特别怕生", "cat"),
    ("我习惯用 SketchUp 建模", "sketchup"),
    ("我明年想考一级注册建筑师", "exam"),
    ("我血压有点高，医生让少熬夜", "bp"),
    ("我下个月要搬家，搬去滨江", "move"),
    ("我喜欢吃辣，但胃不太好", "spicy"),
    ("我手机里存了很多工地的照片", "photos"),
    ("我大学在同济读的", "tongji"),
    ("年糕对牛奶不耐受", "catmilk"),
]

HOLDOUT_QUESTIONS = [
    ("我是做哪一行的", {"designer"}, "direct"),
    ("我妈现在住哪", {"mom"}, "direct"),
    ("我弟弟最近在忙什么", {"brother"}, "direct"),
    ("我吃芒果会怎么样", {"mango"}, "direct"),
    ("我早上喝什么", {"coffee"}, "direct"),
    ("我下周五有什么安排", {"client"}, "temporal"),
    ("我在赶什么项目", {"project"}, "direct"),
    ("我一般几点下班", {"overtime"}, "infer"),
    ("我身体有哪些毛病", {"neck", "bp"}, "aggregate"),
    ("我养了什么植物", {"plant"}, "direct"),
    ("我住在哪个区", {"rent"}, "direct"),
    ("我周末做什么运动", {"run"}, "direct"),
    ("我最喜欢哪个建筑师", {"kahn"}, "direct"),
    ("我会游泳吗", {"swim"}, "direct"),
    ("我最烦工作里的什么", {"revision"}, "infer"),
    ("我女朋友是做什么的", {"girlfriend"}, "direct"),
    ("我家猫叫什么", {"cat"}, "entity"),
    ("我用什么软件建模", {"sketchup"}, "direct"),
    ("我明年有什么打算", {"exam"}, "direct"),
    ("我下个月要做什么", {"move"}, "temporal"),
    ("我在哪读的大学", {"tongji"}, "direct"),
    ("我家猫有什么忌口", {"catmilk"}, "multihop"),
    ("我平时喝什么饮料", {"coffee"}, "synonym"),
    ("我养了什么宠物", {"cat"}, "synonym"),
    ("我车牌号是多少", set(), "abstain"),
    ("我爷爷叫什么名字", set(), "abstain"),
    ("我护照什么时候到期", set(), "abstain"),
]

# v3 的常数（复刻基线用）
NAIVE_FLOOR, NAIVE_GAP, NAIVE_K = 0.20, 1.35, 8.0


def _bigrams(s):
    """v3 的分词：只取 CJK 二元组 + 拉丁词。"""
    import re
    out = re.findall(r"[a-z0-9]+", (s or "").lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", s or ""):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


class NaiveV3:
    """v3.1.1 打分复刻，用于公平对照（图在 v3 实测恒为空，故 graph 项置 0）。"""

    def __init__(self, brain):
        self.b = brain
        self.df = {}
        for n in brain.nodes.values():
            for t in set(_bigrams(n.label + n.gist)):
                self.df[t] = self.df.get(t, 0) + 1
        self.N = max(1, len(brain.nodes))

    def idf(self, t):
        return __import__("math").log(1 + self.N / (1 + self.df.get(t, 0)))

    def C(self, qtoks, n):
        doc = set(_bigrams(n.label + n.gist))
        s = sum(self.idf(t) for t in set(qtoks) & doc)
        return s / (s + NAIVE_K)

    def rank(self, q, k=8):
        qt = _bigrams(q)
        out = []
        for n in self.b.nodes.values():
            if n.status != "live" or n.slice == "archive":
                continue
            c = self.C(qt, n)
            if c <= 0:
                continue
            A = self.b.freshness(n)
            I = n.importance * (0.6 + 0.4 * n.confidence)
            E = self.b.emotion_freshness(n)
            P = c * (0.05 + 0.45 * A + 0.35 * I + 0.15 * E)
            out.append({"id": n.id, "score": P})
        out.sort(key=lambda x: -x["score"])
        return [x for x in out if x["score"] >= NAIVE_FLOOR][:k]


def _build_engine(memories=None, embedding=None):
    ag = MemoryHooks(embedding=embedding)
    keys = {}
    ingested = 0
    for text, key in (memories or MEMORIES):
        ids = ag.on_message(text)
        ingested += len(ids)
        for i in ids:
            n = ag.brain.nodes[i]
            if key not in keys:
                keys[key] = n.id
    ag.scorer._rebuild()
    return ag, keys, ingested


def _evaluate_rank(ag, keys, ranker, questions=None):
    rows = []
    for q, gold, cat in (questions or QUESTIONS):
        ranked = ranker(q)
        top = [r["id"] for r in ranked[:5]]
        gold_ids = {keys[g] for g in gold if g in keys}
        hit = bool(gold_ids & set(top)) if gold_ids else not top
        rows.append({"q": q, "cat": cat, "hit5": hit, "top": top, "gold": gold_ids})
    return rows


def _evaluate_pack(ag, keys, packer_fn, reset=True, questions=None):
    rows = []
    for q, gold, cat in (questions or QUESTIONS):
        if reset:
            ag.session_seen.clear()
            ag.brain.turn = 0
        res = packer_fn(q)
        used = res.get("used") if res.get("used") is not None else res.get("cites", [])
        chars = res.get("chars", 0)
        gold_ids = {keys[g] for g in gold if g in keys}
        if gold_ids:
            hit = bool(gold_ids & set(used))
            noise = len([i for i in used if i not in gold_ids])
        else:
            hit = len(used) == 0
            noise = len(used)
        rows.append({"q": q, "cat": cat, "hit": hit, "noise": noise, "chars": chars})
    return rows


def main():
    as_json = "--json" in sys.argv
    holdout = "--holdout" in sys.argv
    dense_mode = "--dense" in sys.argv
    emb = None
    if dense_mode:
        from jeyi import dense as _dense
        emb = _dense.make_embedder()
        if emb is None:
            print("[dense] 模型或依赖缺失，先跑 python tools/setup_dense.py")
            return 1
    mem = HOLDOUT_MEMORIES if holdout else MEMORIES
    qs = HOLDOUT_QUESTIONS if holdout else QUESTIONS
    if not as_json:
        print(("【留出集】" if holdout else "【主集】")
              + " 主集用于调参，留出集只在定稿后跑一次——两者的差距就是过拟合的度量。")
    ag, keys, ingested = _build_engine(mem, embedding=emb)
    n_nodes = len(ag.brain.nodes)
    edges = sum(len(v) for v in ag.brain.adj.values()) // 2

    # ---------- v4 ----------
    v4_rank = _evaluate_rank(ag, keys, lambda q: ag.scorer.rank(q, k=5), qs)
    v4_pack = _evaluate_pack(ag, keys, lambda q: ag.before_reply(q), questions=qs)

    # ---------- naive v3 ----------
    naive = NaiveV3(ag.brain)
    nv_rank = _evaluate_rank(ag, keys, lambda q: naive.rank(q, k=5), qs)

    def naive_pack(q):
        ranked = naive.rank(q, k=8)
        used, chars = [], 0
        for r in ranked:
            line = "·" + ag.brain.nodes[r["id"]].gist
            if chars + len(line) > 300:
                break
            used.append(r["id"])
            chars += len(line)
        # v3 的 person floor：按插入顺序补最早三条
        persons = [n.id for n in ag.brain.nodes.values()
                   if n.about_person and n.status == "live" and n.slice != "archive"
                   and n.id not in used][:3 - len([i for i in used if ag.brain.nodes[i].about_person])]
        for p in persons:
            if chars + len(ag.brain.nodes[p].gist) > 300:
                break
            used.append(p)
            chars += len(ag.brain.nodes[p].gist)
        return {"used": used, "chars": chars}

    nv_pack = _evaluate_pack(ag, keys, naive_pack, questions=qs)

    # ---------- full context ----------
    full_chars = sum(len(n.gist) for n in ag.brain.nodes.values()
                     if n.status == "live" and n.slice != "archive")
    full_recall = sum(1 for _q, gold, _c in qs if gold) / max(
        1, sum(1 for _q, gold, _c in qs if gold))

    # ---------- 延迟 ----------
    lat = []
    for q, _g, _c in qs[:20]:
        t0 = time.perf_counter()
        ag.scorer.rank(q, k=5)
        lat.append((time.perf_counter() - t0) * 1000)

    def pct(rows, pred):
        return round(100.0 * sum(1 for r in rows if pred(r)) / max(1, len(rows)), 1)

    ans = [r for r in qs if r[1]]
    absta = [r for r in qs if not r[1]]

    def by_cat(rows, cat):
        sub = [r for r in rows if r["cat"] == cat]
        hit = sum(1 for r in sub if r.get("hit", r.get("hit5")))
        return round(100.0 * hit / max(1, len(sub)), 1), len(sub)

    summary = {
        "corpus": {"memories": len(MEMORIES), "ingested_nodes": ingested,
                   "nodes": n_nodes, "edges": edges},
        "v4": {"rank@5": pct(v4_rank, lambda r: r["hit5"]),
               "pack_hit": pct([r for r in v4_pack if r["cat"] != "abstain"], lambda r: r["hit"]),
               "abstain": pct([r for r in v4_pack if r["cat"] == "abstain"], lambda r: r["hit"]),
               "avg_chars": round(sum(r["chars"] for r in v4_pack) / len(v4_pack), 1),
               "avg_noise": round(sum(r["noise"] for r in v4_pack) / len(v4_pack), 2),
               "latency_ms": round(sum(lat) / len(lat), 2)},
        "naive_v3": {"rank@5": pct(nv_rank, lambda r: r["hit5"]),
                     "pack_hit": pct([r for r in nv_pack if r["cat"] != "abstain"], lambda r: r["hit"]),
                     "abstain": pct([r for r in nv_pack if r["cat"] == "abstain"], lambda r: r["hit"]),
                     "avg_chars": round(sum(r["chars"] for r in nv_pack) / len(nv_pack), 1),
                     "avg_noise": round(sum(r["noise"] for r in nv_pack) / len(nv_pack), 2)},
        "full_context": {"avg_chars": full_chars, "recall": round(full_recall * 100, 1)},
    }
    for cat in ("direct", "entity", "synonym", "temporal", "aggregate", "infer", "multihop"):
        summary["v4"]["cat_" + cat] = by_cat(v4_rank, cat)
        summary["naive_v3"]["cat_" + cat] = by_cat(nv_rank, cat)

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    import unicodedata

    def dwidth(s):
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))

    def pad(s, w, right=False):
        gap = max(0, w - dwidth(s))
        return (" " * gap + str(s)) if right else (str(s) + " " * gap)

    W = [26, 16, 16, 16]

    def row(cells):
        return "".join(pad(c, w, right=(i > 0)) for i, (c, w) in enumerate(zip(cells, W)))

    print("=" * 74)
    backend = "稠密开（bge-small-zh-v1.5 int8）+ 词法 RRF" if dense_mode else "纯词法（零依赖）"
    print(f"检索后端：{backend}")
    print(f"语料：{len(mem)} 条记忆 → 写入 {ingested} 条节点 / {edges} 条边；问题 {len(qs)} 个")
    print("注意：合成语料，人工构造，不代表公开基准（见 docs/VALIDATION.md）")
    print("=" * 74)
    print(row(["指标", "v3.1.1 基线", "v4.1", "整段上下文"]))
    print("-" * 74)
    print(row(["rank@5 命中率", f"{summary['naive_v3']['rank@5']}", f"{summary['v4']['rank@5']}", "100.0"]))
    print(row(["投递命中（注入对的）", f"{summary['naive_v3']['pack_hit']}",
              f"{summary['v4']['pack_hit']}", "100.0"]))
    print(row(["弃答正确率", f"{summary['naive_v3']['abstain']}",
              f"{summary['v4']['abstain']}", "0.0"]))
    print(row(["每轮注入字符", f"{summary['naive_v3']['avg_chars']}",
              f"{summary['v4']['avg_chars']}", f"{full_chars}"]))
    print(row(["无关行数/轮", f"{summary['naive_v3']['avg_noise']}",
              f"{summary['v4']['avg_noise']}", "—"]))
    print(row(["检索延迟 ms", "—", f"{summary['v4']['latency_ms']}", "—"]))
    print("-" * 74)
    for cat in ("direct", "entity", "synonym", "temporal", "aggregate", "infer", "multihop"):
        a, na = summary["v4"]["cat_" + cat]
        b, nb = summary["naive_v3"]["cat_" + cat]
        print(f"  {cat:<12}(n={na:>2})  v3 {b:>6}%   v4 {a:>6}%")
    print("=" * 74)

    # 失败清单：诚实列出 v4 没拿回来的问题
    miss = [r for r in v4_rank if not r["hit5"]]
    if miss:
        print("\nv4 未命中（rank@5）：")
        for r in miss:
            print(f"  [{r['cat']}] {r['q']}")
    miss_rank = [r for r in nv_rank if not r["hit5"]]
    if miss_rank:
        print(f"\nv3 未命中 {len(miss_rank)} 个（对照）")


if __name__ == "__main__":
    main()
