```text
 █████╗   ███████╗  ███████╗  ███╗   ██╗  ████████╗          ███████╗  ██╗  ██╗  ██╗
██╔══██╗  ██╔════╝  ██╔════╝  ████╗  ██║  ╚══██╔══╝   ────   ██╔════╝  ██║  ╚██╗██╔╝
███████║  █████╗    █████╗    ██╔██╗ ██║     ██║             █████╗    ██║   ╚███╔╝
██╔══██║  ██╔══╝    ██╔══╝    ██║╚██╗██║     ██║      ────   ██╔══╝    ██║    ██╔██╗
██║  ██║  ███████╗  ███████╗  ██║ ╚████║     ██║             ██║       ██║  ██╔╝ ██╗
╚═╝  ╚═╝  ╚══════╝  ╚══════╝  ╚═╝  ╚═══╝     ╚═╝             ╚═╝       ╚═╝  ╚═╝  ╚═╝
```

> ### 修复你点名的那个 AI 编程 Agent
>
> 一套纯标准库内核，CLI 与 MCP 双入口，诊断、修复、验证同一个目标。
> 不扫描整机，不顺手修别的 Agent，也不拿另一个 Agent 当验证样本。

[English](README.md) / 简体中文

| 10 类故障 | 15 个 Agent | 12 个 MCP 工具 | 5 个平台 | 0 依赖 | 232 测试 |
|:---------:|:-----------:|:--------------:|:-------:|:------:|--------:|
| `catalog.json` | `catalog.json` | stdio 协议 | Win / mac / Linux | 纯标准库 | 全绿 |

---

## 能力树

```
agent-fix
│
├── 入口 1：CLI  (scripts/fix.py)
│   ├── list / agents / info          只读，不动用户状态
│   ├── check <issue> --agent <id>    诊断单个目标
│   ├── apply <issue> --agent <id>    修复 + 验证同一个目标
│   ├── install / uninstall --agent   部署 skill / 清理遗留 hook
│   └── mcp register <agent>          显式注册单个 Agent 的 MCP
│
├── 入口 2：MCP Server  (mcp/server.py, 12 tools)
│   ├── check   apply   info          诊断 / 修复 / 文档
│   ├── agents  versions  logs audit  inventory / 版本 / 日志 / 审计
│   ├── net                              显式 host 的连通性
│   ├── backup  restore                  事务式备份与恢复
│   ├── provider                         生成配置；apply 才写 Claude Code
│   └── hooks                            仅清理遗留 hook
│
├── 内核  (agentfix/, 纯标准库)
│   ├── catalog.py    注册表与路径解析        ← 唯一 agent/issue 数据源
│   ├── state.py      原子写 / ZIP / 回滚     ← 唯一持久化与归档 owner
│   ├── engine.py     检查、修复、诊断
│   ├── result.py     typed 结果与状态旁路
│   ├── report.py     凭据脱敏
│   ├── hooks.py      安装器 / MCP 注册 / 遗留清理
│   ├── mcp.py        MCP 协议与生命周期
│   └── cli.py        命令行与退出码
│
└── 数据  (catalog.json)
    ├── issues  ×10   每类故障 → 适用 Agent 列表
    └── agents  ×15   bin / config home / skills dir / npm 包
```

## 故障目录

| issue id | 症状 | 适用 Agent |
|---|---|---|
| `npm-postinstall-skipped` | `postinstall script was not run` / native binary 缺失 | claude, codex, opencode, pi, gemini, qwen, kimi, minimax |
| `node-version-too-old` | `EBADENGINE`、启动即崩 | 同上 npm 分发类 |
| `npm-registry-mirror` | install 挂起 / `ETIMEDOUT` | 同上 npm 分发类 |
| `gui-path-blind` | GUI 显示"已安装但无法运行"，终端正常 | claude, codex, opencode, hermes |
| `agent-auth-broken` | 未登录 / 401 / 缺 API key | 全部 |
| `provider-config` | 未配置 provider（key / base URL / model） | 全部 |
| `agent-broken-generic` | 二进制跑不起来 | 全部 |
| `net-connectivity` | 指定端点不可达 | claude, codex, opencode, pi, kimi, zcode |
| `opencode-mcp-schema` | opencode.json MCP schema 无效 | opencode |
| `deepseek-harness-broken` | dsh 起不来 | dsh |

## 支持的 Agent

| id | 名称 | 配置目录 |
|---|---|---|
| `claude-code` | Claude Code | `~/.claude` |
| `codex` | Codex CLI | `~/.codex` |
| `opencode` | OpenCode | `~/.config/opencode` |
| `hermes` | Hermes Agent | `~/.local/share/hermes` · `%LOCALAPPDATA%\hermes` |
| `kimi-code` | Kimi Code | `~/.kimi-code` |
| `minimax-code` | MiniMax Code | `~/.minimax` |
| `pi` | Pi (pi-coding-agent) | `~/.pi` |
| `qwen-code` | Qwen Code | `~/.qwen-code` |
| `gemini` | Gemini CLI | `~/.gemini` |
| `cursor` | Cursor | `~/.cursor` |
| `aider` | Aider | `~/.config/aider` |
| `zcode` | ZCode | `~/.zcode` |
| `amp` | Amp | `~/.config/amp` |
| `droid` | Droid | `~/.factory` |
| `dsh` | DeepSeek Harness | — |

> 新增 Agent 是**数据活**：往 `catalog.json` 的 `agents` 里加一条（bin、config home、skills dir、npm 包）即可，CLI 无需改代码。

## 核心规则

所有修复命令都必须同时指定问题和 Agent：

```bash
fix check npm-postinstall-skipped --agent opencode
fix apply npm-postinstall-skipped --agent opencode --yes
```

引擎只解析 `opencode`，只执行适用于它的检查和修复，并且只验证 `opencode`。不会顺带探测、修复或验证设备上的其他 Agent 或模型。

## 快速开始

```bash
git clone https://github.com/qingzhuo-cn/agent-fix.git
cd agent-fix

./scripts/fix list                              # 列出全部故障
./scripts/fix agents                            # 只做 inventory
./scripts/fix check node-version-too-old --agent opencode
./scripts/fix apply node-version-too-old --agent opencode --yes

python scripts/fix.py install --agent opencode  # 部署 skill（仅此一个目标）
```

安装过程不注册启动自愈、不扫描其他 Agent、不批量注册 MCP。

## 工程看点

修复工具最容易犯的错不是修不好，是**报喜**。所以这套内核在几个关键位置都选择了"宁可说不知道"。

| 位置 | 做法 |
|---|---|
| **结果状态** | 四态 `PASS` / `FAIL` / `INCONCLUSIVE` / `SKIPPED`，汇总要求全部 `PASS`；退出码由真实状态决定，不由措辞决定 |
| **退出码** | 0 健康 / 1 失败或不确定 / 2 目标错误 —— 不确定同样非零 |
| **原子写** | 提交后重新核对目标身份，对不上就回滚到落盘前字节；`os.replace` 做不到内核级原子是事实，不是细节 |
| **目录身份** | `(st_dev, st_ino, 条目名签名)` 三元组 —— 只看 `dev/ino` 会被 inode 复用骗过去 |
| **父目录** | 操作开始即记录，提交前后各核对一次；父目录被换掉时，回滚不写进新目录，唯一的好备份也不被挪走 |
| **ZIP 预检** | 先验 EOCD 再构造 `ZipFile`，固定 46 字节缓冲流式走中央目录；伪造 1/10/50 MB 载荷，峰值都停在 0.19 MB |
| **凭据脱敏** | 边界盖到子进程；`token=...` / `{"token": ...}` / `token: ...` 这类最常见写法不再原样漏出 |
| **所有权** | 持久化与归档只有 `state.py` 一个 owner；`hooks.py` 收敛成一条表驱动路径，不新增第二 owner |

两处值得一提的真实修复：

- `uninstall` 曾只收集 `error` 状态，于是 hook 清理返回 `inconclusive` 时照样打印 `ok`、退出 0 —— **一个没能确认完成的清理被记成了成功**。现在这种情况是 `error`。
- `os.path.abspath("CON")` 在 Windows 返回设备命名空间 `\\.\CON`，能让路径绕过组件检查直奔 `mkdir`，并抛出未包装的 `OSError`。现已拒绝整个 `\\.\` 与 `\\?\` 命名空间。

## 证据

| 范围 | 结果 |
|---|---|
| 本地 Windows 3.14 / Windows 3.8 / Ubuntu | 232 测试通过 |
| GitHub Actions 六格矩阵（ubuntu-22.04、windows-2022、macos-15-intel × Python 3.8 / 3.11） | 全绿 |
| MCP 协议冒烟 | 12 工具握手通过，批量工具被协议拒绝 |
| 重构等价性 | 30 组用例差分（3 代理 × 注册/移除 × 5 种文件系统状态），归一化临时目录名后逐字节相同 |
| 代码规模 | 引擎 5276 行 / 测试 4445 行 / 第三方依赖 0 |

## 开发与回归

```bash
python -m unittest discover -s tests -v
python mcp/smoke_test.py
```

## 许可证

[MIT](LICENSE)
