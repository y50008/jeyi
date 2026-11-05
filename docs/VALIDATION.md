# 验证报告 · memory-brain-agent v4.0

验证时间：2026-11-05
验证方式：自动化测试（32 项）+ 检索基准（53 问）+ 多轮回放 + 端到端演示 + 脑图导出截图
对应规范：memory-architecture v3.1.1 + 本次 v4 重构（见 `docs/FIXES.md`）

---

## 一、结论摘要

**v3.1.1 的五个"关键路径断裂"已全部修复并有断言覆盖**：冷启动空检索、单关键词被地板切掉、
图扩散恒为 1、全量卡重启复活、没进上下文的记忆被算作复述。

**检索质量首次有了数字**：在自建 52 条记忆 / 53 问的中文语料上，
rank@5 从 73.6% 提升到 96.2%，投递命中从 77.1% 提升到 95.8%，越界弃答从 0% 提升到 100%，
每轮注入 37.4 字（整段上下文是 484 字，约 13 倍压缩）。

**仍然没做到的**（诚实声明见第四节）：同义改写召回 0%；未跑公开基准；
无多日真实对话数据；所有语料为合成，存在"为自家系统调参"的风险。

## 二、已验证清单（可复现）

| 验证项 | 方法 | 结果 |
|--------|------|------|
| 冷启动第一轮可检索 | `test_B2_cold_start_retrieval` | ✅ 通过 |
| 单关键词命中不被门控切掉 | `test_B3_single_keyword_passes_gate` | ✅ 通过 |
| 改口跨重启不复活（事件溯源） | `test_B1_superseded_survives_reload` | ✅ 通过 |
| 全量卡 append-only（create+patch） | `test_B9_full_card_is_append_only` | ✅ 通过 |
| 只对真正注入的记忆计复述 | `test_B4_mention_only_for_injected` | ✅ 通过 |
| 会话内不重复注入 | `test_B5_no_repeat_within_session` | ✅ 通过 |
| 疑问句不入卡（含句末问号） | `test_B6_question_not_ingested` | ✅ 通过 |
| person floor 无负切片 | `test_B7_person_floor_cap` | ✅ 通过 |
| 归档按天、不豁免陈旧记忆 | `test_B8_archive_uses_days` | ✅ 通过 |
| 原话锚点能进注入包 | `test_B10_verbatim_reaches_model` | ✅ 通过 |
| 三通道/日期/实体都被填充 | `test_B11_three_channels_populated` | ✅ 通过 |
| judge 真的被调用 | `test_B12_judge_is_wired` | ✅ 通过 |
| 中文冲突检测生效 / 边真的建出来 | `test_B14` / `test_B13` | ✅ 通过 |
| 多跳扩散（问题里没有答案词） | `test_graph_diffusion_multihop` | ✅ 通过 |
| 越界问题弃答 | `test_out_of_scope_abstains` | ✅ 通过 |
| 到期承诺强制注入 | `test_due_commitment_is_forced` | ✅ 通过 |
| `[[recall:]]` 协议闭环 | `test_recall_protocol_closes_loop` | ✅ 通过 |
| 长会话注入封顶 | `test_long_session_no_blowup`（50 轮，均值 11 字） | ✅ 通过 |
| 指标为实测值 | `test_B15_stats_are_measured` | ✅ 通过 |
| v3 旧库自动迁移 | `test_B16_v3_data_migration` | ✅ 通过 |
| SQLite 事件表往返 | `test_sqlite_roundtrip` | ✅ 通过 |
| 改口唯一匹配 / traits 证据制 / 归档隔离 / 哈希向量 / embedding | 沿用 v3 契约测试 | ✅ 通过 |

合计 **32/32**，`python tests/test_brain.py` 在 Windows（GBK 控制台）与 Linux 均返回 0。

## 三、检索基准（`python tools/bench.py`）

语料：52 条中文个人记忆（偏好/家人/宠物/健康/工作/日程/改口），53 个自然问句，
分 7 类：direct 38、entity 1、synonym 2、temporal 3、aggregate 1、infer 1、multihop 2、abstain 5。

| 指标 | v3.1.1 基线（复刻） | v4.0 | 整段上下文 |
|------|--------------------|------|------------|
| rank@5 命中率 | 73.6% | **96.2%** | 100% |
| 投递命中（注入包里有没有对的） | 77.1% | **95.8%** | 100% |
| 弃答正确率 | 0.0% | **100%** | 0% |
| 每轮注入字符 | 43.2 | **37.4** | 484 |
| 无关行数 / 轮 | 3.26 | **2.02** | — |
| 检索延迟（52 条） | — | **0.28 ms** | — |

分类命中（rank@5）：

| 类别 | n | v3 | v4 |
|------|---|----|----|
| direct | 38 | 76.3% | **100%** |
| entity | 1 | 0% | **100%** |
| temporal | 3 | 100% | 100% |
| aggregate | 1 | 100% | 100% |
| infer | 1 | 100% | 100% |
| multihop | 2 | 0% | **100%** |
| synonym | 2 | 0% | **0%** |

v4 未命中：`宠物叫什么`、`我平时喝什么饮料` —— 都是同义/上位词改写，没有预训练向量模型时无解。

性能：倒排索引把每轮成本从 O(全部节点) 降到 O(候选)。实测 3000 节点时
v3 的线性扫描为 44 ms/轮，v4 索引路径在同一数据量级下 <1 ms（索引为增量维护）。

## 四、未验证清单（诚实声明）

1. **语料是合成的、由作者构造**。它检验的是"修完 bug 有没有变好"，不是"在真实用户身上好不好"。
   作者对自己语料天然过拟合，96.2% 不能外推。
2. **未跑公开基准**。这一档系统的公开基准是 LongMemEval 与 LoCoMo；
   自建 53 问不能与其他系统比较。（`tools/bench.py` 的数据结构可以直接换成它们的样本。）
3. **没有多日真实对话数据**。衰减/归档/回响的长期行为只在合成时间戳上验证过。
4. **没有人工盲评**。"被遗忘投诉率""主动提起命中率"仍无真人数据。
5. **词法检索的天花板已知**：同义改写 0%。别名表（`brain/lang.ALIASES`）只是
   "同一属性的不同说法"（年纪=岁、老婆=妻子），不处理上位词（饮料≠茶）。
6. **参数未在真实数据上重校准**：`REL_FLOOR=0.08`、`GRAPH_ADMIT=0.22`、`INJECT_CHARS=300`
   都是在上述合成语料上选的；换语料应重跑 bench。
7. **写入抽取仍是规则**：第一人称、句读、情绪词表、日期正则。复杂句式会漏抽或误抽
   （例如关系从句里的事实）。接 LLM 抽取的接口已留出，但未联调。

## 五、环境与复现

- Python 3.10+，**零第三方依赖**（纯标准库；embedding/LLM 走 `urllib`）。
- 复现：
  ```bash
  python tests/test_brain.py     # 32/32
  python tools/bench.py          # 输出上表
  python tools/replay.py         # 多轮回放
  python demo.py                 # 端到端
  python tools/export_map.py     # ui/brain_map_live.html
  ```
- 脑图截图：`docs/brain_map.png`（由 headless Edge 渲染导出页得到）。
