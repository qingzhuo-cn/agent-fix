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
