# FIXES · v3.1.1 → v4.0

本文逐条记录 v3.1.1 审查中发现的问题、根因、修法与对应测试。
编号 B1–B16 与审查报告一致，测试名可直接检索。

原则：**每条修复都必须有一个会失败的断言**。v3 的 10 个测试全绿而检索是坏的，
因为它只断言"机制存在"，不断言"机制有用"。

---

## P0：断掉的关键路径

### B1 改口在重启后失效
- **现象**：`correct()` 把旧卡置为 superseded，重启后旧卡变回 live，与新卡并存。
- **根因**：`full_cards.jsonl` 只在 `add_node` 时写一次创建快照（status 恒为 live），
  而 `_load()` 把它合并回检索池；`save_live()` 又只写 live 节点，superseded 根本不落盘。
- **修法**：存储改**事件溯源**——`create` / `patch` / `edge` 追加为不可变事件，加载即重放。
  `brain/storage.py` 重写；v3 旧库自动迁移。
- **测试**：`test_B1_superseded_survives_reload`、`test_B9_full_card_is_append_only`。

### B2 冷启动第一轮检索必空
- **现象**：新库第一轮 `ranked == []`，注入包靠 person floor 兜底。
- **根因**：`Scorer.__init__` 时 `_N = 0`，`idf = log(1 + N/(1+df)) ≡ 0`，于是 `C ≡ 0`；
  `on_message` 不刷新索引。demo 没暴露是因为手动调了 `_rebuild()`。
- **修法**：BM25 平滑 idf（N=0 也返回正数）+ 倒排索引增量同步（`_sync`，O(新增节点)）。
- **测试**：`test_B2_cold_start_retrieval`。

### B3 单个关键词命中被地板切掉
- **现象**："我家猫叫什么名字" 检索不到"我养了一只猫叫团子"（实测 score 0.156 < 0.20）。
- **根因**：`C = s/(s+8)` 会饱和，单个 idf≈1.8 的词只能给到 C≈0.18；
  再乘 `inner≈0.67` 后必然低于 `CLIFF_FLOOR=0.20`。门控阈值与分数空间不匹配。
- **修法**：改用 **tf-idf 余弦**（与查询长度、语料规模无关），门控阈值 `REL_FLOOR=0.08`
  只在相关度上生效，排序用 `0.65·rel + 0.25·prior + 0.35·graph`。
- **测试**：`test_B3_single_keyword_passes_gate`。

### B4 "没进上下文的记忆"被算作复述
- **现象**：ranked 8 条、实际注入 3 条，8 条的 `mentions` 全被刷新。
- **根因**：`before_reply` 对 `ranked` 全部调 `mention()`。
- **修法**：`_inject` 返回实际写入包内的 id，只对这些计复述（同时刷新 `last_injected`）。
- **测试**：`test_B4_mention_only_for_injected`。

### B5 每轮重复注入同一批记忆
- **现象**：同一 query 连跑 10 轮，只出现 3 条不同内容，每条被注入 10 次。
- **根因**：无任何会话级去重；且复述刷新让这批记忆越来越"新鲜"，形成富者愈富。
- **修法**：`session_seen` 会话级去重（注入过的内容已在模型上下文里），
  超长会话 8 轮后放行（`RECALL_COOLDOWN_TURNS`）。
- **测试**：`test_B5_no_repeat_within_session`。

### B6 疑问句漏进记忆
- **现象**："我喜欢吃什么？""我是不是喜欢这个？" 整句入卡。
- **根因**：`_ME` 正则把"？"排除在匹配片段外，而 `_SKIP` 靠"吗/呢/吧/？"判断——问号永远看不到。
- **修法**：按句读切分并保留分隔符，用整句（含句末问号）判断 `is_question`；
  另加 wh-词表（什么/怎么/为什么/是否/多少…）与假设/玩笑词表。
- **测试**：`test_B6_question_not_ingested`。

---

## P1：正确性

### B7 person floor 负切片失控
- **现象**：上限设 3，实际注入 7 条。
- **根因**：`[:PERSON_FLOOR - count]`，当已选中 person 节点 ≥3 时变负数，`[:-3]` 返回非预期切片。
- **修法**：显式计数 + `max(0, need)`；且**只在纯社交轮次且一条都没命中时**才补，
  顺序按相关度→显著性（不再按插入顺序取"最老的三条"）。
- **测试**：`test_B7_person_floor_cap`。

### B8 归档阈值单位错
- **现象**：60 秒前提过一次的记忆被归档；400 天前提过两次的记忆永不归档。
- **根因**：`base_activation` 用 ACT-R 原始秒数，阈值 0.15 ≈ 44 秒；
  且 `len(mentions) <= 1` 守卫让所有"提过两次"的节点免疫归档。
- **修法**：改为按半衰期判断 `freshness < 0.05`（约 4.3 个半衰期 ≈ 半年），
  规则/自我认知与未到期承诺永不归档。
- **测试**：`test_B8_archive_uses_days`。

### B9 全量卡不是全量档案
- **现象**：`full_cards.jsonl` 3 行、每 id 1 版、status 全是 live、无状态变迁史。
- **修法**：事件溯源（见 B1）；`patch` 事件记录每次状态/切片/到期/复述变更。
- **测试**：`test_B9_full_card_is_append_only`、`test_B16_v3_data_migration`。

### B10 原话锚点永远到不了模型
- **现象**：注入行只有 `gist`，`verbatim` 从未出现；"防图式失真"的防线没接到上下文。
- **修法**：`pack._line()` 在两种情况下附带原话：①24h 内被纠正过的卡；②查询与原话的
  词面重合高于与 gist 的重合（即"用户问的就是那句原话"）。平时不带，省预算。
- **测试**：`test_B10_verbatim_reaches_model`。

### B11 三通道在生产路径上是空的
- **现象**：10 条真实用户消息 → 0 条有 verbatim、0 条有 feeling；label=gist=前 40 字。
- **修法**：写入器重写——原话进 `verbatim`，情绪词表进 `feeling`（label/valence/arousal/why/valid_at），
  日期解析进 `due_at`，专名/地点/数字进 `entities`，gist 去掉口语填充词。
- **测试**：`test_B11_three_channels_populated`。

### B12 judge 从未被调用
- **现象**：`MemoryHooks(judge=...)` 只是赋值，`settle()` 不接收 judge。
- **修法**：`settle(brain, ids, scorer, judge=...)` 真正接线；启发式先筛，
  候选达阈值才调 LLM；LLM 失败降级为"待人工复核"，绝不静默。
- **测试**：`test_B12_judge_is_wired`。

### B13 中文冲突检测恒为 0
- **现象**：10 条中文记忆结算后边数 = 0，图是空壳。
- **根因**：`set(gist.split())`——中文无空格，整句是一个 token，Jaccard 几乎恒为 0。
- **修法**：所有模块统一走 `brain/lang.tokenize`（二元组 + 内容字一元组 + 停用词）。
- **测试**：`test_B13_edges_are_real`、`test_B14_conflict_detection_on_chinese`。

### B14 `graph` 恒为 1.0，扩散/侧抑制是死代码
- **现象**：所有返回节点的 `graph` 分解项都是 1.0；`(1+0.3·graph)` 是常数。
- **根因**：seed 集合 = 所有 C>0 的节点，每个 seed 的 `out=1.0` 且 `mx=1.0`；
  非 seed 的 C=0 又被门控排除 → 扩散结果永远进不了最终集合。
- **修法**：①seed 只取 top-5；②扩散质量不归一化（保留绝对量纲：边权×逐跳衰减）；
  ③图可以独立把节点拉进候选（`g >= GRAPH_ADMIT`）；④只有语义边参与扩散
  （时序边会把整条会话串成链）；⑤每轮最多 1 条"纯靠图进来"的记忆。
- **测试**：`test_graph_diffusion_multihop`。

### B15 第 14 维指标不是指标
- **现象**：`resident_tokens = working×120 + active×45` 是拍出来的常数；
  `dim14_efficiency = 节点数/该常数` 随节点数单调上升，测的是节点数不是效率。
- **修法**：改为实测——累计注入字符、每轮均值、记忆字符总量、压缩比、抑制次数、边数。
- **测试**：`test_B15_stats_are_measured`。

### B16 v3 数据无法平滑升级
- **修法**：`JsonlStorage` 启动时若发现旧的 `full_cards.jsonl` / `live_cards.json`
  且无事件流，自动导入为 `create` 事件（以 live 卡为准，避免复活 superseded）。
- **测试**：`test_B16_v3_data_migration`。

---

## 第二批（v4.1）：把"没法验证"和"验证会骗人"这两件事修掉

### B17 长时程行为根本无法验证
- **现象**：引擎到处直接调 `time.time()`，衰减/归档/到期提醒/周年回响都是长时程行为，
  在真实时间里等半年不是测试，所以这部分从来没被验证过。
- **修法**：新增 `brain/clock.py`（唯一时间源 + 可注入 `VirtualClock`），
  所有模块改为 `clock.now()`；`tools/simulate.py` 用它把一年压进 3 秒逐日推进。
- **测试**：`test_virtual_clock_drives_decay`、`test_due_commitment_fires_on_due_date`、
  `tools/simulate.py` 的 5 条不变量。

### B18 `working` 切片永远是 0
- **现象**：三切片名存实亡——`working` 从未被赋值，UI 图例里那一圈是空的。
- **修法**：`Brain.slice_pass()`：最近 7 天被提起过的最活跃 5 条进 working，其余回 active；
  每次结算时执行。
- **测试**：`test_working_slice_is_populated`。

### B19 归档 = 真遗忘，与设计哲学矛盾
- **现象**：设计说"遗忘是可及性下降，不是可用性消失"，但 `rank()` 直接 `if slice=="archive": continue`，
  一年前的记忆永远取不回来——这就是真遗忘。
- **修法**：`rank(include_archive=True)` + 0.55 排序惩罚；用户说"你还记得…吗"（`has_recall_cue`）
  或模型用 `[[recall: 词]]` 点名时启用，注入行加 `↩` 前缀提示"这是很久以前的事"。
- **测试**：`test_archive_still_recallable_on_explicit_request`。

### B20 图扩散的枢纽膨胀（留出集暴露）
- **现象**：留出集上，`rel=0.0`、只是"连了很多种子"的枢纽节点排在真正的词面命中之前
  （因为扩散质量按种子累加，连 5 个种子就拿 5 倍质量）。
- **修法**：逐跳传播取 `max` 而非 `+=`；排序时"纯图候选"永远排在"词面命中"之后
  （扩散是补召回，不是抢排名）。
- **证据**：`docs/VALIDATION.md` 留出集一节。

### B21 主集数字是虚高的（最需要说清楚的一条）
- **现象**：主集 rank@5 = 96.2%，但那 53 个问题大量复用记忆原词（"我对什么过敏" → "我对花生过敏"）。
- **修法**：新增**留出集**（另一个人设/领域/问法，参数定稿后才写、只跑一次），
  实测 rank@5 = 77.8%、投递 70.8%。**主集与留出集差 18.4 个点，这就是纯词法检索的天花板。**
- **顺带修掉的真 bug**：频率副词"一般/平时/经常/通常"没进停用表，
  导致「我一般几点下班」被「我周末一般会去跑步」抢走（留出集 +3.7 分）。
- **测试**：CI 里 `ci_gate.py --holdout` 的独立门槛。



### B22 稠密分直接覆盖词法分（v4.2）
- **现象**：接入 bge-small 后主集 96.2% → 86.8%，弃答 100% → 0%。
- **根因**：el = max(sparse, dense)。两个余弦不在同一尺度，稠密会把词法的正确排序整个盖掉；
  且低于阈值的稠密候选仍当扩散种子，把邻居拉进注入包。
- **修法**：改 RRF 名次融合（词法第 1 名推不倒）；准入门槛用**原始分**而不是 RRF 值；
  种子要求"有词面信号或稠密过线"。
- **测试**：	est_dense_hybrid_recalls_paraphrase（无模型时自动跳过）+ bench 双集。

### B23 主题边用单字建图（v4.2）
- **现象**：2~3 条记忆的小库里，"咖啡"和"季度报告"被连成一张网，
  稠密路径上表现为弃答题被灌满。
- **根因**：df ≤ 2 的绝对阈值在小库里毫无区分度——任何两句都共享几个字。
- **修法**：双字词无条件建边（"团子""杭州"），单字仅当库 ≥20 条时才允许。
- **测试**：	est_B13_edges_are_real、	est_graph_diffusion_multihop（都是 3 条记忆的小引擎）。

## 工程与文档

| 项 | 修复 |
|----|------|
| 不是 git 仓库 | `git init` + `.gitignore` + 首次提交 |
| 无 LICENSE | 补 MIT（并保留 dsh-identity 血缘署名） |
| Windows 下测试退出码 1 | `tests/test_brain.py` 开头 `sys.stdout.reconfigure(encoding="utf-8")` |
| UI 是手写假数据 | `tools/export_map.py` 从引擎导出 `ui/brain_map_live.html`（数据/边/统计全真） |
| UI 边类型无默认长度 → NaN | 物理常量表加 `topic`/`entity` 并给 `|| 110` 兜底 |
| UI 另写一套打分公式 | 改为 v4 结构（rel/prior/graph）并在页面标注权威数字以 bench 为准 |
| 文档三处公式打架 | README 与 design-spec 统一到 v4 公式 |
| 性能 O(N) | 倒排索引：3000 节点从 44ms/轮降到 <1ms（见 `docs/VALIDATION.md`） |

---

## 关键常数（集中在各模块顶部，可用 bench 重校准）

| 常数 | 值 | 位置 | 含义 |
|------|----|------|------|
| `REL_FLOOR` | 0.08 | score.py | 词面相关度硬门控 |
| `GRAPH_ADMIT` | 0.22 | score.py | 凭图进候选的最低扩散质量 |
| `GRAPH_BONUS` | 0.35 | score.py | 扩散对排序的加成 |
| `SEED_K` / `SPREAD_DECAY` / `SPREAD_TOPK` | 5 / 0.6 / 3 | score.py | 扩散种子、逐跳衰减、侧抑制 |
| `INJECT_CHARS` | 300 | pack.py | 每轮注入字符上限 |
| `DUP_SIM` / `MAX_GRAPH_ONLY` | 0.75 / 1 | pack.py | 冗余抑制、纯图入包上限 |
| `RECALL_COOLDOWN_TURNS` | 8 | pack.py | 会话内重新注入的轮次间隔 |
| `ARCHIVE_FRESHNESS` | 0.05 | core.py | 归档阈值（约 4.3 个半衰期） |
| `BASE_HALF_LIFE_D` | 30 | core.py | 半衰期基数（30–90 天） |
| `WORKING_CAP` / `WORKING_RECENT_D` | 5 / 7 | core.py | 工作记忆容量与"最近"窗口 |
| `ARCHIVE_PENALTY` | 0.55 | score.py | 显式回忆时归档记忆的排序惩罚 |
| `EXPAND_W` | 1.0 | score.py | 别名扩展词权重（实测降权反而掉分） |
| `DENSE_FLOOR` | 0.58 | score.py | 纯稠密命中进候选的最低余弦（0.55 会击穿弃答） |
| `DENSE_TOP_K` / `DENSE_RRF_K` | 50 / 10 | score.py | 稠密召回条数、RRF 平滑常数 |
