# 设计说明书 · memory-brain-agent v4.1

> 人工重构的辅助蓝图。逐模块对照重写，行为以 `tests/test_brain.py` 为验收标准。
> 哲学：发现现成的，超越现成的——超越必须接受检验。

---

## 软件基本信息

- 全称：memory-brain-agent 记忆脑图引擎
- 版本：4.1.0
- 开发目的：为对话型 AI Agent 提供类人长期记忆，用固定的极小上下文预算（≤300 字/轮）
  承载长期经历，并保证可检索、可改口、可审计、可遗忘
- 运行环境：Python 3.10+，零第三方依赖

## 一、功能描述

memory-brain-agent 是一套面向对话型 Agent 的长期记忆引擎。它把记忆组织成一张关联图：
节点是记忆条目，边是同句共现、共享罕见实体、蕴含、矛盾、时序等关系；
工作记忆、活跃记忆、归档记忆不是三套存储，而是同一张图按激活度划分的三个切片。
写入永远追加、永不删除——遗忘只是可及性下降，不是可用性消失。

每条记忆采用三通道结构：意义轮廓（gist，慢衰减，注入主体）、原话锚点
（verbatim，用户原话，平时不注入，仅在"刚被改口"或"用户问的正是这句原话"时附带，
兼作防图式失真的纠错锚点）、情感快照（feeling，含标签/效价/唤醒度/原因/发生时间，
7 天半衰）。

写入侧：按句读切分输入，跳过疑问句、假设句与玩笑（含句末问号判定）；
自动抽取第一人称事实与"关于已知对象"的陈述；重复表述按重合系数去重（重复即复述，
不重复建卡）；带改口线索的句子走状态机，命中唯一旧卡才允许更新，多匹配宁可拒绝；
解析日期得到承诺到期时间，抽取专名/地点/数字作为建图锚点。

检索侧：倒排索引 + BM25 平滑 idf 的 tf-idf 余弦相关度，与查询长度、语料规模无关；
相关度是硬门控（无关记忆无论多重要都不进），排序再叠加新鲜度/重要性/情绪先验；
激活扩散从相关度前 5 的种子出发，只走语义边，逐跳衰减 0.6 且每跳仅前 3 名获得传播资格
（侧抑制）；扩散质量保留绝对量纲，强连接可独立把节点拉进候选，弱连接只是加分。

注入侧：300 字符硬预算内做 MMR 选择——近似重复被抑制，会话内已注入过的不再重复
（模型上下文里已经有了），必含桶（上一轮点名召回、24h 内改口、24h 内到期）优先且不被挤掉；
闲聊轮次若一条都没命中，才补最显著的几条人相关记忆维持在场感；越界问题不硬凑。

结算侧：每次会话结束执行五步——新旧记忆交错重放、预测误差检测（冲突只标记待复核，
不静默合并，可交 LLM 复核，失败降级不阻断）、新记忆挂载到相近道理节点、被提及记忆的
再巩固写回、生成受锚点约束的反思节点；随后按半衰期做归档扫描，并设预算上限
（每会话改写节点 ≤5、新增边 ≤6）防止新旧记忆互相覆盖。

持久化采用事件溯源：创建、状态变更、建边都以不可变事件追加，加载即按序重放，
因此改口/推翻在重启后不会复活；同时支持 SQLite（WAL）与 JSONL（默认）两种后端，
并能自动迁移旧版数据。

Agent 通过四个钩子接入：写入（on_message）、回复前检索（before_reply）、
主动回响（on_proactive）、会话结算（on_session_end）；模型可在输出中用
`[[recall: 关键词]]` 点名召回，引擎在下一轮强制注入，形成闭环。

## 二、总体架构

```
             ┌──────────────── Agent ────────────────┐
             │ on_message / before_reply             │
             │ on_proactive / on_session_end         │
             └──────┬──────────────────────┬─────────┘
                    │                      │
            brain/hooks.py          brain/settle.py
         （抽取、编排、召回闭环）      （五步结算、回响）
                    │                      │
        brain/pack.py              brain/score.py
      （预算选包、去重、必含桶）   （倒排索引、余弦门控、扩散）
                    └──────────┬───────────┘
                               │
                    brain/core.py（节点·边·状态机·切片）
                               │
              brain/storage.py        brain/lang.py
          （事件溯源 / SQLite）   （分词·日期·情绪·实体）
                               │
                        brain/adapters.py
                  （embedding / LLM / 冲突复核）
```

## 三、模块设计

### 3.1 brain/clock.py — 唯一时间源
职责：`now()` 与可注入的 `VirtualClock`；`set_clock/reset` 供模拟与测试使用。
重写要点：**任何模块都不得直接调 `time.time()`**。否则衰减、归档、到期提醒、周年回响
这些长时程行为无法验证（`tools/simulate.py` 靠它把一年压进 3 秒）。

### 3.2 brain/lang.py — 文本原语
职责：分词、停用词、问句/假设判定、日期解析、情绪判定、专名/地点/实体抽取、复述判定用的句尾清洗。
关键决策：全项目**只此一处**分词。中文按「二元组 + 内容字一元组 + 停用词」处理：
二元组保短语（"猫叫"），一元组解决跨词命中（"家猫"/"猫叫" 都能命中 "猫"）。
否定词（不/没/别）绝不进停用表——丢掉"不"会把"我不喜欢"变成"我喜欢"。
频率副词（一般/平时/经常/通常）必须停用，否则「我一般几点下班」会被
「我周末一般会去…」抢走（留出集实测）。

### 3.3 brain/core.py — 全图底座
职责：`Node` 数据结构、节点的增改、带权边的增删与度数上限、四状态机、按天归档。
关键决策：
- 边类型 `temporal / cooccur / topic / entail / conflict / entity / rhyme / causal`，
  每节点最多 8 条边（权重低者先淘汰）；
- 半衰期 `H = 30 天 × (1 + 2·重要性) × (0.5 + 0.5·置信度)` → 30~90 天；
- 归档判据 `freshness < 0.05`（约 4.3 个半衰期）且非规则/自我/未到期承诺；
- 三切片：`slice_pass()` 把最近 7 天最活跃的 5 条放进 working，其余回 active；
- 状态变更一律经 `_touch()` 产出 `patch` 事件。

### 3.4 brain/score.py — 检索
职责：倒排索引、相关度、扩散、显式回忆。
公式：
```
rel   = cos(tf-idf(query), tf-idf(node))          ∈ [0,1]   （别名扩展词等权计入）
prior = 0.45·I + 0.30·A + 0.25·E
score = (0.65·rel + 0.25·prior + 0.35·graph) · align · (0.55 if 归档)
门控： rel ≥ 0.08  或  graph ≥ 0.22（强语义连接）
排序： 词面命中永远排在"纯图候选"之前
```
其中 `A` 为鲜活度、`E` 为情绪鲜活度、`I = 重要性×(0.6+0.4·置信度)`、
`align = 1 + 0.12·valence·当前心情`。
重写要点：**相关度必须与查询长度无关**（不能用会饱和的 `s/(s+8)`）；
扩散质量**不能归一化**（归一化会让 1 跳邻居恒等于种子），逐跳传播取 `max` 而非求和
（求和会让"连了很多种子的枢纽"膨胀）；扩散只能走语义边。

### 3.5 brain/pack.py — 注入选包
职责：预算内选包、冗余抑制、会话去重、必含桶、person floor。
关键决策：必含桶（召回/纠错/到期）先入且不被挤掉；MMR 惩罚冗余；
会话内不重复注入（模型上下文里已有），8 轮后放行；纯图入包每轮 ≤1 条；
person floor 只在"纯社交轮且一条未命中"时补最显著者——绝不按插入顺序取最老的。

### 3.6 brain/settle.py — 结算与回响
职责：五步结算、冲突候选、图式挂载、回响触发。
关键决策：冲突只标记不自动定论；反思节点只由锚点+gist 生成，禁自由发挥；
预算 `nodes≤5 / edges≤6`；回响按优先级（到期承诺 > Zeigarnik > 日期 > 情感）取前 2 条。

### 3.7 brain/hooks.py — Agent 挂接层
职责：四钩子、写入抽取器、注入编排、召回协议、指标。
关键决策：只对**真正进入注入包**的节点计复述；`[[recall:]]` 的结果缓存到 `pending`
并在下一轮强制注入；所有自动抽取节点标 `about_person=True` 但不再因此被无脑注入。

### 3.8 brain/storage.py — 事件溯源
接口：`append(ev)` / `events()` / `save_snapshot(nodes)`。
事件：`{"op":"create","node":{...}}`、`{"op":"patch","id":..,"fields":{...}}`、
`{"op":"edge","a":..,"b":..,"type":..,"w":..}`。
重写要点：**加载必须靠重放事件**，不能靠"全量卡合并"——否则 superseded 会复活。

### 3.9 brain/persona.py / adapters.py
人格层：证据制 traits（≥3 独立证据才 durable、纯情绪不定型、Owner 否决、90 天退休、
上限 20、行为偏置合计封顶 ±0.12）。适配层：本地哈希向量、OpenAI 兼容 embedding/LLM、
启发式冲突复核（阈值 0.45，走统一分词）。

## 四、数据结构（Node）

| 字段 | 类型 | 说明 |
|------|------|------|
| id / label / type | str | 标识；类型 ∈ event/fact/skill/lesson/reflection/rule/self |
| slice | enum | working / active / archive（激活度切片） |
| importance / origin / confidence | float/str/float | 重要性；出处制置信 user .9 / system .8 / agent .45 / web .4 |
| gist | str | 通道 1：意义轮廓 |
| verbatim | list | 通道 2：原话锚点（≤3 条） |
| feeling | dict | 通道 3：label/valence/arousal/why/valid_at |
| entities | list | 建图锚点（日期/地名/专名/编号） |
| due_at | float | 承诺到期时间（前瞻记忆） |
| sources / mentions / hits | list/list/int | 溯源链；复述时间戳（ACT-R 输入）；命中次数 |
| status | enum | live / superseded / overturned / consolidated |
| supersedes / overturn_reason | str | 状态机留痕 |
| feedback_until | float | 24h 强插截止 |
| review / last_injected / version | bool/float/int | 冲突待复核；上次注入时间；版本号 |

## 五、接口契约

```python
ag = MemoryHooks(store_dir=..., storage=..., embedding=..., judge=..., budget=300)
ag.on_message(text, origin="user") -> [node_id]      # 写入（可能为空）
ag.before_reply(context, k=8) -> {"pack", "ranked", "cites", "candidates", "dropped", "chars"}
ag.on_proactive(cap=2) -> [(触发, node_id)]
ag.on_session_end() -> [日志]
ag.handle_turn(agent_text) -> [node_id]              # 解析 [[recall: 词]]
ag.stats() -> {slices, status, nodes, edges, memory_chars,
               avg_inject_chars, compression, suppression, mood, traits}
```

## 六、人工重构指引

1. 按依赖顺序重写：`clock → lang → core → score → pack → settle → hooks → storage/adapters`
   （`persona` 可并行）。
2. 每完成一个模块跑 `python tests/test_brain.py`；**B1–B21 对应的测试是硬契约**，
   尤其不要退回"全量卡合并加载""seed 取所有命中节点""直接调 time.time()"这三种写法。
3. 常量集中在各模块顶部（`REL_FLOOR/GRAPH_ADMIT/INJECT_CHARS/ARCHIVE_FRESHNESS…`），
   换语料后用 `python tools/bench.py` 重校准，不要凭感觉改。
4. 打分公式改动后必须同时跑主集与留出集：
   主集 `rank@5` 不得低于 96.2%，**留出集不得低于 77.8%**——只看主集会过拟合。
   两个集合里 synonym 类别的失败要保留在报告里：那不是 bug，是缺少 embedding。
5. 任何涉及时间逻辑的改动，必须跑 `python tools/simulate.py`（5 条长时程不变量）。
6. 第三方血缘：打分结构、半衰期公式、注入常数校准自 `Anarrogantcat/dsh-identity`（MIT），
   重写时保留署名说明（见 `docs/FIXES.md` 与 LICENSE）。
