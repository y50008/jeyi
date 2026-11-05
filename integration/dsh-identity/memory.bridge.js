/**
 * memory.bridge.js — 把「大肥鱼 · 身份系统」的记忆选层接到 jeyi 引擎上
 *
 * 这是什么：`dsh-identity` 的 `lib/memory.js` 的**同接口替身**。
 * 原实现原封不动地留着（改名为 `memory.orig.js`），只把「哪几条记忆该进上下文」
 * 这一步（`recall` / `memoryCard`）换成 jeyi 来算；
 * 写入、面板、纠正、遗忘、钉住、统计、全量卡……全部仍走原实现。
 *
 * 为什么只接管这一段：
 *   原公式 P = C×(0.05+0.45A+0.35I+0.15E)，C = s/(s+K) 会饱和——
 *   单关键词命中会被 0.15 的门槛切掉（jeyi 的 v3 就是死在这上面）。
 *   而且它没有稠密语义、没有会话作用域、没有图扩散、没有跨会话同一性。
 *   这几项正好是我们的强项，而写入/管理/面板完全不需要动。
 *
 * 安装（两步，可逆）：
 *   cd <dsh-identity>/lib
 *   mv memory.js memory.orig.js
 *   cp .../memory.bridge.js ./memory.js
 *   cp .../memory.fetch-worker.mjs ./memory.fetch-worker.mjs
 *
 * 配置（环境变量）：
 *   JEYI_URL=http://127.0.0.1:8765   引擎地址
 *   JEYI_TOKEN=...                   可选，X-Memory-Token
 *   JEYI=off                         1/off 时完全退回原实现
 *   JEYI_TIMEOUT=400                 单次请求超时（毫秒）
 *
 * 约定：**任何异常都不影响大肥鱼跑**。引擎不可用/超时/脏数据 → 静默退回原实现。
 */

import { Worker, MessageChannel, receiveMessageOnPort } from 'node:worker_threads'

import * as base from './memory.orig.js'

const URL_BASE = (process.env.JEYI_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '')
const TOKEN = process.env.JEYI_TOKEN || ''
const OFF = ['off', '0', 'false'].includes(String(process.env.JEYI || '').toLowerCase())
const TIMEOUT = Math.max(50, Number(process.env.JEYI_TIMEOUT || 400))

// ── 同步桥：worker 里异步 fetch，主线程同步取 ──────────────────────────────
let port1 = null
let seq = 0
let healthy = true
let downUntil = 0
const DOWN_MS = 30_000
const SLEEP = new Int32Array(new SharedArrayBuffer(4))

function startWorker() {
  const { port1: p1, port2 } = new MessageChannel()
  const w = new Worker(new URL('./memory.fetch-worker.mjs', import.meta.url), {
    workerData: { port: port2, baseUrl: URL_BASE, token: TOKEN, timeout: TIMEOUT },
    transferList: [port2],
  })
  w.unref()                       // 不因这个 worker 拖住进程退出
  port1 = p1
}

/** 同步调用引擎；超时或出错返回 null（调用方回退原实现）。 */
function call(path, body) {
  if (OFF || !healthy || Date.now() < downUntil) return null
  if (!port1) {
    try { startWorker() } catch { healthy = false; downUntil = Date.now() + DOWN_MS; return null }
  }
  const my = ++seq
  try { port1.postMessage({ seq: my, path, body }) } catch { healthy = false; downUntil = Date.now() + DOWN_MS; return null }
  const deadline = Date.now() + TIMEOUT
  while (Date.now() < deadline) {
    const msg = receiveMessageOnPort(port1)
    if (msg && msg.message && msg.message.seq === my) {
      if (!msg.message.ok) return null
      return msg.message.data
    }
    // 每 0.5ms 醒一次，既不忙等也不拖长延迟
    Atomics.wait(SLEEP, 0, 0, 0.5)
  }
  return null                     // 超时：本轮回退，且不再惩罚（worker 仍可用）
}

function withFallback(fn, fallback) {
  try {
    const out = fn()
    if (out) return out
  } catch { /* 落到 fallback */ }
  healthy = false
  downUntil = Date.now() + DOWN_MS
  return fallback()
}

// ── 写入：原实现是权威（面板/全量卡照常），同步镜像给引擎 ──────────────────
export function addMemory(peerKey, args, cfg) {
  const out = base.addMemory(peerKey, args, cfg)
  try {
    if (out) {
      call('/v1/add', {
        peer: peerKey,
        text: out.text || args?.text,
        importance: out.importance ?? args?.importance,
        origin: out.origin || args?.origin,
        at: out.at ? Date.parse(out.at) / 1000 : null,
      })
    }
  } catch { /* 镜像失败不影响写入 */ }
  return out
}

// ── 召回：引擎排出顺序，映射回原实现的 item 对象（下游字段一个不变）───────
//    关键区分：**"引擎弃答"和"引擎挂了"是两件事**。
//    前者要尊重（不注入 = 防硬凑），后者才退回原实现。
//    一开始把两者混在一起，导致一次弃答就把引擎整体降级 30 秒。
export function recall(peerKey, query, cfg, top = 5) {
  const d = call('/v1/recall', { peer: peerKey, query, top })
  if (d === null) return base.recall(peerKey, query, cfg, top)   // 引擎不可用
  const items = Array.isArray(d.items) ? d.items : []
  if (!items.length) return []                                   // 引擎说"没有"，就别硬凑
  let all = []
  try { all = base.listMemory(peerKey) || [] } catch { all = [] }
  const byText = new Map(all.map((it) => [String(it.text || '').trim(), it]))
  const out = []
  for (const h of items.slice(0, top)) {
    const key = String(h.text || '').trim()
    const item = byText.get(key)
    if (item) out.push({ ...item, _C: h.C, _P: h.P })
    else out.push({
      id: h.id, text: key, category: h.category || 'event',
      importance: h.importance ?? 0.5,
      at: new Date(h.at || Date.now()).toISOString(),
      origin: 'user', status: 'active', _C: h.C, _P: h.P,
    })
  }
  return out
}

// ── 记忆卡：引擎给的紧凑文本 ───────────────────────────────────────────────
//    空卡是**合法答案**（这轮没有值得注入的记忆），不能退回原实现去灌水。
export function memoryCard(peerKey, cfg, query = '') {
  const d = call('/v1/card', { peer: peerKey, query })
  if (d === null) return base.memoryCard(peerKey, cfg, query)    // 引擎不可用
  return d.card || ''
}

/** 会话切换：把大肥鱼的 peer/会话边界告诉引擎（作用域隔离用）。 */
export function setMemorySession(peerKey, session) {
  return call('/v1/session', { peer: peerKey, session: session || peerKey })
}

// 其余导出原样透传（面板、纠正、遗忘、pin、统计、全量卡……）
export const {
  CATEGORIES, ORIGINS, DEAD_STATUSES, PERSON_KINDS,
  normalizeOrigin, confidenceOf, isLiveMemory, tokenize,
  fullCard, scoreOf, pickInject, listMemory, memoryStats,
  correctMemory, overturnMemory, forgetMemory, pinMemory,
  prunePeer, consolidatePeer, queueRecall, takePendingRecall,
} = base
