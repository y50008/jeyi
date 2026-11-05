/**
 * memory.fetch-worker.mjs — 给同步桥用的 HTTP worker
 *
 * 主线程要"同步地"问引擎（原实现的 recall/memoryCard 都是同步函数），
 * 但 fetch 是异步的。做法：worker 里做异步 fetch，主线程用
 * `receiveMessageOnPort` 取结果——这是 Node 里唯一不阻塞事件循环、
 * 也不忙等的同步桥接方式。
 */
import { parentPort, workerData } from 'node:worker_threads'

const { port, baseUrl, token, timeout } = workerData

async function call(path, body) {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeout)
  try {
    const res = await fetch(baseUrl + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Memory-Token': token } : {}) },
      body: JSON.stringify(body),
      signal: ctl.signal,
    })
    if (!res.ok) return { ok: false, error: 'http ' + res.status }
    const data = await res.json()
    return data && data.ok !== false ? { ok: true, data } : { ok: false, error: data?.error || 'bad payload' }
  } catch (err) {
    return { ok: false, error: String(err?.message || err) }
  } finally {
    clearTimeout(timer)
  }
}

port.on('message', async (msg) => {
  if (!msg || msg.__ping) { port.postMessage({ seq: msg?.seq ?? 0, ok: true, data: { pong: true } }); return }
  const r = await call(msg.path, msg.body)
  port.postMessage({ seq: msg.seq, ...r })
})

port.postMessage({ ready: true })
