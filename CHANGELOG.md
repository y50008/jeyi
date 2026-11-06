# 更新日志

## v5.1.0 — 2026-11-06

**主题：装进 DeepSeek Harness，加一个能看见记忆的入口。**

### 新增

- **DSH 插件 `jeyi-dsh`**（`integration/dsh-plugin/`），宿主 + 浏览器两半：
  - 宿主：每条用户消息走 `/v1/message` 抽取式写回；每次 prompt 组装走 `/v1/card` 召回，
    把 ≤300 字卡片作为 `system prompt` 最后一节注入（每步重渲染、不进历史，历史不膨胀）。
  - 浏览器：左侧栏「记忆脑图」入口 + 面板（记忆列表 / 语义检索 / 内嵌脑图 / peer 切换 / 返回对话）。
  - 宿主开 `/jeyi-memory` 同源代理：浏览器不直连侧车，跟着 DSH 的登录态走，不依赖 CORS。
- **`docs/INSTALL-DSH.md`**：五步安装指南（装引擎 → 起服务 → link 插件 → 写 profile patch →
  刷新验证），含排错表与卸载步骤。
- **侧车新接口 `POST /v1/message`**：`on_message` 抽取式写入（问句、假设、玩笑不入卡）。
- **按请求指定会话**：`/v1/add`、`/v1/message`、`/v1/recall`、`/v1/card` 接受 `session` 字段，
  服务端按 peer 加锁——两个会话并发时不会把彼此的会话私有记忆看串。
- **侧车 CORS**：只放行本机 Origin（`127.0.0.1` / `localhost` / `[::1]`），外站一律不给，
  供本地面板/工具读取。
- `/v1/stats` 增加 `turns` 与路由命中计数 `routes`，排查「插件到底有没有在调我」。
- 宿主侧安装自检文件 `~/.jeyi/dsh-host-plugin.json`（加载时间、端口、peer）。

### 修复

- **开机自启 VBS 编码**：Windows Script Host 只认 ANSI / UTF-16LE(BOM)，旧实现写 UTF-8 无 BOM，
  中文路径被按 GBK 解码成乱码，登录后静默失败。改为 `encoding="utf-16"`。
- **开机自启 VBS 引号**：`sh.Run """C:\py.exe"" ""x.py"""` —— VBScript 内嵌引号要写两个，
  旧实现漏了外层引号，WScript 弹「语句未结束 800A0401」。已修，并用 cscript 探针校验。
- **面板请求无超时**：侧车慢/没起时面板会一直转圈。所有请求加 8 秒超时 + 过期响应丢弃，
  失败显示错误条与「刷新」。
- **面板没有出口**：主区被面板占住后回不到对话。新增「返回对话」
  （`ctx.layout.selectPanel(null)`）；点侧栏会话也会自动切回。

### 实测

| 项 | 结果 |
|---|---|
| 引擎回归 | `tests/test_jeyi.py` 40/40 |
| 检索质量 | 主集 96.2%（词法）/ 98.1%（稠密）；留出集 77.8% / 85.2% |
| 多轮压缩 | 全量重放 4530 字 → 实际注入 207 字（4.6%，21.9x） |
| 插件单测 | assemble 注入 / 宿主代理注册 / 客户端双槽位，3 个全过 |
| 链路 | 左侧入口热加载即出现；`/jeyi-memory/api/health` → 200 |

## v5.0.0

- 检索系统重做（主集 + 留出集 + 一年时间线三套检验），留出集 77.8% → 85.2%。
- 会话作用域（`Node.scope`）与跨会话同一性（`Node.sessions`）。
- 大肥鱼（dsh-identity）桥接。
