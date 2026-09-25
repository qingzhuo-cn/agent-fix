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

## 它最难的部分，是不在没修好时说修好了

修复工具最容易犯的错不是修不好，是**报喜**。

所以结果不是"成功/失败"这种二选一，而是四态：`PASS`、`FAIL`、`INCONCLUSIVE`、`SKIPPED`。汇总时保守到底——每一项都得是 `PASS` 才算数。CLI 按真实状态返回退出码：不健康非零，**不确定也非零**。

这不是设计洁癖，是个真实的谎报。`uninstall` 曾经只收集 `error` 状态，于是 hook 清理悄悄返回 `inconclusive` 时，它照样打印 `ok`、照样退出 0。一个没能确认完成的清理，被当成了一次成功。现在这种情况是 `error`。

状态也不再从人类可读文本里猜。`StatusText` / `StatusLines` 带着一条显式的 `.status` 旁路，退出码跟着真实状态走，不跟着措辞走。

同一份执念长在写文件的地方。`os.replace` 在标准库里无法对同用户的并发写入者做到内核级原子——这是绕不过去的事实，不是可以忽略的细节。所以引擎把"检查之后、落盘之前"整段当成攻击窗口：提交之后重新核对目标身份，对不上就回滚到落盘前的字节。

细节是有代价的。目录身份取 `(st_dev, st_ino, 条目名签名)` 三元组，因为光看 `dev/ino` 会被 inode 复用骗过去——一个全新的文件可以拿着和旧文件相同的编号。父目录的身份在操作开始时就记住，提交前后各核对一次；父目录若被换掉，回滚不会写进新目录，唯一的好备份也不会被挪过去。

于是写入只有三种结局：完整生效，完整回退，或者明确拒绝并说明拒绝的理由。**没有"看起来成功了"**。

ZIP 归档是同一个思路的另一种形状。先做 EOCD 预检，确认结构自洽之后才构造 `ZipFile`，中间用固定 46 字节缓冲流式走中央目录。伪造条目数曾经能跳过这一步，让整段尾部载荷被读进内存；现在伪造 1 MB、10 MB 还是 50 MB，峰值都稳稳停在 0.19 MB。

凭据遮蔽的边界一直盖到子进程。`api_key`、`access_token`、Bearer 头早就盖住了，但 `token=...`、`{"token": ...}`、`token: ...` 这些配置文件里最常见的写法原本会原样漏出去——因为它们不够"像"密钥。子进程输出在成功和失败两条路径上都过遮蔽层，原实现只遮了失败那条。

## 收敛，而不是再加一个 owner

`hooks.py` 里曾有三份几乎一模一样的 MCP 注册逻辑，现在是同一条表驱动路径，代理之间的差异作为数据放在 `_MCP_JSON_SPECS` 里。

这个文件本身评估过要不要拆分，结论是**不拆**。新代理本来就是 `catalog.json` 里的数据，加一个代理不需要改代码；而它的四个职责共用同一套 `_write`/`_load_json` 底座、目标还是同一批用户配置文件——拆开只会多出一层共享工具，恰好是规则里明令禁止的"第二 owner"。

有些重构是行数问题，有些是所有权问题。这道题属于后者，所以动的是重复，不是目录。

## 证据

232 个测试，三套本地解释器（Windows 3.14 / Windows 3.8 / Ubuntu），外加 GitHub Actions 六格矩阵——ubuntu-22.04、windows-2022、macos-15-intel，各跑 Python 3.8 与 3.11，全绿。

改写 MCP 注册路径时用了一个 30 组用例的差分工具（3 个代理 × 注册/移除 × 5 种文件系统状态），比对重构前后的状态文本与落盘 JSON。归一化随机临时目录名之后，两次运行逐字节相同。

行为等价是证明出来的，不是假设的。

引擎 5276 行，测试 4445 行，零第三方依赖。

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
