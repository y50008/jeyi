# 安装与使用

从零到"大肥鱼能记住人"，大约 10 分钟。分四步：

1. [装引擎（30 秒）](#一装引擎)
2. [跑起来看看（1 分钟）](#二跑起来看看)
3. [接大肥鱼（3 分钟）](#三接大肥鱼)
4. [开机自启 + 面板（1 分钟）](#四开机自启与面板)

> 只想要 Python 里用、不接大肥鱼？做完第 1、2 步就够，然后看
> [四钩子接入](#五在你的-python-agent-里用)。

---

## 环境要求

| 项 | 最低 | 说明 |
|----|------|------|
| Python | 3.10+ | 核心零依赖，纯标准库 |
| Node | 20+ | 仅接大肥鱼时需要（大肥鱼是 Node 插件） |
| 磁盘 | 1MB / 150MB | 纯词法 / 带稠密（模型 24MB + 依赖） |

Windows / macOS / Linux 都行。下面的命令以 Windows PowerShell 为例，
Linux/macOS 把 `.venv\Scripts\` 换成 `.venv/bin/` 即可。

---

## 一、装引擎

```powershell
git clone https://github.com/y50008/memory-brain-agent.git memory-brain
cd memory-brain
python -m venv .venv
.\.venv\Scripts\activate

# 只想跑纯词法（零依赖，立刻可用）
# 到这里就结束了，不需要 pip install 任何东西

# 想要更高的召回率（留出集 77.8% → 85.2%）
pip install -e ".[dense]"
python tools\setup_dense.py          # 下 24MB 模型 + 自检，约 40 秒
```

`setup_dense.py` 会打印语义自检和真实延迟。如果卡住不动，
换镜像：`python tools\setup_dense.py --fp32`（95MB，路径不同但更稳）。

**中文语料用中文模型，英文语料用英文模型**：

```powershell
python -c "from brain import dense; dense.download(kind='en')"
```

## 二、跑起来看看

```powershell
python tests\test_brain.py     # 40/40 应该全绿
python demo.py                 # 端到端：写入→检索→多跳→弃答→改口→结算
python tools\bench.py          # 检索基准（主集）
python tools\bench.py --holdout    # 留出集（泛化能力的诚实读数）
python tools\simulate.py       # 一年时间线：什么会遗忘、什么会提醒
```

带稠密再跑一遍（用 venv 里的 python）：

```powershell
python tools\bench.py --dense
python tools\bench.py --dense --holdout
```

预期：主集 96.2 → 98.1，留出集 77.8 → 85.2，弃答保持 100%。

## 三、接大肥鱼

大肥鱼 = [`dsh-identity`](https://github.com/Anarrogantcat/dsh-identity)（「大肥鱼 · 身份系统」，MIT）。
它自带一套记忆实现，我们**只替换"哪几条记忆进上下文"这一层**。

### 3.1 起侧车

```powershell
# 另开一个终端（或下面第四节做成开机自启）
.\.venv\Scripts\python.exe -m brain.serve --port 8765 --store .\data\peers --dense
```

看到这行就对了：

```
memory-brain sidecar on http://127.0.0.1:8765  store=...  dense=on
```

### 3.2 装桥接（3 条命令，可逆）

```powershell
cd <你的 dsh-identity 包目录>\lib
move memory.js memory.orig.js
copy <memory-brain>\integration\dsh-identity\memory.bridge.js .\memory.js
copy <memory-brain>\integration\dsh-identity\memory.fetch-worker.mjs .\
```

不知道包在哪？用验证工具自动找：

```powershell
python tools\verify_fish_bridge.py --dense
```

### 3.3 验证

```powershell
python tools\verify_fish_bridge.py --fish <dsh-identity 包目录> --dense
```

它会用**大肥鱼真实的 memory.js** 做对照，输出应该以 `=> OK` 结尾：

```
RESULT orig=3/4 bridge=4/4 card_ok=true listed=6 => OK
```

差别在多跳那条：问「我家猫身体有什么问题」，原实现返回空（问句里没有"海鲜"），
桥接靠图扩散把「团子对海鲜过敏」找出来了。

### 3.4 配引擎地址

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `MEMORY_BRAIN_URL` | `http://127.0.0.1:8765` | 侧车地址 |
| `MEMORY_BRAIN` | 空 | 填 `off` 完全退回原实现 |
| `MEMORY_BRAIN_TIMEOUT` | `400` | 单次请求超时（毫秒） |
| `MEMORY_BRAIN_TOKEN` | 空 | 侧车启了 `--token` 才需要 |

**引擎挂了不影响大肥鱼**：桥接 30 秒内自动退回原实现。

## 四、开机自启与面板

### 4.1 一条命令装好

```powershell
.\.venv\Scripts\python.exe tools\install_autostart.py --install
```

它会：
- 写配置 `~/.memory-brain/config.json`；
- 在「启动」文件夹放一个**不弹窗**的 `memory-brain.vbs`（登录即静默启动）；
- 立刻把服务拉起来并自检。

```
配置文件   C:\Users\你\.memory-brain\config.json
开机自启   ...\Startup\memory-brain.vbs  （删除该文件即可取消）
当前状态   已运行  port=8765 v4.6
面板       http://127.0.0.1:8765/
```

### 4.2 面板

浏览器打开 `http://127.0.0.1:8765/`，就是当前这个"人"的记忆脑图：
节点按类型着色、大小表示重要性、边是共现/主题/蕴含/冲突，
点节点可以"抽丝剥茧"看轮廓→原话/情感→溯源。20 秒自动刷新。

想看别人：`http://127.0.0.1:8765/?peer=<peerKey>`。
列所有 peer：`http://127.0.0.1:8765/v1/peers`。

### 4.3 常用命令

```powershell
python tools\install_autostart.py --status      # 看状态
python tools\install_autostart.py --uninstall   # 取消自启（服务可能还在跑）
python tools\install_autostart.py --install --port 9000 --no-dense --store D:\mem\peers
```

日志：`~/.memory-brain/logs/sidecar.log`（超过 5MB 自动轮转）。

## 五、在你的 Python Agent 里用

```python
from brain import MemoryHooks

ag = MemoryHooks(store_dir="./data")

# ① 每条用户消息
ag.on_message(user_text)

# ② 生成回复前：拿注入块（≤300 字）塞进 system prompt
ctx = ag.before_reply(user_text)
system_prompt += "\n" + ctx["pack"]

# ③ 模型输出后：解析 [[recall: 词]]，下一轮自动补入
ag.handle_turn(model_output)

# ④ 会话结束：结算 + 归档 + 落盘
ag.on_session_end()
```

带稠密：

```python
from brain import dense
ag = MemoryHooks(store_dir="./data", embedding=dense.make_embedder())
```

多会话隔离（v4.4）：

```python
ag.on_message("我对花生过敏")                       # 跨会话长期
ag.remember("先做登录页，下周再排期", scope="session")  # 只在本会话可见
ag.set_session("S2")                                # 切会话
```

## 六、HTTP 接口（非 Python 接入）

侧车就是一套 REST，任何语言都能接：

```powershell
curl -X POST http://127.0.0.1:8765/v1/add    -d '{"peer":"u1","text":"我对花生过敏"}'
curl -X POST http://127.0.0.1:8765/v1/recall -d '{"peer":"u1","query":"我对什么过敏","top":5}'
curl -X POST http://127.0.0.1:8765/v1/card   -d '{"peer":"u1","query":"今天聊什么"}'
```

| 接口 | 作用 |
|------|------|
| `POST /v1/add` | 写入一条记忆 |
| `POST /v1/recall` | 召回（可传 `dense_floor` 调阈值） |
| `POST /v1/card` | 拿注入块（每轮重建用，无状态） |
| `POST /v1/forget` | 遗忘（按 id 或关键词） |
| `POST /v1/session` | 切会话（作用域隔离） |
| `POST /v1/stats` | 指标 |
| `GET /v1/graph` | 脑图数据（`?peer=`） |
| `GET /` | 实时面板 |
| `GET /v1/health` | 健康检查 |

## 七、出问题怎么查

| 现象 | 先看 | 处理 |
|------|------|------|
| 面板打不开 | `install_autostart.py --status` | 没起来就看 `~/.memory-brain/logs/sidecar.log` |
| 大肥鱼没变化 | 环境变量 `MEMORY_BRAIN_URL` 指对了？ | 先 `curl /v1/health`，再跑 `verify_fish_bridge.py` |
| 桥接后记忆变少 | 是不是引擎弃答了 | `POST /v1/recall` 带上 `"dense_floor":0.5` 试试 |
| 稠密没生效 | `python -c "from brain import dense;print(dense.available(),dense.model_present())"` | 缺依赖或模型，重跑 `setup_dense.py` |
| 端口被占 | 会打印"已在服务" | 换 `--port`，或先停掉旧进程 |
| 中文乱码（Windows） | 控制台是 GBK | `chcp 65001`，或设 `PYTHONIOENCODING=utf-8` |

## 八、卸载

```powershell
# 1. 取消自启
python tools\install_autostart.py --uninstall
# 2. 还原大肥鱼
cd <dsh-identity>\lib; move memory.orig.js memory.js; del memory.fetch-worker.mjs
# 3. 删数据（可选）
rm -r .\data\peers, $env:USERPROFILE\.memory-brain
```

---

## 附：目录说明

```
brain/                引擎
  lang.py             分词 / 日期 / 情绪 / 实体
  core.py             节点·边·状态机·切片·作用域
  score.py            倒排索引 + 余弦门控 + 图扩散 + RRF
  pack.py             300 字预算内的选包
  settle.py           五步结算 / 冲突 / 回响
  hooks.py            四个钩子 + 会话作用域
  storage.py          事件溯源（JSONL / SQLite）
  dense.py            可选稠密层（ONNX / numpy）
  serve.py            HTTP 侧车 + 面板
  viz.py              脑图数据包
tools/                bench / simulate / replay / serve_daemon / install_autostart / package_release
integration/dsh-identity/   大肥鱼桥接（桥 + worker + 验证 + 说明）
ui/brain_map.html     脑图前端（无外部依赖）
docs/                 INTRO / INSTALL / VALIDATION / FIXES / design-spec
```
