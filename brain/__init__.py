"""memory-brain — v4.0 全图底座记忆引擎（agent 插件）

发现现成的，超越现成的——超越必须接受检验。
"""
from .core import Brain, Node, CONFIDENCE, TYPES, EDGE_TYPES
from .score import Scorer, Index
from .pack import build as build_pack
from .persona import Persona, Mood
from .hooks import MemoryHooks
from .settle import settle, resonate
from .storage import JsonlStorage, SqliteStorage, NullStorage
from .adapters import (HashingEmbedding, OpenAICompatibleEmb, cosine,
                       HeuristicJudge, OpenAICompatibleLLM)
from .lang import tokenize, jaccard

__version__ = "4.5.0"
__all__ = [
    "Brain", "Node", "Scorer", "Index", "Persona", "Mood", "MemoryHooks",
    "settle", "resonate", "build_pack", "tokenize", "jaccard",
    "JsonlStorage", "SqliteStorage", "NullStorage",
    "HashingEmbedding", "OpenAICompatibleEmb", "OpenAICompatibleLLM",
    "HeuristicJudge", "cosine", "CONFIDENCE", "TYPES", "EDGE_TYPES",
]
