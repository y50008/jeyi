"""score.py — 检索打分 v4.0：倒排索引 + tf-idf 余弦门控 + 真·激活扩散

v3 的三处结构性错误，这里逐个修：
  1. idf 在 N=0 时恒为 0 → C 恒为 0 → 首轮检索全空。改为 BM25 平滑 idf，N=0 也不塌。
  2. C = s/(s+8) 会饱和，单个关键词命中恒低于 0.2 地板 → 正确记忆被切掉。
     改为 tf-idf 余弦：与查询长度、语料规模无关，阈值 0.06 可标定。
  3. seed = 所有 C>0 的节点 → 每个 seed 的 graph 恒为 1.0 → (1+0.3·graph) 是常数，
     扩散/侧抑制全是死代码。改为：seed 只取 top-k；扩散质量只给「非 seed」节点，
     且 graph 可以独立把节点拉过门控（这才是「提到相关就能牵出相关」）。

性能：倒排索引把每轮成本从 O(N) 降到 O(候选数)。v3 在 3000 节点时 44ms/轮，v4 实测见 bench。
"""
from __future__ import annotations
import math
from . import clock
from .adapters import cosine
from .lang import tokenize, expand, ALIASES

# ---- 标定常数（全部可通过 bench.py 重新校准）----
DENSE_W = 0.35          # 有 embedding 时语义分权重
IDF_SMOOTH = 0.5        # BM25 平滑
REL_FLOOR = 0.08        # 词面相关度硬门控（余弦）：低于此值且没有强图连接 → 不进候选
GRAPH_ADMIT = 0.22      # 只有强连接（同句共现/同一稀有实体）才能"凭图入候选"
GRAPH_BONUS = 0.35      # 图的排序加成
SEED_K = 5              # 扩散种子数
ARCHIVE_PENALTY = 0.55  # 显式回忆时归档节点的排序惩罚（能翻出来，但排在活跃记忆之后）
EXPAND_W = 1.0          # 别名扩展词权重。实测降到 0.5 反而掉主集分（"年纪→岁"需要等权），保持等权
SPREAD_DECAY = 0.6      # 逐跳衰减
SPREAD_TOPK = 3         # 每跳只有 top-3 获得传播资格（侧抑制）
# 只有「语义边」参与扩散：会话时序边会把整条会话串成链，
# 让每个节点都成为 1 跳邻居（v4 早期实测：一个临时序边就能把无关记忆拉过门控）。
SPREAD_EDGE_TYPES = ("entity", "cooccur", "entail", "topic", "conflict")


class Index:
    """倒排索引：postings[token][nid] = tf。增量维护，按节点版本同步。"""

    def __init__(self):
        self.postings: dict[str, dict[str, int]] = {}
        self.doclen: dict[str, int] = {}
        self.N = 0
        self.total_len = 0

    # ---- 构建 ----
    def add(self, nid: str, tokens: list):
        if nid in self.doclen:              # 已存在则先移除，保证幂等
            self.remove(nid)
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        for t, c in counts.items():
            self.postings.setdefault(t, {})[nid] = c
        dl = len(tokens)
        self.doclen[nid] = dl
        self.total_len += dl
        self.N += 1

    def remove(self, nid: str):
        dl = self.doclen.pop(nid, None)
        if dl is None:
            return
        self.total_len -= dl
        self.N -= 1
        for t, plist in list(self.postings.items()):
            if nid in plist:
                del plist[nid]
                if not plist:
                    del self.postings[t]

    @property
    def avgdl(self) -> float:
        return (self.total_len / self.N) if self.N else 1.0

    def df(self, t: str) -> int:
        return len(self.postings.get(t, ()))

    def idf(self, t: str) -> float:
        """BM25 概率 idf：N=0 时返回一个小的正数，不塌成 0。"""
        df = self.df(t)
        n = max(self.N, 1)
        return math.log(1.0 + (n - df + IDF_SMOOTH) / (df + IDF_SMOOTH))

    def candidates(self, qtoks: list, limit: int = 5000) -> set:
        """只取命中查询词的节点；按 idf 从高到低扫，避免长尾爆炸。"""
        toks = sorted(set(qtoks), key=lambda t: -self.idf(t))
        out: set[str] = set()
        for t in toks:
            plist = self.postings.get(t)
            if not plist:
                continue
            out.update(plist.keys())
            if len(out) >= limit:
                break
        return out

    def tf(self, nid: str, t: str) -> int:
        return self.postings.get(t, {}).get(nid, 0)


class Scorer:
    def __init__(self, brain, embedding=None):
        self.brain = brain
        self.embedding = embedding
        self.index = Index()
        self._known: set[str] = set()
        self._veccache: dict[str, list] = {}
        self._seen_version = -1

    # ---- 索引同步（增量，O(新增节点)）----
    def _sync(self):
        b = self.brain
        if b.version == self._seen_version:
            return
        for nid, n in b.nodes.items():
            if nid not in self._known:
                self.index.add(nid, tokenize(self._text(n)))
                self._known.add(nid)
        self._seen_version = b.version

    @staticmethod
    def _text(n) -> str:
        return " ".join([n.label, n.gist, " ".join(n.verbatim), " ".join(n.entities or [])])

    def _rebuild(self):
        """全量重建（加载后/结算后调用；测试也用它作为显式契约）。"""
        self.index = Index()
        self._known.clear()
        self._veccache.clear()
        for nid, n in self.brain.nodes.items():
            if n.status != "live":
                continue
            self.index.add(nid, tokenize(self._text(n)))
            self._known.add(nid)
        self._seen_version = self.brain.version

    # ---- 相关度 ----
    def _weights(self, n) -> dict:
        """tf-idf 权重（次线性 tf）。"""
        toks = tokenize(self._text(n))
        if not toks:
            return {}
        counts: dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        return {t: (1.0 + math.log(c)) * self.index.idf(t) for t, c in counts.items()}

    def query_weights(self, tokens: list) -> dict:
        """查询的 tf-idf 权重；别名扩展词降权（EXPAND_W）。

        为什么降权：扩展是在猜。给猜出来的同义词和原词一样的权重，
        会把查询向量拉偏——「我最烦工作里的什么」扩出 上班/公司/讨厌/不喜欢 后，
        真正命中的"讨厌"反而被稀释到门控线下（留出集实测 gold rel 只有 0.039）。
        """
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        qw = {t: (1.0 + math.log(c)) * self.index.idf(t) for t, c in counts.items()}
        for t in counts:
            for a in ALIASES.get(t, ()):
                if a not in qw:
                    qw[a] = EXPAND_W * self.index.idf(a)
        return qw

    def rel(self, qw: dict, n) -> float:
        """tf-idf 余弦 ∈ [0,1]。与查询长度无关，可跨语料规模标定。

        qw 是 query_weights() 的输出（原始词 + 降权的扩展词）。
        为兼容旧调用，传入 token 列表时会自动折算。
        """
        if not qw:
            return 0.0
        if not isinstance(qw, dict):
            qw = self.query_weights(list(qw))
        qnorm = math.sqrt(sum(v * v for v in qw.values())) or 1.0
        dw = self._weights(n)
        if not dw:
            return 0.0
        dnorm = math.sqrt(sum(v * v for v in dw.values())) or 1.0
        dot = 0.0
        for t, w in qw.items():
            if t in dw:
                dot += w * dw[t]
        return dot / (qnorm * dnorm)

    # ---- 扩散激活（v4：只给非种子节点加质量，且能独立过门控）----
    def spread(self, seeds: list, max_hop: int = 2) -> dict:
        """返回 {nid: 原始扩散质量}，未经归一化。

        v3 把扩散质量除以最大值 → 任何 1 跳邻居都变成 1.0，等价于"和种子一样相关"，
        于是无关记忆被拉进注入包。这里保留绝对量纲：边权 × 逐跳衰减，
        一个 0.45 的边只能贡献 0.27，远低于一次真正的词面命中。
        """
        out: dict[str, float] = {}
        seen = set(seeds)
        frontier = [(s, 1.0) for s in seeds]
        hop = 0
        while frontier and hop < max_hop:
            gain: dict[str, float] = {}
            for nid, v in frontier:
                for nb, et, w in self.brain.adj.get(nid, []):
                    if nb in seen or et not in SPREAD_EDGE_TYPES:
                        continue
                    # 取 max 而不是 +=：一个节点同时连到 5 个种子时，
                    # 累加会让"枢纽节点"拿到 5 倍质量、盖过真正的词面命中。
                    gain[nb] = max(gain.get(nb, 0.0), v * w)
            frontier = [(nid, g * SPREAD_DECAY)
                        for nid, g in sorted(gain.items(), key=lambda x: -x[1])[:SPREAD_TOPK]]
            for nid, v in frontier:
                seen.add(nid)
                out[nid] = max(out.get(nid, 0.0), v)
            hop += 1
        return out

    # ---- 主入口 ----
    def rank(self, query: str, k: int = 8, mood: float = 0.0,
             include_archive: bool = False) -> list:
        """include_archive=True 时把归档节点也纳入（带惩罚），用于"你还记得…吗"这类显式回忆。

        设计原则是"遗忘=可及性下降，不是可用性消失"：常规检索不翻归档，
        但用户点名回忆时必须能翻出来——否则就是真正的遗忘。
        """
        self._sync()
        qtoks = tokenize(query)
        qw = self.query_weights(qtoks)
        qvec = self.embedding.embed(query) if self.embedding else None
        cand = self.index.candidates(list(qw))
        # 候选：命中索引的 + 有 feeling 文本命中的（情感押韵通道）
        scored: dict[str, dict] = {}
        for nid in cand:
            n = self.brain.nodes.get(nid)
            if n is None or n.status != "live":
                continue
            if n.slice == "archive" and not include_archive:
                continue
            base = self.rel(qw, n)
            dense = 0.0
            if self.embedding and qvec is not None:
                nv = self._nodevec(n)
                if nv:
                    dense = max(0.0, cosine(qvec, nv))
            rel = (1 - DENSE_W) * base + DENSE_W * dense if self.embedding else base
            scored[nid] = {"rel": rel, "sparse": base, "dense": dense}

        # 种子 = 纯相关度 top-k（不掺入重要性，避免"重要但不相关"污染扩散源）
        seeds = [x["id"] for x in
                 sorted(({"id": nid, **v} for nid, v in scored.items()),
                        key=lambda x: -x["rel"])[:SEED_K]]
        mass = self.spread(seeds)
        for nid, g in mass.items():
            n = self.brain.nodes.get(nid)
            if n is None or n.status != "live":
                continue
            if n.slice == "archive" and not include_archive:
                continue
            cur = scored.setdefault(nid, {"rel": 0.0, "sparse": 0.0, "dense": 0.0})
            cur["graph"] = g

        out = []
        now = clock.now()
        for nid, v in scored.items():
            n = self.brain.nodes[nid]
            rel = v["rel"]
            g = v.get("graph", 0.0)
            # 门控：词面够近，或图连接足够强（同句共现级），二者取一
            if rel < REL_FLOOR and g < GRAPH_ADMIT:
                continue
            A = self.brain.freshness(n, now)
            E = self.brain.emotion_freshness(n, now)
            I = n.importance * (0.6 + 0.4 * n.confidence)
            prior = 0.45 * I + 0.30 * A + 0.25 * E
            align = 1.0
            if n.feeling:
                align = 1 + 0.12 * float(n.feeling.get("valence", 0)) * mood
            penalty = ARCHIVE_PENALTY if n.slice == "archive" else 1.0
            score = (0.65 * rel + 0.25 * prior + GRAPH_BONUS * g) * align * penalty
            out.append({"id": nid, "label": n.label, "score": round(score, 4),
                        "_graph_only": rel < REL_FLOOR,
                        "decomp": {"rel": round(rel, 3), "sparse": round(v["sparse"], 3),
                                   "dense": round(v["dense"], 3), "graph": round(g, 3),
                                   "I": round(I, 3), "A": round(A, 3), "E": round(E, 3),
                                   "align": round(align, 3)}})
        # 词面命中永远排在"纯靠扩散进来"的候选之前：扩散是补召回，不是抢排名。
        # 否则连了很多种子的枢纽节点会以 rel=0 压过真正的命中（留出集实测）。
        out.sort(key=lambda x: (x["_graph_only"], -x["score"]))
        for x in out:
            x.pop("_graph_only", None)
        return out[:k]

    def _nodevec(self, n):
        if not self.embedding:
            return None
        if n.id not in self._veccache:
            self._veccache[n.id] = self.embedding.embed(self._text(n))
        return self._veccache[n.id]
