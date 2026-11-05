"""adapters.py — 生产适配层（README 诚实声明 ①②的实装）

Embedding:
  HashingEmbedding      本地确定性哈希向量（零依赖，演示质量，可离线）
  OpenAICompatibleEmb   任何 OpenAI 兼容 /v1/embeddings 端点（urllib，无第三方包）
LLM:
  HeuristicJudge        本地启发式冲突复核（默认，零依赖）
  OpenAICompatibleLLM   远程复核 / 反思生成
"""
from __future__ import annotations
import hashlib, json, math, urllib.request

def _l2norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

class HashingEmbedding:
    """char-bigram 特征哈希 → 定长向量。确定性、零依赖、可复现。

    诚实说明：它是词袋哈希，不是语义模型。能提升"字面相近但词序不同"的召回，
    不能把「饮料」和「铁观音」连起来——那需要真模型（见 OpenAICompatibleEmb）。
    """
    def __init__(self, dim=128): self.dim = dim
    def embed(self, text: str) -> list:
        from .lang import tokenize
        v = [0.0] * self.dim
        for tok in set(tokenize(text)):
            h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
            x = int.from_bytes(h, "big")
            v[x % self.dim] += 1.0 if (x >> 63) == 0 else -1.0
        return _l2norm(v)

def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))

class OpenAICompatibleEmb:
    def __init__(self, api_base, api_key, model, timeout=30):
        self.url = api_base.rstrip("/") + "/v1/embeddings"
        self.key, self.model, self.timeout = api_key, model, timeout
    def embed(self, text: str) -> list:
        req = urllib.request.Request(self.url, method="POST", data=json.dumps(
            {"model": self.model, "input": text}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["data"][0]["embedding"]

class HeuristicJudge:
    """冲突复核：词面 Jaccard 复核（中文必须用 lang.tokenize，不能用 split）。

    默认判据收敛到「高度重合但表述不同才可疑」——它只是把候选交给人/LLM 的筛子，
    不是结论。真判定请换 OpenAICompatibleLLM。
    """
    def __init__(self, threshold=0.45):
        self.threshold = threshold
    def review_conflict(self, gist_a: str, gist_b: str) -> bool:
        from .lang import tokenize, jaccard
        if not gist_a or not gist_b:
            return False
        return jaccard(tokenize(gist_a), tokenize(gist_b)) >= self.threshold

class OpenAICompatibleLLM:
    def __init__(self, api_base, api_key, model, timeout=60):
        self.url = api_base.rstrip("/") + "/v1/chat/completions"
        self.key, self.model, self.timeout = api_key, model, timeout
    def _chat(self, prompt: str) -> str:
        req = urllib.request.Request(self.url, method="POST", data=json.dumps(
            {"model": self.model, "messages": [{"role": "user", "content": prompt}],
             "temperature": 0}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    def review_conflict(self, gist_a: str, gist_b: str) -> bool:
        out = self._chat(f"以下两条记忆是否矛盾？只回答 YES 或 NO。\nA: {gist_a}\nB: {gist_b}")
        return "YES" in out.upper()
