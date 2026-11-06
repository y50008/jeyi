/**
 * jeyi-dsh — 把 jeyi 记忆脑图接进 DeepSeek Harness 的 system prompt。
 *
 * 一次请求的生命周期里：
 *   1. 每一段 prompt 组装（system-prompt/assemble）顺带问一次 jeyi：
 *      「这个用户以前说过什么，和本轮问题有关？」
 *   2. 拿回来的 ≤300 字卡片作为 system prompt 的最后一节注入；
 *      它每轮重渲染、不进历史，所以不会像逐轮消息那样越滚越长。
 *   3. 本轮用户原话异步写回 jeyi（先召回、后写入，避免把当前这句话
 *      当成"记忆"再回显一遍）。
 *
 * 设计约束：
 *   · 零依赖：只用 Node 内置 fetch / crypto，不 import 任何 @deepseek-ai 包，
 *     这样 link 进 profile 时不需要额外解析依赖。
 *   · 引擎挂了不影响 DSH：所有网络错误都在本地吞掉，返回原 assembly。
 *   · 每个用户消息只召回一次（按 session+eventSeq 做键），工具循环里的
 *     后续 step 直接用缓存。
 */

export const name = 'jeyi-memory'
export const inject = []

// 装完 / 重载后留下一个时间戳：排查"插件到底有没有被宿主重新加载"最省事。
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULTS = {
  url: 'http://127.0.0.1:8770',
  peer: 'dsh',
  budget: 300,
  inject: true,
  remember: true,
  timeoutMs: 600,
  maxCachedTurns: 64,
}

/** 本插件注入的 user/message 用这个 source.kind 标记，扫历史时跳过。 */
const SOURCE_KIND = 'jeyi-memory'
const HEADER = '【长期记忆 · jeyi 自动召回】以下是此前会话里关于用户的事实，按需使用；与本轮冲突时以本轮为准，不要原样复述：'

/** 只取文本块，别的模态一律忽略。 */
function textOf(content) {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  const parts = []
  for (const block of content) {
    if (block && block.type === 'text' && typeof block.text === 'string') parts.push(block.text)
  }
  return parts.join('\n')
}

/** 会话 id（拿不到就退回空串，仍能工作）。 */
function sessionIdOf(agent) {
  try {
    const id = agent?.session?.id
    return typeof id === 'string' ? id : String(id ?? '')
  } catch {
    return ''
  }
}

/**
 * 从会话日志里倒着找最近一条"真人说的话"。
 * 跳过本插件的注入和 time-context 之类的运行时快照。
 */
function latestUserMessage(agent) {
  try {
    const session = agent?.session
    if (!session) return null
    const seq = Number(session.seq) || 0
    for (let i = seq - 1; i >= 0; i -= 1) {
      const event = session.eventAt(i)
      if (!event || event.type !== 'user/message') continue
      const source = event.data?.source
      const kind = source?.kind
      if (kind === SOURCE_KIND) continue
      if (kind !== undefined && kind !== 'user' && kind !== 'steer') continue
      const text = textOf(event.data?.content).trim()
      if (text) return { text, key: `${sessionIdOf(agent)}:${i}` }
    }
  } catch {
    /* 会话 API 变了也不该拖垮一轮对话 */
  }
  return null
}

/** 带超时的 JSON POST；任何失败都返回 null。 */
async function postJson(url, body, timeoutMs) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) return null
    return await response.json()
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

export function apply(ctx, config = {}) {
  const cfg = { ...DEFAULTS, ...config }
  const base = String(cfg.url || DEFAULTS.url).replace(/\/+$/, '')
  const peer = String(cfg.peer || DEFAULTS.peer)
  const budget = Math.max(60, Math.min(2000, Number(cfg.budget) || DEFAULTS.budget))
  const timeoutMs = Math.max(50, Number(cfg.timeoutMs) || DEFAULTS.timeoutMs)
  const remember = cfg.remember !== false
  const injectMemory = cfg.inject !== false

  /** key -> { card, chars, at } */
  const cache = new Map()

  const cardUrl = `${base}/v1/card`
  const messageUrl = `${base}/v1/message`
  const addUrl = `${base}/v1/add`

  async function recall(userText, sessionId) {
    const result = await postJson(
      cardUrl,
      { peer, query: userText, budget, session: sessionId },
      timeoutMs,
    )
    if (!result || result.ok === false) return null
    const card = typeof result.card === 'string' ? result.card.trim() : ''
    return { card, chars: Number(result.chars) || card.length }
  }

  ctx.on('system-prompt/assemble', async (assembly, context, next) => {
    const assembled = await next()
    try {
      if (!injectMemory) return assembled
      const agent = context?.agent
      if (!agent) return assembled
      const message = latestUserMessage(agent)
      if (!message) return assembled

      let entry = cache.get(message.key)
      if (entry === undefined) {
        const recalled = await recall(message.text, sessionIdOf(agent))
        entry = recalled ?? { card: '', chars: 0 }
        cache.set(message.key, { ...entry, at: Date.now() })
        if (cache.size > cfg.maxCachedTurns) {
          cache.delete(cache.keys().next().value)
        }
        // 先召回、后写入：当前这句话不该被当成"记忆"再回显一遍。
        // /v1/message 走抽取（问句/假设不入卡）；老侧车没有这个路由时退回 /v1/add。
        if (remember) {
          const body = { peer, text: message.text, session: sessionIdOf(agent), origin: 'user' }
          postJson(messageUrl, body, timeoutMs).then((result) => {
            if (result === null) postJson(addUrl, body, timeoutMs).catch(() => {})
          }).catch(() => {})
        }
      }
      if (!entry.card) return assembled
      const text = `${HEADER}\n${entry.card}`
      return {
        ...assembled,
        sections: [
          ...assembled.sections,
          { name: 'jeyi:memory', text, interpolate: false },
        ],
      }
    } catch {
      return assembled
    }
  })

  // 浏览器里直接 fetch 127.0.0.1:8770 会被同源/CSP 策略挡掉，所以由宿主开一条
  // 同源只读代理：/jeyi-memory/api/* → 侧车 /v1/*，/jeyi-memory/panel → 脑图页。
  // 面板跟着 DSH 的登录态走，不用再给侧车开 CORS。
  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => webCtx.webServer.register({
      kind: 'prefix',
      path: '/jeyi-memory',
      resource: async (request) => {
        try {
          const url = new URL(request.url, 'http://localhost')
          const rest = url.pathname.slice('/jeyi-memory'.length) || '/'
          if (rest === '/' || rest === '/panel' || rest === '/panel/') {
            const response = await fetch(`${base}/?${url.searchParams.toString()}`, {
              signal: AbortSignal.timeout(5000),
            })
            return new Response(await response.arrayBuffer(), {
              status: response.status,
              headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
            })
          }
          const target = `${base}/v1${rest.replace(/^\/api/, '')}${url.search}`
          const init = { method: request.method, signal: AbortSignal.timeout(5000), headers: {} }
          if (request.method === 'POST') {
            init.headers['content-type'] = 'application/json'
            init.body = await request.text()
          }
          const response = await fetch(target, init)
          return new Response(await response.arrayBuffer(), {
            status: response.status,
            headers: {
              'content-type': response.headers.get('content-type') || 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            },
          })
        } catch (error) {
          return new Response(JSON.stringify({ ok: false, error: String((error && error.message) || error) }), {
            status: 502,
            headers: { 'content-type': 'application/json; charset=utf-8' },
          })
        }
      },
    }), 'jeyi: memory bridge')
  })

  // 落一个"安装自检"文件：排查"插件到底加没加载"最省事（~/.jeyi/dsh-host-plugin.json）。
  try {
    const here = dirname(fileURLToPath(import.meta.url))
    mkdirSync(join(homedir(), '.jeyi'), { recursive: true })
    writeFileSync(join(homedir(), '.jeyi', 'dsh-host-plugin.json'), JSON.stringify({
      appliedAt: new Date().toISOString(),
      pid: process.pid,
      url: base,
      peer,
      clientBundle: existsSync(join(here, 'client.js')),
    }, null, 2), 'utf8')
  } catch {
    /* 自检信息写不出来不影响记忆 */
  }

  fetch(`${base}/v1/health`, { signal: AbortSignal.timeout(timeoutMs) })
    .then((response) => (response.ok ? response.json() : null))
    .then((health) => {
      if (!health || health.ok !== true) {
        ctx.logger?.warn?.(`[jeyi-dsh] 记忆侧车未响应：${base}（对话不受影响，记忆注入会先空着）`)
      }
    })
    .catch(() => {
      ctx.logger?.warn?.(`[jeyi-dsh] 记忆侧车未响应：${base}（对话不受影响，记忆注入会先空着）`)
    })
}
