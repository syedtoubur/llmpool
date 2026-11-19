# AGENTS.md: handoff for any coding agent (Claude Code, Codex, etc.)

Read this whole file first, then `docs/plan.md` (the master plan) and `docs/contracts.md` (the interfaces). Together they hold the full context, so you can continue from exactly where the previous session stopped.

---

## 1. What this project is
**llmpool** is a private pool of friends' and family's Windows PCs in Dhaka. Each PC shares the local LLMs that fit in its spare GPU memory or RAM. A member's agent harness can then use models on other members' PCs: one as orchestrator, others as subagents.

- **Stack:**
  - Python 3.13+ with `uv`.
  - llama.cpp `llama-server`, pinned to tag **b12470**.
  - Tailscale (later Headscale) as the private network.
  - FastAPI coordinator, Pydantic AI orchestrator.
- **Admin / project owner:** the repo owner (GitHub `syedtoubur`), called **ADMIN** below. The admin has **no networking or security background**.
- **Devices:**
  - **A:** the admin's laptop. Intel UHD iGPU, 7.7 GB RAM, often under 1.5 GB free. A caller, plus at most a tiny worker.
  - **B:** a friend's Windows desktop with an **NVIDIA GPU**. **You are probably running on B.** B's owner is also non-technical.
  - **Device 3:** joins later.

## 2. Where the project stands (updated 2026-11-19)
The build runs in waves (see `docs/plan.md`).

**Wave 0: verify the unknowns. Almost done.**

| ID | What | Status |
|---|---|---|
| S1 | Memory probe `spikes/memprobe.py`, `node_agent/memory.py` | Merged. Ran on A: `kind=igpu`, usable 0 bytes (`limited_by=system_ram_reserve`; A has too little free RAM). **Needs a run on B.** |
| S2 | llama-server checker `spikes/llama_check.py` | Merged. Now uses the `gh auth token` to avoid the GitHub API anonymous rate limit. Could **not** run on A: first the rate limit, then too little RAM. **Needs a run on B.** |
| S3 | Pydantic AI probe `spikes/pydantic_probe.py` | Merged. **Needs a run against a real llama-server (on B).** |
| S4 | Security gate `python -m security.gate`, `ops/firewall.ps1`, `ops/tailnet-policy.hujson` | Merged. On A: bindings PASS, SMB 445 WARN; firewall FAIL and Tailscale FAIL (not set up yet, expected). |
| S5 | Model catalogue `ops/catalogue.json` with pinned SHA-256 (Qwen3.5 9B/4B/2B Q4_K_M), `ops/verify_model.py` | Merged. |
| — | `ops/run_on_B.ps1`: one-command bundle that runs S1+S2+S3+gate on B and writes one zip | Merged. Dry-run tested on A only. |
| — | `docs/phase0-findings.md` | **Not written yet.** It needs B's results. |

The test suite has 85 tests, all passing on `main`.

**Wave 1 (two-device build): not started.** It covers the coordinator, node proxy, supervisor, pool client, orchestrator and the full security gate; see `docs/plan.md`.
**Wave 2 (Headscale + device 3): not started.**

## 3. Your job right now on device B
Work through these in order. **Explain each step to B's owner in plain language before doing it.** Stop and ask whenever a step needs admin rights, a login, or a decision.

1. **Check the tools.** You need `git`, `uv` and `gh` (GitHub CLI). If any is missing, install it per-user:
   `winget install --id Git.Git -e --scope user`, `winget install --id astral-sh.uv -e --scope user`, `winget install --id GitHub.cli -e --scope user`.
   Reopen the shell so PATH updates.
2. **Log in to GitHub:** `gh auth login`. The owner does this in the browser; never ask them for the code or a token. The login matters: without it, the llama.cpp release lookup hits GitHub's 60-requests/hour anonymous limit (we already hit that on A).
3. **Check the GPU:** run `nvidia-smi`. Record the GPU name, driver version and total/free VRAM. The `cuda12` llama.cpp build needs a recent NVIDIA driver; if the driver is too old, tell the owner and suggest updating it through the NVIDIA app or GeForce Experience.
4. **Set up the project in the repo folder:** run `uv sync --frozen`, then `uv run pytest -q`. Expect `85 passed`, maybe with a few Windows-only skips.
5. **Optional rehearsal:** `powershell -ExecutionPolicy Bypass -File ops\run_on_B.ps1 -DryRun`. All steps should be SKIPPED, with exit code 0.
6. **Run the real bundle:** `powershell -ExecutionPolicy Bypass -File ops\run_on_B.ps1`.
   - It auto-picks the largest catalogue model that fits in free VRAM, downloads it from Hugging Face and verifies its SHA-256.
   - Then it runs the memory probe (compared against Windows "Available" RAM and `nvidia-smi`), the llama-server check, the Pydantic probe and the security gate.
   - It takes about 15–60 min. It needs 10–15 GB of free disk space.
   - Details: `docs/run-on-B.md`.
7. **Read the results:** `reports/B/summary.json` plus each step's JSON/log. For every FAIL, find the cause.
   - **Expected FAILs before the network setup:** `tailscale_up` and `firewall_rules` in the security gate. Do not "fix" these by weakening anything.
   - **Anything else that FAILs:** diagnose it. If the cause is a bug in this repo's code, see section 5.
8. **Report back to ADMIN:**
   - The owner sends ADMIN the `report_B_<PC>_<date>.zip` from the repo root. It contains only scrubbed text: no models, binaries, tokens or user paths.
   - Also write a short plain-language summary for ADMIN: one line per step, saying PASS/FAIL, what it means and what's next.
   - If you have push access, or work from a fork, you may also add `docs/phase0-findings-B.md` in a PR. Include **only** hardware facts and pass/fail results: no usernames, IPs, tokens or serial numbers.
9. **Then stop.** Tailscale setup (`docs/setup-tailnet.md`) and the firewall rule happen **together with ADMIN**, since ADMIN owns the tailnet and must invite B. Wave 1 coding is coordinated by ADMIN's session.

## 4. Safety rules (non-negotiable)
- **Never** weaken security to make a check pass. That includes:
  - disabling Windows Defender or the firewall;
  - opening ports;
  - binding llama-server to anything other than `127.0.0.1`;
  - skipping hash verification.
- **Ask the owner before** anything that needs Administrator rights, changes system settings, installs services, or opens network access. `ops/firewall.ps1 -Apply` needs admin rights and is part of the later Tailscale step, not this one.
- **Use only models from `ops/catalogue.json`** whose SHA-256 matches. Never load `.bin`/pickle files or unverified downloads.
- **Never commit or upload any of these:** `reports/`, `models/`, `bin/`, `*.gguf`, `*.key`, `owner.toml`, `.env`, report zips, tokens. They are in `.gitignore`; keep it that way.
- **Never** log or store prompt or completion text. Logs hold metadata only (see `docs/contracts.md`).
- **Keep the owner's machine usable.** The owner always comes first: don't leave servers running, and stop anything you started.
- **This repo is currently public.** Treat everything you commit as visible to the world.

## 5. How to change code
- `docs/contracts.md` is the source of truth for interfaces. **Don't change it yourself**; propose the change to ADMIN.
- **Keep fixes small and test-backed.**
  - Add or adjust a test in `tests/`.
  - Run `uv run pytest -q`; everything must pass.
  - Commit on a branch, not `main`, with a clear message.
  - Open a PR to `syedtoubur/llmpool`, or a PR from a fork.
- **Don't** edit `pyproject.toml` or `uv.lock` unless the fix truly needs a dependency. Say so in the PR.
- Windows PowerShell 5.1 compatibility is required for `.ps1` files: no `&&`, `||`, `?:` or `??`.
- In ADMIN's main session, all code is written by Codex workers and Claude coordinates and reviews. Follow the same discipline: small reviewed diffs, tests first.

## 6. How to talk to the owners
- Use plain language. Define each technical term once, in one line.
- Explain results in numbered steps: what was expected, what happened, what's next.
- When there's a choice, give 2–3 concrete options with the recommended one first.
- Never claim success without showing the command output that proves it.

## 7. Known issues and lessons
- **GitHub API rate limit** (403 "rate limit exceeded"): the anonymous limit is shared per public IP, and Dhaka ISPs often use CGNAT. Fix: `gh auth login`. `llama_check.py` picks up the token automatically and never writes it to reports.
- **Codex on a ChatGPT account** rejects `--model gpt-5.4-mini`. Omit `--model` and set difficulty with `--effort` only.
- **Codex sandbox** may be unable to write `.git` in worktrees (`index.lock: Permission denied`). Codex edits; the coordinator commits.
- **Device A's RAM:** usable for sharing is 0 at the default 2 GB reserve. Whether to lower its reserve to 1 GB is an open decision for ADMIN (it's an `owner.toml` setting, not code).
- **Memory comparisons:** compare psutil "available" with Windows **Available** (`Win32_PerfFormattedData_PerfOS_Memory.AvailableBytes`), never with "Free".

## 8. Map of the repo
```
AGENTS.md               this file
CLAUDE.md               points here
docs/plan.md            master plan (waves, security gate design, costs)
docs/contracts.md       interface contracts (ports, tokens, APIs, owner.toml, memory, catalogue)
docs/setup-tailnet.md   click-by-click Tailscale join guide
docs/run-on-B.md        click-by-click guide for running the B bundle
ops/run_on_B.ps1        one-command Wave 0 bundle for device B
ops/catalogue.json      pinned model catalogue; verify_model.py / build_catalogue.py / add_model.py
ops/firewall.ps1        firewall rule for :8443 from 100.64.0.0/10 (admin; later step)
ops/tailnet-policy.hujson  default-deny tailnet ACL
node_agent/memory.py    memory probe library (contracts: Memory probe interface)
spikes/                 Wave 0 probes: memprobe, llama_check, pydantic_probe
security/               security gate (python -m security.gate)
coordinator/ orchestrator/ poolclient/   Wave 1 packages (empty stubs for now)
tests/                  pytest suite (fakes in tests/fakes)
```
