# Join the private network (Tailscale free plan): step by step

**What this is:** Tailscale creates a private, encrypted network between your devices and your friend's, even though you're on different internet providers. Nobody outside it can see these devices through it. Each device gets an address that starts with `100.`.

**Cost:** free (the Personal plan allows up to 6 people and unlimited devices, for non-commercial use).

## You (the admin): do this once
1. Go to https://tailscale.com/download/windows. Download and install Tailscale.
2. Open Tailscale from the tray (bottom-right, near the clock). Click **Log in**, then sign in with your Google or Microsoft account. This creates your private network, which Tailscale calls a "tailnet".
3. Open https://login.tailscale.com/admin/machines. You should see your laptop with an address like `100.x.y.z`.
4. **Turn on key expiry protection:** on the Machines page, open your laptop's `...` menu and leave **Key expiry** ON. This is the default and the safe choice.
5. **Invite your friend:** go to https://login.tailscale.com/admin/users and click **Invite users**. Enter your friend's email and send the invite.
6. **Lock down who can reach what.** This is the most important security step.
   - Open https://login.tailscale.com/admin/acls.
   - Replace everything there with the contents of `ops/tailnet-policy.hujson` from this project. Put your email and your friend's email into `group:members`.
   - Click **Save**.
   - From then on, each device can reach only port 8443 (the model service) on the other, and nothing else. Not files, not remote desktop, not anything else.

## Your friend (device B)
1. Open the invite email and accept it with their Google or Microsoft account.
2. Install Tailscale from https://tailscale.com/download/windows and log in with the same account.
3. Tell you the `100.x.y.z` address shown when they click the Tailscale tray icon.

## Check it worked (either device)
Open PowerShell and run:
```
tailscale status
tailscale ping <the other device's 100.x address>
```
- `tailscale status` should list both devices.
- `tailscale ping` should answer with `pong`. If it says `via DERP(...)`, traffic is being relayed through Tailscale's servers. That's fine, just a little slower. If it shows an IP address and port, the devices are connected directly.

Our security gate (`uv run python -m security.gate --peer <other 100.x address>`) then confirms that **only** port 8443 is reachable.

## Things to ask your internet provider (optional, helps with speed)
- "Is my connection behind CGNAT, or do I have a public IP?" CGNAT means more traffic is relayed. It's safe, just slower.
