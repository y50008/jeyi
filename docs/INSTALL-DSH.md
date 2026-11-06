# 记忆脑图 × DeepSeek Harness 安装指南

> 目标：DSH 每个会话都能记住你，左侧栏多一个「记忆脑图」入口，每轮只多花 ≤300 字。
> 本文从零到跑通约 5 分钟，Windows PowerShell 为例；macOS/Linux 把路径分隔符换掉即可。

## 装完是什么样

| 你会得到 | 说明 |
|---|---|
| 左侧栏「记忆脑图」 | 点开看记忆列表 / 语义检索 / 脑图 |
| 跨会话记忆 | 换个会话说「我之前说我做什么的」，它答得上来 |
| 每轮 ≤300 字注入 | 记忆卡片进 system prompt，**不进历史**，历史不随轮数膨胀 |
| 自动写入 | 每条用户消息抽取第一人称事实；问句、假设、玩笑不入卡 |
| 可检索 / 可遗忘 | 语义检索 + `忘掉 X` 这类改口会被新事实覆盖 |

---

## 前置条件

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11（macOS/Linux 同源，未实测桌面版自动启动） |
| Python | 3.10+（本项目在 3.13 上验证） |
| DSH | DeepSeek Harness 桌面版（profile 目录 `~/.dsh/profiles/desktop`） |
| 磁盘 | 纯词法 ~1MB；带稠密模型 ~150MB |

> 端口：jeyi 默认 8765，但**百度输入法（BaiduPinyin.exe）会占用 8765** 且不响应 HTTP，
> 表现为「端口在监听但请求超时」。本指南统一用 **8770**。

---

## 安装（五步）

下面 `<jeyi>` 指本项目目录，例如
`C:\Users\你\Documents\DeepSeek工程项目\记忆脑图`。

### 1. 建虚拟环境（可选稠密）

```powershell
cd <jeyi>
python -m venv .venv
.\.venv\Scripts\activate

# 零依赖模式到此就够；想要更高召回（留出集 77.8% → 85.2%）继续：
pip install -e ".[dense]"
python tools\setup_dense.py      # 24MB 本地模型 + 自检，约 40 秒
```

跑一遍自检：

```powershell
python tests\test_jeyi.py        # 期望 40/40 通过
python demo.py                   # 端到端演示：写入→检索→多跳→改口→结算
```

### 2. 起记忆服务（并设成开机自启）

```powershell
.\.venv\Scripts\python.exe tools\install_autostart.py --install --port 8770 `
  --store "<jeyi>\data\peers"
```

看到这些就对了：

```
配置文件   C:\Users\你\.jeyi\config.json
开机自启   ...\Startup\jeyi.vbs  （删除该文件即可取消）
当前状态   已运行  port=8770 v5.1.0
面板       http://127.0.0.1:8770/
```

验一下：

```powershell
curl http://127.0.0.1:8770/v1/health
# {"ok": true, "service": "jeyi", "version": "5.1.0"}
```

### 3. 把插件 link 进 DSH profile

```powershell
$profile = "$env:USERPROFILE\.dsh\profiles\desktop"
New-Item -ItemType Directory -Force "$profile\node_modules" | Out-Null
New-Item -ItemType Junction "$profile\node_modules\jeyi-dsh" `
  -Target "<jeyi>\integration\dsh-plugin"
```

用 junction（目录联接）而不是复制：之后改插件代码只需重载，不用再拷。

### 4. 在 profile 的 `cordis.patch.yml` 末尾加一条

文件：`~\.dsh\profiles\desktop\cordis.patch.yml`

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

DSH 的 profile 默认开着 HMR：**保存即生效**，不用重启应用。

### 5. 刷新页面，确认入口

按一次 `Ctrl+R` 刷新 DSH 页面（左侧栏的客户端插件在页面加载时装配）。
左侧栏「自动化任务」下面会出现 **「记忆脑图」**，点开：

- 顶部统计条：记忆 / 关联 / 跨会话 / 会话私有 / 每轮注入字数 / 压缩比
- 记忆列表：点卡片看原话、情绪、来源
- 检索框：输入一句话看命中（没命中会明说「宁可弃答也不硬凑」）
- 「脑图」页签：内嵌实时脑图
- 右上角「返回对话」：随时切回聊天

---

## 验证清单

```powershell
# 1. 服务健康
curl http://127.0.0.1:8770/v1/health

# 2. 宿主代理通不通（端口换成你 DSH 当前的 GUI 端口）
curl http://127.0.0.1:47219/jeyi-memory/api/health

# 3. 插件有没有在调它（routes 里应出现 /v1/card、/v1/message）
curl -X POST http://127.0.0.1:8770/v1/stats -d '{"peer":"dsh"}'
```

三个插件单测（需要 DSH 自带的 node，用 Electron 以 node 模式跑）：

```powershell
$exe = "C:\Users\你\AppData\Local\Programs\DeepSeek Harness\DeepSeek Harness.exe"
$env:ELECTRON_RUN_AS_NODE = 1
& $exe <jeyi>\integration\dsh-plugin\test\plugin.test.mjs http://127.0.0.1:8770
& $exe <jeyi>\integration\dsh-plugin\test\host.test.mjs   <jeyi>\integration\dsh-plugin\lib\index.js
& $exe <jeyi>\integration\dsh-plugin\test\client.test.mjs <jeyi>\integration\dsh-plugin\lib\client.js
```

最后做一次「人味」验证：新开一个会话，问「我之前说我做什么项目的？」——
答得上，说明记忆卡片进了上下文。

---

## 日常怎么用

| 你说 | 会发生什么 |
|---|---|
| 「我对花生过敏，吃了会起疹子」 | 写成一张卡，之后换会话也能召回 |
| 「我之前说我对什么过敏？」 | 按语义召回，命中卡片 |
| 「其实我不对花生过敏，是坚果」 | 隐式改口：旧卡标 superseded，新卡生效 |
| 「忘掉花生这件事」 | 遗忘接口把卡置为 overturned，不再注入 |
| 「你还记得我上次说的那个项目吗」 | 显式回忆：连归档卡也能翻出来 |

记忆量大了以后：

```powershell
python tools\export_map.py --store "data\peers\dsh" --out ui\map_live.html   # 导出静态脑图
curl http://127.0.0.1:8770/                                               # 实时面板
```

---

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| `端口在服务` 但请求超时 | 8765 被百度输入法占用 | 换 `--port 8770` 并同步改插件 `url` |
| 左侧没有「记忆脑图」 | 页面没重新加载客户端 bundle | `Ctrl+R`；仍无则看 profile 里 junction 是否指向正确目录 |
| 面板报「连不上记忆侧车」 | 服务没起 / 端口不对 | `install_autostart.py --status`；起服务后点面板「刷新」 |
| 面板一直转圈 | v5.1.0 之前没有请求超时 | 升级到 v5.1.0；面板所有请求 8 秒超时并给出错误 |
| 开机自启没生效 | 旧版 VBS 编码/引号 bug（已修） | 重跑 `install_autostart.py --install` |
| 稠密没生效 | 缺依赖或模型 | `python -c "from jeyi import dense;print(dense.available(),dense.model_present())"` |
| 注入为空但记忆有 | 引擎弃答（相关问题度不够） | 问得更具体；或 `POST /v1/recall` 带 `"dense_floor":0.5` |
| Windows 控制台乱码 | GBK 控制台 | `chcp 65001` 或设 `PYTHONIOENCODING=utf-8` |

日志：`~\.jeyi\logs\sidecar.log`（超过 5MB 自动轮转）。

---

## 卸载（完全可逆）

```powershell
# 1. 摘掉插件：删 profile 的 cordis.patch.yml 里 jeyi-memory 那一段
# 2. 删链接
Remove-Item "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\jeyi-dsh" -Force
# 3. 停服务 / 取消自启
python tools\install_autostart.py --uninstall
# 4. 删数据（可选）
Remove-Item <jeyi>\data\peers -Recurse -Force
Remove-Item "$env:USERPROFILE\.jeyi" -Recurse -Force
```

安装前的 profile 备份仍在：`cordis.patch.yml.bak-jeyi-*`、`package.json.bak-jeyi-*`。

---

## 它是怎么省 token 的

两条路径的差别在**注入位置**：

```
逐轮追加消息（常见做法）      历史 = [消息1][记忆][消息2][记忆]…   ← 越滚越长
jeyi 的做法                  system prompt 末尾一节，每步重渲染后覆盖 ← 长度恒定
```

实测（`python tools/replay.py`，多轮回放）：

```
记忆总量 302 字；若每轮整段重放 = 4530 字
实际累计注入 207 字 → 全量重放的 4.6%（平均每轮 13.8 字，压缩比 21.9x）
```

检索质量（`python tools/bench.py --holdout`）：零依赖 77.8%，带稠密 85.2%；
弃答率 100%（宁可不答，也不硬凑）。
