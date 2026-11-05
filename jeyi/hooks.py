"""hooks.py — Agent 四个钩子 v4.0：真实写入通道 + 预算选包 + 召回协议闭环

v3 的实现缺口（本次全部修复）：
  · on_message 只做正则抽「我…」，label=gist=m[:40]，verbatim/feeling 永远为空，
    三通道在生产路径上退化成单通道，且截断会切在词中间。
    → v4：按句读切分 → 问题/假设/玩笑过滤（含句末问号，修 v3 的漏判）→
      原话进 verbatim、情绪词进 feeling、日期进 due_at、命名进 entities。
  · 所有「我…」都标 about_person=True，而 _inject 按插入顺序取前 3 条 → 每轮塞最老的 3 条。
  · before_reply 对 ranked 全部调 mention()，没进注入包的也算"复述过"。
    → v4：只对真正进入注入包的节点计复述。
  · [[recall: 词]] 只返回 id 没人用 → v4 缓存到 pending，下一轮强制注入，协议真正闭环。
"""
from __future__ import annotations
import re
import time
from . import clock

from .core import Brain
from .adapters import HeuristicJudge
from .score import Scorer
from .persona import Persona
from . import pack as packer
from . import settle as st
from .lang import (tokenize, expand, jaccard, split_clauses, is_question, is_hypothetical,
                   is_hearsay, has_commitment, has_correction_cue, sentiment,
                   extract_entities, extract_names, find_date, strip_filler, strip_tail,
                   is_planned, has_recall_cue, is_ephemeral)

INJECT_CHARS = packer.INJECT_CHARS
MAX_PER_MSG = 3
MIN_CLAUSE = 4

_RECALL = re.compile(r"\[\[recall:\s*(.+?)\]\]")
_NEG_RE = re.compile(r"不(?!少|久|错|假|好意思|差)|没(?!什么)|别(?!人)")
_PERSON_MARK = ("我", "俺", "本人", "我的", "咱")
_SOCIAL = ("你", "咱们", "最近", "怎么样", "在吗", "在不在", "谢谢", "晚安", "早安",
           "辛苦", "还好", "聊聊", "陪我")


class MemoryHooks:
    def __init__(self, store_dir=None, storage=None, embedding=None, judge=None,
                 budget: int = INJECT_CHARS, session: str = "S1", user: str = "default"):
        self.brain = Brain(store_dir, storage=storage)
        self.scorer = Scorer(self.brain, embedding=embedding)
        self.judge = judge if judge is not None else HeuristicJudge()
        self.persona = Persona()
        self.budget = budget
        self.session = session
        self.user = user
        self.session_new: list[str] = []
        self.session_seen: dict[str, int] = {}
        self.pending: list[str] = []
        self._last_node: str = None
        self._known_names: set[str] = set()
        self._stats = {"turns": 0, "injected_items": 0, "injected_chars": 0,
                       "suppressed": 0, "cross_session_merges": 0,
                       "session_scoped": 0}

    # ------------------------------------------------------------ 会话切换
    def set_session(self, session: str):
        """切到新的会话。

        会话级注入去重表清空（新会话的"已在上下文里"要从零算），
        但记忆本身留着——跨会话的长期记忆照样可见，会话私有的自动隐身。
        """
        if session and session != self.session:
            self.session = session
            self.session_seen.clear()
            self.brain.turn = 0
        return self.session

    def _visible_scopes(self) -> set:
        return {self.session}

    def _rehearse(self, n) -> None:
        """同一条记忆再次被确认：记下是哪个会话，跨会话则上调一点置信度。"""
        before = len(n.sessions)
        self.brain.mention(n.id, session=self.session)
        if len(n.sessions) > before:
            self._stats["cross_session_merges"] += 1

    # ------------------------------------------------------------ ① 写入
    def on_message(self, text: str, origin="user") -> list:
        found: list[str] = []
        created_this_msg: list[str] = []
        for clause, delim in split_clauses(text):
            if len(found) >= MAX_PER_MSG:
                break
            full = clause + delim
            if len(clause) < MIN_CLAUSE:
                continue
            if is_question(full) or is_hypothetical(clause):
                continue                       # 疑问/假设/玩笑不入卡（保守抽取）
            personal = any(m in clause for m in _PERSON_MARK)
            known = any(nm and nm in clause for nm in self._known_names)
            if not personal and not known:
                continue                       # 只收第一人称事实，或关于"已知对象"的陈述
            n = self._make_node(clause, origin)
            if n is None:
                continue
            found.append(n.id)
            created_this_msg.append(n.id)
        for nid in created_this_msg:
            self._link(nid, created_this_msg)
        self._last_node = created_this_msg[-1] if created_this_msg else self._last_node
        self.session_new.extend(found)
        return found

    def remember(self, text: str, *, origin: str = "user", session: str = None,
                 at: float = None, importance: float = 0.55, about_person: bool = True,
                 entities=None, dedup: bool = True, link: bool = True,
                 scope: str = "user"):
        """直通写入：跳过中文抽取启发式，把一段文本原样存成一条记忆。

        用途：外部语料导入（英文基准）、结构化数据落库、从别的记忆系统迁移。
        `on_message` 是"从聊天里小心翼翼地猜"，`remember` 是"我确定这条要记"。

        at:    指定创建时间（外部语料的会话日期），会影响衰减与显式回忆的排序。
        dedup: 导入基准时置 False，保证"一条输入 = 一个节点"，证据映射不会错位。
        scope: "user"=跨会话长期；"session"=只在本会话可见（临时方案/一次性上下文）。
               去重命中时不会新建节点，而是把"这个会话也确认过"记到 sessions 上。
        """
        text = (text or "").strip()
        if not text:
            return None
        if dedup:
            dup = self._find_duplicate(tokenize(text), text)
            if dup is not None:
                self._rehearse(dup)
                return dup.id
        ts = at or clock.now()
        kw = {}
        if at:
            kw = {"created": at, "mentions": [at]}
        n = self.brain.add_node(
            label=text[:80], gist=text[:160], type="fact", origin=origin,
            importance=importance, about_person=about_person,
            verbatim=[text[:200]],
            entities=entities if entities is not None else extract_entities(text),
            session=session or self.session, sources=["import"], scope=scope, **kw)
        if scope == "session":
            self._stats["session_scoped"] += 1
        if link:
            self._link(n.id, [n.id])
        self._last_node = n.id
        self.session_new.append(n.id)
        return n.id

    def _make_node(self, clause: str, origin: str):
        toks = tokenize(clause)
        if not toks:
            return None
        # ---- 隐式改口优先于去重：否则"我对花生过敏"会被判成"我对花生不过敏"的复述 ----
        if has_correction_cue(clause):
            cand = self._best_match(toks, floor=0.45)
            if cand is not None:
                newn, msg = self.brain.correct(cand.label, clause, who=origin)
                if newn is not None:
                    return newn
        # ---- 指纹去重：重复表述 = 复述，不重复建卡 ----
        dup = self._find_duplicate(toks, clause)
        if dup is not None:
            self._rehearse(dup)
            return None
        feel = sentiment(clause)
        due, _raw = find_date(clause)
        if due and not is_planned(clause):
            due = None                          # 泛泛陈述（"我周末喜欢爬山"）不挂到期
        typ = "rule" if (has_commitment(clause) and due) else "fact"
        if any(k in clause[:8] for k in ("我叫", "我是", "我住", "我在")):
            typ = "self" if len(clause) <= 24 else typ
        imp = 0.55
        if feel:
            imp += 0.12
        if due:
            imp += 0.18
        if len(clause) >= 14:
            imp += 0.05
        feeling = None
        if feel:
            label, val, arousal = feel
            feeling = {"label": label, "valence": val, "arousal": arousal,
                       "why": clause[:40], "valid_at": time.strftime("%Y-%m-%d")}
        node = self.brain.add_node(
            label=clause[:60], gist=(strip_filler(clause))[:80],
            type=typ, origin=("web" if is_hearsay(clause) else origin),
            importance=round(min(0.95, imp), 2), about_person=True,
            verbatim=[clause[:120]], feeling=feeling, due_at=(due or 0.0),
            entities=extract_entities(clause), session=self.session,
            scope=("session" if is_ephemeral(clause) else "user"),
            sources=["auto-extract"])
        if node.scope == "session":
            self._stats["session_scoped"] += 1
        self._known_names.update(extract_names(clause))
        return node

    def _find_duplicate(self, toks, clause: str = ""):
        """指纹去重：重合系数 + "不新增信息"判据。

        「我喜欢喝铁观音，不假」 vs 「我喜欢喝铁观音，只喝浓香型」：
        Jaccard 0.63（漏判）但重合系数 0.80 —— 去掉句尾语气词后重合系数 = 1.0。
        同时加两条护栏：
          · 否定极性不同 → 绝不判重（"过敏" vs "不过敏" 是改口，不是复述）
          · 新句子带来了新内容（a-b 超过 1 个词）→ 不算复述，宁可多存一条
            （"我对花生过敏" → "…吃了会起疹子" 是新信息，不能丢）
        """
        core = strip_tail(clause) or clause
        a = set(tokenize(core))
        neg_a = _NEG_RE.search(core) is not None
        self.scorer._sync()
        for nid in self.scorer.index.candidates(toks, limit=200):
            n = self.brain.nodes.get(nid)
            if not n or n.status != "live" or n.slice == "archive":
                continue
            b = set(tokenize(strip_tail(n.gist + " " + n.label) or (n.gist + n.label)))
            if not a or not b:
                continue
            if neg_a != (_NEG_RE.search(strip_tail(n.gist + " " + n.label)) is not None):
                continue
            if len(a & b) / min(len(a), len(b)) < 0.82:
                continue
            if len(a - b) <= 1:          # 新句子没有带来新信息
                return n
        return None

    def _best_match(self, toks, floor=0.45):
        best, bs = None, floor
        for n in self.brain.nodes.values():
            if n.status != "live" or n.type == "reflection":
                continue
            s = jaccard(toks, tokenize(n.gist + " " + n.label))
            if s > bs:
                best, bs = n, s
        return best

    def _link(self, nid: str, same_msg: list):
        """写时建图：同句共现 / 共享稀有词（主题边）/ 会话时序。

        图不通，扩散就是死的；但边也不能滥发：
        v4 早期版本用「情感标签相同」建 rhyme 边，而情感词表只有十几个标签，
        结果几乎每个节点都连到同一个"难过"节点，扩散把无关记忆全拉过了门控。
        情感押韵仍留在打分里（E 与 align），不再作为图边。
        """
        n = self.brain.nodes[nid]
        for other in same_msg:
            if other != nid:
                self.brain.add_edge(nid, other, "cooccur", 0.70)
        # 主题边：与共享「稀有词」的节点相连。
        # 稀有必须是真稀有：df ≤ 2 且不是时间词——否则「过敏」「下周」这类词
        # 会把半个记忆库连成一个巨连通分量，扩散就变成全库注水。
        self.scorer._sync()
        idx = self.scorer.index
        linked, seen_other = 0, set()
        n_total = max(1, idx.N)
        for tok in sorted(set(tokenize(n.gist)), key=lambda t: idx.df(t)):
            df = idx.df(tok)
            if df <= 0 or df > 2:
                continue
            # 双字词天然是主题（"团子"/"杭州"）；单字只有在库足够大时才算——
            # 2~3 条记忆的小库里，任何两句都共享几个字，"稀有"没有意义
            # （实测把「咖啡」和「季度报告」连成了一张网）。
            if len(tok) == 1 and n_total < 20:
                continue
            if find_date(tok)[0] is not None:
                continue                      # 时间词不是主题
            for other in list(idx.postings.get(tok, {}))[:2]:
                if other == nid or other in same_msg or other in seen_other:
                    continue
                seen_other.add(other)
                self.brain.add_edge(nid, other, "topic", 0.40)
                linked += 1
            if linked >= 3:
                break
        if self._last_node and self._last_node != nid and self._last_node in self.brain.nodes:
            self.brain.add_edge(nid, self._last_node, "temporal", 0.25)

    # ------------------------------------------------------------ ② 检索 + 选包
    def before_reply(self, context: str, k: int = 8, stateless: bool = False) -> dict:
        self.brain.turn += 1
        qtoks = expand(tokenize(context))
        # 显式回忆（「你还记得…吗」）允许翻归档；常规对话不翻，避免被陈年记忆淹没
        recall_mode = has_recall_cue(context)
        ranked = self.scorer.rank(context, k=k, mood=self.persona.mood.value(),
                                  include_archive=recall_mode,
                                  scopes=self._visible_scopes())
        rel_map = {r["id"]: r["decomp"]["rel"] for r in ranked}
        social = any(w in context for w in _SOCIAL) or len(qtoks) == 0
        # stateless=True：每轮都要完整卡片（外部系统重建系统提示），不做会话内去重。
        # 默认 False：对话式调用，模型上下文里已经有的不重复烧 token。
        seen = {} if stateless else self.session_seen
        p = packer.build(self.brain, ranked, qtoks=qtoks, budget=self.budget,
                         session_seen=seen, pending=self.pending,
                         social=social, rel_map=rel_map, allow_archive=recall_mode)
        self.pending = []
        # 只对真正进入注入包的节点计复述（v3 把没注入的也算，污染 ACT-R）
        now = clock.now()
        for nid in p["used"]:
            self.brain.mention(nid, now, session=self.session)
            self.brain.nodes[nid].last_injected = now
            if not stateless:
                self.session_seen[nid] = self.brain.turn
            self.brain._dirty.add(nid)
        self._stats["turns"] += 1
        self._stats["injected_items"] += len(p["used"])
        self._stats["injected_chars"] += p["chars"]
        self._stats["suppressed"] += sum(p["dropped"].values())
        return {"pack": p["text"], "ranked": ranked, "cites": p["used"],
                "candidates": [r["id"] for r in ranked],
                "dropped": p["dropped"], "chars": p["chars"]}

    # ------------------------------------------------------------ ③ 回响
    def on_proactive(self, cap: int = 2) -> list:
        return st.resonate(self.brain, cap=cap)

    # ------------------------------------------------------------ ④ 结算
    def on_session_end(self):
        log = st.settle(self.brain, self.session_new, self.scorer, judge=self.judge)
        self.persona.sweep()
        self.brain.save()
        self.session_new = []
        self.session_seen.clear()
        self.pending = []
        return log

    # ------------------------------------------------------------ 召回协议
    def handle_turn(self, agent_text: str) -> list:
        """解析 [[recall: 词]]，缓存到下一轮强制注入（协议闭环）。"""
        terms = _RECALL.findall(agent_text or "")
        hits: list[str] = []
        for term in terms:
            for r in self.scorer.rank(term, k=3, include_archive=True,
                                   scopes=self._visible_scopes()):
                if r["id"] not in hits:
                    hits.append(r["id"])
        self.pending = (self.pending + hits)[:6]
        return sorted(set(hits))

    # ------------------------------------------------------------ 指标
    def stats(self) -> dict:
        slices = {"working": 0, "active": 0, "archive": 0}
        status = {"live": 0, "superseded": 0, "overturned": 0}
        memory_chars = 0
        session_scoped = cross_session = 0
        for n in self.brain.nodes.values():
            slices[n.slice] = slices.get(n.slice, 0) + 1
            status[n.status] = status.get(n.status, 0) + 1
            if n.status == "live":
                memory_chars += len(n.gist) + len(n.verbatim[0] if n.verbatim else "")
            if n.scope == "session":
                session_scoped += 1
            if len(n.sessions) > 1:
                cross_session += 1
        edges = sum(len(v) for v in self.brain.adj.values()) // 2
        turns = max(1, self._stats["turns"])
        avg_chars = self._stats["injected_chars"] / turns
        return {
            "slices": slices, "status": status,
            "nodes": len(self.brain.nodes), "edges": edges,
            "memory_chars": memory_chars,
            "avg_inject_chars": round(avg_chars, 1),
            "compression": round(memory_chars / max(1.0, avg_chars), 1),
            "suppression": self._stats["suppressed"],
            # 作用域与跨会话同一性：session_scoped 只在本会话可见；
            # cross_session 是"在 ≥2 个会话里被确认过"的记忆（同一性合并的证据）
            "session": self.session,
            "session_scoped": session_scoped,
            "cross_session": cross_session,
            # 写入去重时跨会话命中的次数（注入侧的会话记录不算在这里）
            "cross_session_merges": self._stats["cross_session_merges"],
            "mood": round(self.persona.mood.value(), 3),
            "traits": {k: v.state for k, v in self.persona.traits.items()},
        }
