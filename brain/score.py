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
# ---- 稠密检索（只有装了 embedding 才生效）----
DENSE_TOP_K = 50        # 稠密召回条数
DENSE_FLOOR = 0.58      # 纯稠密命中进候选的最低余弦（按弃答分布标定）
DENSE_RRF_K = 10        # RRF 平滑常数：越小越看重头部名次
# 稠密票权 < 1：两路检索器不是同等可靠。词法在"有原词"时几乎不会错，
# 稠密在"没原词"时才发光；等权 RRF 会让稠密的误判推翻词法的正确第 1 名
# （LoCoMo 实测：等权 50.5% < 词法 54.2% < 加权 0.5 的 55.1%）。
DENSE_WEIGHT = 0.5


def _rrf(rank_a, rank_b, k: int = None, w_dense: float = None) -> float:
    """加权 Reciprocal Rank Fusion，归一化到 [0,1]（两路都排第 1 时为 1.0）。

    为什么不用分数融合：稠密余弦与 tf-idf 余弦不在一个尺度上，
    取 max 会让稠密单方面覆盖词法的正确排序（主集实测 96.2 → 86.8）。
    为什么还要加权：稠密单独在 LoCoMo 上只有 30.6%（词法 54.2%），
    给它同等票权就是让它拖后腿。
    """
    k = DENSE_RRF_K if k is None else k
    w_dense = DENSE_WEIGHT if w_dense is None else w_dense
    s = (1.0 / (k + rank_a)) if rank_a else 0.0
    d = (w_dense / (k + rank_b)) if rank_b else 0.0
    return (s + d) * (k + 1) / (1.0 + w_dense)


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
        self._seen_version = -1
        # 稠密索引：只有装了 embedding 才存在（否则整个稠密路径不参与，零开销）
        self.dense = None
        if embedding is not None and getattr(embedding, "semantic", True):
            try:
                from .dense import DenseIndex
                self.dense = DenseIndex()
            except Exception:
                self.dense = None

    # ---- 索引同步（增量，O(新增节点)）----
    def _sync(self):
        b = self.brain
        if b.version == self._seen_version:
            return
        new_ids = [nid for nid in b.nodes if nid not in self._known]
        for nid in new_ids:
            self.index.add(nid, tokenize(self._text(b.nodes[nid])))
            self._known.add(nid)
        if self.dense is not None and new_ids:
            texts = [self._dense_text(b.nodes[nid]) for nid in new_ids]
            vecs = self._embed_batch(texts)
            for nid, v in zip(new_ids, vecs):
                if v:
                    self.dense.add(nid, v)
        self._seen_version = b.version

    def _embed_batch(self, texts):
        """优先走批接口（ONNX 批推理快一个量级），退化为逐条。"""
        emb = self.embedding
        if hasattr(emb, "embed_batch"):
            try:
                return emb.embed_batch(texts)
            except Exception:
                pass
        return [emb.embed(t) for t in texts]

    def _embed_query(self, text: str):
        emb = self.embedding
        if emb is None:
            return None
        if hasattr(emb, "embed_query"):
            try:
                return emb.embed_query(text)
            except Exception:
                pass
        return emb.embed(text)

    @staticmethod
    def _text(n) -> str:
        """词法索引用的文本：多留几路词面（label/gist/verbatim/entities）能提高召回。"""
        return " ".join([n.label, n.gist, " ".join(n.verbatim), " ".join(n.entities or [])])

    @staticmethod
    def _dense_text(n) -> str:
        """稠密向量用的文本：去重后的干净表述。

        自动抽取的节点里 label ≈ gist ≈ verbatim（同一句话三遍），
        把它整段喂给向量模型会让句子加权重复、语义被稀释；
        gist 已经是去掉口语填充词的版本，用它 + 实体就够。
        """
        parts = []
        for s in (n.gist, n.label):
            s = (s or "").strip()
            if not s:
                continue
            # 一条是另一条的前缀时只留长的（导入的节点 label=text[:80]、gist=text[:160]）
            if any(s == p or s.startswith(p) or p.startswith(s) for p in parts):
                if parts and len(s) > len(parts[0]):
                    parts[0] = s
                continue
            parts.append(s)
        for e in (n.entities or [])[:3]:
            if e and e not in parts and e not in n.gist:
                parts.append(e)
        return " ".join(parts) or n.label

    def _rebuild(self):
        """全量重建（加载后/结算后调用；测试也用它作为显式契约）。"""
        self.index = Index()
        self._known.clear()
        live = [(nid, n) for nid, n in self.brain.nodes.items() if n.status == "live"]
        for nid, n in live:
            self.index.add(nid, tokenize(self._text(n)))
            self._known.add(nid)
        if self.dense is not None:
            from .dense import DenseIndex
            self.dense = DenseIndex()
            if live:
                vecs = self._embed_batch([self._dense_text(n) for _nid, n in live])
                for (nid, _n), v in zip(live, vecs):
                    if v:
                        self.dense.add(nid, v)
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
             include_archive: bool = False, scopes=None) -> list:
        """include_archive=True 时把归档节点也纳入（带惩罚），用于"你还记得…吗"这类显式回忆。

        设计原则是"遗忘=可及性下降，不是可用性消失"：常规检索不翻归档，
        但用户点名回忆时必须能翻出来——否则就是真正的遗忘。

        scopes: 当前可见的会话集合（如 {"S3"}）。
          · scope="user"   的记忆任何会话都可见；
          · scope="session" 的记忆只在它出现过的会话里可见——这是防"会话间干扰"的那道闸。
          scopes=None 表示不做作用域过滤（评测/调试用）。
        """
        self._sync()
        qtoks = tokenize(query)
        qw = self.query_weights(qtoks)
        qvec = self._embed_query(query) if self.embedding else None
        scope_set = set(scopes) if scopes is not None else None

        def visible(n) -> bool:
            if n.scope != "session" or scope_set is None:
                return True
            return bool(set(n.sessions) & scope_set) or n.session in scope_set

        # ---- 词法一路 ----
        sparse: dict[str, float] = {}
        for nid in self.index.candidates(list(qw)):
            n = self.brain.nodes.get(nid)
            if n is None or n.status != "live":
                continue
            if n.slice == "archive" and not include_archive:
                continue
            if not visible(n):
                continue
            sparse[nid] = self.rel(qw, n)

        # ---- 稠密一路：扫全库，不受词法候选集限制（「宠物→猫」能命中的前提）----
        dense_cos: dict[str, float] = {}
        if self.dense is not None and qvec is not None and len(self.dense):
            for nid, sim in self.dense.search(qvec, k=DENSE_TOP_K):
                n = self.brain.nodes.get(nid)
                if n is None or n.status != "live":
                    continue
                if n.slice == "archive" and not include_archive:
                    continue
                if not visible(n):
                    continue
                dense_cos[nid] = max(0.0, sim)

        # ---- 名次融合（RRF）----
        s_order = sorted(sparse, key=lambda x: -sparse[x])
        d_order = sorted(dense_cos, key=lambda x: -dense_cos[x])
        r_s = {nid: i + 1 for i, nid in enumerate(s_order)}
        r_d = {nid: i + 1 for i, nid in enumerate(d_order)}
        scored: dict[str, dict] = {}
        for nid in set(sparse) | set(dense_cos):
            s_rel = sparse.get(nid, 0.0)
            d_cos = dense_cos.get(nid, 0.0)
            # 入池条件：有任何词面信号（再弱也要留着当扩散桥），或稠密已过线。
            # 低于线的稠密候选必须丢弃——它是"topically 相近但没答上"的那一类，
            # 留在池里会当种子扩散出邻居，把弃答题全灌满（实测弃答 100% → 0%）。
            if s_rel <= 0.0 and d_cos < DENSE_FLOOR:
                continue
            scored[nid] = {"rel": _rrf(r_s.get(nid), r_d.get(nid)),
                           "sparse": s_rel, "dense": d_cos}

        # 种子 = 两路各自的强信号 top-k。这里**不能**先按门控筛：
        # 弱词面命中（「我家猫身体…」只命中一个"猫"字）恰恰是多跳扩散的桥，
        # 提前筛掉它，图扩散就永远起不了步（v4.1 的 test_graph_diffusion_multihop）。
        seed_score = {nid: max(v["sparse"], v["dense"]) for nid, v in scored.items()}
        seeds = sorted(seed_score, key=lambda x: -seed_score[x])[:SEED_K]
        mass = self.spread(seeds)
        for nid, g in mass.items():
            n = self.brain.nodes.get(nid)
            if n is None or n.status != "live":
                continue
            if n.slice == "archive" and not include_archive:
                continue
            if not visible(n):          # 扩散也不能把别的会话的私有记忆拉进来
                continue
            cur = scored.setdefault(nid, {"rel": 0.0, "sparse": 0.0, "dense": 0.0})
            cur["graph"] = g

        out = []
        now = clock.now()
        for nid, v in scored.items():
            n = self.brain.nodes[nid]
            rel = v["rel"]                  # RRF 融合分，只用于排序
            g = v.get("graph", 0.0)
            by_lex = v["sparse"] >= REL_FLOOR
            by_dense = v["dense"] >= DENSE_FLOOR
            # 准入：词法够近、稠密够自信、图连接够强，三者取一。
            # 注意 RRF 分不能拿来当门槛——它是名次归一化值，第一名恒为 0.5 起。
            if not (by_lex or by_dense or g >= GRAPH_ADMIT):
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
                        "_graph_only": not (by_lex or by_dense),
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

