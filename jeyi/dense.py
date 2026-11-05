"""dense.py — 可选的稠密检索层（不装依赖时引擎退化为纯词法，一切照常）

这一层解决的是 v4.1 留出集暴露的天花板：纯词法 77.8%，
剩下的失败全是「宠物→猫」「饮料→咖啡」「毛病→颈椎不好」这类改写，词面零重合。

设计原则（"最实惠"的落点）：
  · 核心库仍然零依赖：本模块只在 `numpy + onnxruntime + tokenizers` 可用时才加载；
  · 模型用 bge-small-zh-v1.5 的 int8 量化版（约 24MB，CPU 3-8ms/句），
    不联网、不要 GPU、不要 API key；
  · 想用远程 embedding 也行（OpenAICompatibleEmb），接口完全一致。

装法：
    pip install numpy onnxruntime tokenizers
    python tools/setup_dense.py            # 下载模型 + 自检 + 跑一遍留出集
"""
from __future__ import annotations
import json
import math
import os
import urllib.request

_CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".cache", "jeyi")

# 同一套 bge-small 的两个语言版本，都是 24MB int8。
# 关键：**模型语言必须和语料语言一致**。用中文模型跑英文基准，
# 稠密几乎不涨分（LoCoMo 实测 48.6% → 49.0%），换英文同款才有意义。
MODEL_REPOS = {
    "zh": ("Xenova/bge-small-zh-v1.5", "bge-small-zh-v1.5"),
    "en": ("Xenova/bge-small-en-v1.5", "bge-small-en-v1.5"),
}
DEFAULT_KIND = "zh"
DEFAULT_MODEL_DIR = os.path.join(_CACHE_ROOT, MODEL_REPOS[DEFAULT_KIND][1])
MODEL_FILES = ("model_int8.onnx", "model_quantized.onnx", "model.onnx")   # 由省到贵依次尝试
_UA = {"User-Agent": "Mozilla/5.0 jeyi/dense"}


def model_dir_for(kind: str = DEFAULT_KIND) -> str:
    return os.path.join(_CACHE_ROOT, MODEL_REPOS.get(kind, MODEL_REPOS[DEFAULT_KIND])[1])

# bge 中文检索的查询指令前缀（s2p）。短问句上加前缀略掉点时收益为负，默认关闭，
# 由 setup_dense.py 自检决定要不要开——见 tools/setup_dense.py 的 calibration。
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


# ---------------------------------------------------------------- 向量索引
class DenseIndex:
    """id ⇄ 向量矩阵 + 余弦 top-k。有 numpy 走矩阵乘，没有则退化为纯 Python 线性扫描。"""

    def __init__(self, max_pure_python: int = 2000):
        self.ids: list[str] = []
        self._vecs: list = []
        self._rows: dict[str, int] = {}
        self._mat = None            # numpy 矩阵（惰性重建）
        self._dirty = True
        self.dim = 0
        self.max_pure_python = max_pure_python
        self.np = None
        try:
            import numpy as _np
            self.np = _np
        except ImportError:
            self.np = None

    def __len__(self):
        return len(self.ids)

    @property
    def backend(self) -> str:
        return "numpy" if self.np is not None else "pure-python"

    def add(self, nid: str, vec) -> None:
        if self.np is not None:
            v = self.np.asarray(vec, dtype="float32")
        else:
            v = [float(x) for x in vec]
        if nid in self._rows:
            self._vecs[self._rows[nid]] = v
        else:
            self._rows[nid] = len(self.ids)
            self.ids.append(nid)
            self._vecs.append(v)
        self.dim = len(v)
        self._dirty = True

    def remove(self, nid: str) -> None:
        i = self._rows.pop(nid, None)
        if i is None:
            return
        self.ids.pop(i)
        self._vecs.pop(i)
        self._rows = {n: k for k, n in enumerate(self.ids)}
        self._dirty = True

    def _matrix(self):
        if self.np is None or not self._dirty:
            return self._mat
        if not self._vecs:
            self._mat = None
        else:
            m = self.np.vstack(self._vecs).astype("float32")
            # 预归一化：查询向量也归一化后，余弦 = 点积
            norms = self.np.linalg.norm(m, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._mat = m / norms
        self._dirty = False
        return self._mat

    def search(self, qvec, k: int = 50, allowed=None):
        """返回 [(nid, cosine)]，按余弦降序。allowed 可传入允许的 id 集合做过滤。"""
        if not self.ids or qvec is None:
            return []
        if self.np is not None:
            m = self._matrix()
            if m is None:
                return []
            q = self.np.asarray(qvec, dtype="float32")
            nq = float(self.np.linalg.norm(q)) or 1.0
            sims = m @ (q / nq)
            if allowed is not None:
                mask = self.np.array([1 if i in allowed else 0 for i in self.ids])
                sims = sims * mask - (1 - mask) * 1e9
            k = min(k, len(self.ids))
            idx = self.np.argpartition(-sims, k - 1)[:k]
            idx = idx[self.np.argsort(-sims[idx])]
            return [(self.ids[int(i)], float(sims[int(i)])) for i in idx]
        # 纯 Python 兜底：只支持小库
        if len(self.ids) > self.max_pure_python:
            raise RuntimeError(
                f"没有 numpy，纯 Python 扫描被限制在 {self.max_pure_python} 条以内"
                f"（当前 {len(self.ids)}）。装 numpy 或改用词法检索。")
        q = [float(x) for x in qvec]
        nq = math.sqrt(sum(x * x for x in q)) or 1.0
        out = []
        for nid, v in zip(self.ids, self._vecs):
            if allowed is not None and nid not in allowed:
                continue
            nv = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append((nid, sum(a * b for a, b in zip(q, v)) / (nq * nv)))
        out.sort(key=lambda x: -x[1])
        return out[:k]


# ---------------------------------------------------------------- 本地 ONNX 嵌入
class OnnxEmbedder:
    """bge-small-zh-v1.5（或其他 BERT 类句向量）ONNX 推理，CLS pooling + L2 归一化。

    纯 CPU。24MB 的 int8 模型在普通笔记本上单句 3~8ms；一年 1 万条记忆全量建索引约 1 分钟。
    """

    semantic = True

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR, max_len: int = 128,
                 query_prefix: str = "", threads: int = 0):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.dir = model_dir
        self.max_len = max_len
        self.query_prefix = query_prefix or ""
        tok_path = os.path.join(model_dir, "tokenizer.json")
        if not os.path.exists(tok_path):
            raise FileNotFoundError(f"缺少 {tok_path}，先跑 python tools/setup_dense.py")
        self.tok = Tokenizer.from_file(tok_path)
        self.tok.enable_truncation(max_length=max_len)
        # 按批内最长补齐，而不是一律补到 max_len：记忆条目大多 <30 字，
        # 这一项能把建索引和查询耗时降到约 1/4。
        self.tok.enable_padding(pad_id=0, pad_token="[PAD]")

        onnx_path = None
        for name in MODEL_FILES:
            p = os.path.join(model_dir, name)
            if os.path.exists(p):
                onnx_path = p
                break
        if onnx_path is None:
            raise FileNotFoundError(f"{model_dir} 下找不到 model*.onnx")
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        self.dim = int(self.sess.get_outputs()[0].shape[-1])

    def embed(self, text: str):
        return self.embed_batch([text])[0]

    def embed_batch(self, texts):
        import numpy as np
        encs = self.tok.encode_batch([t or " " for t in texts])
        ids = np.asarray([e.ids for e in encs], dtype="int64")
        mask = np.asarray([e.attention_mask for e in encs], dtype="int64")
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self.sess.run(None, feeds)[0]
        cls = hidden[:, 0, :]                       # bge 用 CLS pooling
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (cls / norms).astype("float32").tolist()

    def embed_query(self, text: str):
        """检索用查询向量：按需加 bge 的 s2p 指令前缀。"""
        return self.embed(self.query_prefix + (text or ""))


# ---------------------------------------------------------------- 自动装配
def available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
        return True
    except ImportError:
        return False


def model_present(model_dir: str = None, kind: str = DEFAULT_KIND) -> bool:
    model_dir = model_dir or model_dir_for(kind)
    return (os.path.isdir(model_dir)
            and os.path.exists(os.path.join(model_dir, "tokenizer.json"))
            and any(os.path.exists(os.path.join(model_dir, n)) for n in MODEL_FILES))


def make_embedder(model_dir: str = None, query_prefix: str = "", kind: str = DEFAULT_KIND):
    """能装就装，装不上返回 None（引擎自动退回词法）。"""
    model_dir = model_dir or model_dir_for(kind)
    if not available() or not model_present(model_dir):
        return None
    try:
        return OnnxEmbedder(model_dir, query_prefix=query_prefix)
    except Exception:
        return None


def download(kind: str = DEFAULT_KIND, model_dir: str = None, quantized: bool = True,
             endpoint: str = "https://hf-mirror.com") -> str:
    """下载 bge-small 的 ONNX 与 tokenizer（默认走 hf-mirror，国内可用）。

    kind: "zh"（中文语料）/ "en"（英文语料）——**跑什么语言的语料就用什么模型**。
    int8 量化版约 24MB、CPU 单句 2.8ms，精度相对 fp32 只差不到 1 个点——
    "最实惠"就落在这一档：不要 GPU、不联网、不要 API key。
    """
    repo, sub = MODEL_REPOS.get(kind, MODEL_REPOS[DEFAULT_KIND])
    model_dir = model_dir or os.path.join(_CACHE_ROOT, sub)
    os.makedirs(model_dir, exist_ok=True)
    files = ["tokenizer.json", "config.json"]
    model_file = "onnx/model_int8.onnx" if quantized else "onnx/model.onnx"
    files.append(model_file)
    for rel in files:
        url = f"{endpoint.rstrip('/')}/{repo}/resolve/main/{rel}"
        out = os.path.join(model_dir, os.path.basename(rel))
        if os.path.exists(out) and os.path.getsize(out) > 1024:
            continue
        tmp = out + ".part"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
            total, done = 0, 0
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {os.path.basename(rel)} {done*100//total}%", end="")
        print()
        os.replace(tmp, out)
    return model_dir


if __name__ == "__main__":
    print("dense 可用:", available(), "| 模型在位:", model_present())
    e = make_embedder()
    print("embedder:", type(e).__name__ if e else None)
    if e:
        v = e.embed("我对花生过敏")
        print("dim:", len(v), "样例前 5 维:", [round(x, 3) for x in v[:5]])
