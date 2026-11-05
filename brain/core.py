"""core.py — 全图底座 v4.0：节点 / 真实边 / 状态机 / 按天归档 / 事件溯源落盘

相对 v3.1.1 的关键改动：
  · 边真的会被建出来（同句共现 / 实体共享 / 会话时序 / 蕴含 / 冲突），并且有度数上限；
    v3 的边只由 settle 用 str.split() 算中文 Jaccard 产生 → 实测恒为 0 条边，图是空壳。
  · 归档阈值改用「天」为单位的半衰期（v3 用 ACT-R 原始秒数，0.15 的阈值 ≈ 44 秒，
    导致 60 秒前提过的记忆被归档；而只提过 2 次的记忆永远不归档）。
  · 所有状态变更走事件溯源（create / patch / edge），superseded 不再在重启后复活。
  · 新增 due_at（承诺到期）与 entities（建图锚点），供前瞻记忆与扩散使用。
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict, fields as dc_fields
from collections import defaultdict

from . import clock
from .lang import extract_entities, find_date, has_commitment

TYPES = ("event", "fact", "skill", "lesson", "reflection", "rule", "self")
SLICES = ("working", "active", "archive")
EDGE_TYPES = ("temporal", "causal", "cooccur", "rhyme", "conflict", "entail", "entity", "topic")
CONFIDENCE = {"user": 0.9, "agent": 0.45, "web": 0.4, "system": 0.8}

BASE_HALF_LIFE_D = 30.0
EMOTION_HALF_LIFE_D = 7.0
DAY = 86400.0

MAX_DEGREE = 8              # 每节点边数上限（防 O(N²) 爆炸）
ARCHIVE_FRESHNESS = 0.05    # 鲜活度低于此值且无到期承诺 → 归档（约 4.3 个半衰期 ≈ 半年）
KEEP_TYPES = ("rule", "self")   # 规则与自我认知永不归档
WORKING_CAP = 5             # 工作记忆切片容量
WORKING_RECENT_D = 7.0      # 只有最近 7 天被提起过的节点才有资格进工作记忆


def now() -> float:
    return clock.now()


@dataclass
class Node:
    id: str
    label: str
    type: str = "event"
    slice: str = "active"
    importance: float = 0.5
    origin: str = "agent"
    confidence: float = 0.45
    session: str = "S1"
    about_person: bool = False
    gist: str = ""
    verbatim: list = field(default_factory=list)      # 通道2：原话锚点（sacred）
    feeling: dict = None                              # 通道3：情感快照
    sources: list = field(default_factory=list)
    created: float = field(default_factory=now)
    mentions: list = field(default_factory=list)
    status: str = "live"
    supersedes: str = None
    overturn_reason: str = None
    feedback_until: float = 0.0
    hits: int = 0
    # ---- v4 新增 ----
    due_at: float = 0.0
    entities: list = field(default_factory=list)
    review: bool = False                              # 冲突候选，待复核
    last_injected: float = 0.0
    version: int = 1

    def __post_init__(self):
        if self.origin in CONFIDENCE:
            self.confidence = CONFIDENCE[self.origin]
        if not self.gist:
            self.gist = self.label
        if self.type not in TYPES:
            self.type = "fact"

    def touch(self, t: float = None):
        self.mentions.append(t or now())
        self.hits += 1


class Brain:
    def __init__(self, store_dir: str = None, storage=None):
        from .storage import JsonlStorage, NullStorage
        self.nodes: dict[str, Node] = {}
        self.adj: dict[str, list] = defaultdict(list)   # nid -> [(nb, type, w)]
        self.eidx: dict[str, list] = defaultdict(list)  # 实体 -> [nid]（建图用的倒排）
        self.version = 0
        self.turn = 0
        self.store_dir = store_dir
        if storage is not None:
            self.storage = storage
        elif store_dir:
            self.storage = JsonlStorage(store_dir)
        else:
            self.storage = NullStorage()
        self._seq = 0
        self._dirty: set[str] = set()
        self._load()

    # ------------------------------------------------------------ 持久化
    def _load(self):
        events = self.storage.events()
        for ev in events:
            op = ev.get("op")
            if op == "create":
                d = ev.get("node") or {}
                n = self._make_node(d)
                if n:
                    self.nodes[n.id] = n
                    self._index_entities(n)
                    self._bump_seq(n.id)
            elif op == "patch":
                n = self.nodes.get(ev.get("id"))
                if n:
                    for k, v in (ev.get("fields") or {}).items():
                        if hasattr(n, k):
                            setattr(n, k, v)
            elif op == "edge":
                a, b = ev.get("a"), ev.get("b")
                if a in self.nodes and b in self.nodes:
                    self.add_edge(a, b, ev.get("type", "cooccur"),
                                  float(ev.get("w", 0.5)), persist=False)
        self.version += 1

    @staticmethod
    def _make_node(d: dict):
        allowed = {f.name for f in dc_fields(Node)}
        clean = {k: v for k, v in d.items() if k in allowed}
        if "id" not in clean or "label" not in clean:
            return None
        return Node(**clean)

    def _bump_seq(self, nid: str):
        try:
            self._seq = max(self._seq, int(nid.lstrip("n")) + 1)
        except ValueError:
            self._seq += 1

    def _emit(self, ev: dict):
        self.storage.append(ev)
        self.version += 1

    def _touch(self, n: Node, **changes):
        for k, v in changes.items():
            setattr(n, k, v)
        n.version += 1
        self._emit({"op": "patch", "ts": now(), "id": n.id, "fields": changes})

    def save(self):
        """批量落盘：mentions/hits 这类高频变更只在会话结束写一次。"""
        for nid in list(self._dirty):
            n = self.nodes.get(nid)
            if n:
                self.storage.append({"op": "patch", "ts": now(), "id": nid,
                                     "fields": {"mentions": n.mentions, "hits": n.hits,
                                                "last_injected": n.last_injected}})
                self._dirty.discard(nid)
        self.storage.save_snapshot([asdict(n) for n in self.nodes.values()])
        self.version += 1

    # ------------------------------------------------------------ 图操作
    def add_node(self, **kw) -> Node:
        if "id" not in kw:
            kw["id"] = f"n{self._seq:03d}"
            self._seq += 1
        n = Node(**kw)
        self.nodes[n.id] = n
        self.adj.setdefault(n.id, [])
        if not n.mentions:
            n.mentions.append(now())
        if not n.entities:
            n.entities = extract_entities(n.label + " " + n.gist)
        self._index_entities(n)
        if not n.due_at and n.type in ("rule", "event", "fact"):
            due, _raw = find_date(n.label + " " + n.gist)
            if due and has_commitment(n.label + " " + n.gist):
                n.due_at = due
        self.storage.append({"op": "create", "ts": now(), "node": asdict(n)})
        self.version += 1
        return n

    def _index_entities(self, n: Node):
        for e in (n.entities or []):
            if n.id not in self.eidx[e]:
                self.eidx[e].append(n.id)

    def by_entity(self, entity: str, exclude=None) -> list:
        ex = exclude or set()
        return [i for i in self.eidx.get(entity, ())
                if i not in ex and self.nodes.get(i) and self.nodes[i].status == "live"]

    def add_edge(self, a: str, b: str, type: str = "cooccur", w: float = 0.5,
                 persist: bool = True):
        if a == b or a not in self.nodes or b not in self.nodes:
            return
        assert type in EDGE_TYPES, type
        w = max(0.0, min(1.0, float(w)))
        for nb, et, ew in self.adj[a]:
            if nb == b and et == type:
                if abs(ew - w) < 1e-9:
                    return
                self.adj[a] = [(x, y, (max(z, w) if x == b and y == type else z))
                               for x, y, z in self.adj[a]]
                self.adj[b] = [(x, y, (max(z, w) if x == a and y == type else z))
                               for x, y, z in self.adj[b]]
                if persist:
                    self._emit({"op": "edge", "ts": now(), "a": a, "b": b,
                                "type": type, "w": max(w, 0.0)})
                return
        self.adj[a].append((b, type, w))
        self.adj[b].append((a, type, w))
        self._prune(a)
        self._prune(b)
        if persist:
            self._emit({"op": "edge", "ts": now(), "a": a, "b": b, "type": type, "w": w})

    def _prune(self, nid: str):
        """度数上限：保留权重最高的 MAX_DEGREE 条边（侧抑制的结构版）。"""
        lst = self.adj.get(nid, [])
        if len(lst) <= MAX_DEGREE:
            return
        lst.sort(key=lambda x: -x[2])
        keep, drop = lst[:MAX_DEGREE], lst[MAX_DEGREE:]
        self.adj[nid] = keep
        drop_pairs = {(nb, et) for nb, et, _ in drop}
        for nb, et, _w in drop:
            self.adj[nb] = [(x, y, z) for x, y, z in self.adj[nb]
                            if not (x == nid and y == et)]

    def neighbors(self, nid: str) -> list:
        return self.adj.get(nid, [])

    # ------------------------------------------------------------ 状态机
    def mention(self, nid: str, t: float = None):
        n = self.nodes.get(nid)
        if not n:
            return
        n.touch(t)
        self._dirty.add(nid)

    def set_status(self, nid: str, status: str, reason: str = None):
        n = self.nodes.get(nid)
        if not n:
            return
        changes = {"status": status}
        if reason:
            changes["overturn_reason"] = reason
        self._touch(n, **changes)

    def set_slice(self, nid: str, slice_: str):
        n = self.nodes.get(nid)
        if n and n.slice != slice_:
            self._touch(n, slice=slice_)

    def correct(self, match_label: str, new_fact: str, who: str = "user"):
        """改口：唯一匹配才动，多匹配宁可拒绝（P7 绝不虚构）。"""
        hits = [n for n in self.nodes.values()
                if n.status == "live" and match_label in n.label]
        if len(hits) != 1:
            return None, f"唯一匹配失败：命中 {len(hits)} 条，拒绝改口"
        old = hits[0]
        self.set_status(old.id, "superseded", reason=f"{who}: 被新事实更新")
        newn = self.add_node(label=old.label, type=old.type, origin=who,
                             importance=max(0.7, old.importance),
                             about_person=old.about_person,
                             gist=new_fact, verbatim=[new_fact],
                             supersedes=old.id, sources=[old.id, who])
        self._touch(newn, feedback_until=now() + DAY)
        self.add_edge(old.id, newn.id, "conflict", 0.9)
        return newn, "ok"

    def overturn(self, match_label: str, reason: str, who: str = "user"):
        hits = [n for n in self.nodes.values()
                if n.status == "live" and match_label in n.label]
        if len(hits) != 1:
            return None, f"唯一匹配失败：命中 {len(hits)} 条，拒绝推翻"
        n = hits[0]
        self.set_status(n.id, "overturned", reason=f"{who}: {reason}")
        return n, "ok"

    # ------------------------------------------------------------ 动力学
    def half_life(self, n: Node) -> float:
        """H = 30天 × (1+2·重要性) × (0.5+0.5·置信度) → 30~90 天。"""
        return BASE_HALF_LIFE_D * (1 + 2 * n.importance) * (0.5 + 0.5 * n.confidence)

    def freshness(self, n: Node, t: float = None) -> float:
        t = t or now()
        last = max(n.mentions) if n.mentions else n.created
        return 2.0 ** (-max(0.0, t - last) / (self.half_life(n) * DAY))

    def emotion_freshness(self, n: Node, t: float = None) -> float:
        if not n.feeling:
            return 0.0
        t = t or now()
        last = max(n.mentions) if n.mentions else n.created
        return float(n.feeling.get("arousal", 0.5)) * 2.0 ** (
            -max(0.0, t - last) / (EMOTION_HALF_LIFE_D * DAY))

    def base_activation(self, n: Node, t: float = None) -> float:
        """ACT-R 基线激活：时间按「天」归一（v3 用秒，阈值失去物理意义）。"""
        t = t or now()
        s = 0.0
        for m in n.mentions:
            s += max(0.0, (t - m) / DAY) ** -0.5
        return s

    def archive_pass(self, t: float = None) -> int:
        """遗忘 = 可及性下降，不是可用性消失：只改切片，full 卡永远在。"""
        t = t or now()
        moved = 0
        for n in self.nodes.values():
            if n.slice == "archive" or n.status != "live":
                continue
            if n.type in KEEP_TYPES:
                continue
            if n.due_at and n.due_at > t:          # 未到期承诺不归档
                continue
            if self.freshness(n, t) < ARCHIVE_FRESHNESS:
                self.set_slice(n.id, "archive")
                moved += 1
        return moved

    def slice_pass(self, t: float = None) -> int:
        """把「最近 7 天被提起过」的最活跃节点放进 working 切片（容量 5）。

        v3/v4.0 里 working 切片永远是 0——三切片名存实亡。工作记忆的语义是
        "此刻在嘴边的东西"，所以判据是最近性 × 显著性，不是单纯的重要性。
        """
        t = t or now()
        cands = []
        for n in self.nodes.values():
            if n.status != "live" or n.slice == "archive":
                continue
            last = max(n.mentions) if n.mentions else n.created
            recent = (t - last) <= WORKING_RECENT_D * DAY
            if not recent:
                if n.slice == "working":
                    self.set_slice(n.id, "active")
                continue
            cands.append((self.freshness(n, t) * (0.5 + n.importance), n))
        cands.sort(key=lambda x: -x[0])
        changed = 0
        for i, (_s, n) in enumerate(cands):
            want = "working" if i < WORKING_CAP else "active"
            if n.slice != want:
                self.set_slice(n.id, want)
                changed += 1
        return changed
