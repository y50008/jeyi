"""serve.py — 本地记忆服务（零依赖 HTTP sidecar）

为什么需要它：本引擎是 Python，大肥鱼（dsh-identity）是 Node 插件。
跨语言对接最便宜的方式不是重写成 JS，而是**把它变成一个本地 HTTP 服务**：
Node 那边用 `fetch` 调几行，我们的引擎一点不改。

设计约束（与项目一致）：
  · 只用标准库 `http.server`，不引入 Flask/FastAPI；
  · 每个 peer（大肥鱼里的会话/群/人）一个独立记忆库，互相不串；
  · 端口只绑 127.0.0.1，不对外网暴露；
  · 所有接口幂等或可重放，失败时对方可以直接回退到自己的实现。

启动：
    python -m brain.serve --port 8765 --store ./data/peers
接口：
    GET  /v1/health
    POST /v1/add     {peer, text, importance?, origin?, quote?, at?}
    POST /v1/recall  {peer, query, top?}          -> {items:[{id,text,score,C,P}]}
    POST /v1/card    {peer, query, budget?}       -> {card, used:[id], chars}
    POST /v1/forget  {peer, keyword}
    POST /v1/stats   {peer}
    POST /v1/session {peer, session}              -> 切会话（作用域隔离用）
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import MemoryHooks, JsonlStorage          # noqa: E402
from brain import viz                                 # noqa: E402

DEFAULT_STORE = os.path.join(os.getcwd(), "data", "peers")


class PeerStore:
    """peer → 引擎。懒加载，加锁（ThreadingHTTPServer 会并发进来）。"""

    def __init__(self, root: str, budget: int = 300, embedding=None):
        self.root = root
        self.budget = budget
        self.embedding = embedding
        self._engines: dict[str, MemoryHooks] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _safe(peer: str) -> str:
        keep = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in str(peer or "default"))
        return keep[:64] or "default"

    def peers(self) -> list:
        try:
            return sorted(d for d in os.listdir(self.root)
                          if os.path.isdir(os.path.join(self.root, d)))
        except OSError:
            return []

    def pick(self, peer: str = "") -> str:
        """没指定 peer 时取最近活跃的那个（面板打开就看最新的人）。"""
        if peer:
            return peer
        best, best_t = "", -1.0
        for p in self.peers():
            d = os.path.join(self.root, p)
            try:
                t = max((os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d)),
                        default=0.0)
            except OSError:
                t = 0.0
            if t > best_t:
                best, best_t = p, t
        return best or "default"

    def get(self, peer: str) -> MemoryHooks:
        key = self._safe(peer)
        with self._lock:
            ag = self._engines.get(key)
            if ag is None:
                d = os.path.join(self.root, key)
                os.makedirs(d, exist_ok=True)
                ag = MemoryHooks(storage=JsonlStorage(d), budget=self.budget,
                                 session=key, user=key, embedding=self.embedding)
                self._engines[key] = ag
            return ag


def _op_add(store: PeerStore, body: dict) -> dict:
    ag = store.get(body.get("peer"))
    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    nid = ag.remember(
        text,
        origin=body.get("origin") or "user",
        at=float(body["at"]) if body.get("at") else None,
        importance=float(body.get("importance") or 0.55),
        scope=body.get("scope") or "user",
        dedup=body.get("dedup", True),
    )
    return {"ok": True, "id": nid, "nodes": len(ag.brain.nodes)}


def _op_recall(store: PeerStore, body: dict) -> dict:
    ag = store.get(body.get("peer"))
    q = (body.get("query") or "").strip()
    top = max(1, min(20, int(body.get("top") or 5)))
    if not q:
        return {"ok": True, "items": []}
    # 允许调用方按自己的数据调稠密闸门（0.30~0.90）。
    # 默认 0.58 是"弃答准确率 100%"下选出来的；放宽能多召回改写句，代价是可能硬凑。
    floor = body.get("dense_floor")
    from brain import score as _score
    old = _score.DENSE_FLOOR
    if floor is not None:
        _score.DENSE_FLOOR = max(0.30, min(0.90, float(floor)))
    try:
        ranked = ag.scorer.rank(q, k=top, scopes=ag._visible_scopes())
    finally:
        _score.DENSE_FLOOR = old
    items = []
    for r in ranked:
        n = ag.brain.nodes.get(r["id"])
        if n is None:
            continue
        items.append({
            "id": r["id"], "text": n.gist or n.label,
            "score": r["score"], "C": r["decomp"]["rel"], "P": r["score"],
            "category": n.type, "importance": n.importance,
            "at": int(n.created * 1000),
        })
    return {"ok": True, "items": items}


def _op_card(store: PeerStore, body: dict) -> dict:
    ag = store.get(body.get("peer"))
    if body.get("budget"):
        ag.budget = max(60, min(2000, int(body["budget"])))
    q = (body.get("query") or "").strip()
    # stateless：大肥鱼每轮重建系统提示，必须每轮都给完整卡片（不做会话内去重）
    res = ag.before_reply(q or "最近怎么样", stateless=True)
    return {"ok": True, "card": res["pack"], "used": res["cites"], "chars": res["chars"]}


def _op_forget(store: PeerStore, body: dict) -> dict:
    ag = store.get(body.get("peer"))
    kw = (body.get("keyword") or "").strip()
    nid = body.get("id")
    removed = 0
    for n in list(ag.brain.nodes.values()):
        if n.status != "live":
            continue
        if (nid and n.id == nid) or (kw and kw in (n.gist + n.label)):
            ag.brain.set_status(n.id, "overturned", reason="user forget")
            removed += 1
            if not nid:
                break
    return {"ok": True, "removed": removed}


def _op_session(store: PeerStore, body: dict) -> dict:
    ag = store.get(body.get("peer"))
    ag.set_session(body.get("session") or ag.session)
    return {"ok": True, "session": ag.session}


def _op_stats(store: PeerStore, body: dict) -> dict:
    ag = store.get(body.get("peer"))
    st = ag.stats()
    st["ok"] = True
    return st


ROUTES = {
    "/v1/add": _op_add,
    "/v1/recall": _op_recall,
    "/v1/card": _op_card,
    "/v1/forget": _op_forget,
    "/v1/session": _op_session,
    "/v1/stats": _op_stats,
}


class Handler(BaseHTTPRequestHandler):
    store: PeerStore = None
    token: str = ""

    def log_message(self, fmt, *args):      # 静默：别把每轮请求刷到终端
        pass

    def _send(self, code: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/v1/health":
            return self._send(200, {"ok": True, "service": "memory-brain", "version": "4.6"})
        if path == "/v1/peers":
            return self._send(200, {"ok": True, "peers": self.store.peers()})
        if path == "/v1/graph":
            peer = self.store.pick(self._query("peer"))
            ag = self.store.get(peer)
            data = viz.payload(ag)
            return self._send(200, {"ok": True, **data})
        if path in ("/", "/index.html"):
            # 实时面板：每 peer 一张脑图，20 秒自动刷新
            peer = self.store.pick(self._query("peer"))
            ag = self.store.get(peer)
            html = viz.render_html(viz.payload(ag), live=True)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._send(404, {"ok": False, "error": "not found"})

    def _query(self, name: str) -> str:
        if "?" not in self.path:
            return ""
        from urllib.parse import parse_qs
        return (parse_qs(self.path.split("?", 1)[1]).get(name) or [""])[0]

    def do_POST(self):
        if self.token:
            got = (self.headers.get("X-Memory-Token") or "").strip()
            if got != self.token:
                return self._send(403, {"ok": False, "error": "bad token"})
        fn = ROUTES.get(self.path)
        if fn is None:
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except (ValueError, UnicodeDecodeError) as e:
            return self._send(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            return self._send(200, fn(self.store, body))
        except Exception as e:                       # 绝不 500 到对方脸上
            return self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def serve(port: int = 8765, store_dir: str = DEFAULT_STORE, budget: int = 300,
          token: str = "", dense: bool = False):
    embedding = None
    if dense:
        from brain import dense as dense_mod
        embedding = dense_mod.make_embedder()
    st = PeerStore(store_dir, budget=budget, embedding=embedding)
    Handler.store = st
    Handler.token = token
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"memory-brain sidecar on http://127.0.0.1:{port}  "
          f"store={store_dir}  dense={'on' if embedding else 'off'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--budget", type=int, default=300)
    ap.add_argument("--token", default=os.environ.get("MEMORY_BRAIN_TOKEN", ""))
    ap.add_argument("--dense", action="store_true")
    a = ap.parse_args()
    serve(a.port, a.store, a.budget, a.token, a.dense)


if __name__ == "__main__":
    main()
