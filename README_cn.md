<div align="center">

<p align="center">
<img width="1000px" alt="agent-fix" src="docs/assets/banner.svg">
</p>

# agent-fix ![Awesome](https://cdn.rawgit.com/sindresorhus/awesome/d7305f38d29fed78fa85652e3a63e154dd8e8829/media/badge.svg)

**AI 编程 Agent 通用修复技能（skill）与命令行工具** —— 用一套技能修复 Claude Code、
Codex、OpenCode、Hermes、Kimi Code、Pi、ZCode、Cursor、Gemini CLI、Aider、Qwen Code
以及任何 npm 分发的 CLI：既可以在终端里用，也可以在程序里调用，还可以被其它 Agent
直接加载使用。

[English](README.md) / 简体中文

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)]()

</div>

## 目录

- [为什么需要 agent-fix](#为什么需要-agent-fix)
- [特性](#特性)
- [快速开始](#快速开始)
- [使用方法](#使用方法)
  - [CLI 命令](#cli-命令)
  - [兼容性矩阵](#兼容性矩阵)
  - [问题目录](#问题目录)
  - [在程序中调用](#在程序中调用)
  - [MCP server（任意 agent 可调用的 13 个工具）](#mcp-server任意-agent-可调用的-13-个工具)
- [工作原理](#工作原理)
- [扩展目录](#扩展目录)
- [常见问题 FAQ](#常见问题-faq)
- [相关项目](#相关项目)
- [许可证](#许可证)

## 为什么需要 agent-fix

AI 编程 Agent 通常通过 npm、图形切换工具（如 CC-Switch）、版本管理器等方式安装和升级。
当这些工具出问题时，所有 Agent 都会以相似的方式坏掉：

- `opencode --version` → **"postinstall script was not run"**（`ignore-scripts` /
  `--ignore-scripts` 的经典陷阱，**每次升级都会复发**）
- `claude --version` → **"native binary not installed"**
- CC-Switch 显示 **"已安装 · 无法运行"**，但终端里明明能用
- `EBADENGINE`、`ETIMEDOUT`、`401 Unauthorized`、`Not logged in` …

这些修复方法散落在各种 GitHub issue 和聊天记录里。**agent-fix** 把它们收集到一个
版本化的机器可读目录（`catalog.json`）加人类可读文档（`fixes/*.md`）中，并附带一个
零依赖的 CLI（`scripts/fix.py`），可以诊断、修复、验证 —— 支持 Windows、macOS、Linux。

本项目源于一次真实的反复事故：同一台机器上，OpenCode 和 Claude Code 五天内坏了五次，
根因相同，但每次都要手动敲不同的命令。有了这个技能，修复只需要一条命令：
`fix apply npm-postinstall-skipped --yes`。

## 特性

- 🔧 **7 类问题，1 条命令** — `fix doctor` 全面体检；`fix apply <id>` 修复并验证
- 🤖 **全 Agent 覆盖，注册表驱动** — `catalog.json` 内置 agent 注册表：Claude Code、
  Codex、OpenCode、Hermes、Kimi Code、Pi、ZCode、Cursor、Gemini CLI、Aider、
  Qwen Code、Amp、Droid + 任意 npm CLI；`fix doctor` 会检查**本机实际安装的每一个
  agent**，而不只是四大主流。新增 agent = 一行数据，零代码
- 🖥️ **跨平台** — Windows（含 Git Bash、WSL 兼容）、macOS、Linux
- 🧩 **技能 + CLI + API 三合一** — 可作为 skill 被 Agent 加载，可在终端调用，也可作为 Python 模块导入
- ⚡ **MCP server** — 零依赖 stdio MCP server（`mcp/server.py`，13 个工具），让 Claude Code、OpenCode、Cursor、ZCode、Codex 把整个工具箱（`fix_doctor`、`net_diagnose`、`provider_setup`…）当原生工具直接调用
- 📦 **零依赖** — 纯 Python 3.8+ 标准库
- 🔁 **可做看门狗** — `fix auto` 自动体检并自动修复；失败时非零退出，可直接挂 cron/CI
- 💉 **启动即自愈** — 安装器会自动注册各 Agent 的启动钩子（Claude Code `SessionStart`、Codex `[hooks] session_start`、OpenCode 插件、Hermes cron 看门狗），每次启动 agent 自动体检+修复；`fix selfheal` 健康时零输出，绝不打扰
- 🧪 **修复必验证** — 每个修复都以真实命令验证收尾，而不是只跑 `--version`

## 快速开始

```bash
git clone https://github.com/qingzhuo-cn/agent-fix.git
cd agent-fix

# 1) CLI —— 无需安装，直接可用
./scripts/fix doctor

# 2) 把技能安装进你的各 Agent（Claude Code / OpenCode / Hermes / Codex 钩子）
./install/install.sh            # POSIX 或 Git Bash
powershell -File install\install.ps1   # Windows PowerShell

# 3) 试试
fix list
```

Windows 用户：完整检查覆盖需要 Git Bash（CLI 会自动检测，找不到时对
npm/node/registry 类检查回退到 cmd.exe）。

## 使用方法

### CLI 命令

| 命令 | 作用 | 退出码 |
|------|------|--------|
| `fix list` | 列出目录中的全部问题 | 0 |
| `fix agents` | 列出 agent 注册表及本机已安装的 agent | 0 |
| `fix check` | 运行全部诊断（含每个已装 agent 的二进制检查） | 0 健康 / 1 有问题 |
| `fix check <id>...` | 只诊断指定问题 | 0 / 1 |
| `fix doctor` | `fix check` 的别名 | 0 / 1 |
| `fix apply <id> [--yes]` | 修复一个问题并验证 | 0 验证通过 |
| `fix auto` | 全部体检 → 自动修复有问题的（看门狗模式） | 0 全部修复 |
| `fix info <id>` | 打印对应的 `fixes/` 文档 | 0 |
| `fix --json` / `fix check --json` | 给程序用的机器可读输出 | — |

典型会话：

```bash
$ fix doctor
== npm-postinstall-skipped: npm postinstall skipped -> native binary missing
    [FAIL] opencode binary runs
          Error: postinstall script was not run
   -> BROKEN. Fix with: fix apply npm-postinstall-skipped --yes

$ fix apply npm-postinstall-skipped --yes
    [FIX ] Re-run opencode postinstall        → ok (12.4s)
    [FIX ] Re-run claude-code install script  → ok (1.1s)
    [VERIFY OK] opencode --version            → v1.18.10
    [VERIFY OK] claude --version              → 2.1.220 (Claude Code)
=> verified OK
```

### 兼容性矩阵

| Agent | 技能格式 | 安装路径 | 自动加载 |
|-------|---------|----------|----------|
| Hermes | `SKILL.md` | `~/.local/share/hermes/skills/agent-fix/`（Win: `%LOCALAPPDATA%\hermes\skills\agent-fix\`） | ✅ |
| Claude Code | `SKILL.md` | `~/.claude/skills/agent-fix/` | ✅ |
| Codex CLI | `SKILL.md` + `AGENTS.md` | `~/.codex/skills/agent-fix/` | ✅ |
| OpenCode | `SKILL.md` + `AGENTS.md` | `~/.config/opencode/skill/agent-fix/` | ✅ |
| Kimi Code | `SKILL.md`（自动发现） | `~/.kimi-code/skills/agent-fix/` | ✅ |
| Pi | `SKILL.md` | `~/.pi/agent/skills/agent-fix/` | ✅ |
| ZCode 及共享 | `SKILL.md` | `~/.agents/skills/agent-fix/` | ✅ |
| Cursor 等 | `AGENTS.md` | 仓库根目录 | ✅ |
| 任意 npm CLI | `fix` CLI | `~/bin/fix` | — |

> 注册表里全部 13 个 agent（含 Gemini CLI、Aider、Qwen Code、Amp、Droid）即使没装
> 本技能也会被 `fix doctor` 自动检测体检 —— 见 [fixes/agent-matrix.md](fixes/agent-matrix.md)。

### 问题目录

| ID | 问题 | 受影响 Agent | 文档 |
|----|------|-------------|------|
| `agent-broken-generic` | 任意已装 agent 二进制无法运行（动态检查，注册表驱动） | 全部 | [doc](fixes/agent-matrix.md) |
| `npm-postinstall-skipped` | npm `ignore-scripts`/`--ignore-scripts` 跳过 postinstall → native binary 缺失 | claude-code, opencode, codex, pi, 任意 npm CLI | [doc](fixes/npm-postinstall.md) |
| `gui-path-blind` | GUI 程序（CC-Switch、ZCode Desktop 等）看不到 Agent 可执行文件（注册表 PATH） | 全部 Agent、CC-Switch | [doc](fixes/gui-path.md) |
| `node-version-too-old` | Node 版本过旧，Agent 启动即崩 | claude-code, codex, opencode, pi | [doc](fixes/node-version.md) |
| `npm-registry-mirror` | npm 安装/升级慢或不可达 | 全部 npm Agent | [doc](fixes/npm-registry.md) |
| `agent-auth-broken` | 未登录 / OAuth 过期 / 缺少 API Key | claude-code, codex, kimi-code, pi | [doc](fixes/agent-auth.md) |
| `provider-config` | 未配置 provider —— 为任意 provider 设置 key/base URL/model（DeepSeek/OpenAI/Anthropic/Google/Ollama/...） | 全部 | [doc](fixes/provider-config.md) |
| `net-connectivity` | Agent API 端点不可达（TCP/DNS/代理层，所有 agent 的底层依赖） | 全部（网络层） | [doc](fixes/net-connectivity.md) |

Agent 专项文档：[Kimi Code](fixes/kimi-code.md) · [Pi](fixes/pi.md) · [ZCode](fixes/zcode.md)

### 在程序中调用

```python
import sys
sys.path.insert(0, "/path/to/agent-fix-skill/scripts")
from fix import load_catalog, check_issue, apply_issue, auto_fix

catalog = load_catalog()
issue = next(i for i in catalog["issues"] if i["id"] == "npm-postinstall-skipped")

state = check_issue(issue, quiet=True)              # 诊断
print("broken" if state["broken"] else "healthy")

outcome = apply_issue(issue, yes=True, quiet=True)  # 修复 + 验证
print("verified:", outcome["verified"])
```

或者用子进程 + `--json`：

```python
import json, subprocess
out = subprocess.run(["fix", "check", "--json"], capture_output=True, text=True)
report = json.loads(out.stdout)
```

### MCP server（任意 agent 可调用的 13 个工具）

同一套工具箱以 MCP server 形式暴露，**任何支持 MCP 的 agent**（Claude Code、
OpenCode、Cursor、ZCode、Codex）都能把它当原生工具调用：

| 分组 | 工具 |
|------|------|
| 核心审查/修复 | `fix_agents`、`fix_doctor`、`fix_check`、`fix_apply`、`fix_info` |
| 小分支技能 | `net_diagnose`（端点延迟+代理）、`version_check`、`config_audit`（解析错误+泄露密钥）、`log_triage`、`backup_configs`、`restore_configs`、`provider_setup`（任意 provider）、`deepseek_setup`（快捷方式） |

```bash
python scripts/mcp_register.py all        # 向所有已装 agent 注册
claude mcp list | grep agent-fix          # 验证: ✔ Connected
```

然后直接对你的 agent 说话：*"run fix_doctor and tell me what's broken"*、
*"net_diagnose — is DeepSeek reachable?"*、*"backup_configs before I upgrade"*、
*"deepseek_setup with key sk-…"*。完整文档：[mcp/README.md](mcp/README.md)。

## 工作原理

```
                ┌─────────────────────────────┐
                │       catalog.json          │  唯一数据源
                │  checks · fixes · verify    │  （问题定义）
                └──────────────┬──────────────┘
                               │
        ┌──────────────────────┼───────────────────────┐
        ▼                      ▼                       ▼
  fixes/*.md            scripts/fix.py           SKILL.md / AGENTS.md
  人类/Agent 可读         CLI + Python API         各 Agent 的加载器
  知识库                 （仅标准库）             （Hermes/Claude/OpenCode/Codex）
```

`catalog.json` 里的每个问题都是数据 —— `checks`（诊断）、`fixes`（修复命令，可按
平台门控）、`verify`（修复后确认）。CLI 只是这套数据的薄引擎，因此新增问题无需改
代码。同样的内容以 `fixes/*.md` 提供给人类和偏好读文档的 Agent。

## 扩展目录

1. 在 `catalog.json` 中追加一个问题块（`id`、`checks`、`fixes`、`verify`、`doc`）。
2. 添加对应的 `fixes/<id>.md` 文档。
3. 验证：`fix check <id>`；用 `fix apply <id> --yes` 测试修复。
4. 提 PR。

## 常见问题 FAQ

**Q: 为什么 OpenCode 每次升级后都会坏？**
A: npm 安装/升级跳过了它的 `postinstall` 脚本（见
[npm-postinstall.md](fixes/npm-postinstall.md)）。用
`fix apply npm-postinstall-skipped --yes` 修一次，然后挂上看门狗：
`0 9 * * * cd /path/to/agent-fix-skill && ./scripts/fix auto >> fix.log 2>&1`。

**Q: CC-Switch 显示"已安装 · 无法运行"，但终端里能用？**
A: GUI 程序不会继承 shell 的 PATH，它读的是 Windows 注册表 PATH。执行
`fix apply gui-path-blind --yes`，然后重启 GUI 程序。详见
[gui-path.md](fixes/gui-path.md)。

**Q: 可以用 DeepSeek 模型吗？**
A: 可以 —— `deepseek-provider` 详细说明了如何把 Claude Code
（`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`）、Codex/OpenCode
（`OPENAI_BASE_URL=https://api.deepseek.com`）和 Hermes 指向 DeepSeek API。详见
[deepseek-provider.md](fixes/deepseek-provider.md)。

**Q: 需要管理员权限吗？**
A: 不需要。全部是用户级操作（配置文件、用户 PATH、用户级 npm 全局目录）。

**Q: 有依赖吗？**
A: 没有。`scripts/fix.py` 是纯 Python 3.8+ 标准库。bash 包装脚本需要 `bash`
（POSIX 或 Windows 上的 Git Bash）。

## 相关项目

- [CC-Switch](https://github.com/farion1231/cc-switch) — Claude/Codex/OpenCode 供应商切换
  工具，其检测逻辑催生了 `gui-path-blind` 文档
- [nvm-windows](https://github.com/coreybutler/nvm-windows) / [fnm](https://fnm.vercel.app) —
  推荐的 Node 版本管理器

## 许可证

[MIT](LICENSE)
