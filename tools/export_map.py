"""tools/export_map.py — 把引擎里的真实记忆导出成脑图（v4.0 新增）

v3 的 ui/brain_map.html 里是 31 条手写节点 + 32 条手写边，并在 JS 里另写了一遍打分公式，
与 Python 引擎零连接——页面上的数字是"演示值"，不是测量值。
v4 用这个脚本把引擎导出成自包含 HTML：数据、边、统计全部来自真实引擎。

用法：
    python tools/export_map.py                # 用内置的项目史语料演示
    python tools/export_map.py --store ./data # 从真实记忆库导出
    python tools/export_map.py --out ui/x.html
"""
from __future__ import annotations
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from brain import MemoryHooks, JsonlStorage, __version__      # noqa: E402

# 内置语料：本项目自己的历史（与 topic/ 文档一致），用来演示"记忆长什么样"
DEMO = [
    ("细节在末梢，向上一次轮廓，再向上是事件。没有真正的遗忘，也没有不遗忘。", "lesson", 0.95, "振奋"),
    ("我们的意义就是发现现成的，超越现成的。超越必须接受检验。", "lesson", 0.95, "振奋"),
    ("我最近在做一个记忆脑图引擎，想解决长上下文丢记忆的问题。", "fact", 0.85, "期待"),
    ("我养了一只猫叫团子，它最近老掉毛。", "fact", 0.6, "喜欢"),
    ("我对花生过敏，吃东西要看配料表。", "fact", 0.9, "担心"),
    ("我下周三要去杭州出差，顺便去西湖走走。", "event", 0.7, "期待"),
    ("我讨厌开会，尤其是超过一小时的会。", "fact", 0.6, "烦"),
    ("我喜欢喝铁观音，只喝浓香型。", "fact", 0.55, "喜欢"),
    ("写完 v3 才发现扩散检索是死的，很难受。", "reflection", 0.8, "难过"),
    ("我准备在年底前把项目开源，配上基准测试。", "rule", 0.85, "期待"),
]


def build_demo():
    ag = MemoryHooks()
    for text, typ, imp, feel in DEMO:
        ids = ag.on_message(text)
        for i in ids:
            n = ag.brain.nodes[i]
            n.type = typ
            n.importance = imp
        if not ids:                     # 抽取器可能跳过（比如无第一人称的句子），手工补
            n = ag.brain.add_node(label=text, gist=text, type=typ, origin="user",
                                  importance=imp, about_person=True,
                                  verbatim=[text])
            ag._known_names.update()
    for n in ag.brain.nodes.values():
        if n.feeling and feel:
            n.feeling["label"] = feel
    ag.scorer._rebuild()
    ag.before_reply("记忆引擎 扩散 检索")
    ag.on_session_end()
    return ag


def load_store(path):
    return MemoryHooks(storage=JsonlStorage(path))


def export(ag, out_path):
    sess_ids = {}
    for n in ag.brain.nodes.values():
        sess_ids.setdefault(n.session, len(sess_ids) + 1)
    sess = [{"id": k, "label": f"切片 · 会话 {i}"} for k, i in sess_ids.items()]

    nodes = []
    for n in ag.brain.nodes.values():
        nodes.append({
            "id": n.id, "label": n.label, "type": n.type, "slice": n.slice,
            "imp": round(n.importance, 2), "sess": n.session,
            "gist": n.gist,
            "vb": (n.verbatim or [])[:3],
            "feel": ({"label": n.feeling.get("label", ""),
                      "why": n.feeling.get("why", ""),
                      "valid_at": n.feeling.get("valid_at", "")}
                     if n.feeling else None),
            "src": (n.sources or [])[:4] or ["auto-extract"],
        })
    edges = []
    seen = set()
    for a, lst in ag.brain.adj.items():
        for b, et, w in lst:
            key = tuple(sorted((a, b))) + (et,)
            if key in seen:
                continue
            seen.add(key)
            edges.append([a, b, et, round(w, 2)])

    st = ag.stats()
    payload = {
        "meta": {
            "version": __version__,
            "nodes": len(nodes),
            "edges": len(edges),
            "avg_inject_chars": st.get("avg_inject_chars", 0),
            "compression": st.get("compression", 0),
            "exported": time.strftime("%Y-%m-%d %H:%M"),
            "note": "数据来自引擎导出",
        },
        "sess": sess,
        "nodes": nodes,
        "edges": edges,
    }

    with open(os.path.join(ROOT, "ui", "brain_map.html"), encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    inject = f"<script>window.__BRAIN__={blob};</script>\n"
    idx = html.find("<script>")
    if idx < 0:
        raise SystemExit("模板里找不到 <script> 标签")
    html = html[:idx] + inject + html[idx:]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return payload


def main():
    args = sys.argv[1:]
    store = None
    out = os.path.join(ROOT, "ui", "brain_map_live.html")
    for i, a in enumerate(args):
        if a == "--store" and i + 1 < len(args):
            store = args[i + 1]
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]
    ag = load_store(store) if store else build_demo()
    p = export(ag, out)
    print(f"导出 {p['meta']['nodes']} 节点 / {p['meta']['edges']} 边 → {out}")
    print(f"平均注入 {p['meta']['avg_inject_chars']} 字，压缩比 {p['meta']['compression']}x")


if __name__ == "__main__":
    main()
