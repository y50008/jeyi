import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'

const entry = process.argv[2]
const mod = await import(pathToFileURL(entry).href)

assert.equal(mod.name, 'jeyi-memory')
assert.deepEqual(mod.inject, [])

const events = {}
const injected = []
const ctx = {
  on(event, handler) { events[event] = handler },
  inject(services, register) { injected.push({ services, register }) },
  get() { return undefined },
  logger: { warn() {}, info() {} },
  baseUrl: 'file:///C:/fake/profile/',
}

mod.apply(ctx, { url: 'http://127.0.0.1:59999', peer: 'host-test', timeoutMs: 150 })
assert.equal(typeof events['system-prompt/assemble'], 'function', 'missing assemble listener')
assert.equal(injected.length, 1, 'missing webServer inject')
assert.deepEqual(injected[0].services, ['webServer'])
assert.equal(typeof injected[0].register, 'function')

// 引擎不可达时：原样返回 assembly（不抛错、不注入）
const assembly = { sections: [{ name: 'a', text: 'x' }], contexts: [], tools: [], variables: {} }
const agent = {
  session: {
    id: 'S-host',
    seq: 1,
    eventAt: () => ({ type: 'user/message', data: { content: [{ type: 'text', text: '你好' }], source: { kind: 'user' } } }),
  },
}
const out = await events['system-prompt/assemble'](assembly, { agent }, async () => assembly)
assert.equal(out.sections.length, 1, 'outage must not inject')

// 代理注册在 fake webServer 上必须不炸
let registered = null
const fakeWeb = {
  effect(fn) { fn() },
  webServer: { register(route) { registered = route; return () => {} } },
}
injected[0].register(fakeWeb)
assert.ok(registered && registered.path === '/jeyi-memory', 'webServer route not registered')

console.log('jeyi-dsh host plugin test: OK')
