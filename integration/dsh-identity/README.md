# 给大肥鱼（dsh-identity）接记忆管理

这个目录让 [dsh-identity](https://github.com/Anarrogantcat/dsh-identity)（「大肥鱼 · 身份系统」，
MIT）**只把"哪几条记忆该进上下文"这一步**换成 jeyi，其余一切照旧。

## 接管什么、不接管什么

| 环节 | 谁负责 | 说明 |
|------|--------|------|
| `recall` / `memoryCard`（选哪几条进系统提示） | **jeyi** | 这是我们的强项：余弦门控、稠密语义、图扩散、会话作用域 |
| `addMemory`（写入、去重、强化） | 大肥鱼 | 面板、全量卡、来源/置信度全靠它，不动 |
| `correctMemory` / `overturnMemory` / `forgetMemory` | 大肥鱼 | 面板按钮照常 |
| `pinMemory` / `prunePeer` / `consolidatePeer` / `memoryStats` / `listMemory` | 大肥鱼 | 原样透传 |

写入时会**同步镜像一份**给引擎（同一个 peerKey），否则引擎看不到新记忆。

## 为什么值得接

原实现的打分是 `P = C × (0.05 + 0.45A + 0.35I + 0.15E)`，`C = s/(s+K)` 会饱和，
单关键词命中会被 0.15 的门槛切掉；也没有稠密语义、图扩散、会话作用域。
jeyi 恰好在这几项上有实测数据（留出集 77.8% → 85.2%，多跳 0% → 100%）。

## 安装（三步，可逆）

```bash
cd <你的 dsh-identity 包目录>/lib
mv memory.js memory.orig.js
cp <jeyi>/integration/dsh-identity/memory.bridge.js ./memory.js
cp <jeyi>/integration/dsh-identity/memory.fetch-worker.mjs ./memory.fetch-worker.mjs
```

然后起引擎（另开一个终端，或做成开机自启）：

```bash
cd <jeyi>
python -m jeyi.serve --port 8765 --store ./data/peers            # 纯词法，零依赖
python -m jeyi.serve --port 8765 --store ./data/peers --dense    # 稠密（推荐）
```

`index.js` 一行都不用改——桥接模块保持了 `lib/memory.js` 的全部导出。

### 配置（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `JEYI_URL` | `http://127.0.0.1:8765` | 引擎地址 |
| `JEYI` | （空） | `off` 时完全退回原实现 |
| `JEYI_TIMEOUT` | `400` | 单次请求超时（毫秒） |
| `JEYI_TOKEN` | （空） | 可选，`X-Memory-Token` |

**引擎挂了不影响大肥鱼**：任何异常/超时都会静默退回原实现，只是暂时失去增强。

## 验证（跑的是大肥鱼真实的 memory.js）

```bash
python tools/verify_fish_bridge.py --fish <dsh-identity 包目录> --dense
```

它在临时沙箱里把大肥鱼真实的 `lib/memory.js` 装成 `memory.orig.js`，起一个 sidecar，
用同一批记忆、同一批问题分别问两边。实测结果：

```
== 原实现 lib/memory.orig.js ==
  PASS  我对什么过敏       -> 我对花生过敏，吃了会起疹子   [词面重合（基线）]
  PASS  我家猫叫什么名字     -> 我养了一只猫叫团子，它最近老掉毛   [词面重合（基线）]
  FAIL  我家猫身体有什么问题   -> (空)   [多跳：问句里没有"海鲜"]
  PASS  我护照什么时候到期    -> (空)   [弃答：记忆里没有]

== 桥接 jeyi ==
  PASS  我对什么过敏       -> 我对花生过敏，吃了会起疹子   [词面重合（基线）]
  PASS  我家猫叫什么名字     -> 我养了一只猫叫团子，它最近老掉毛   [词面重合（基线）]
  PASS  我家猫身体有什么问题   -> 我养了一只猫叫团子，它最近老掉毛   [多跳：问句里没有"海鲜"]
  PASS  我护照什么时候到期    -> (空)   [弃答：记忆里没有]

RESULT orig=3/4 bridge=4/4 card_ok=true listed=6 => OK
```

`listMemory=6`、`memoryStats`、`forgetMemory` 都在，面板不受影响。

## 三个只有真接线才会暴露的坑（都已修）

1. **把"弃答"当成"引擎挂了"**：引擎返回空结果被判定为失败，于是降级 30 秒 +
   退回原实现去灌水。修法是把"不可达"和"没结果"分开——`call()` 只返回 `null` 表示不可达。
2. **记忆卡要每轮重建，引擎却在做会话内去重**：大肥鱼每轮重建系统提示，
   我们的引擎假设"模型上下文里已经有了，别重复"。修法：`before_reply(stateless=True)`，
   接口层默认开启。
3. **同步接口 vs 异步 HTTP**：`recall`/`memoryCard` 必须是同步返回值。
   修法：Worker 里异步 fetch，主线程 `receiveMessageOnPort` 同步取（不阻塞事件循环、不忙等），
   一次往返 3~20ms。

## 限制（说在前面）

- **只有"选记忆"这一层被替换**。分类、情绪强度、pin、摘要压缩等仍由原实现决定；
  引擎只看到 `text`，看不到 `category` 的情绪权重。
- **镜像依赖写入经过 `addMemory`**。如果有人直接改 `<peerKey>.json`，引擎不会知道；
  这种情况跑一次 `python -m jeyi.serve` 的 `/v1/add` 回填即可。
- **`peerKey` 即隔离边界**。不同群/不同人各一个库，互不可见；不会串。
- 引擎是**独立进程**，多一个大肥鱼之外的东西要保活；退了就自动回落到原实现。

## 卸载

```bash
cd <dsh-identity>/lib && mv memory.orig.js memory.js && rm -f memory.fetch-worker.mjs
```
