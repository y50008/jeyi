/**
 * jeyi-dsh 的浏览器半边：左侧栏入口 + 记忆面板。
 *
 * 这个文件是"已构建"的客户端包格式（window.__ModuleLoader__.load 工厂），
 * 不依赖打包器，宿主 /plugins 路由会直接把它发给浏览器。
 * 只用 baseline 模块：react（不 import 任何 @deepseek-ai/* 客户端包）。
 */
window.__ModuleLoader__.load({
  id: "jeyi-dsh",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    const React = require("react");
    const h = React.createElement;

    /** 侧车默认地址（"打开完整脑图"直连用；面板数据走宿主的同源代理）。 */
    const DEFAULT_URL =
      (typeof globalThis !== "undefined" && globalThis.__JEYI_DSH_URL__) || "http://127.0.0.1:8770";
    /** 宿主插件的同源代理前缀，见 lib/index.js 的 /jeyi-memory 路由。 */
    const API = "/jeyi-memory";
    const PANEL_ID = "jeyi-memory";
    const PEER_KEY = "jeyi-dsh.peer";

    const TYPE_LABEL = {
      fact: "事实", self: "自述", rule: "计划", event: "事件",
      lesson: "道理", reflection: "反思", web: "转述",
    };

    const CSS = `
.jeyi-root{height:100%;overflow:auto;box-sizing:border-box;padding:calc(var(--dsh-frame-top-clearance,48px) + 10px) 28px 32px;color:var(--dsw-alias-label-primary,#1a1a1a);font-size:13px;line-height:20px;}
.jeyi-head{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-bottom:16px;}
.jeyi-title{font-size:20px;line-height:28px;font-weight:600;margin:0;}
.jeyi-sub{color:var(--dsw-alias-label-tertiary,#8a8a8a);font-size:12px;}
.jeyi-spacer{flex:1;}
.jeyi-btn{border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.14));background:var(--dsw-specific-menu,#fff);color:var(--dsw-alias-label-primary,#1a1a1a);border-radius:8px;padding:5px 11px;font-size:12px;cursor:pointer;}
.jeyi-btn:hover{background:var(--dsw-alias-fill-l1,rgba(0,0,0,.05));}
.jeyi-btn.primary{background:#4f7cff;border-color:#4f7cff;color:#fff;}
.jeyi-btn.primary:hover{background:#3d6bf0;}
.jeyi-tabs{display:flex;gap:4px;margin-bottom:14px;border-bottom:.5px solid var(--dsw-alias-border-l1,rgba(0,0,0,.08));}
.jeyi-tab{border:0;background:0 0;padding:7px 12px;font-size:13px;color:var(--dsw-alias-label-tertiary,#8a8a8a);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;}
.jeyi-tab.on{color:var(--dsw-alias-label-primary,#1a1a1a);border-bottom-color:#4f7cff;font-weight:500;}
.jeyi-stats{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;}
.jeyi-pill{border:.5px solid var(--dsw-alias-border-l1,rgba(0,0,0,.1));background:var(--dsw-alias-fill-l1,rgba(0,0,0,.03));border-radius:999px;padding:3px 10px;font-size:12px;color:var(--dsw-alias-label-secondary,#666);}
.jeyi-pill b{color:var(--dsw-alias-label-primary,#1a1a1a);font-weight:600;}
.jeyi-toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;}
.jeyi-input{box-sizing:border-box;height:30px;padding:0 10px;border:.5px solid var(--dsw-alias-border-l2,rgba(0,0,0,.14));border-radius:8px;background:var(--dsw-specific-menu,#fff);color:inherit;font-size:13px;outline:none;}
.jeyi-input:focus{border-color:#4f7cff;}
.jeyi-search{flex:1;min-width:200px;max-width:420px;}
.jeyi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;}
.jeyi-card{border:.5px solid var(--dsw-alias-border-l1,rgba(0,0,0,.1));border-radius:10px;padding:10px 12px;background:var(--dsw-specific-menu,#fff);cursor:pointer;transition:border-color .12s,background .12s;}
.jeyi-card:hover{border-color:#4f7cff66;background:var(--dsw-alias-fill-l1,rgba(0,0,0,.02));}
.jeyi-card.sel{border-color:#4f7cff;box-shadow:0 0 0 1px #4f7cff33;}
.jeyi-card-top{display:flex;align-items:center;gap:6px;margin-bottom:6px;}
.jeyi-type{font-size:11px;line-height:16px;padding:0 6px;border-radius:999px;background:var(--dsw-alias-fill-l2,rgba(0,0,0,.06));color:var(--dsw-alias-label-secondary,#666);}
.jeyi-type.fact{background:#4f7cff14;color:#3d6bf0;}
.jeyi-type.rule{background:#e8a33d18;color:#b97f24;}
.jeyi-type.lesson{background:#34a85318;color:#2b8a45;}
.jeyi-type.reflection{background:#a259ff18;color:#8248d6;}
.jeyi-imp{margin-left:auto;font-size:11px;color:var(--dsw-alias-label-tertiary,#8a8a8a);font-variant-numeric:tabular-nums;}
.jeyi-gist{font-size:13px;line-height:20px;word-break:break-word;}
.jeyi-meta{margin-top:6px;font-size:11px;color:var(--dsw-alias-label-tertiary,#8a8a8a);display:flex;gap:8px;flex-wrap:wrap;}
.jeyi-empty{padding:36px 8px;text-align:center;color:var(--dsw-alias-label-tertiary,#8a8a8a);}
.jeyi-err{border:.5px solid #e5484d55;background:#e5484d0d;color:#c62a2f;border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:12px;}
.jeyi-detail{border:.5px solid var(--dsw-alias-border-l1,rgba(0,0,0,.1));border-radius:10px;padding:12px 14px;margin-bottom:12px;background:var(--dsw-alias-fill-l1,rgba(0,0,0,.02));}
.jeyi-detail h4{margin:0 0 8px;font-size:13px;font-weight:600;}
.jeyi-kv{display:grid;grid-template-columns:72px 1fr;gap:4px 10px;font-size:12px;color:var(--dsw-alias-label-secondary,#666);}
.jeyi-kv b{color:var(--dsw-alias-label-primary,#1a1a1a);font-weight:500;}
.jeyi-frame{width:100%;height:calc(100vh - 210px);min-height:420px;border:.5px solid var(--dsw-alias-border-l1,rgba(0,0,0,.1));border-radius:10px;background:#fff;}
.jeyi-hit{display:flex;gap:8px;align-items:baseline;padding:6px 8px;border-radius:8px;}
.jeyi-hit:hover{background:var(--dsw-alias-fill-l1,rgba(0,0,0,.04));}
.jeyi-score{font-size:11px;color:#3d6bf0;font-variant-numeric:tabular-nums;}
`;

    function injectCss() {
      if (typeof document === "undefined") return;
      const tagId = "jeyi-dsh/memory-panel.css";
      if (document.querySelector('style[data-plugin-css="' + tagId + '"]')) return;
      const tag = document.createElement("style");
      tag.dataset.plugin = "jeyi-dsh";
      tag.dataset.pluginCss = tagId;
      tag.textContent = CSS;
      document.head.appendChild(tag);
    }

    /** 左侧栏图标：三节点一张图（记忆脑图）。 */
    function MemoryIcon(props) {
      const size = (props && props.size) || 18;
      return h("svg", {
        width: size, height: size, viewBox: "0 0 24 24", fill: "none",
        "aria-hidden": "true", focusable: "false",
      },
        h("circle", { cx: 12, cy: 5.2, r: 2.2, stroke: "currentColor", strokeWidth: 1.6 }),
        h("circle", { cx: 5.6, cy: 17.2, r: 2.2, stroke: "currentColor", strokeWidth: 1.6 }),
        h("circle", { cx: 18.4, cy: 17.2, r: 2.2, stroke: "currentColor", strokeWidth: 1.6 }),
        h("path", { d: "M10.6 7.2 7 15.1M13.4 7.2 17 15.1M7.8 17.2h8.4", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" }),
      );
    }

    function useJson(url) {
      const [state, setState] = React.useState({ loading: true, error: "", data: null });
      const seq = React.useRef(0);
      const load = React.useCallback(() => {
        const mine = ++seq.current;
        setState((prev) => ({ ...prev, loading: true, error: "" }));
        fetch(url, { cache: "no-store", signal: AbortSignal.timeout(8000) })
          .then((res) => {
            if (!res.ok) throw new Error("HTTP " + res.status);
            return res.json();
          })
          .then((data) => {
            if (mine === seq.current) setState({ loading: false, error: "", data });
          })
          .catch((err) => {
            if (mine !== seq.current) return;
            const message = err && err.name === "TimeoutError"
              ? "请求超时（8 秒）"
              : String((err && err.message) || err);
            setState({ loading: false, error: message, data: null });
          });
      }, [url]);
      React.useEffect(() => { load(); }, [load]);
      return [state, load];
    }

    function Stats(props) {
      const meta = (props.graph && props.graph.meta) || {};
      const pills = [
        ["记忆", meta.nodes || 0],
        ["关联", meta.edges || 0],
        ["跨会话", meta.cross_session || 0],
        ["会话私有", meta.session_scoped || 0],
        ["每轮注入", (meta.avg_inject_chars || 0) + " 字"],
        ["压缩比", (meta.compression || 0) + "x"],
      ];
      return h("div", { className: "jeyi-stats" },
        pills.map(([label, value]) => h("span", { className: "jeyi-pill", key: label },
          label + " ", h("b", null, String(value)))))
    }

    function MemoryPage(props) {
      const base = (props && props.url) || DEFAULT_URL;
      const api = (props && props.api) || API;
      const initialPeer = (props && props.peer) || "dsh";
      const [peer, setPeer] = React.useState(() => {
        try { return localStorage.getItem(PEER_KEY) || initialPeer; } catch { return initialPeer; }
      });
      const [peerInput, setPeerInput] = React.useState(peer);
      const [tab, setTab] = React.useState("list");
      const [selected, setSelected] = React.useState(null);
      const [query, setQuery] = React.useState("");
      const [hits, setHits] = React.useState(null);
      const [searching, setSearching] = React.useState(false);
      const [state, reload] = useJson(api + "/api/graph?peer=" + encodeURIComponent(peer));

      const switchPeer = () => {
        const next = (peerInput || "").trim() || "dsh";
        setPeer(next);
        setSelected(null);
        setHits(null);
        try { localStorage.setItem(PEER_KEY, next); } catch { /* 隐私模式 */ }
      };

      const search = async (event) => {
        if (event) event.preventDefault();
        const q = (query || "").trim();
        if (!q) { setHits(null); return; }
        setSearching(true);
        try {
          const res = await fetch(api + "/api/recall", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ peer, query: q, top: 20 }),
          });
          const data = await res.json();
          setHits(Array.isArray(data.items) ? data.items : []);
        } catch (err) {
          setHits([]);
        } finally {
          setSearching(false);
        }
      };

      const graph = state.data;
      const nodes = graph && Array.isArray(graph.nodes)
        ? graph.nodes.slice().sort((a, b) => (b.imp || 0) - (a.imp || 0))
        : [];

      const header = h("div", { className: "jeyi-head" },
        h("div", null,
          h("h2", { className: "jeyi-title" }, "记忆脑图"),
          h("div", { className: "jeyi-sub" }, "jeyi 长期记忆 · 每轮只注入 ≤300 字")),
        h("div", { className: "jeyi-spacer" }),
        props && props.onClose
          ? h("button", { className: "jeyi-btn", onClick: props.onClose }, "返回对话")
          : null,
        h("a", { className: "jeyi-btn", href: base + "/?peer=" + encodeURIComponent(peer), target: "_blank", rel: "noreferrer" }, "打开完整脑图"),
        h("button", { className: "jeyi-btn", onClick: reload }, state.loading ? "刷新中…" : "刷新"));

      const tabs = h("div", { className: "jeyi-tabs" },
        h("button", { className: "jeyi-tab" + (tab === "list" ? " on" : ""), onClick: () => setTab("list") }, "记忆列表"),
        h("button", { className: "jeyi-tab" + (tab === "map" ? " on" : ""), onClick: () => setTab("map") }, "脑图"));

      const toolbar = h("div", { className: "jeyi-toolbar" },
        h("input", {
          className: "jeyi-input", value: peerInput, spellCheck: false,
          onChange: (e) => setPeerInput(e.target.value),
          onKeyDown: (e) => { if (e.key === "Enter") switchPeer(); },
          title: "记忆库身份（peer）",
        }),
        h("button", { className: "jeyi-btn", onClick: switchPeer }, "切换"),
        h("form", { style: { display: "flex", gap: 8, flex: 1 }, onSubmit: search },
          h("input", {
            className: "jeyi-input jeyi-search", value: query, placeholder: "检索记忆，例如：我对什么过敏",
            onChange: (e) => setQuery(e.target.value),
          }),
          h("button", { className: "jeyi-btn primary", type: "submit", disabled: searching }, searching ? "检索中…" : "检索")));

      const error = state.error
        ? h("div", { className: "jeyi-err" },
            "连不上记忆侧车（经宿主代理 " + api + " → " + base + "）：" + state.error +
            "。先确认记忆服务在跑（python -m jeyi.serve），再点刷新。")
        : null;

      const detail = selected
        ? h("div", { className: "jeyi-detail" },
            h("h4", null, selected.gist || selected.label),
            h("div", { className: "jeyi-kv" },
              h("span", null, "类型"), h("b", null, TYPE_LABEL[selected.type] || selected.type),
              h("span", null, "重要度"), h("b", null, Math.round((selected.imp || 0) * 100) + "%"),
              h("span", null, "作用域"), h("b", null, selected.scope === "session" ? "仅本会话" : "跨会话"),
              h("span", null, "来源"), h("b", null, (selected.src || []).join(", ")),
              selected.vb && selected.vb.length
                ? h("span", null, "原话") : null,
              selected.vb && selected.vb.length ? h("b", null, selected.vb[0]) : null,
              selected.feel ? h("span", null, "情绪") : null,
              selected.feel ? h("b", null, selected.feel.label + (selected.feel.why ? "：" + selected.feel.why : "")) : null))
        : null;

      const hitList = hits && hits.length
        ? h("div", { className: "jeyi-detail" },
            h("h4", null, "检索结果（" + hits.length + "）"),
            hits.map((item) => h("div", { className: "jeyi-hit", key: item.id },
              h("span", { className: "jeyi-score" }, (item.score || 0).toFixed(2)),
              h("span", null, item.text))))
        : (hits ? h("div", { className: "jeyi-detail" }, h("h4", null, "检索结果"), h("div", { className: "jeyi-sub" }, "没有命中——jeyi 宁可弃答也不硬凑。")) : null);

      const list = nodes.length
        ? h("div", { className: "jeyi-grid" },
            nodes.map((node) => h("div", {
              key: node.id,
              className: "jeyi-card" + (selected && selected.id === node.id ? " sel" : ""),
              onClick: () => setSelected(node),
            },
              h("div", { className: "jeyi-card-top" },
                h("span", { className: "jeyi-type " + (node.type || "fact") }, TYPE_LABEL[node.type] || node.type || "事实"),
                node.scope === "session" ? h("span", { className: "jeyi-type" }, "本会话") : null,
                h("span", { className: "jeyi-imp" }, Math.round((node.imp || 0) * 100) + "%")),
              h("div", { className: "jeyi-gist" }, node.gist || node.label),
              h("div", { className: "jeyi-meta" },
                h("span", null, node.id),
                node.sessions ? h("span", null, "在 " + node.sessions + " 个会话确认") : null))))
        : h("div", { className: "jeyi-empty" },
            state.loading ? "加载中…" : "这个记忆库还是空的。和 agent 聊几句，它会把事实写进来。");

      const body = tab === "map"
        ? h("iframe", {
            className: "jeyi-frame",
            src: api + "/panel?peer=" + encodeURIComponent(peer),
            title: "jeyi 记忆脑图",
          })
        : h("div", null, detail, hitList, list);

      return h("div", { className: "jeyi-root" },
        header, error, tabs, graph ? h(Stats, { graph }) : null, toolbar, body);
    }

    const inject = ["slots", "layout"];

    function apply(ctx) {
      injectCss();
      ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
        name: "sidebar.panellist",
        id: PANEL_ID,
        order: 30,
        label: "记忆脑图",
      }, MemoryIcon));

      ctx.slots.inject("main", () => ctx.slots.register({
        name: "main",
        key: PANEL_ID,
        inject: () => ({
          url: DEFAULT_URL,
          peer: "dsh",
          api: API,
          onClose: () => {
            try { ctx.layout.selectPanel(null); } catch { /* 布局服务不在就只留按钮 */ }
          },
        }),
      }, MemoryPage));
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  },
});
