"""pack.py — 注入包选择 v4.0（把"打分"和"选包"分开，这才是省 token 的真正位置）

v3 的 _inject 有四个问题，这里全部重做：
  1. 检索为空时按插入顺序塞最早 3 条 person 记忆 → 300 字符预算被无关内容吃满。
     → 改为：相关度优先；空检索时最多 1 条「最显著」锚点，且仅在社交性轮次。
  2. 每轮无脑重复注入同一批记忆（实测 10 轮只出现 3 条、重复 30 次）。
     → 会话级去重：注入过的记忆不再重复（模型上下文里已经有了），超长会话 8 轮后放行。
  3. person floor 用 [:3-n]，n≥3 时变成负切片，注入条数失控（实测 cap=3 却给 7 条）。
     → 显式计数 + 上限，绝不出现负切片。
  4. 预算超了就 break，后面的短行也进不来。
     → 超长行跳过继续，不中断。
  新增：必含桶（主动召回 / 24h 纠错 / 到期承诺）走独立通道，不被预算挤掉，也不占相关度名额。
"""
from __future__ import annotations
import time as _time
from . import clock

from .lang import tokenize, jaccard

INJECT_CHARS = 300          # 每轮注入硬预算（字符）
PERSON_FLOOR = 3            # 在场感保底条数（只在有相关度支撑时生效）
DUP_SIM = 0.75              # 与已选内容重复到此程度即丢弃
REDUNDANCY_W = 0.35         # MMR 冗余惩罚
MIN_LINE = 6                # 小于此长度的行不值得占预算
RECALL_COOLDOWN_TURNS = 8   # 超长会话中允许重新注入的轮次间隔
MAX_GRAPH_ONLY = 1          # 每轮最多允许几条"纯靠图扩散"进来的记忆（锦上添花，不喧宾夺主）

ORIGIN_MARK = {"user": "·", "system": "§", "agent": "~", "web": "?"}


def _line(brain, n, now, qtoks) -> str:
    """单条记忆的紧凑编码。字符就是 token，能省一个是一个。

    归档记忆用 ↩ 前缀标记——它是"很久没提起但确实记得"的东西，
    让模型可以用过去时/保留语气提起，而不是当成刚发生的事。
    """
    mark = ORIGIN_MARK.get(n.origin, "~")
    body = n.gist or n.label
    line = (f"↩{mark}" if n.slice == "archive" else mark) + body
    # 通道2：原话锚点 —— 只在"查询更贴近原话"或"这是纠错卡"时附带，
    # 平时不带（避免每轮多烧 20-40 字）。这是 v3 里 verbatim 永远到不了模型的修复。
    if n.verbatim:
        vb = n.verbatim[0]
        if n.feedback_until > now or jaccard(tokenize(vb), qtoks) > jaccard(tokenize(body), qtoks):
            if len(line) + len(vb) + 3 <= INJECT_CHARS:
                line += f"「{vb[:40]}」"
    # 通道3：情感押韵 —— 仅当情感标签与查询有词面共鸣时附带
    if n.feeling:
        flab = str(n.feeling.get("label", ""))
        if flab and jaccard(tokenize(flab), qtoks) > 0:
            line += f"〔{flab}〕"
    if n.due_at:
        line += "⏰" + _time.strftime("%m-%d", _time.localtime(n.due_at))
    return line


def _salience(brain, n, now) -> float:
    return 0.55 * n.importance + 0.45 * brain.freshness(n, now)


def build(brain, ranked, qtoks=None, budget: int = INJECT_CHARS,
          session_seen: dict = None, pending: list = None, now: float = None,
          social: bool = False, rel_map: dict = None,
          allow_archive: bool = False) -> dict:
    """返回 {text, used, forced, dropped, chars}。

    session_seen: {node_id: 注入时的轮次}，由 hooks 维护，会话结束清空。
    pending: 上一轮 [[recall: 词]] 召回的节点 id，本轮强制注入一次。
    allow_archive: 显式回忆模式——归档记忆可以入包（带 ↩ 标记）。
    """
    now = now or clock.now()
    session_seen = session_seen if session_seen is not None else {}
    qtoks = qtoks or []
    rel_map = rel_map or {}
    used: list[str] = []
    forced: list[str] = []
    dropped = {"budget": 0, "dup": 0, "seen": 0, "person_cap": 0, "graph_cap": 0}
    lines: list[str] = []
    used_chars = 0

    def try_add(nid, force=False) -> bool:
        nonlocal used_chars
        if nid in used:
            return True
        n = brain.nodes.get(nid)
        if n is None or n.status != "live":
            return False
        if n.slice == "archive" and not allow_archive:
            return False
        line = _line(brain, n, now, qtoks)
        if len(line) < MIN_LINE:
            return False
        if used_chars + len(line) + (1 if lines else 0) > budget:
            dropped["budget"] += 1
            return False                     # 跳过继续，不 break
        lines.append(line)
        used.append(nid)
        used_chars += len(line) + (1 if len(lines) > 1 else 0)
        if force:
            forced.append(nid)
        return True

    # ---- 必含桶 ----
    # ① 主动召回协议 [[recall: 词]]：模型点名要的记忆必须到位
    for nid in (pending or []):
        try_add(nid, force=True)
    # ② 24h 纠错强插：改过口的事，最近一天内每轮都要看见（防旧记忆回潮）
    for n in sorted(brain.nodes.values(), key=lambda x: -x.feedback_until):
        if n.feedback_until > now and n.status == "live":
            try_add(n.id, force=True)
    # ③ 到期/逾期承诺（前瞻记忆）：这是"主动提起"最该命中的一类
    due = [n for n in brain.nodes.values()
           if n.due_at and n.status == "live" and n.slice != "archive"
           and n.due_at <= now + 86400]
    for n in sorted(due, key=lambda x: x.due_at):
        try_add(n.id, force=True)

    # ---- MMR 选包 ----
    selected_toks = [tokenize(brain.nodes[i].gist) for i in used if i in brain.nodes]
    graph_only = 0
    for r in ranked:
        nid = r["id"]
        n = brain.nodes.get(nid)
        if n is None:
            continue
        if nid in used:
            continue
        # 凭图进来的（没有词面命中）限量：扩散可以锦上添花，不能淹没有效命中
        if r["decomp"].get("rel", 0.0) <= 0.0:
            if graph_only >= MAX_GRAPH_ONLY:
                dropped["graph_cap"] += 1
                continue
        # 会话级去重：注入过的内容模型上下文里已经有了，重复注入 = 纯浪费
        if nid in session_seen:
            if brain.turn - session_seen[nid] < RECALL_COOLDOWN_TURNS:
                dropped["seen"] += 1
                continue
        toks = tokenize(n.gist or n.label)
        # 冗余抑制：和已选内容高度重合的近似重复不占预算
        if selected_toks:
            sim = max((jaccard(toks, t) for t in selected_toks), default=0.0)
            if sim >= DUP_SIM:
                dropped["dup"] += 1
                continue
            mmr = r["score"] - REDUNDANCY_W * sim
        else:
            mmr = r["score"]
        if mmr <= 0:
            continue
        before = used_chars
        if try_add(nid):
            selected_toks.append(toks)
            if r["decomp"].get("rel", 0.0) <= 0.0:
                graph_only += 1
        elif used_chars == before:
            continue

    # ---- person floor：在场感，但只在「纯社交轮次且一条都没选中」时补 ----
    # v3 的版本是每轮无条件补 3 条最早的人相关记忆 → 信息型提问里全是噪音。
    # 这里的判断是：既然相关记忆已经进来了，人就在场，不需要再补；
    # 只有闲聊/问候这类没有任何检索命中的轮次，才用最显著的人相关记忆兜住在场感。
    persons = [i for i in used if brain.nodes[i].about_person]
    need = PERSON_FLOOR - len(persons)
    if need > 0 and social and not persons:
        cands = [n for n in brain.nodes.values()
                 if n.about_person and n.status == "live" and n.slice != "archive"
                 and n.id not in used]
        scored = sorted(((rel_map.get(n.id, 0.0), _salience(brain, n, now), n.id)
                         for n in cands), key=lambda x: (-x[0], -x[1]))
        for _rel, _s, nid in scored[:need]:
            if not try_add(nid, force=True):
                dropped["person_cap"] += 1

    return {"text": "\n".join(lines), "used": used, "forced": forced,
            "dropped": dropped, "chars": used_chars}
