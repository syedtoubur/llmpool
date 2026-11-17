# Interface contracts (v0.1)

These contracts are the source of truth. Workstreams code against them and do not depend on each other's internals. Any change must be made here first by the coordinator (Claude).

## Topology and ports
- Every device joins one tailnet: the Tailscale free plan now, Headscale later. Tailnet addresses are in `100.64.0.0/10`. WireGuard already encrypts traffic between devices, so services on the tailnet speak **plain HTTP**.
- The **node proxy** listens on `<tailnet-ip>:8443`. It is the only port a node exposes.
- **llama-server** listens on `127.0.0.1:18080-18099`. It is never reachable from off the machine.
- The **coordinator API** listens on `<tailnet-ip>:8444`, on the coordinator host only. Its **admin API** listens on `127.0.0.1:9000` and is never on the tailnet.
- The **pool shim** (the OpenAI-compatible endpoint for harnesses) listens on `127.0.0.1:11500` on the caller's own device.
- Tailnet ACL (default deny):
  - members may reach `member devices : tcp 8443`;
  - members may reach `tag:coordinator : tcp 8444`;
  - nothing else is allowed.

## Identities and secrets
- **Coordinator signing key:** Ed25519. The private key stays on the coordinator host. Every node agent pins the public key in its config, base64url-encoded.
- **Member API key:** 32 random bytes in base64url, prefixed `mk_`. The coordinator stores only `sha256(key)`.
- **Node key:** 32 random bytes in base64url, prefixed `nk_`. The node agent uses it to authenticate heartbeats. The coordinator stores only the hash.
- All key comparisons are constant-time. Keys never appear in logs.

## Access token (coordinator → caller → node proxy)
- Format: `base64url(payload_json) + "." + base64url(ed25519_signature over the payload_json bytes)`, with no padding.
- Payload fields: `{"v":1,"iss":"llmpool-coordinator","sub":"<member_id>","node":"<node_id>","model":"<model_id>","iat":<unix>,"exp":<unix>,"jti":"<uuid4>"}`.
- `exp - iat` is at most 300 seconds.
- The proxy rejects the token if any of these hold:
  - the signature is bad;
  - `v` is not 1;
  - `iss` is wrong;
  - `node` is not this node;
  - `model` is not served by this node, or does not match the request body's `model`;
  - it has expired, allowing ±30 s clock skew;
  - `iat` is in the future beyond the skew;
  - `jti` or `sub` is on the revocation list, which the proxy learns from heartbeat responses.

## Coordinator API (tailnet :8444; `Authorization: Bearer <key>`)
- `POST /v1/nodes/heartbeat`. Node key. Sent every 5 s.
  - Request body:
    ```json
    {"node_id":"...","ts":<unix>,"agent_version":"0.1.0",
     "proxy_url":"http://100.x.y.z:8443",
     "sharing":{"active":true,"window_end":<unix|null>,"paused":false},
     "memory":{"kind":"cuda|dgpu|igpu|cpu","usable_bytes":int,"total_bytes":int,
               "reserve_bytes":int,"method":"nvml|pdh+dxgi|pdh+dxgi+psutil|psutil"},
     "models":[{"model_id":"...","sha256":"...","state":"loaded|loadable",
                "ctx":8192,"tok_per_s":float|null,"est_bytes":int}]}
    ```
  - Response: `{"revoked_jti":[...],"revoked_members":[...],"catalogue_version":"..."}`.
  - A node with no heartbeat for 15 s is offline.
- `GET /v1/pool/models`. Member key. Returns live entries only:
  ```json
  [{"node_id","owner","model_id","state","usable_bytes","tok_per_s","kind","role_hint":"orchestrator|worker|background","proxy_url"}]
  ```
  - `role_hint` is `background` for `kind=cpu`, `orchestrator` for the largest GPU model, and `worker` otherwise.
- `POST /v1/tokens`. Member key. Request `{"node_id","model_id"}`, response `{"token","exp","proxy_url"}`.
- Admin API (127.0.0.1:9000 only, no auth beyond loopback): create, list and revoke members; register, approve and revoke nodes; load the catalogue.
- **Audit log:** stores only `ts, member_id, node_id, model_id, event, status`. It never stores prompts, completions or keys.

## Node proxy (tailnet :8443)
- The allowlist is exactly two routes:
  - `POST /v1/chat/completions`
  - `GET /v1/models`
- Everything else returns `404`, including other methods on those paths, which return `405`.
- Auth: `Authorization: Bearer <access token>`.
- Default limits, configurable in owner.toml:
  - body at most 256 KiB;
  - `max_tokens` at most 2048 (clamped when absent);
  - total message characters at most 64,000;
  - concurrent upstream requests at most 1;
  - 30 requests per minute per member;
  - an upstream timeout.
- Forwarded body fields are limited to these: `model, messages, tools, tool_choice, temperature, top_p, max_tokens, stream, stop, response_format, seed, presence_penalty, frequency_penalty`. All other fields are dropped.
- Errors are returned as `{"error":{"code":"...","message":"..."}}` and never echo the request body.
- Logging records only `ts, member_id, model_id, status, prompt_tokens, completion_tokens, duration_ms`. It never records message content.
- If the client disconnects, the proxy closes the upstream connection.
- When the owner pauses sharing or the sharing window closes, the proxy aborts in-flight guest requests with `503 {"error":{"code":"owner_preempted"}}`.

## owner.toml (on each node; never committed)
```toml
[node]
node_id = "node-b"
coordinator_url = "http://100.x.y.z:8444"
node_key_file = "data/node.key"
coordinator_pubkey = "<base64url>"

[sharing]
timezone = "Asia/Dhaka"
hours = ["18:00-23:30"]          # local time windows
memory_budget_mb = 6000          # most the guest may use
reserve_ram_mb = 2048            # always kept free for the owner
pause_file = "data/PAUSE"        # if this file exists, sharing is paused

[limits]
max_tokens = 2048
max_body_kb = 256
rate_per_min = 30
concurrency = 1

[[models]]
model_id = "qwen3.5-9b-q4km"
path = "models/Qwen3.5-9B-Q4_K_M.gguf"
```

## Memory probe interface (`node_agent.memory`)
`probe() -> MemoryReport`, where `MemoryReport` has these fields:
- `kind`: `cuda`, `dgpu`, `igpu` or `cpu`;
- `usable_bytes`;
- `total_bytes`;
- `used_bytes`;
- `method`;
- `adapters`, a list of per-adapter details.
- `limited_by`: `gpu_memory`, `system_ram_reserve`, `budget` or `none`;
- `available_ram_bytes` and `reserve_bytes`.

How `usable_bytes` is computed depends on `kind`:
- **cuda:** NVML free − 512 MiB margin.
- **dgpu:** DXGI DedicatedVideoMemory − PDH Dedicated Usage − 512 MiB.
- **igpu:** the smaller of (DXGI SharedSystemMemory − PDH Shared Usage) and (psutil available − reserve).
- **cpu:** psutil available − reserve.

In every case the result is clamped to `>= 0` and to `memory_budget_mb`.

Memory totals and usage are defined by source: **cuda** uses NVML total and used; **dgpu** uses DXGI DedicatedVideoMemory as total and PDH Dedicated Usage as used; **igpu** uses DedicatedVideoMemory + SharedSystemMemory as total and PDH Dedicated Usage + Shared Usage as used; **cpu** uses psutil total and total − available as used. For an igpu, `usable_bytes` is the minimum of SharedSystemMemory − shared usage and psutil available − reserve, clamped to at least zero and to the configured budget. `limited_by` names the term that made usable capacity smallest.

## Model catalogue (`ops/catalogue.json`)
`{"version":"...","models":[{"model_id","repo","file","sha256","size_bytes","license","est_bytes_ctx8192","roles":["orchestrator","worker","background"]}]}`

Rules:
- A node serves a model only if its file's SHA-256 matches the catalogue.
- Only `.gguf` files whose header starts with `GGUF` magic are accepted.
