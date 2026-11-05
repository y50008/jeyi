"""viz.py — 把引擎状态变成脑图数据包（侧车与导出工具共用）

同一个函数喂两条路：
  · `tools/export_map.py` → 生成静态 `ui/map_live.html`（双击即看）
  · `jeyi/serve.py`      → 在 `http://127.0.0.1:8765/` 实时托管面板（自动刷新）

数据形状与 `ui/map.html` 约定一致：
  {meta:{nodes,edges,...}, sess:[{id,label}], nodes:[{id,label,type,slice,imp,sess,gist,vb,feel,src}],
   edges:[[a,b,type,w], ...]}
"""
from __future__ import annotations
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "ui", "map.html")


def payload(ag) -> dict:
    """从 MemoryHooks 实例抽出一份可直接喂给前端的快照。"""
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
            "scope": n.scope,
            "sessions": len(n.sessions or []),
        })

    edges, seen = [], set()
    for a, lst in ag.brain.adj.items():
        for b, et, w in lst:
            key = tuple(sorted((a, b))) + (et,)
            if key in seen:
                continue
            seen.add(key)
            edges.append([a, b, et, round(w, 2)])

    st = ag.stats()
    return {
        "meta": {
            "nodes": len(nodes),
            "edges": len(edges),
            "peer": st.get("session", ""),
            "avg_inject_chars": st.get("avg_inject_chars", 0),
            "compression": st.get("compression", 0),
            "session_scoped": st.get("session_scoped", 0),
            "cross_session": st.get("cross_session", 0),
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "实时面板 · 引擎导出",
        },
        "sess": sess,
        "nodes": nodes,
        "edges": edges,
    }


def render_html(data: dict, template: str = None, live: bool = False) -> str:
    """把数据塞进模板，返回自包含 HTML。live=True 时前端会定时刷新。"""
    template = template or TEMPLATE
    with open(template, encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    inject = f"<script>window.__BRAIN__={blob};window.__BRAIN_LIVE__={str(live).lower()};</script>\n"
    idx = html.find("<script>")
    if idx < 0:
        raise RuntimeError("模板里找不到 <script> 标签")
    return html[:idx] + inject + html[idx:]
