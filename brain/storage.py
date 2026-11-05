"""storage.py — 事件溯源存储 v4.0（双卡制的正确实现）

v3 的双卡制是反的：
  · full 卡只在 add_node 时写一次「创建快照」，之后改口/归档/推翻全不入档；
  · 而 load() 又把 full 卡合并回检索池 → 被 superseded 的旧卡在重启后**复活成 live**，
    改口的唯一匹配保证跨重启失效（实测：重启后两条同 label 都是 live）。
v4 改为事件溯源：
  · 每一次状态变更（create / patch / edge）追加一条不可变事件，永不改历史；
  · 加载 = 按序重放事件 → 得到唯一正确的当前状态，superseded 不会复活；
  · live 卡快照只做物化视图（可选，丢了也能从事件重建）。
事件格式：
  {"op":"create","ts":..,"node":{...}}            节点创建（全量）
  {"op":"patch","ts":..,"id":..,"fields":{...}}   状态/切片/到期/复述变更（增量）
  {"op":"edge","ts":..,"a":..,"b":..,"type":..,"w":..}
"""
from __future__ import annotations
import json
import os
import sqlite3


class NullStorage:
    def append(self, ev): pass
    def events(self): return []
    def save_snapshot(self, nodes): pass
    # ---- v3 兼容别名 ----
    def dump_full(self, d): pass
    def load(self): return {}
    def save_live(self, ds): pass


class JsonlStorage:
    """默认后端：events.jsonl（append-only）+ live_cards.json（物化视图）。"""

    def __init__(self, store_dir):
        os.makedirs(store_dir, exist_ok=True)
        self.dir = store_dir
        self.events_path = os.path.join(store_dir, "events.jsonl")
        self.snapshot_path = os.path.join(store_dir, "live_cards.json")
        self._migrate_v3()

    # ---- 写 ----
    def append(self, ev: dict):
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def save_snapshot(self, nodes):
        tmp = self.snapshot_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(nodes, f, ensure_ascii=False)
        os.replace(tmp, self.snapshot_path)

    # ---- 读 ----
    def events(self):
        if not os.path.exists(self.events_path):
            return
        with open(self.events_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue        # 半行（崩溃截断）跳过，不炸整库

    # ---- v3 数据迁移（一次性）----
    def _migrate_v3(self):
        if os.path.exists(self.events_path):
            return
        full = os.path.join(self.dir, "full_cards.jsonl")
        live = os.path.join(self.dir, "live_cards.json")
        if not (os.path.exists(full) or os.path.exists(live)):
            return
        seen = {}
        if os.path.exists(full):
            for line in open(full, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # v3 的 full 卡永远停在 live 状态，不能直接信；以 live 卡为准
                seen.setdefault(d.get("id"), d)
        if os.path.exists(live):
            try:
                for d in json.load(open(live, encoding="utf-8")):
                    seen[d["id"]] = d
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        for d in seen.values():
            d.setdefault("status", "live")
            self.append({"op": "create", "ts": d.get("created", 0), "node": d})
        with open(self.events_path + ".migrated", "w", encoding="utf-8") as f:
            f.write("migrated from v3 full_cards/live_cards\n")

    # ---- v3 兼容别名（外部脚本可能还在用）----
    def dump_full(self, d):
        self.append({"op": "create", "ts": d.get("created", 0), "node": d})

    def load(self):
        out = {}
        for ev in self.events():
            if ev.get("op") == "create":
                d = ev["node"]
                out.setdefault(d["id"], d)
            elif ev.get("op") == "patch":
                n = out.get(ev.get("id"))
                if n:
                    n.update(ev.get("fields") or {})
        return out

    def save_live(self, ds):
        self.save_snapshot(list(ds))


class SqliteStorage:
    """生产后端：WAL + 事务，事件表 append-only。"""

    def __init__(self, path):
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                op  TEXT NOT NULL,
                doc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_cards(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
        """)
        self.con.commit()

    def append(self, ev: dict):
        self.con.execute("INSERT INTO events(op, doc) VALUES(?,?)",
                         (ev.get("op", "?"), json.dumps(ev, ensure_ascii=False)))
        self.con.commit()

    def events(self):
        for doc, in self.con.execute("SELECT doc FROM events ORDER BY seq"):
            try:
                yield json.loads(doc)
            except json.JSONDecodeError:
                continue

    def save_snapshot(self, nodes):
        with self.con:
            self.con.execute("DELETE FROM live_cards")
            self.con.executemany("INSERT INTO live_cards VALUES(?,?)",
                                 [(n["id"], json.dumps(n, ensure_ascii=False)) for n in nodes])

    # ---- v3 兼容别名 ----
    def dump_full(self, d):
        self.append({"op": "create", "ts": d.get("created", 0), "node": d})

    def load(self):
        out = {}
        for ev in self.events():
            if ev.get("op") == "create":
                out.setdefault(ev["node"]["id"], ev["node"])
            elif ev.get("op") == "patch":
                n = out.get(ev.get("id"))
                if n:
                    n.update(ev.get("fields") or {})
        return out

    def save_live(self, ds):
        self.save_snapshot(list(ds))
