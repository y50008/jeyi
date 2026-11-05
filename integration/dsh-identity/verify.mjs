/**
 * verify.mjs — 用大肥鱼**真实的 memory.js** 做对照：原实现 vs 桥接引擎
 *
 * 由 tools/verify_fish_bridge.py 调用：它会把这个文件连同
 * 大肥鱼的 lib/*.js 一起放进临时沙箱，起一个 sidecar，然后跑这里。
 *
 * 验证四件事：
 *   1. 词面重合的基线问题，两边都能答（桥不能比原来差）
 *   2. 多跳问题（问句里没有答案词），只有桥能答
 *   3. 越界问题，两边都弃答（不能为了"有输出"硬凑）
 *   4. 记忆卡非空、面板接口（listMemory/memoryStats/forgetMemory）原样可用
 */
import * as orig from './lib/memory.orig.js'
import * as bridge from './lib/memory.js'

const CFG = { memory: { enabled: true, recallTop: 5, injectTop: 3, recallMinC: 0.15 } }

const MEMS = [
  ['我对花生过敏，吃了会起疹子', 'fact', 0.9],
  ['我养了一只猫叫团子，它最近老掉毛', 'fact', 0.8],
  ['团子对海鲜过敏，不能乱喂', 'fact', 0.8],
  ['我喜欢喝铁观音，只喝浓香型', 'preference', 0.7],
  ['我下周三要去杭州出差', 'event', 0.7],
  ['我每天早上七点起床', 'fact', 0.5],
]

const QUERIES = [
  ['我对什么过敏', '花生', '词面重合（基线）'],
  ['我家猫叫什么名字', '团子', '词面重合（基线）'],
  ['我家猫身体有什么问题', '海鲜', '多跳：问句里没有"海鲜"'],
  ['我护照什么时候到期', null, '弃答：记忆里没有'],
]

function run(mod, peer, tag) {
  for (const [text, category, importance] of MEMS) {
    mod.addMemory(peer, { text, category, importance, source: 'group', origin: 'user' }, CFG)
  }
  console.log(`\n== ${tag} ==`)
  let pass = 0
  for (const [q, want, note] of QUERIES) {
    const hits = mod.recall(peer, q, CFG, 5).map((h) => h.text)
    const ok = want === null ? hits.length === 0 : hits.some((t) => t.includes(want))
    if (ok) pass++
    console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${q.padEnd(12)} -> ${hits.length ? hits[0].slice(0, 22) : '(空)'}   [${note}]`)
  }
  return pass
}

const pOrig = 'verify-orig'
const pBrid = 'verify-bridge'
const passOrig = run(orig, pOrig, '原实现 lib/memory.orig.js')
const passBrid = run(bridge, pBrid, '桥接 jeyi')

const card = bridge.memoryCard(pBrid, CFG, '我家猫身体有什么问题')
console.log('\n== 记忆卡（桥接）==')
console.log(`  非空: ${Boolean(card && card.trim())}  长度: ${(card || '').length}`)
console.log(`  含"海鲜": ${(card || '').includes('海鲜')}`)
console.log(`  内容: ${String(card || '(空)').replace(/\n/g, ' / ').slice(0, 140)}`)

const listed = orig.listMemory(pBrid)?.length ?? 0
const stats = Boolean(bridge.memoryStats(pBrid))
const forgot = JSON.stringify(bridge.forgetMemory(pBrid, { keyword: '起床' }))
console.log('\n== 透传接口（面板用）==')
console.log(`  listMemory: ${listed} 条`)
console.log(`  memoryStats: ${stats}`)
console.log(`  forgetMemory: ${forgot}`)

const ok = passBrid >= passOrig && passBrid === QUERIES.length
  && Boolean(card && card.includes('海鲜')) && stats && listed === MEMS.length
console.log(`\nRESULT orig=${passOrig}/${QUERIES.length} bridge=${passBrid}/${QUERIES.length} `
  + `card_ok=${Boolean(card && card.includes('海鲜'))} listed=${listed} => ${ok ? 'OK' : 'NG'}`)
process.exit(ok ? 0 : 1)
