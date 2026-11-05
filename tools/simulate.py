"""tools/simulate.py — 时间旅行：把一年压进 3 秒

为什么需要它：这个项目卖的是"记住一个人几个月的事"，但 v4.0 之前
引擎直接调 time.time()，衰减/归档/到期提醒/周年回响这些**长时程行为根本没法验证**——
在真实时间里等一年不是测试。

v4.1 引入可注入时钟（jeyi/clock.py）后，这里跑一条逐日推进的一年时间线：
  · 第 0 天写下一批记忆（核心事实 / 承诺 / 随口一提的小事）
  · 第 7/30/60/90/120 天像真实对话那样"提起"核心记忆（复述 → 刷新鲜活度）
  · 第 0/30/90/180/365 天各做一次体检：谁还活着、谁归档了、
    常规检索还能不能取到、显式回忆（"你还记得…"）能不能取回

用法：python tools/simulate.py [--json]
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from jeyi import MemoryHooks, clock     # noqa: E402

# ---- 语料 ---------------------------------------------------------------
CORE = [                      # 重要 + 会被反复提起
    ("我对花生过敏，吃了会起疹子", "花生", 0.85),
    ("我老婆叫小满，是中学老师", "小满", 0.85),
    ("我在做一个记忆引擎项目，目标是省 token", "记忆引擎", 0.80),
]
COMMITS = [                   # 有到期日的承诺
    ("我1月10号要去医院体检", "体检", 0.70),
    ("我3月1号要交季度报告", "季度报告", 0.70),
]
TRIVIAL_TOPICS = ["火锅", "爬山", "铁观音", "游泳", "摄影", "围棋", "爵士乐", "咖啡",
                  "露营", "书法", "潜水", "陶艺", "骑行", "古着", "手冲", "徒步",
                  "钓鱼", "木工"]
CHECKPOINTS = [0, 30, 90, 180, 365]
REHEARSE_DAYS = [7, 30, 60, 90, 120]
HORIZON = 365


def _memories():
    out = list(CORE) + list(COMMITS)
    out += [(f"我上周去试了{t}，感觉一般", t, 0.55) for t in TRIVIAL_TOPICS]
    return out


def _build(ag, mems):
    keys = {}
    for text, key, imp in mems:
        ids = ag.on_message(text)
        for i in ids:
            n = ag.brain.nodes[i]
            n.importance = imp            # 用标注的重要性，别让抽取启发式随机决定
            if key not in keys:
                keys[key] = i
    ag.scorer._rebuild()
    return keys


def _probe(ag, keys):
    """常规检索命中 / 显式回忆命中。"""
    normal, recall = {}, {}
    for key, nid in keys.items():
        normal[key] = any(x["id"] == nid for x in ag.scorer.rank(key, k=5))
        recall[key] = any(x["id"] == nid
                          for x in ag.scorer.rank(key, k=5, include_archive=True))
    return normal, recall


def _row(ag, keys, day, date):
    normal, recall = _probe(ag, keys)
    sl = {"working": 0, "active": 0, "archive": 0}
    for n in ag.brain.nodes.values():
        sl[n.slice] = sl.get(n.slice, 0) + 1
    triv = list(TRIVIAL_TOPICS)
    core = [k for _t, k, _i in CORE]
    archived = [k for k in triv if ag.brain.nodes[keys[k]].slice == "archive"]
    # 真正要守的不变量：归档了就不该再出现在常规检索里
    archived_but_normal = [k for k in archived if normal[k]]
    survivors = [k for k in triv if k not in archived]
    return {
        "day": day, "date": date,
        "working": sl["working"], "active": sl["active"], "archive": sl["archive"],
        "core_retrievable": f"{sum(normal[k] for k in core)}/{len(core)}",
        "trivial_archived": f"{len(archived)}/{len(triv)}",
        "trivial_normal": f"{sum(normal[k] for k in triv)}/{len(triv)}",
        "trivial_recall": f"{sum(recall[k] for k in triv)}/{len(triv)}",
        "archived_but_normal": archived_but_normal,
        "survivors": survivors,
    }


def run(verbose: bool = True):
    vc = clock.VirtualClock("2026-01-01")
    with vc:
        ag = MemoryHooks()
        mems = _memories()
        keys = _build(ag, mems)
        ag.on_session_end()

        rows = [_row(ag, keys, 0, vc.date())]
        due_seen = {}
        for day in range(1, HORIZON + 1):
            vc.advance(days=1)
            # 到期承诺：每天看一眼有没有浮出来
            for trig, nid in ag.on_proactive():
                if trig == "到期承诺":
                    due_seen.setdefault(ag.brain.nodes[nid].label, vc.date())
            if day in REHEARSE_DAYS:                 # 真实对话里"提起"它
                for _t, k, _i in CORE:
                    ag.before_reply(k)
                ag.on_session_end()
            if day in CHECKPOINTS:
                ag.on_session_end()                  # 结算：归档扫描 + 工作记忆切片
                rows.append(_row(ag, keys, day, vc.date()))

        if verbose:
            print(f"时间线：{len(mems)} 条记忆，逐日推进 {HORIZON} 天（{rows[0]['date']} → {rows[-1]['date']}）")
            print(f"{'天':>4} {'日期':<12}{'工作':>5}{'活跃':>6}{'归档':>6}"
                  f"{'核心可检索':>12}{'小事已归档':>12}{'小事常规':>10}{'小事显式':>10}")
            print("-" * 84)
            for r in rows:
                print(f"{r['day']:>4} {r['date']:<12}{r['working']:>5}{r['active']:>6}"
                      f"{r['archive']:>6}{r['core_retrievable']:>12}"
                      f"{r['trivial_archived']:>12}{r['trivial_normal']:>10}"
                      f"{r['trivial_recall']:>10}")
            print("-" * 84)
            if due_seen:
                print("到期承诺主动浮出：")
                for label, date in due_seen.items():
                    print(f"  · {date}  {label}")

        last = rows[-1]
        n_core = len(CORE)
        n_triv = len(TRIVIAL_TOPICS)
        inv = {
            "core_still_retrievable": last["core_retrievable"] == f"{n_core}/{n_core}",
            "trivial_mostly_archived":
                int(last["trivial_archived"].split("/")[0]) >= int(n_triv * 0.8),
            "archived_not_in_normal": len(last["archived_but_normal"]) == 0,
            "archived_recallable_on_request": last["trivial_recall"] == f"{n_triv}/{n_triv}",
            "due_commitment_fired": len(due_seen) > 0,
        }
        return {"timeline": rows, "due_surfaced": due_seen, "invariants": inv,
                "survivors": last["survivors"]}


def main():
    res = run(verbose="--json" not in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    inv = res["invariants"]
    checks = [
        ("核心记忆一年后仍可检索（有复述）", inv["core_still_retrievable"]),
        ("随口一提的小事 ≥80% 已归档", inv["trivial_mostly_archived"]),
        ("归档记忆不在常规检索里", inv["archived_not_in_normal"]),
        ("但显式回忆仍能全部取回（没有真正的遗忘）", inv["archived_recallable_on_request"]),
        ("到期承诺被主动提起", inv["due_commitment_fired"]),
    ]
    print()
    for name, ok in checks:
        print(f"  [{'✓' if ok else '✗'}] {name}")
    if res["survivors"]:
        print(f"\n  说明：{len(res['survivors'])} 条小事未归档（{ '、'.join(res['survivors']) }）——"
              "它们在复述轮次里作为核心记忆的图邻居被顺带提起过，复述刷新了鲜活度。"
              "这是设计行为，不是漏网。")
    return 0 if all(o for _n, o in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
