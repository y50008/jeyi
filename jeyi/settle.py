"""settle.py — 五步结算 v4.0 + 回响触发

v3 的两个致命实现 bug：
  · 冲突检测与图式挂载用 set(gist.split()) 算中文 Jaccard——中文没空格，整句是一个 token，
    相似度恒为 0，所以「预测误差检测」和「图式挂载」两步在中文语料上**从未生效过**
    （实测：10 条真实记忆结算后边数 = 0）。v4 统一走 lang.tokenize。
  · judge 参数从未被调用（hooks 里赋值后无人使用）。v4 真正接线：
    候选冲突先过启发式，再由 LLM 复核（若配置），复核结论写入 review 标记。
"""
from __future__ import annotations
import time
from . import clock
from .core import WORKING_CAP

from .lang import tokenize, jaccard

SETTLE_BUDGET = {"nodes": 5, "edges": 6}
CONFLICT_SIM = 0.45         # 词面重合到此程度才进入冲突复核
ENTAIL_SIM = 0.18           # 达到此相似度可挂到道理节点
JUDGE_MIN_SIM = 0.30        # 达到此相似度才值得花一次 LLM 调用


def _weighted_sim(a: str, b: str, idf=None) -> float:
    """idf 加权的余弦重合度。

    为什么不用 Jaccard：模板句会骗人。「我上周去试了火锅，感觉一般」和
    「我上周去试了爬山，感觉一般」词面 Jaccard 高达 0.85，但它们是两件不相干的事；
    用 idf 加权后，模板词（df 高、权重低）几乎不贡献相似度，真正的主题词才算数。
    没有索引时退化为普通 Jaccard（测试里的小图也够用）。
    """
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa or not sb:
        return 0.0
    if idf is None:
        return jaccard(sa, sb)
    wa = {t: idf(t) ** 2 for t in sa}
    wb = {t: idf(t) ** 2 for t in sb}
    na = sum(wa.values()) ** 0.5
    nb = sum(wb.values()) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    inter = sum(min(wa[t], wb.get(t, 0.0)) for t in sa & sb)
    return inter / (na * nb)


def _conflict_reason(brain, judge, a, b, sim):
    """返回 None 表示不是冲突；否则返回原因字符串。LLM 复核是可选增强，失败不阻断。"""
    if judge is None or sim < JUDGE_MIN_SIM:
        return f"词面重合 {sim:.2f}，待复核"
    try:
        if judge.review_conflict(a.gist, b.gist):
            return f"LLM 判定矛盾（词面重合 {sim:.2f}）"
        return None
    except Exception as e:                      # 网络/端点故障：降级为待复核，绝不静默
        return f"复核失败（{type(e).__name__}），待人工复核"


def settle(brain, new_ids: list, scorer=None, judge=None) -> list:
    t = clock.now()
    budget = dict(SETTLE_BUDGET)
    log = []
    news = [brain.nodes[i] for i in new_ids if i in brain.nodes]

    # ① replay 交错：新节点与强邻居一并重放（防灾难性遗忘）
    replayed = 0
    for n in news:
        n.touch(t)
        for nb, _et, w in brain.adj.get(n.id, []):
            if w >= 0.4 and budget["nodes"] > 0:
                m = brain.nodes.get(nb)
                if m and m.status == "live":
                    m.touch(t)
                    budget["nodes"] -= 1
                    replayed += 1
    log.append(f"① replay 交错：{len(news)} 新节点，{replayed} 个强邻居一并重放")

    # ② 预测误差检测：词面相近但值不同 → 冲突候选，不静默、不自动定论
    idf = (scorer.index.idf if scorer is not None else None)
    cand = 0
    seen_pairs = set()
    for n in news:
        ntok = tokenize(n.gist + " " + n.label)
        for m in brain.nodes.values():
            if m.id == n.id or m.status != "live" or not ntok:
                continue
            pair = tuple(sorted((n.id, m.id)))
            if pair in seen_pairs:
                continue
            sim = _weighted_sim(n.gist + " " + n.label, m.gist + " " + m.label, idf)
            if sim < CONFLICT_SIM:
                continue
            if n.gist.strip() == m.gist.strip():
                continue                        # 完全同义是复述，不是冲突
            seen_pairs.add(pair)
            reason = _conflict_reason(brain, judge, n, m, sim)
            if not reason:
                continue
            if budget["edges"] > 0:
                brain.add_edge(n.id, m.id, "conflict", round(min(0.95, 0.5 + sim), 2))
                budget["edges"] -= 1
            if not n.review:
                n.review = True
            cand += 1
    log.append(f"② 预测误差：{cand} 个冲突候选（已标记待复核，不静默合并）")

    # ③ 图式挂载：挂到最相近的道理节点，只存差量
    lessons = [n for n in brain.nodes.values()
               if n.type == "lesson" and n.status == "live"]
    mounted = 0
    for n in news:
        ntok = tokenize(n.gist)
        best, bw = None, 0.0
        for L in lessons:
            if L.id == n.id:
                continue
            ov = jaccard(ntok, tokenize(L.gist))
            if ov > bw:
                best, bw = L, ov
        if best and bw >= ENTAIL_SIM and budget["edges"] > 0:
            brain.add_edge(n.id, best.id, "entail", round(bw, 2))
            budget["edges"] -= 1
            mounted += 1
    log.append(f"③ 图式挂载：{mounted} 条挂到既有道理节点")

    # ④ 再巩固写回：本会话被提起的节点已在 mention() 中刷新；这里只做一致性收尾
    log.append("④ 再巩固写回：提起即刷新（仅对真正进入注入包的节点计数）")

    # ⑤ 反思节点：只由锚点 + gist 生成，禁自由发挥（补丁 E）
    anchors = [n.id for n in news if n.verbatim or n.gist]
    r = brain.add_node(
        label=f"反思 · 会话结算 {time.strftime('%m-%d %H:%M', time.localtime(t))}",
        type="reflection", importance=0.6, about_person=False,
        gist=(f"本会话新增 {len(news)} 条；预算余额 nodes={budget['nodes']} edges={budget['edges']}；"
              f"冲突候选 {cand} 个；图式挂载 {mounted} 条；被重放邻居 {replayed} 个。"),
        sources=anchors)
    r.touch(t)
    log.append(f"⑤ 反思节点 {r.id} 已生成（锚点 {len(anchors)} 条，置信降档）")

    moved = brain.archive_pass(t)
    log.append(f"⑥ 归档扫描：{moved} 条转入 archive（full 卡仍在）")
    work = brain.slice_pass(t)
    log.append(f"⑦ 工作记忆切片：{work} 条变更（容量 {WORKING_CAP}）")

    if scorer:
        scorer._rebuild()
    return log


PRIORITY = {"到期承诺": 0, "Zeigarnik 未竟": 1, "日期回响": 2, "情感押韵": 3}


def resonate(brain, t: float = None, cap: int = 2) -> list:
    """回响：返回 [(触发类型, node_id)]，按优先级取前 cap 条（规范：主动 ≤2 次/对话）。"""
    t = t or clock.now()
    out = []
    today = time.localtime(t)
    for n in brain.nodes.values():
        if n.status != "live":
            continue
        if n.due_at and n.due_at <= t + 86400:
            out.append(("到期承诺", n.id))
        if any(et == "conflict" for _nb, et, _w in brain.adj.get(n.id, [])):
            out.append(("Zeigarnik 未竟", n.id))
        try:
            c = time.localtime(n.created)
            if (c.tm_mon, c.tm_mday) == (today.tm_mon, today.tm_mday) and t - n.created > 300 * 86400:
                out.append(("日期回响", n.id))
        except (ValueError, OSError):
            pass
        if n.feeling and n.slice == "archive":
            out.append(("情感押韵", n.id))
    seen, uniq = set(), []
    for trig, nid in out:
        key = (trig, nid)
        if key not in seen:
            seen.add(key)
            uniq.append((trig, nid))
    uniq.sort(key=lambda x: PRIORITY.get(x[0], 9))
    return uniq[:cap]
