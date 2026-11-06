# 接 DeepSeek Harness（jeyi-dsh 插件）

把 jeyi 的记忆卡片接进 DSH 的 system prompt，并在**左侧栏加一个「记忆脑图」入口**：
跨会话记住人，每轮只多花 ≤300 字，点开就能看见记忆。

和「接大肥鱼」的区别：大肥鱼那次是替换它自己的 `memory.js`；DSH 这边不需要改宿主的任何文件，
装一个 cordis 插件即可——宿主升级不会把你的改动冲掉，插件挂了宿主也照常说话。

```
       每条用户消息                         每次模型请求
你 ──────────────► jeyi 侧车 ──────────► 插件召回 ≤300 字卡片
                      ▲                        │
                      │  抽取式写回（问句不入卡）  ▼
                      └──────────────── DSH system prompt 最后一节

左侧栏「记忆脑图」──► 浏览器面板 ──► /jeyi-memory/api/*（宿主同源代理）──► 侧车
```

## 左侧入口与面板

插件由两半组成：

| 半边 | 文件 | 作用 |
|------|------|------|
| 宿主（Node） | `lib/index.js` | 召回/写回 + 注册 `/jeyi-memory` 同源代理 |
| 浏览器 | `lib/client.js` | 左栏入口 + 「记忆脑图」面板（列表 / 检索 / 脑图） |

面板里能做的事：

- **返回对话**：面板占的是主区，右上角「返回对话」调 `ctx.layout.selectPanel(null)` 切回会话；
  侧栏点任意会话也会切回。
- **记忆列表**：每条记忆的类型、重要度、作用域（跨会话/仅本会话）、被几个会话确认过；点开看原话、情绪、来源。
- **检索**：输入一句话，走 `/v1/recall`，按相关度显示命中（没命中就明说"宁可弃答也不硬凑"）。
- **脑图**：内嵌实时面板（节点/边/自动刷新），另开浏览器看完整互动版。
- **peer 切换**：改记忆库身份（默认 `dsh`），存在 localStorage。

面板所有请求都有 **8 秒超时**：侧车没起来时显示错误 + 「刷新」，不会一直转圈；
重复请求用序号丢弃过期响应，切 peer 不会串数据。

数据不走 `fetch("http://127.0.0.1:8770")`——浏览器直连本地另一个端口会被同源/CSP 策略挡掉
（实测 `Failed to fetch`）。宿主插件开了同源代理：

```
/jeyi-memory/api/graph?peer=dsh   → http://127.0.0.1:8770/v1/graph?peer=dsh
/jeyi-memory/api/recall           → http://127.0.0.1:8770/v1/recall
/jeyi-memory/panel?peer=dsh       → http://127.0.0.1:8770/?peer=dsh（脑图 HTML）
```

面板因此跟着 DSH 的登录态走，侧车不需要对浏览器开 CORS。
（`jeyi/serve.py` 里仍留了只放行本机 Origin 的 CORS 头，给别的本地页面用。）

## 它到底做了什么

| 时机 | 动作 | 为什么 |
|------|------|--------|
| 用户消息进会话 | `POST /v1/message`：抽取第一人称事实写进记忆库 | 问句、假设、玩笑不入卡（jeyi 自己的抽取规则） |
| 每次 prompt 组装 | `POST /v1/card`：按本轮问题召回，≤300 字 | 检索而不是重放，所以不随会话变长 |
| 召回非空 | 在 `system-prompt/assemble` 末尾追加一节 `jeyi:memory` | 进 system prompt 而不进历史，**每轮重渲染、不累积** |
| 引擎不在线 | 静默跳过，请求照常 | 记忆是增强，不是依赖 |

关键设计：**注入 system prompt，而不是追加一条 user 消息**。DSH 每步重渲染 system prompt，
所以记忆不会像普通消息那样在历史里越滚越大（注水），这也是 token 省下来的地方。

## 安装

已经装过的话，`--status` 那一步会显示 OK。从零开始是四步：

### 1. 起引擎（带稠密）

```powershell
cd <jeyi>
python tools\install_autostart.py --install --port 8770 `
  --store C:\Users\y5000\Documents\DeepSeek工程项目\记忆脑图\data\peers
```

> **端口注意**：jeyi 默认 8765，但百度输入法（BaiduPinyin.exe）会占 8765 且不响应 HTTP，
> 表现为「端口在服务、请求超时」。这里用 8770。换成别的端口时，插件的 `url` 一起改。

确认：

```powershell
curl http://127.0.0.1:8770/v1/health     # {"ok":true,"service":"jeyi","version":"5.0.0"}
```

### 2. 把插件包 link 进 profile

```powershell
$profile = "$env:USERPROFILE\.dsh\profiles\desktop"
New-Item -ItemType Directory -Force "$profile\node_modules" | Out-Null
New-Item -ItemType Junction "$profile\node_modules\jeyi-dsh" `
  -Target "<jeyi>\integration\dsh-plugin"
```

用 Junction 而不是复制：改插件代码只需要重启 DSH，不用重新拷。

### 3. 在 profile 的 `cordis.patch.yml` 末尾加一条

```yaml
- insert:
    - id: jeyi-memory
      name: jeyi-dsh
      config:
        url: http://127.0.0.1:8770
        peer: dsh
        budget: 300
        inject: true
        remember: true
        timeoutMs: 700
```

DSH 的 profile 默认开着 HMR，**保存即生效**，不用重启。看不到效果再重启一次。

### 4. 验证

```powershell
# 插件的纯逻辑单测（不需要 DSH，只连侧车）
& "C:\...\DeepSeek Harness.exe" integration\dsh-plugin\test\plugin.test.mjs http://127.0.0.1:8770
# 期望：jeyi-dsh plugin test: OK

# 宿主半边（召回/写回 + 代理注册）
& "C:\...\DeepSeek Harness.exe" integration\dsh-plugin\test\host.test.mjs <jeyi>\integration\dsh-plugin\lib\index.js

# 浏览器半边（两个槽位注册）
& "C:\...\DeepSeek Harness.exe" integration\dsh-plugin\test\client.test.mjs <jeyi>\integration\dsh-plugin\lib\client.js

# 宿主代理通不通
curl http://127.0.0.1:47219/jeyi-memory/api/health   # 端口换成你当前的 GUI 端口
# 看宿主有没有在调它
curl -X POST http://127.0.0.1:8770/v1/stats -d '{"peer":"dsh"}'
# routes 里应出现 /v1/card 和 /v1/message
```

左侧栏出现「记忆脑图」后点开；如果没出现，刷新一次页面（Ctrl+R），
client 半边是宿主编译期发布的 bundle，页面重载后一定能拿到。
面板报「连不上记忆侧车」时，先点面板里的刷新——侧车重启后页面不会自己重试。

然后随便问一句有关的旧事，比如「我之前说我在做什么项目」——答得上就说明卡片进了 prompt。

## 验证记录（2026-11-06 实测）

| 项 | 结果 |
|----|------|
| 插件热加载 | ✅ profile patch 保存后无需重启，侧车开始收到 `/v1/card`、`/v1/message` |
| 左侧入口 | ✅ 热加载后左栏出现「记忆脑图」，点击打开面板（截图见仓库 issue 记录） |
| 会话 id 传递 | ✅ 请求里的 session = DSH 的 `session-610663db-…` |
| 抽取式写入 | ✅ 问句「安装完了吗？」→ 0 张卡；陈述句 → 1 张卡 |
| 会话隔离 | ✅ S-A 看不到 S-B 的 `scope=session` 记忆，S-B 看得到 |
| 宿主代理 | ✅ `/jeyi-memory/api/health` → 200；`/jeyi-memory/api/graph?peer=dsh` → 4 节点 |
| 引擎宕机 | ✅ 单测里指向死端口，200ms 内原样返回，不抛错 |
| 回归 | ✅ `tests/test_jeyi.py` 40/40 |

> 踩过的坑：浏览器直接 `fetch("http://127.0.0.1:8770")` 会报 `Failed to fetch`（跨源被拦），
> 所以面板不直连侧车，改走宿主的 `/jeyi-memory` 同源代理。

## 省了多少 token

`python tools/replay.py`（项目自带的多轮回放）：

```
记忆总量 302 字；若每轮整段重放 = 4530 字
实际累计注入 207 字 → 是全量重放的 4.6%（平均每轮 13.8 字，压缩比 21.9x）
```

DSH 这边的差异是「注入位置」：同样 300 字，追加成消息会留在历史里反复计费；
进 system prompt 则每步重渲染、被下一次覆盖，历史长度不随轮数增长。

## 配置项

| 字段 | 默认 | 说明 |
|------|------|------|
| `url` | `http://127.0.0.1:8770` | 侧车地址 |
| `peer` | `dsh` | 记忆库身份。同一个 peer = 同一份长期记忆 |
| `budget` | `300` | 每轮注入字符上限 |
| `inject` | `true` | 关掉只写不读（调试用） |
| `remember` | `true` | 关掉只读不写（只读模式） |
| `timeoutMs` | `700` | 单次请求超时；引擎慢时先调大 |

## 卸载（可逆）

```powershell
# 1. 删插件（DSH 立刻不再注入）
$p = "$env:USERPROFILE\.dsh\profiles\desktop\cordis.patch.yml"
#    删掉文末的 - insert: jeyi-memory 那一段

# 2. 删链接
Remove-Item "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\jeyi-dsh" -Force

# 3. 停引擎 / 取消自启
python tools\install_autostart.py --uninstall
```

profile 里还留了安装前的备份：`cordis.patch.yml.bak-jeyi-*`、`package.json.bak-jeyi-*`。

## 两个踩过的坑（已修在项目里）

1. **自启 VBS 编码**：Windows Script Host 只认 ANSI / UTF-16LE(BOM)。
   旧实现写 UTF-8 无 BOM，路径含中文时按 GBK 解码成乱码，登录后静默失败。
   已改为 `encoding="utf-16"`。
2. **自启 VBS 引号**：`sh.Run """C:\py.exe"" ""x.py"""`——VBScript 里嵌引号要写两个。
   旧实现漏了外层引号，WScript 弹「语句未结束 800A0401」。已修。
