"""tools/bench_external.py — 跑公开记忆基准的检索层（LoCoMo / LongMemEval）

为什么需要它：主集和留出集都是自造的，作者知道自己系统怎么工作，天然过拟合。
公开基准至少把"语料由别人写"这一条补上。

**测的是检索，不是问答。** LoCoMo / LongMemEval 的官方指标是 LLM 判分答案正确率，
那需要一个 LLM 在环（每问一次调用）。这个脚本测的是引擎自己负责的那一段：
**该命中证据的那一轮，有没有出现在 rank@k / 注入包里**。这是端到端准确率的上界。

数据：
  LoCoMo        10 段对话 / ~2000 条 QA / 含多跳·时序·对抗
                https://github.com/snap-research/locomo  （data/locomo10.json）
  LongMemEval   xiaowu0162/longmemeval 的 longmemeval_s（278MB，单文件）

用法：
  python tools/bench_external.py --dataset locomo
  python tools/bench_external.py --dataset locomo --dense --samples 2
  python tools/bench_external.py --dataset longmemeval --limit 30
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from jeyi import MemoryHooks                      # noqa: E402
from jeyi import dense as dense_mod        # noqa: E402

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "jeyi", "bench")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}


def parse_locomo_date(s: str):
    """"1:56 pm on 8 May, 2023" → 时间戳。解析不出来就返回 None。"""
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", s)
    if not m:
        return None
    day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2).lower()), int(m.group(3))
    if not mon:
        return None
    hm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", s, re.I)
    hh, mm = (int(hm.group(1)) % 12, int(hm.group(2))) if hm else (0, 0)
    if hm and hm.group(3).lower() == "pm":
        hh += 12
    try:
        return time.mktime((year, mon, day, hh, mm, 0, 0, 0, -1))
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------- 引擎构造
def make_embedder(use_dense: bool, lang: str = "en"):
    if not use_dense:
        return None
    emb = dense_mod.make_embedder(kind=lang)
    if emb is None:
        raise SystemExit(
            f"稠密模型不可用（kind={lang}），先跑：python tools/setup_dense.py --model {lang}")
    return emb


def new_engine(embedder, budget: int = 300):
    return MemoryHooks(embedding=embedder, budget=budget)


def _reset_sessions(ag):
    """每个问题当成一次新会话：清掉会话级去重，但保留记忆。"""
    ag.session_seen.clear()
    ag.brain.turn = 0


# ---------------------------------------------------------------- LoCoMo
def run_locomo(embedder, samples, samples_limit=None, per_sample_limit=None, verbose=True):
    rows = []
    for si, sample in enumerate(samples):
        if samples_limit is not None and si >= samples_limit:
            break
        ag = new_engine(embedder)          # 每段对话是不同的人，必须换引擎
        conv = sample["conversation"]
        sid = sample.get("sample_id", f"S{si}")
        # 按会话顺序导入：一轮 = 一条记忆，dia_id → node_id
        turn2node = {}
        session_keys = sorted(
            (k for k in conv if re.fullmatch(r"session_\d+", k)),
            key=lambda k: int(k.split("_")[1]))
        for sk in session_keys:
            ts = parse_locomo_date(conv.get(sk + "_date_time", ""))
            for turn in conv.get(sk, []):
                txt = (turn.get("text") or "").strip()
                if not txt:
                    continue
                stamp = time.strftime("[%Y-%m-%d] ", time.localtime(ts)) if ts else ""
                nid = ag.remember(stamp + txt, origin="user",
                                  session=sk.replace("_", ""), at=ts,
                                  about_person=True, dedup=False, link=True)
                if nid:
                    turn2node[turn.get("dia_id")] = nid

        qas = sample.get("qa", [])
        if per_sample_limit:
            qas = qas[:per_sample_limit]
        for qa in qas:
            q = (qa.get("question") or "").strip()
            gold = {turn2node[e] for e in qa.get("evidence", []) if e in turn2node}
            cat = qa.get("category")
            if not q:
                continue
            _reset_sessions(ag)
            ranked = ag.scorer.rank(q, k=5)
            top = {r["id"] for r in ranked}
            res = ag.before_reply(q)
            used = set(res["cites"])
            if gold:
                hit5 = bool(gold & top)
                packed = bool(gold & used)
            else:
                # 对抗类：记忆里本来就没有答案，正确行为是"不要硬凑"
                hit5 = not top
                packed = not used
            rows.append({"sample": sid, "cat": cat, "q": q, "gold": bool(gold),
                         "hit5": hit5, "packed": packed,
                         "chars": res["chars"], "n": len(used)})
        if verbose:
            print(f"  {sid}: {len([r for r in rows if r['sample'] == sid])} 问 "
                  f"（累计记忆 {len(ag.brain.nodes)} 条）", flush=True)
    return rows


# ---------------------------------------------------------------- LongMemEval
def load_longmemeval(limit=None):
    path = os.path.join(CACHE, "longmemeval_s.json")
    if not os.path.exists(path):
        raise SystemExit(f"缺少 {path}\n"
                         f"下载：python tools/fetch_bench.py --dataset longmemeval")
    t0 = time.time()
    data = json.load(open(path, encoding="utf-8"))
    print(f"  载入 LongMemEval {len(data)} 问，{time.time()-t0:.1f}s")
    return data[:limit] if limit else data


def run_longmemeval(embedder, questions, verbose=True):
    rows = []
    for qi, item in enumerate(questions):
        ag = new_engine(embedder)          # 每问一个干净引擎（haystack 逐问独立）
        sessions = item.get("haystack_sessions") or []
        sids = item.get("haystack_session_ids") or [f"s{i}" for i in range(len(sessions))]
        dates = item.get("haystack_dates") or [None] * len(sessions)
        sid2nodes = {}
        for s_i, sess in enumerate(sessions):
            ts = parse_iso(dates[s_i]) if s_i < len(dates) else None
            nodes = []
            for turn in sess:
                txt = (turn.get("content") or "").strip()
                if not txt or turn.get("role") not in ("user", "assistant"):
                    continue
                stamp = time.strftime("[%Y-%m-%d] ", time.localtime(ts)) if ts else ""
                nid = ag.remember(stamp + txt, origin=("user" if turn["role"] == "user" else "agent"),
                                  at=ts, dedup=False, link=False)
                if nid:
                    nodes.append(nid)
            sid2nodes[sids[s_i]] = nodes
        gold_ids = set()
        for e in (item.get("answer_session_ids") or []):
            gold_ids |= set(sid2nodes.get(e, []))
        q = item.get("question", "")
        _reset_sessions(ag)
        ranked = ag.scorer.rank(q, k=5)
        top = {r["id"] for r in ranked}
        res = ag.before_reply(q)
        used = set(res["cites"])
        has_answer = item.get("has_answer", True)
        if has_answer and gold_ids:
            hit5, packed = bool(gold_ids & top), bool(gold_ids & used)
        else:
            hit5, packed = not top, not used
        rows.append({"sample": item.get("question_id", qi), "cat": item.get("question_type", "?"),
                     "q": q, "gold": has_answer, "hit5": hit5, "packed": packed,
                     "chars": res["chars"], "n": len(used)})
        if verbose and (qi + 1) % 10 == 0:
            print(f"    {qi+1}/{len(questions)}", flush=True)
    return rows


def parse_iso(s):
    if not s:
        return None
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return time.mktime(time.strptime(s.strip(), fmt))
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------- 报告
def report(rows, label):
    ans = [r for r in rows if r["gold"]]
    adv = [r for r in rows if not r["gold"]]

    def pct(xs, k):
        return round(100.0 * sum(1 for r in xs if r[k]) / max(1, len(xs)), 1)

    print("=" * 66)
    print(f"{label}：{len(rows)} 问（可答 {len(ans)} / 无答案 {len(adv)}）")
    print("-" * 66)
    print(f"  证据命中 rank@5      {pct(ans, 'hit5'):>6}%")
    print(f"  证据进注入包          {pct(ans, 'packed'):>6}%")
    if adv:
        print(f"  无答案题不硬凑        {pct(adv, 'packed'):>6}%")
    print(f"  每轮注入字符          {sum(r['chars'] for r in rows)/max(1,len(rows)):>6.1f}")
    by = {}
    for r in rows:
        by.setdefault(r["cat"], []).append(r)
    print("-" * 66)
    for cat, xs in sorted(by.items(), key=lambda kv: str(kv[0])):
        g = [r for r in xs if r["gold"]]
        a = [r for r in xs if not r["gold"]]
        extra = f"  无答案不硬凑 {pct(a, 'packed')}%" if a else ""
        print(f"  cat={cat:<18} n={len(xs):>3}  rank@5 {pct(g, 'hit5') if g else 0:>5}%"
              f"  入包 {pct(g, 'packed') if g else 0:>5}%{extra}")
    print("=" * 66)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["locomo", "longmemeval"], default="locomo")
    ap.add_argument("--dense", action="store_true")
    ap.add_argument("--lang", choices=["zh", "en"], default="en",
                    help="稠密模型语言，必须和数据语言一致（两个基准都是英文）")
    ap.add_argument("--samples", type=int, default=None, help="LoCoMo 用几段对话")
    ap.add_argument("--per-sample", type=int, default=None, help="每段对话取多少问")
    ap.add_argument("--limit", type=int, default=30, help="LongMemEval 取多少问")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ag = make_embedder(args.dense, args.lang)
    backend = (f"稠密({args.lang}) + 词法 RRF" if args.dense else "纯词法")
    t0 = time.time()

    if args.dataset == "locomo":
        path = os.path.join(CACHE, "locomo10.json")
        if not os.path.exists(path):
            raise SystemExit(f"缺少 {path}\n下载：python tools/fetch_bench.py --dataset locomo")
        samples = json.load(open(path, encoding="utf-8"))
        print(f"LoCoMo {len(samples)} 段对话 · 后端：{backend}")
        rows = run_locomo(ag, samples, args.samples, args.per_sample)
        report(rows, "LoCoMo（检索层）")
    else:
        qs = load_longmemeval(args.limit)
        print(f"LongMemEval {len(qs)} 问 · 后端：{backend}")
        rows = run_longmemeval(ag, qs)
        report(rows, "LongMemEval-S（检索层）")

    print(f"耗时 {time.time()-t0:.1f}s")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    main()
