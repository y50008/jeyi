memory-brain-agent · v4.1 记忆脑图引擎
========================================
哲学：发现现成的，超越现成的——**超越必须接受检验**。

这是什么
--------
一个可运行的 Agent 长期记忆引擎，目标是同一句话：
**用固定的、极小的 token 预算记住一个人几个月的事，并且记得住、找得回、改得口。**

每一次回复只注入 ≤300 字的记忆（不随对话历史增长），
换来的是一条可检索、可衰减、可改口、可审计的记忆图谱。

v4.0 的核心变化：**把"打分/选包"从一堆互相干扰的启发式，重做成了可测量的检索系统**，
并补上了 v3.1.1 缺失的那场检验（`tools/bench.py`）。

先看数字（合成语料，见 docs/VALIDATION.md 的诚实声明）
------------------------------------------------------------
**主集**（53 问，用于调参）：

| 指标 | v3.1.1 | **v4.0** | 整段上下文 |
|------|--------|----------|------------|
| rank@5 命中率 | 73.6% | **96.2%** | 100% |
| 投递命中（注入包里有没有对的） | 77.1% | **95.8%** | 100% |
| 越界问题弃答正确率 | 0.0% | **100%** | 0% |
| 每轮注入字符 | 43.2 | **37.4** | 484 |
| 检索延迟（52 条记忆） | — | **0.2 ms** | — |

**留出集**（27 问，另一个人设/领域/问法，参数定稿后才写、只跑一次）：

| 指标 | v3.1.1 | v4.0 |
|------|--------|------|
| rank@5 命中率 | 55.6% | **77.8%** |
| 投递命中 | 58.3% | **70.8%** |
| 越界问题弃答 | 0% | **100%** |
| 每轮注入字符 | 40.0 | **27.7** |

**这两组数字必须一起看**：主集 96.2% 里有一半的分数来自"问题复用了记忆原词"；
换成真正改写过问法的留出集，掉到 77.8%。差的那 18.4 个点就是**纯词法检索的天花板**，
剩下的失败集中在同义/上位词改写（"平时喝什么饮料"→"每天喝铁观音"、"我身体有哪些毛病"→"颈椎不好"）。
接一个真 embedding 才能补上，接口已留好。

长时程（`python tools/simulate.py`：把一年压进 3 秒）
----------------------------------------------------
可注入时钟（`brain/clock.py`）让"半年后会发生什么"第一次变成可验证的东西：

| 天 | 工作 | 活跃 | 归档 | 核心可检索 | 小事已归档 | 小事常规 | 小事显式 |
|---:|-----:|-----:|-----:|-----------:|-----------:|---------:|---------:|
| 0 | 5 | 19 | 0 | 3/3 | 0/18 | 18/18 | 18/18 |
| 90 | 5 | 25 | 0 | 3/3 | 0/18 | 18/18 | 18/18 |
| 180 | 1 | 31 | 0 | 3/3 | 0/18 | 18/18 | 18/18 |
| 365 | 1 | 7 | 25 | 3/3 | 17/18 | 1/18 | 18/18 |

读到的是：反复提起的核心记忆一年后仍在；随口一提的小事 17/18 归档；
归档之后常规检索取不到（`1/18` 那条是作为图邻居被顺带提起过、复述刷新的，属设计行为）；
但**显式回忆（"你还记得…吗"）能把 18/18 全部取回**——这份记忆卡永远在。
两条承诺分别在到期前一天被主动提起（`2026-01-09`、`2026-02-28`）。

快速开始
--------
```bash
python demo.py                 # 端到端：写入→建图→检索→多跳→弃答→召回→改口→结算→指标
python tests/test_brain.py     # 36 项测试（无 pytest 依赖）
python tools/bench.py          # 检索质量基准（主集）：v3 基线 vs v4 vs 整段上下文
python tools/bench.py --holdout# 留出集：泛化能力的诚实读数
python tools/simulate.py       # 一年时间线：衰减/归档/到期提醒/显式回忆
python tools/replay.py         # 多轮会话回放：看注入包与省下的 token
python tools/export_map.py     # 导出真实脑图 → ui/brain_map_live.html（双击即看）
```

接入你的 Agent（四个钩子）
-------------------------
```python
from brain import MemoryHooks

ag = MemoryHooks(store_dir="./data")          # 零依赖，默认 JSONL 事件溯源

# 每条用户消息：
ag.on_message(user_text)                      # 写入：三通道 + 建图 + 去重 + 改口
# 生成回复前：
ctx = ag.before_reply(user_text)              # 检索 + 选包
system_prompt += ctx["pack"]                  # ctx["pack"] 是 ≤300 字的记忆块
# 模型输出后：
pending = ag.handle_turn(model_output)        # 解析 [[recall: 词]]，下一轮强制补入
# 会话结束：
ag.on_session_end()                           # 五步结算 + 归档 + 落盘
```

生产替换点（诚实声明）
--------------------
| 位置 | 现状 | 生产建议 |
|------|------|----------|
| 语义召回 | 词法（二元组+一元组+别名表） | 接 `OpenAICompatibleEmb`（任何 /v1/embeddings）；同义改写只能靠它 |
| 冲突复核 | `HeuristicJudge` 词面复核 | 换 `OpenAICompatibleLLM`，已在 `settle()` 接线，失败自动降级为待复核 |
| 写入抽取 | 规则（第一人称/句读/情绪词/日期） | 可换 LLM 抽取，接口在 `hooks.on_message` |
| 衰减/注入常数 | 30 天半衰期、300 字预算 | 用 `tools/bench.py` 在你自己的数据上重校准 |

```python
from brain import MemoryHooks, SqliteStorage, OpenAICompatibleEmb, OpenAICompatibleLLM
ag = MemoryHooks(storage=SqliteStorage("./data/brain.db"),
                 embedding=OpenAICompatibleEmb("https://api.example.com", "KEY", "bge-small"),
                 judge=OpenAICompatibleLLM("https://api.example.com", "KEY", "your-model"))
```

架构（v4）
---------
```
你 → on_message ──► lang.py 分词/情绪/日期/实体 ──► core.py 节点+边+状态机
                                                        │ 事件溯源
                                                        ▼
                                                  storage.py (events.jsonl / sqlite)
你 ← before_reply ◄── pack.py 预算选包 ◄── score.py 倒排索引+余弦门控+图扩散
```

| 模块 | 职责 | v4 关键点 |
|------|------|-----------|
| `brain/clock.py` | 唯一时间源 | 可注入虚拟时钟，长时程行为变成可验证 |
| `brain/lang.py` | 文本原语 | 全项目唯一分词器；中文二元组+内容字一元组+停用词 |
| `brain/core.py` | 全图底座 | 节点/真实边/状态机/按天归档/三切片/`due_at` |
| `brain/score.py` | 检索 | 倒排索引、tf-idf 余弦门控、**可用的**扩散激活与侧抑制、显式回忆 |
| `brain/pack.py` | 选包 | 预算内 MMR、会话去重、必含桶（召回/纠错/到期） |
| `brain/settle.py` | 结算 | 五步结算、idf 加权冲突候选、图式挂载、回响 |
| `brain/hooks.py` | 四钩子 | 写入抽取器、注入编排、召回协议闭环 |
| `brain/storage.py` | 持久化 | 事件溯源（create/patch/edge）、SQLite WAL、v3 迁移 |
| `brain/persona.py` | 人格层 | 证据制 traits、心情半衰、偏置封顶 |
| `brain/adapters.py` | 适配 | embedding / LLM / 冲突复核 |

三通道与双卡制（设计意图，v4 才真正接通）
----------------------------------------
- **通道 1 gist**：极慢衰减的意义轮廓，注入包的主体。
- **通道 2 verbatim**：用户原话锚点。v3 从不注入；v4 只在"24h 内被改口"或
  "用户问的正是那句原话"时附带，兼作防图式失真的纠错锚点。
- **通道 3 feeling**：情绪快照（label/valence/arousal/why/valid_at），7 天半衰，
  参与 `E` 与心情对齐，命中时以 `〔情绪〕` 紧凑标记注入。
- **双卡制**：live 卡可注入可衰减；全量卡是 append-only 事件流，永不注入、永不删改。
  v3 的实现是反的（重启后 superseded 会复活），v4 用事件重放修正。
- **三切片**：working（最近 7 天最活跃的 5 条）/ active / archive 真的都在用；
  归档不等于删除——常规检索不翻归档，但"你还记得…吗"和 `[[recall:]]` 能把它取回来（行首 `↩` 标记），
  这样才对得上「没有真正的遗忘，也没有不遗忘」。

移植与重构
----------
- `docs/design-spec.md` —— 人工重构蓝图（模块/公式/契约/字段表），可直接照着重写。
- `docs/FIXES.md` —— v3→v4 的 B1–B16 修复清单（每条都有对应测试名）。
- `docs/VALIDATION.md` —— 已验证 / 未验证清单与测量方法。

目录
----
```
brain/            引擎（clock/lang/core/score/pack/settle/hooks/storage/persona/adapters）
tests/            36 项测试
tools/            bench.py 基准 · simulate.py 一年模拟 · replay.py 回放
                  export_map.py 导出脑图 · ci_gate.py 质量门禁
ui/brain_map.html 脑图模板（无外部依赖）
ui/brain_map_live.html  由引擎导出生成的真实脑图
demo.py           端到端演示
```

血缘与许可
----------
打分结构与部分常数（半衰期公式、断崖注入、person floor、24h 强插、字符预算、
`[[recall:]]` 协议）校准自 `Anarrogantcat/dsh-identity`（MIT），已在 `docs/design-spec.md` 署名。
本项目代码为本项目逐行自写，采用 MIT（见 LICENSE）。
