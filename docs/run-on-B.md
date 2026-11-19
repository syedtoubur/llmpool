# Run the device B test bundle

You need a Windows PC, internet, and ideally an NVIDIA GPU. Ask ADMIN for
the private repository invitation first. This test changes no firewall,
services, or system settings and requires no administrator rights to run.
The installer commands below install the three tools for your Windows user.

1. Click Start, type **PowerShell**, and open **Windows PowerShell** normally.
   Paste each line below and press Enter. Accept installer prompts:

   ```powershell
   winget install --id Git.Git -e --scope user
   winget install --id astral-sh.uv -e --scope user
   winget install --id GitHub.cli -e --scope user
   ```

   If winget is missing, install **App Installer** from Microsoft Store first.
   If a managed PC blocks installation, ask ADMIN for help.
2. Close PowerShell and open it again so it can find the tools. Run:

   ```powershell
   gh auth login
   ```

   Choose **GitHub.com**, **HTTPS**, and **Login with a web browser**.
   Follow the displayed browser instructions and authenticate Git when asked.
   Never send anyone the login code or a token.
3. In your browser, sign in to that same GitHub account and accept the invite
   to **syedtoubur/llmpool** (check email or the repository page).
4. Back in PowerShell, paste these lines, one at a time:

   ```powershell
   cd $HOME
   git clone https://github.com/syedtoubur/llmpool.git
   cd llmpool
   powershell -ExecutionPolicy Bypass -File ops/run_on_B.ps1
   ```

   If you already cloned it, open PowerShell in that folder and run the last
   line. Keep the window open until it prints **Send this file to ADMIN**.

Allow roughly 15–60 minutes on a good connection; slow internet or CPU
inference can take longer. uv installs Python dependencies locally. The runner
selects a model fitting free VRAM minus 512 MiB, or the smallest model when
none fits. Model downloads are approximately **1.4, 3.0, or 6.2 GB**, plus Python
dependencies and llama.cpp binaries. Keep at least 10 GB free, preferably
15 GB. It probes memory for 30 seconds, tests local inference and Pydantic,
then runs the security gate. The server listens only on your PC at
127.0.0.1:18081 and stops afterwards.

FAIL lines are useful results. Read the **Fix** line for missing tools or
login problems. Tailscale and firewall checks may fail before ADMIN sets up
the tailnet; send the zip anyway. A bad model hash prevents model execution.
The test does not enable sharing. GPU driver details and memory measurements
are included, but GitHub tokens and Windows user-profile paths are scrubbed.

The one zip is beside README.md in the **llmpool** folder, named
`report_B_YOUR-PC_yyyyMMdd-HHmm.zip`. Send **that zip** to ADMIN through your
usual agreed channel. Do not send the models or bin folders. Details also
remain under `reports/B`. Repeating within the same minute replaces the zip.
The bundle includes report text (`.json`, `.csv`, `.log`, `.txt`); do not put
personal files in the report folder.

For an offline rehearsal, with no downloads, installs, servers or network:

```powershell
powershell -ExecutionPolicy Bypass -File ops/run_on_B.ps1 -DryRun
```

All checks will say SKIPPED and it still makes a zip. Optional flags:
`-Build cuda12` (also cuda13, vulkan, cpu), `-ModelId ID`,
`-ModelPath "C:\path\existing.gguf"` (select its matching ModelId too),
`-OutDir "reports\another-run"`, or `-PlanOnly` to print the JSON plan without
creating reports. Existing model files are always verified. Interrupted model
downloads resume from a `.partial` file on the next run.

To stop, press **Ctrl+C** in the PowerShell window. The runner stops its
Pydantic server in a finally block; avoid forcibly closing the window or
ending PowerShell in Task Manager. If the PC is shut down mid-test, reopen
PowerShell and run again. Send the zip from a completed run.

To remove everything after sending the zip, close any test window, open File
Explorer, go to your user folder, and delete the **llmpool** folder you cloned
(including models, binaries, reports, cache and virtual environment). Empty
the Recycle Bin to reclaim disk space. Git, uv and GitHub CLI can be removed
from **Settings → Apps → Installed apps** if you no longer want them. Run
`gh auth logout` first if you want to remove the CLI login. No firewall or
service cleanup is needed.
