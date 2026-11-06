import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'

let captured = null
globalThis.window = {
  __ModuleLoader__: {
    load(def) { captured = def },
  },
}

const fakeReact = {
  createElement: (type, props, ...children) => ({ type, props, children }),
  useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
  useCallback: (fn) => fn,
  useEffect: () => {},
  useMemo: (fn) => fn(),
  useRef: (init) => ({ current: init }),
  useEffectEvent: undefined,
}

const file = process.argv[2]
await import(pathToFileURL(file).href)

assert.ok(captured, 'client bundle did not call window.__ModuleLoader__.load')
assert.equal(captured.id, 'jeyi-dsh')

const mod = captured.factory((name) => {
  if (name === 'react') return fakeReact
  throw new Error('unexpected require: ' + name)
})

assert.equal(typeof mod.apply, 'function', 'missing apply')
assert.deepEqual(mod.inject, ['slots', 'layout'], 'inject must be ["slots", "layout"]')

const registered = []
const fakeCtx = {
  layout: { selectPanel() {} },
  slots: {
    inject(slot, fn) { fn() },
    register(options, component) {
      registered.push({ options, component })
      return () => {}
    },
  },
}

mod.apply(fakeCtx)
assert.equal(registered.length, 2, 'expected two slot registrations, got ' + registered.length)
const panellist = registered.find((r) => r.options.name === 'sidebar.panellist')
const main = registered.find((r) => r.options.name === 'main')
assert.ok(panellist, 'missing sidebar.panellist registration')
assert.equal(panellist.options.id, 'jeyi-memory')
assert.equal(panellist.options.label, '记忆脑图')
assert.equal(typeof panellist.component, 'function')
assert.ok(main, 'missing main registration')
assert.equal(main.options.key, 'jeyi-memory')
assert.equal(typeof main.component, 'function')
assert.equal(typeof main.options.inject, 'function')
assert.equal(main.options.inject().url, 'http://127.0.0.1:8770')
assert.equal(main.options.inject().api, '/jeyi-memory')

console.log('jeyi-dsh client bundle test: OK (2 slots registered)')
