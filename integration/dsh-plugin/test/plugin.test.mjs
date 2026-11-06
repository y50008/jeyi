import assert from 'node:assert/strict'
import { apply, name } from '../lib/index.js'

const url = process.argv[2] || 'http://127.0.0.1:8770'
// 每次跑用一个新 peer，测试可重复（否则上一轮写进去的记忆会让"首轮应为空"失败）。
const PEER = 'dsh-plugin-test-' + Date.now().toString(36)

function fakeAgent(events) {
  const session = {
    id: 'S-test',
    seq: events.length,
    eventAt: (i) => events[i],
  }
  return { session }
}

function harness(config) {
  const listeners = {}
  const warnings = []
  const ctx = {
    on(event, fn) { listeners[event] = fn },
    inject(_services, _register) { /* 宿主代理在 host.test.mjs 里单独测 */ },
    get() { return undefined },
    logger: { warn: (msg) => warnings.push(String(msg)) },
    baseUrl: 'file:///C:/fake/profile/',
  }
  apply(ctx, config)
  return { listeners, warnings }
}

const assembly = {
  sections: [{ name: 'harness:identity', text: 'You are an AI agent.' }],
  contexts: [],
  tools: [],
  variables: {},
}
const next = async () => assembly

const events = [
  { type: 'turn/start', data: { turn: 1 } },
  { type: 'user/message', data: { content: [{ type: 'text', text: '我对花生过敏，吃了会起疹子' }], source: { kind: 'user' } } },
]
const agent = fakeAgent(events)

const { listeners, warnings } = harness({ url, peer: PEER, budget: 300, timeoutMs: 1200 })
assert.ok(typeof listeners['system-prompt/assemble'] === 'function', 'must register system-prompt/assemble')
assert.equal(name, 'jeyi-memory')

// 1) 正常路径：注入一节 jeyi:memory，且首轮召回为空时不硬凑。
const first = await listeners['system-prompt/assemble'](assembly, { agent }, next)
assert.equal(first.sections.length, 1, 'empty memory must not add a section')

// 2) 写入一条记忆后，第二轮应当召回并注入。
const add = await fetch(`${url}/v1/add`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ peer: PEER, text: '我对花生过敏，吃了会起疹子', session: 'S-test' }),
}).then((r) => r.json())
assert.equal(add.ok, true, 'seed add failed: ' + JSON.stringify(add))

// 事件序号变了：模拟下一条用户消息（key 变化 → 重新召回）
events.push({ type: 'user/message', data: { content: [{ type: 'text', text: '我对什么过敏来着' }], source: { kind: 'user' } } })
agent.session.seq = events.length

const second = await listeners['system-prompt/assemble'](assembly, { agent }, next)
const injected = second.sections.find((s) => s.name === 'jeyi:memory')
assert.ok(injected, 'memory section missing: ' + JSON.stringify(second.sections))
assert.ok(injected.text.includes('花生'), 'recalled text wrong: ' + injected.text)
assert.equal(injected.interpolate, false)
assert.match(injected.text, /长期记忆/)

// 3) 失败降级：引擎地址不可达时原样返回，不抛错。
const broken = harness({ url: 'http://127.0.0.1:59999', peer: 'x', timeoutMs: 200 })
const t0 = Date.now()
const third = await broken.listeners['system-prompt/assemble'](assembly, { agent }, next)
assert.equal(third.sections.length, 1, 'outage must not add a section')
assert.ok(Date.now() - t0 < 1500, 'outage path must fail fast')

// 4) 空会话不炸。
const emptyAgent = { session: { id: 'S-empty', seq: 0, eventAt: () => undefined } }
const fourth = await harness({ url, peer: PEER }).listeners['system-prompt/assemble'](assembly, { agent: emptyAgent }, next)
assert.equal(fourth.sections.length, 1)

console.log('jeyi-dsh plugin test: OK')
console.log('injected =', JSON.stringify(injected.text))
console.log('warnings =', warnings.length)
