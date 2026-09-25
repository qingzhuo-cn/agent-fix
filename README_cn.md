# agent-fix

**用户要求修复哪个 AI 编程 Agent，就只处理哪个目标。**

`agent-fix` 是一个零依赖知识库和 CLI，支持在 Windows、macOS、Linux 上诊断、修复和验证 Claude Code、Codex、OpenCode、Hermes、Kimi Code、Pi、ZCode、Cursor、Gemini CLI、Aider、Qwen Code、Amp、Droid 以及 npm 分发的编程 Agent。

[English](README.md) / 简体中文

## 核心规则

所有修复命令都必须同时指定问题和 Agent：

```bash
fix check npm-postinstall-skipped --agent opencode
fix apply npm-postinstall-skipped --agent opencode --yes
```

引擎只解析 `opencode`，只执行适用于它的检查和修复，并且只验证 `opencode`。不会顺带探测、修复或验证设备上的其他 Agent 或模型。

## 工程看点

这个工具比较特别的地方，不是它能修哪些故障，而是它**不信任自己拿到的任何东西**：ZIP 不信任、文件系统在"检查之后、落盘之前"不信任、Agent 返回的状态文本不信任、连自己跑的命令输出也不信任。下面几处最能说明这种取向。

### 原子写：把"检查后到落盘前"当成攻击窗口

标准库的 `os.replace` **无法**对同用户的并发写入者做到内核级原子。`agentfix/state.py` 不假装它是原子的，而是把整段窗口当成必须防守的面：

- 落盘前捕获目标内容与身份，提交后**再校验一次**；不一致就回滚到落盘前的原内容。
- 目录身份取 `(st_dev, st_ino, 条目名签名)` 三元组。只用 `dev/ino` 不够——inode 复用能让一个全新的文件拿到与旧文件相同的编号。
- 父目录身份在操作开始时捕获，在提交前和提交后各校验一次。父目录被换掉时，回滚**不会**写进新目录，唯一的好备份也**不会**被移过去。
- 目录交换的恢复标记记录 stage / backup / 旧目标三方身份；被篡改的备份宁可不装，也不当成原件。
- 恢复事务在快照或父目录被换掉后直接拒绝，而不是猜。

### ZIP：先预检，再分配

先做 EOCD 预检，再用固定 46 字节缓冲流式遍历中央目录，**确认归档结构自洽之后**才构造 `ZipFile` 对象。伪造条目数曾能跳过中央目录遍历，让尾部载荷被读进内存。现在这类输入在分配发生之前就被拒绝：伪造载荷无论是 1 MB、10 MB 还是 50 MB，峰值占用都稳定在约 0.19 MB。

### 不确定就报告不确定

四态结果（`PASS` / `FAIL` / `INCONCLUSIVE` / `SKIPPED`），保守汇总要求全部 `PASS`；CLI 按真实用户状态返回非零退出码（0 健康 / 1 失败或不确定 / 2 目标错误）。

这里修掉过一个很典型的谎报：`uninstall` 原本只收集 `error`，于是 hook 清理返回 `inconclusive` 时它照样报 `ok` 并退出 0——一个**没能确认完成**的清理被当成了成功。现在这种情况是 `error`。

状态也不再从人类可读文本里推断。`StatusText` / `StatusLines` 携带显式的 `.status` 旁路，退出码由真实状态决定，不由措辞决定。

### 脱敏的边界一直盖到子进程

`api_key`、`access_token`、`secret`、query string、Bearer 头都被遮蔽。但 `token=...`、`{"token": ...}`、`token: ...` 这些配置里最常见的写法原本会**原样漏出**——现已覆盖。子进程的 stdout/stderr 在成功和失败两条路径上都过遮蔽层，原实现只遮了异常那条。

### 收敛，而不是新增 owner

持久化、归档、原子写只有 `state.py` 一个 owner。`hooks.py` 里曾有三份几乎一样的 MCP 注册逻辑，现在收敛成一条表驱动路径，代理之间的差异作为数据留在 `_MCP_JSON_SPECS` 里。`hooks.py` 本身评估过拆分，结论是**不拆**：新增代理本来就是 `catalog.json` 数据、不需要改代码，而四个职责共用同一套 `_write`/`_load_json` 底座、目标还是同一批用户配置文件——拆开只会多出一个共享工具层，也就是规则明令禁止的"第二 owner"。

### 证据

232 个测试，在 Windows 3.14、Windows 3.8、Ubuntu 三套本地解释器上跑，再加 GitHub Actions 六格矩阵（ubuntu-22.04、windows-2022、macos-15-intel × Python 3.8 / 3.11）全绿。

改写 MCP 注册路径时，用一个 30 组用例的差分工具（3 个代理 × 注册/移除 × 5 种文件系统状态）比对重构前后的状态文本与落盘 JSON，归一化随机临时目录名后**逐字节相同**——行为等价是证明出来的，不是假设的。

引擎 5276 行、测试 4445 行，**零第三方依赖**，纯标准库。

## 为什么需要 agent-fix

常见故障包括：

- `postinstall script was not run` / `native binary not installed`
- GUI 显示“已安装但无法运行”，终端却正常
- `EBADENGINE`、Node 版本过旧、npm registry 超时
- 登录过期、API Key 缺失、provider 或模型配置错误
- OpenCode MCP 配置 schema 无效

项目将解决方法维护在 `catalog.json` 和 `fixes/*.md` 中，CLI 与 MCP 共用同一套纯标准库引擎。

## 快速开始

```bash
git clone https://github.com/qingzhuo-cn/agent-fix.git
cd agent-fix

./scripts/fix list
./scripts/fix check npm-postinstall-skipped --agent opencode
./scripts/fix apply npm-postinstall-skipped --agent opencode --yes
```

只为一个指定 Agent 安装 skill：

```bash
python scripts/fix.py install --agent opencode
python scripts/fix.py mcp register opencode   # 可选，显式注册
```

安装过程不会注册启动自愈，不会扫描其他 Agent，也不会批量注册 MCP。

## CLI 命令

| 命令 | 作用 |
|---|---|
| `fix list` | 列出已知问题 ID |
| `fix agents` | 用户显式请求的设备 inventory；不诊断、不修复 |
| `fix check <issue> --agent <id>` | 只检查一个 Agent 的一个问题 |
| `fix apply <issue> --agent <id> [--yes]` | 只修复并验证同一 Agent |
| `fix info <issue>` | 输出对应知识库文档 |
| `fix net <host> [--timeout N]` | 只检查一个明确指定的主机 |
| `fix mcp register\|remove <agent-id>` | 只修改一个 Agent 的 MCP 注册 |
| `fix install\|uninstall --agent <id>` | 只部署或移除一个 Agent 的文件 |

项目不再提供 `doctor`、`auto`、`selfheal`。全机诊断、定时看门狗和启动自动修复已被删除。

## 问题目录

| ID | 问题 |
|---|---|
| `agent-broken-generic` | 指定 Agent 的二进制无法运行 |
| `npm-postinstall-skipped` | npm 生命周期脚本被跳过 |
| `gui-path-blind` | GUI 进程找不到指定 Agent 的二进制 |
| `node-version-too-old` | Node 版本不满足指定 Agent 的要求 |
| `npm-registry-mirror` | npm registry 缓慢或不可达 |
| `agent-auth-broken` | 登录或 API 凭据缺失/过期 |
| `provider-config` | provider key、base URL 或 model 未配置 |
| `net-connectivity` | 指定 Agent 的 API 端点不可达 |
| `opencode-mcp-schema` | OpenCode MCP 配置 schema 无效 |
| `deepseek-harness-broken` | `dsh` 缺失或无法启动 |

完整索引见 [fixes/README.md](fixes/README.md)。

## Python API

```python
from agentfix import catalog, engine

cat = catalog.load_catalog()
issue = catalog.find_issue(cat, "npm-postinstall-skipped")
target = engine.resolve_target(cat, issue, "opencode")

state = engine.check_issue(issue, agent=target, quiet=True)
outcome = engine.apply_issue(issue, agent=target, yes=True, quiet=True)
```

`resolve_target` 只探测指定 registry entry。未知、不适用或未检测到的目标会抛出 `TargetError`；空目标绝不会被误报为健康或验证成功。

## MCP Server

stdio MCP server 暴露 12 个工具：

`check`、`apply`、`info`、`agents`、`versions`、`net`、`logs`、`audit`、`backup`、`restore`、`provider`、`hooks`。

除用户显式调用的 `agents` inventory 外，每个操作工具都要求一个 `agent_id` 或 `host`。`check` 和 `apply` 同时要求 `issue_id` 与 `agent_id`。`apply` 和 `restore` 默认 dry-run。`provider` 也默认只读；`apply=true` 只写入 Claude Code settings，其他目标只返回手工配置步骤。`hooks` 只能检查或删除一个指定 Agent 的历史 hook，不能安装新 hook。

```bash
python scripts/fix.py mcp register claude-code
python mcp/smoke_test.py
```

完整说明见 [mcp/README.md](mcp/README.md)。

## 支持的 Agent

注册表当前包括 Claude Code、Codex、OpenCode、Hermes、Kimi Code、Pi、ZCode、Cursor、Gemini CLI、Aider、Qwen Code、Amp 和 Droid。注册表只是解析用户明确目标的数据源，不代表工具会遍历设备上的全部 Agent。

## 安全约束

- 检查、修复和修复后验证始终使用同一目标。
- 网络诊断只连接指定 host，或 catalog 中属于指定 Agent 的端点。
- 版本、日志、配置审计、备份、恢复和 provider 工具都要求目标。
- 禁止安装启动自愈和定时 watchdog。
- 输出默认遮盖 API Key、token 和代理凭据。
- 恢复操作拒绝不安全路径，只写入指定 Agent 的已知配置目录。
- Windows 下不会调用裸 `bash`，避免误进入 WSL。

## 开发与回归

```bash
python -m py_compile agentfix/*.py scripts/fix.py mcp/server.py mcp/smoke_test.py tests/*.py
python -m unittest discover -s tests -v
python mcp/smoke_test.py
```

回归测试会构造多个假 Agent，并断言定向检查、修复和验证不会执行另一个 Agent 的命令。

## 许可证

[MIT](LICENSE)
