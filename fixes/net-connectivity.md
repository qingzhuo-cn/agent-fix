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

Check the selected Agent's mapped endpoint:

```bash
fix check net-connectivity --agent <id>
```

Or test one explicit host directly:

```bash
fix net api.deepseek.com
python scripts/fix.py net api.deepseek.com --timeout 5
```

MCP: `net(host=...)` uses the same single-host engine.

Output looks like:

```text
  OK      api.deepseek.com          (131ms)
  TIMEOUT api.openai.com            (>5.0s)
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
manual guidance. `fix apply net-connectivity --agent <id> --yes` reports the
steps but does not change the network:

1. **Proxy set? Test it.**
   ```bash
   env | grep -iE "http_proxy|https_proxy|all_proxy"      # what's set
   curl -x "$HTTPS_PROXY" -sI https://api.deepseek.com  # proxy alive? preserve exit
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
4. **Retry** after the network change with
   `fix check net-connectivity --agent <id>`.

## Verify

```bash
fix check net-connectivity --agent <id>       # mapped endpoint is reachable
fix net api.deepseek.com                      # explicit host is reachable
```

## Prevention

- Know which providers you actually use; unreachable *other* endpoints are
  informational, not an agent fault.
- If you rely on a proxy, make the agents pick it up consistently
  (`NO_PROXY` for localhost) and keep the proxy healthy.
- For mainland-China setups, expect OpenAI/Google endpoints to be unreachable
  without a proxy — that's environmental, not a repair target. DeepSeek,
  Moonshot (Kimi), Zhipu (GLM), Alibaba (Qwen) are usually reachable directly.
