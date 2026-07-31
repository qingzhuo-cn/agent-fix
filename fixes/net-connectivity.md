# Agent API endpoints unreachable (network / proxy / DNS)

- **ID:** `net-connectivity`
- **Affects:** every agent — this is the network layer underneath all of them.
- **Tags:** `network`, `proxy`, `dns`, `latency`, `china`

## Symptom

- Agents fail with `ETIMEDOUT`, `ECONNRESET`, `socket hang up`, or generic
  "connection failed" — often **several agents at once** (the network is the
  shared dependency).
- One provider is slow while others are fine (e.g. OpenAI unreachable but
  DeepSeek fast — typical for mainland China without a proxy).
- `npm install` hangs (registry unreachable) — see
  [npm-registry.md](npm-registry.md) for the mirror fix.

## Root cause

TCP/DNS layer problems, not the agents themselves: a dead proxy, DNS pollution,
VPN/network switch, hosts-file entries, or an ISP that blocks a provider's
domain. Diagnosis is cheap and often rules out whole classes of "agent broken"
reports in one command.

## Check

CLI (part of `fix doctor`, or standalone):

```bash
fix check net-connectivity        # 9 endpoints, TCP:443, 5s timeout each
python scripts/netcheck.py        # same engine, full report + proxy env
```

MCP: `net_diagnose` (same engine).

Output looks like:

```
  OK      deepseek                    api.deepseek.com          (131ms)
  TIMEOUT openai (codex)              api.openai.com            (>5.0s)
  DNS-FAIL alibaba (qwen)             dashscope.aliyuncs.com
  ...
  HTTP_PROXY = http://127.0.0.1:7890
  HTTPS_PROXY = http://127.0.0.1:7890
```

Interpretation:

| Result | Meaning |
|--------|---------|
| `OK (Nms)` | endpoint reachable; N = TCP handshake latency |
| `TIMEOUT` | TCP connect timed out — likely blocked/firewalled (or proxy dead) |
| `DNS-FAIL` | domain doesn't resolve — DNS pollution / wrong hosts entry / wrong domain |
| `ERROR` | other OS error (routing, network down) |
| proxy env set | traffic may be going through a proxy — if endpoints fail while a proxy is set, the proxy itself is likely down |

## Fix

The network layer has no one-size-fits-all auto-repair, so this issue's fix is
manual guidance (the CLI prints it; `fix apply net-connectivity --yes` won't
change your network):

1. **Proxy set? Test it.**
   ```bash
   env | grep -iE "http_proxy|https_proxy|all_proxy"      # what's set
   curl -x "$HTTPS_PROXY" -sI https://api.deepseek.com | head -1   # proxy alive?
   ```
   If a proxy is configured but dead, fix the proxy/VPN (or temporarily
   `unset HTTPS_PROXY HTTP_PROXY ALL_PROXY` and retry).
2. **No proxy? The endpoint may be blocked by your ISP.** Options:
   - Use a VPN/proxy for the affected provider, or
   - Switch that agent to a reachable provider (e.g. DeepSeek —
     see [deepseek-provider.md](deepseek-provider.md)), or
   - Use a mirror where one exists (npm → [npm-registry.md](npm-registry.md);
     GitHub → `https://ghproxy`/`mirror.ghproxy.com` style mirrors for raw assets).
3. **DNS-FAIL?** Check the hosts file and DNS:
   ```bash
   nslookup api.deepseek.com          # what's your DNS returning?
   # Windows: C:\Windows\System32\drivers\etc\hosts  — remove stale entries
   # Flush DNS: ipconfig /flushdns  (Win) | sudo systemd-resolve --flush-caches (Linux)
   ```
4. **Retry** after the network change: `fix check net-connectivity`.

## Verify

```bash
fix check net-connectivity        # all OK → network layer healthy
python scripts/netcheck.py        # exit 0 = all endpoints reachable
```

## Prevention

- Know which providers you actually use; unreachable *other* endpoints are
  informational, not an agent fault.
- If you rely on a proxy, make the agents pick it up consistently
  (`NO_PROXY` for localhost) and keep the proxy healthy.
- For mainland-China setups, expect OpenAI/Google endpoints to be unreachable
  without a proxy — that's environmental, not a repair target. DeepSeek,
  Moonshot (Kimi), Zhipu (GLM), Alibaba (Qwen) are usually reachable directly.
