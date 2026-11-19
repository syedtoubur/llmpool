> Status as of 2026-11-19: Wave 0 nearly done (see AGENTS.md section 2). Copied from the coordinator's private plan file so agents on other devices have full context.
> Note: Codex on a ChatGPT account rejects `gpt-5.4-mini`; ignore that model in the tier table and use `--effort` only.

# Local LLM Pool (Dhaka): Phase 0 verification, then 2-device and 3-device bring-up, built by Codex workstreams

## Context
The goal is a pool for friends and family in Dhaka. Members' Windows devices share whichever LLMs fit in their free GPU memory, or in free RAM on CPU-only machines. A member's agent harness can then use several of those models at once: one model acts as orchestrator and the others as subagents.

Research found nothing that does this end to end. Mesh LLM and GPUStack are the closest; `orailnoor/cross-platform-llm-client` is only a chat client. So we build our own coordinator on top of stock components.

The user asked for:
- every open question to be checked before implementation;
- a start with **two devices**, then a third;
- **Codex subagents for all coding**, with Claude as the coordinator who splits the work into parallel tasks;
- **strict, automated network and security tests**, because the user has no networking or security background.

### Settled decisions
- **Membership:** invite-only and non-commercial for now. The user is the sole admin. The design should allow a later commercial or open "earn with your GPU" phase.
- **Network:** the two-device test runs on the **Tailscale free plan** (৳0). Before device 3 joins, it moves to **self-hosted Headscale**, with its DERP relay on a **Dhaka BDIX VPS** (about ৳1,000/mo).
- **Serving:** each device serves whole models only. The owner always comes first: they declare sharing hours and a memory budget they can reliably spare.
- **Privacy:** hosts are strictly isolated from callers. Prompts are protected by trust and no-logging defaults, because a host can technically read them.
- **Stack:** Python for the coordinator and node agent. llama.cpp `llama-server`, using the CUDA build for NVIDIA, Vulkan for AMD/Intel, and CPU otherwise.
- **Models:** a curated GGUF catalogue with pinned SHA-256 hashes. CPU-only nodes are workers only.
- **Orchestrator:** a Pydantic AI orchestrator that uses the best live pool model, plus an OpenAI-compatible shim. Coding comes first.
- **Devices:**
  - A: this laptop. Intel UHD iGPU, 7.7 GB RAM (about 0.8 GB free at check time), i3-1215U. Role: caller and tiny worker.
  - B: a friend's Windows PC with an NVIDIA GPU, in another home on another ISP.

### Facts already verified
- **Windows memory counters (PDH):** `\GPU Adapter Memory(*)\Dedicated Usage | Shared Usage | Total Committed`, with instances named like `luid_0x00000000_0x000137a5_phys_0`. On A's Intel iGPU, all use shows up as Shared Usage. DXGI `CurrentUsage` and Vulkan `heapUsage` count only the calling process, so PDH is the source of system-wide usage.
- **llama.cpp b12470:** has Windows builds for CUDA (12.4 and 13.5), Vulkan and CPU, plus GitHub attestations. `--fit` is on by default. `--no-webui` and `--no-slots` exist, `/metrics` is off by default, and slot saving is off by default.
- **Headscale v0.29.7:** supports HuJSON grants with an `ip` port field, embedded DERP, and `derp.urls: []`.
- **Tailscale free plan:** up to 6 users, unlimited devices, ACLs included, non-commercial use only.
- **Local tooling:** Codex CLI 0.169.0, Node, Git and Python 3.13 are installed.

### Cost decision
- **Testing on A and B:** ৳0 in cash. Electricity on B is about ৳2–4 per hour under load. All software and the Qwen3.5 weights are free (MIT, BSD or Apache licences).
- **Before device 3:** VPSBangladesh Starter at ৳1,000/mo (৳1,050 with VAT), with Host4speed at ৳1,190/mo as the fallback, and a free DuckDNS or sslip.io hostname.
  - Before paying, get written confirmation of: public IPv4, VPN/tunnel use allowed, and UDP 3478 plus TCP 80/443 open.
  - Do not use HostSeba, which bans VPN/tunnel use.
  - Migrating from Tailscale means `tailscale logout`, then `tailscale up --login-server … --authkey …`. The node software stays the same.

---

## Execution model: Claude coordinates, Codex codes
- **Roles**
  - **Claude (main thread):** owns the plan, the interface contracts, the task briefs and merges. Runs the tests, reviews results, and explains outcomes to the user in plain language.
  - **Codex (`codex:codex-rescue` agents):** write all code and tests. Claude does not write product code itself.
- **Parallelism:** each workstream runs in its own git worktree on its own branch (`Agent` with `isolation: "worktree"`). Workstreams in the same wave run simultaneously. Claude merges a branch into `main` only after its tests pass.
- **Contracts first:** before Wave 1, Claude writes `docs/contracts.md`. It defines the heartbeat JSON, token format, proxy allowlist, `owner.toml` schema and the pool-models API. Workstreams code against it, so they don't need each other's code.
- **Model and effort per task difficulty.** Exact model IDs are confirmed with `/codex:setup` before Wave 0; "default" means Codex's default model.

| Tier | Typical tasks | Codex setting |
|---|---|---|
| Easy | Scaffolding, config files, CLI wrappers, docs, simple probes | `--model gpt-5.4-mini --effort low` |
| Medium | Coordinator CRUD/registry, supervisor process control, pool client, orchestrator | default model `--effort medium` |
| Hard / security-critical | Memory probe (Windows APIs), auth tokens, proxy allowlist, ACL policy, firewall scripts, security test suite | default model `--effort high` (`xhigh` for token/proxy) |

- **Quality gates for every workstream:**
  1. Codex writes tests alongside the code, and `pytest` must pass in its worktree.
  2. Claude reviews the diff with `/code-review` at medium, or high for security-critical work.
  3. Security-critical branches also get a Codex adversarial review (`/codex:adversarial-review`) from a separate Codex run.
  4. Merge, then rerun the full test suite and the security gate on `main`.

---

## Wave 0: Verification spikes (all before product code)
Results go into `docs/phase0-findings.md`: one row per unknown, with the observed value and the resulting decision.

| ID | Workstream (Codex, in parallel) | Tier | Output |
|---|---|---|---|
| S1 | `spikes/memprobe.py`: PDH via `win32pdh`, DXGI totals via ctypes joined by LUID (skip the software renderer), NVML, psutil. Logs every 2 s to CSV. | Hard | Probe plus unit tests for the LUID parsing and memory maths |
| S2 | `spikes/llama_check.ps1`: downloads llama.cpp release builds, then runs `gh attestation verify` and `--list-devices`. Starts llama-server with locked flags. Checks: endpoint 404s, whether prompts appear in logs, cancel-on-disconnect, a Qwen3.5 tool call, and tokens/s. | Medium | Script plus a JSON report |
| S3 | `spikes/pydantic_probe.py`: OpenAIChatModel/OpenAIProvider against a node, agent delegation, FallbackModel with a node killed mid-run, and ToolOutput vs PromptedOutput. | Medium | Probe plus findings |
| S4 | `ops/tailnet-policy.hujson` (default-deny; members to members on `tcp:8443` only) and `ops/firewall.ps1` (allow :8443 from 100.64.0.0/10 only). Also the first version of the **network security gate** (see the security section). | Hard | Policy, firewall script, `security/netgate.py` |
| S5 | `ops/catalogue.json`: pinned SHA-256 for each model, taken from the Hugging Face API (Qwen3.5-9B Q4_K_M, Qwen3.5-4B Q4_K_M, a ~2B model). Includes licence and memory at `-c 8192`. | Easy | Catalogue plus a hash-verification script |

**Who runs what:**
- Claude runs S1, S2 and S3 on A.
- The user sends B's owner a single `run_on_B.ps1` bundle. It runs S1, S2 and the security gate and writes one report file to send back.
- The user and B's owner join the tailnet. Claude gives click-by-click steps.
- **Non-technical checks:** ask both ISPs about CGNAT. Ask a lawyer or the ISP about BTRC's VPN rules; this is a gate before any commercial or open launch. B's owner signs the one-page member agreement.

**Exit:** every findings row has a value and a decision, and the security gate passes on A and B.

## Wave 1: Two-device build (parallel worktrees, against `docs/contracts.md`)

| ID | Workstream | Tier | Depends on |
|---|---|---|---|
| W1 | `coordinator/`. FastAPI + SQLite. Includes: members with Ed25519 keys; node registry from 5 s heartbeats; catalogue; `GET /v1/pool/models`; short-lived (5 min) signed tokens scoped to member, node and model; metadata-only audit log. It never receives prompts. Runs on A or B for now. | Medium (token code: Hard) | contracts |
| W2 | `node_agent/memory.py` (S1 algorithm per vendor) and `node_agent/supervisor.py`. The supervisor starts llama-server on 127.0.0.1 with the locked flags as a low-privilege user, verifies the model hash before launch, and pre-empts guests when the owner needs the machine or the sharing window closes. Also `owner.toml` and a "pause sharing" command. | Hard | S1, S2 |
| W3 | `node_agent/proxy.py`. Listens on the Tailscale IP, port :8443. Allowlist: only `POST /v1/chat/completions` and `GET /v1/models`. It verifies the token, caps tokens, context and concurrency, and logs no prompt text. | Hard (`xhigh`) | contracts |
| W4 | `poolclient/` (list models, get a token, call a node) and a local OpenAI-compatible shim on 127.0.0.1. | Medium | contracts |
| W5 | `orchestrator/` (Pydantic AI). Uses the best live GPU model as orchestrator and the others as workers (CPU nodes get background tasks only). Each role gets a FallbackModel, with structured plans and retry on another node. | Medium | S3, W4 interface |
| W6 | `security/` full gate (below) and `tests/integration/` with a fake llama-server. | Hard | contracts |

**Wave 1 exit (two devices):**
1. A and B both appear in `/v1/pool/models`, and the memory figures track Task Manager within about 10%.
2. A calls B's 9B model and B calls A's ~2B model.
3. Pausing B removes it within about 10 s, and any in-flight request fails cleanly.
4. A small coding task completes, with B's 9B model as orchestrator and A's model as a worker subagent.
5. The **security gate passes 100%** on both devices.

## Wave 2: Move to Headscale, then add device 3
- **Ops workstream (Easy/Medium tier):** `ops/headscale/` contains config, install script and policy, plus a migration runbook. The user rents the VPS after the provider confirms the requirements in writing. Claude guides each step.
- Re-enrol A and B, then re-run the full security gate. Record BDIX relay latency against the Tailscale-relay baseline.
- Device 3: run the S1/S2 bundle on its hardware first (the AMD/Arc discrete-GPU memory branch is still unverified). Enrol it with a single-use pre-auth key and re-run the gate.
- **v1 done:** a coding task finishes with one orchestrator plus at least two subagents on other members' machines, and survives one node being paused mid-run.

---

## Strict network and security gate (`security/`, runs on every device and after every merge)
The gate runs with one command, `python -m security.gate`. It prints a plain-English PASS/FAIL line per check, with the reason and the fix. Any FAIL blocks a merge and blocks a node from joining the pool.

**Network exposure (run from a peer and locally)**
- Scan every TCP port from 1 to 65535 on the peer's Tailscale IP. Only 8443 may be open. RDP 3389, SMB 445, SSH 22, 11434 and 8080 must all be closed.
- Scan the device's LAN IP and public IP from a peer. 8443 must not be reachable there.
- `llama-server` and the coordinator admin API must be bound to 127.0.0.1 only. Verified with `Get-NetTCPConnection`.
- The Windows Firewall rule exists, and its scope is exactly 100.64.0.0/10 and TCP 8443.
- The tailnet ACL is default-deny. A non-member device on the tailnet cannot reach 8443.

**Authentication and authorization (proxy and coordinator)**
- Each of these must be rejected:
  - no token;
  - a malformed token;
  - a token with a wrong signature;
  - an expired token;
  - a token for another node or another model;
  - a replayed token after it is revoked;
  - a member key that has been revoked.
- Every non-allowlisted path and method returns 404 or 403. This covers `/slots`, `/props`, `/metrics`, `/completion`, `/tokenize`, `/embedding`, `/lora-adapters`, the web UI, and path-traversal variants. The check includes a fuzz set of more than 200 paths.
- Oversized requests are rejected: too many max_tokens, a context that is too long, too much concurrency, and bodies over the size limit. A per-member rate limit is enforced.

**Host isolation**
- llama-server runs as the low-privilege user. That user cannot write to the model directory or read the owner's profile.
- A model file whose hash doesn't match is refused. A non-GGUF or pickle file is refused.
- Binaries pass `gh attestation verify`.

**Privacy**
- Send a canary prompt (a random UUID string), then grep for that UUID in the proxy logs, the llama-server logs, the coordinator DB, and the audit log. It must appear **zero** times.
- The coordinator never receives prompt bodies, which is checked by inspecting its request log.

**Resilience**
- Kill llama-server mid-stream. The client gets a clean error and the node drops out of the list within 10 s.
- Activate the owner's pause or memory pressure. The guest is pre-empted.

**Independent review**
- Every security-critical branch gets a Codex adversarial review.
- Claude runs `/security-review` before each wave closes.

---

## Verification (end to end)
- `pytest` for unit and integration tests in every worktree, then again on `main` after each merge.
- `python -m security.gate` on A and B (and later on device 3), plus a peer-side scan from the other device.
- `spikes/memprobe.py` compared against Task Manager on each device.
- A scripted coding run with node kill and pause, checking failover.
- After each wave, Claude reports to the user in plain language: what was tested, what passed, and what it means.
