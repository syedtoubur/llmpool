[CmdletBinding()]
param(
    [string]$ModelId = 'auto',
    [string]$ModelPath,
    [ValidateSet('auto','cuda12','cuda13','vulkan','cpu')][string]$Build = 'auto',
    [switch]$DryRun,
    [string]$OutDir = 'reports/B',
    [switch]$PlanOnly,
    # Offline planning seam: raw free VRAM, before the 512 MiB reserve.
    [long]$FreeVramBytes = -1
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Scrub([string]$value) {
    foreach ($secret in @($env:GH_TOKEN, $env:GITHUB_TOKEN)) {
        if ($secret) { $value = $value.Replace($secret, '[REDACTED]') }
    }
    $value = $value -replace '(?i)(gh[opusr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)', '[REDACTED]'
    if ($env:USERPROFILE) {
        foreach ($profilePath in @($env:USERPROFILE.Replace('\','\\'), $env:USERPROFILE, $env:USERPROFILE.Replace('\','/'))) {
            $value = [regex]::Replace($value, [regex]::Escape($profilePath), '~', 'IgnoreCase')
        }
    }
    # Also scrub paths emitted by tools running under another Windows profile.
    $value = $value -replace '(?i)[A-Z]:[\\/]+Users[\\/]+[^\\/"\r\n]+', '~'
    return $value
}
function Save-Text([string]$path, [string]$value) {
    [IO.File]::WriteAllText($path, (Scrub $value), $utf8)
}
function Run-Native([string]$exe, [string[]]$arguments, [string]$log) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { throw "$exe is missing. Fix: install it using docs/run-on-B.md and reopen PowerShell." }
    # Native stderr is diagnostic data; native exit status decides success.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = & $exe @arguments 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($log) { Save-Text (Join-Path $out $log) (($lines | Out-String)) }
    if ($code -ne 0) { throw "$exe exited with code $code. See $log. Fix: review the log and retry." }
    return $lines
}

$gpu = @()
if (-not $DryRun -and $FreeVramBytes -lt 0) {
    try {
        $raw = @(& nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv,noheader,nounits 2>$null)
        if ($LASTEXITCODE -eq 0) {
            $gpu = @($raw | ForEach-Object {
                $v = $_ -split ','
                [ordered]@{name=$v[0].Trim(); driver=$v[1].Trim(); total_bytes=[long]$v[2].Trim()*1MB; free_bytes=[long]$v[3].Trim()*1MB}
            })
            # A model must fit on one adapter; do not add unrelated GPUs together.
            $FreeVramBytes = ($gpu | Measure-Object -Property free_bytes -Maximum).Maximum
        }
    } catch { $gpu = @() }
}
if ($Build -eq 'auto') {
    $Build = 'cpu'
    if ($gpu.Count -gt 0 -or $FreeVramBytes -ge 0) { $Build = 'cuda12' }
    elseif (Test-Path -LiteralPath (Join-Path $env:WINDIR 'System32/vulkan-1.dll')) { $Build = 'vulkan' }
}
$catalogue = Get-Content -LiteralPath (Join-Path $root 'ops/catalogue.json') -Raw | ConvertFrom-Json
$models = @($catalogue.models | Sort-Object est_bytes_ctx8192)
$model = $null
if ($ModelId -eq 'auto') {
    $fits = @($models | Where-Object { $_.est_bytes_ctx8192 -le ($FreeVramBytes - 512MB) })
    $model = $models[0]
    if ($fits.Count -gt 0) { $model = $fits[-1] }
} else { $model = $models | Where-Object { $_.model_id -eq $ModelId } | Select-Object -First 1 }
if (-not $model) { throw "Unknown model ID. Fix: choose a model_id from ops/catalogue.json." }
$ModelId = $model.model_id
$providedPath = [bool]$ModelPath
if (-not $ModelPath) { $ModelPath = Join-Path $root ('models/' + $model.file) }
$ModelPath = [IO.Path]::GetFullPath($ModelPath)
$out = [IO.Path]::GetFullPath($OutDir)
if ($out.TrimEnd('\') -eq $root.TrimEnd('\') -or $out.StartsWith((Join-Path $root 'models'), 'OrdinalIgnoreCase') -or $out.StartsWith((Join-Path $root 'bin'), 'OrdinalIgnoreCase')) {
    throw 'OutDir must be a report directory, separate from the repo root, models and bin.'
}
$url = 'https://huggingface.co/' + $model.repo + '/resolve/main/' + $model.file
$serverArgs = '--host 127.0.0.1 --port 18081 -m "' + $ModelPath + '" -c 8192 --no-webui --no-slots --jinja'
if ($Build -ne 'cpu') { $serverArgs += ' -ngl 99' } else { $serverArgs += ' -ngl 0' }
$plan = @(
    [ordered]@{name='preflight'; command='git --version; uv --version; gh --version; gh auth status; nvidia-smi; check free disk space'},
    [ordered]@{name='sync'; command='uv sync --frozen'},
    [ordered]@{name='pytest'; command='uv run pytest -q'},
    [ordered]@{name='memory-snapshot'; command='uv run python spikes/memprobe.py --once --json; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits; Get-CimInstance Win32_OperatingSystem'},
    [ordered]@{name='memory-series'; command=('uv run python spikes/memprobe.py --interval 2 --duration 30 --out "' + $out + '/memory.csv"')},
    [ordered]@{name='model-download'; command=('curl.exe -L --fail -C - -o "' + $ModelPath + '.partial" "' + $url + '" (skip existing model; rename partial on success)')},
    [ordered]@{name='model-verify'; command=('uv run python ops/verify_model.py --catalogue ops/catalogue.json --model-id ' + $ModelId + ' --path "' + $ModelPath + '"')},
    [ordered]@{name='llama-check'; command=('uv run python spikes/llama_check.py --build ' + $Build + ' --tag b12470 --model "' + $ModelPath + '" --bin-dir bin --out "' + $out + '/llama-check.json"')},
    [ordered]@{name='pydantic-probe'; command=('bin/b12470/' + $Build + '/llama-server.exe ' + $serverArgs + '; wait /health up to 300s; uv run python spikes/pydantic_probe.py --endpoint B=http://127.0.0.1:18081/v1=local --endpoint worker=http://127.0.0.1:18081/v1=local --api-key local --out "' + $out + '/pydantic.json"; stop server in finally')},
    [ordered]@{name='security-gate'; command=('uv run python -m security.gate --json-out "' + $out + '/gate.json"')}
)
$summary = [ordered]@{schema_version=1; hostname=[Environment]::MachineName; timestamp=[DateTime]::UtcNow.ToString('o'); build=$Build; model_id=$ModelId; gpu=$gpu; model=$model; free_vram_bytes=$FreeVramBytes; plan=$plan; steps=@()}
if ($PlanOnly) { Write-Output (Scrub ($summary | ConvertTo-Json -Depth 20)); exit 0 }
New-Item -ItemType Directory -Path $out -Force | Out-Null
$verified = $false
$llamaOK = $false
$diskOK = $false
# Keep imports anchored in this checkout, including script invocations from spikes/.
$env:PYTHONPATH = $root + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:UV_CACHE_DIR = Join-Path $root '.uv-cache'
try {
    foreach ($step in $plan) {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        $status = 'pass'
        $message = 'Completed.'
        Write-Host (Scrub ("Planned: " + $step.command))
        try {
            if ($DryRun) { $status = 'skipped'; $message = 'Dry run: no command executed.' }
            else {
                switch ($step.name) {
                    'preflight' {
                        $problems = @()
                        foreach ($tool in @('git','uv','gh')) {
                            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { $problems += "FAIL: $tool is missing. Fix: install $tool using the guide, then open PowerShell again." }
                        }
                        if (Get-Command gh -ErrorAction SilentlyContinue) {
                            try { Run-Native gh @('auth','status') 'gh-auth.log' | Out-Null }
                            catch { $problems += 'FAIL: GitHub is not logged in. Fix: run gh auth login.' }
                        }
                        Save-Text (Join-Path $out 'gpu.json') ($gpu | ConvertTo-Json -Depth 10)
                        if ($providedPath -and -not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) { $problems += 'FAIL: ModelPath does not exist. Fix: supply an existing GGUF file.' }
                        $disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + [IO.Path]::GetPathRoot($ModelPath).TrimEnd('\') + "'")
                        $needed = [long]$model.size_bytes + 1GB
                        if (Test-Path -LiteralPath $ModelPath) { $needed = 1GB }
                        $diskOK = $null -ne $disk -and $disk.FreeSpace -ge $needed
                        Save-Text (Join-Path $out 'disk.json') (@{free_bytes=$disk.FreeSpace; required_bytes=$needed} | ConvertTo-Json)
                        if (-not $diskOK) { $problems += "FAIL: Not enough free disk space (need $needed bytes). Fix: free space on the model drive and retry." }
                        Save-Text (Join-Path $out 'preflight.log') (($problems + 'Preflight checked tools, GitHub login, GPU and disk.') -join "`n")
                        if ($problems.Count) { throw ($problems -join "`n") }
                    }
                    'sync' { Run-Native uv @('sync','--frozen') 'sync.log' | Out-Null }
                    'pytest' { Run-Native uv @('run','pytest','-q') 'pytest.log' | Out-Null }
                    'memory-snapshot' {
                        $started = [DateTime]::UtcNow.ToString('o')
                        $sampler=$null
                        try {
                            $sampler=Start-Process -FilePath (Get-Command uv).Source -ArgumentList 'run python spikes/memprobe.py --once --json' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $out 'memory.json') -RedirectStandardError (Join-Path $out 'memory-snapshot.log')
                            $os = Get-CimInstance Win32_OperatingSystem
                            $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
                            $reference = [ordered]@{snapshot_started=$started; sampled_at=[DateTime]::UtcNow.ToString('o'); available_ram_bytes=[long]$memory.AvailableBytes; total_ram_bytes=[long]$os.TotalVisibleMemorySize*1KB; gpu=@(); tolerance_percent=10}
                            if ($gpu.Count) {
                                $reference.gpu = @(Run-Native nvidia-smi @('--query-gpu=memory.used,memory.total','--format=csv,noheader,nounits') 'memory-reference-gpu.log')
                            }
                            if (-not $sampler.WaitForExit(60000)) { throw 'Memory snapshot timed out.' }
                            $sampler.Refresh()
                            if ($sampler.ExitCode -ne 0) { throw 'Memory snapshot failed. See memory-snapshot.log.' }
                        } finally {
                            if ($sampler -and -not $sampler.HasExited) { Stop-Process -Id $sampler.Id -Force -ErrorAction SilentlyContinue }
                        }
                        $snapshot=Get-Content -LiteralPath (Join-Path $out 'memory.json') -Raw | ConvertFrom-Json
                        $delta=[Math]::Abs($snapshot.available_ram_bytes-$reference.available_ram_bytes) / [Math]::Max(1,$reference.available_ram_bytes)*100
                        $reference.ram_difference_percent=$delta
                        $reference.ram_within_10_percent=($delta -le 10)
                        $reference.gpu_comparisons=@()
                        $cudaAdapters=@($snapshot.adapters | Where-Object { $_.kind -eq 'cuda' })
                        for ($i=0; $i -lt [Math]::Min($cudaAdapters.Count,$reference.gpu.Count); $i++) {
                            $values=$reference.gpu[$i] -split ','
                            $used=[double]$values[0].Trim()*1MB
                            $total=[double]$values[1].Trim()*1MB
                            $usedDelta=[Math]::Abs($cudaAdapters[$i].used_bytes-$used)/[Math]::Max(1MB,$used)*100
                            $totalDelta=[Math]::Abs($cudaAdapters[$i].total_bytes-$total)/[Math]::Max(1MB,$total)*100
                            $reference.gpu_comparisons+=@{adapter=$cudaAdapters[$i].name; used_difference_percent=$usedDelta; total_difference_percent=$totalDelta; within_10_percent=($usedDelta -le 10 -and $totalDelta -le 10)}
                        }
                        Save-Text (Join-Path $out 'memory-reference.json') ($reference | ConvertTo-Json -Depth 10)
                        if ($delta -gt 10) { throw 'Memory snapshot differs from Windows available RAM by more than 10 percent. See memory-reference.json.' }
                        if (@($reference.gpu_comparisons | Where-Object { -not $_.within_10_percent }).Count) { throw 'GPU memory differs from nvidia-smi by more than 10 percent. See memory-reference.json.' }
                    }
                    'memory-series' { Run-Native uv @('run','python','spikes/memprobe.py','--interval','2','--duration','30','--out',(Join-Path $out 'memory.csv')) 'memory-series.log' | Out-Null }
                    'model-download' {
                        if (Test-Path -LiteralPath $ModelPath -PathType Leaf) { $status='skipped'; $message='Existing model; verification still required.' }
                        elseif ($providedPath -or -not $diskOK) { $status='skipped'; $message='Model path or disk preflight failed.' }
                        else {
                            New-Item -ItemType Directory -Path (Split-Path $ModelPath -Parent) -Force | Out-Null
                            Run-Native curl.exe @('-L','--fail','-C','-','-o',($ModelPath+'.partial'),$url) 'model-download.log' | Out-Null
                            Move-Item -LiteralPath ($ModelPath+'.partial') -Destination $ModelPath
                        }
                    }
                    'model-verify' {
                        Run-Native uv @('run','python','ops/verify_model.py','--catalogue','ops/catalogue.json','--model-id',$ModelId,'--path',$ModelPath) 'model-verify.log' | Out-Null
                        $verified=$true
                    }
                    'llama-check' {
                        if (-not $verified) { $status='skipped'; $message='Model verification failed; model steps stopped.' }
                        else {
                            Run-Native uv @('run','python','spikes/llama_check.py','--build',$Build,'--tag','b12470','--model',$ModelPath,'--bin-dir','bin','--out',(Join-Path $out 'llama-check.json')) 'llama-check.log' | Out-Null
                            $llamaOK=$true
                        }
                    }
                    'pydantic-probe' {
                        if (-not $verified -or -not $llamaOK) { $status='skipped'; $message='Requires a verified model and successful llama check.' }
                        else {
                            $exe = Get-ChildItem -LiteralPath (Join-Path $root ('bin/b12470/'+$Build)) -Filter llama-server.exe -Recurse | Select-Object -First 1
                            if (-not $exe) { throw 'llama-server.exe missing. Fix: review llama-check.log.' }
                            if (Get-NetTCPConnection -LocalPort 18081 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 18081 is in use. Fix: close the existing local server and retry.' }
                            $server=$null
                            try {
                                $server = Start-Process -FilePath $exe.FullName -ArgumentList $serverArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $out 'pydantic-server.log') -RedirectStandardError (Join-Path $out 'pydantic-server-error.log')
                                $deadline=[DateTime]::UtcNow.AddSeconds(300)
                                $healthy=$false
                                while ([DateTime]::UtcNow -lt $deadline) {
                                    if ($server.HasExited) { throw 'llama-server stopped before it was ready. See server logs.' }
                                    try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:18081/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { $healthy=$true; break } } catch { }
                                    Start-Sleep -Seconds 1
                                }
                                if (-not $healthy) { throw 'Server was not ready within 300 seconds. See server logs.' }
                                Run-Native uv @('run','python','spikes/pydantic_probe.py','--endpoint','B=http://127.0.0.1:18081/v1=local','--endpoint','worker=http://127.0.0.1:18081/v1=local','--api-key','local','--out',(Join-Path $out 'pydantic.json')) 'pydantic.log' | Out-Null
                                $probe=Get-Content -LiteralPath (Join-Path $out 'pydantic.json') -Raw | ConvertFrom-Json
                                $bad=@($probe.checks | Where-Object { $_.passed -eq $false -or ($null -ne $_.success_rate -and $_.success_rate -lt 1) })
                                if ($bad.Count) { throw 'Some Pydantic checks failed. See pydantic.json.' }
                            } finally {
                                if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue; $server.WaitForExit() }
                            }
                        }
                    }
                    'security-gate' { Run-Native uv @('run','python','-m','security.gate','--json-out',(Join-Path $out 'gate.json')) 'gate.log' | Out-Null }
                }
            }
        } catch {
            $status='fail'; $message=Scrub $_.Exception.Message
            if ($step.name -eq 'security-gate') { $message += ' Tailscale/firewall failures are expected before tailnet setup; send the report anyway.' }
            Write-Host ("FAIL: " + $message)
        } finally {
            $summary.steps += [ordered]@{name=$step.name; status=$status; seconds=[Math]::Round($watch.Elapsed.TotalSeconds,3); summary=$message}
        }
    }
} finally {
    Save-Text (Join-Path $out 'summary.json') ($summary | ConvertTo-Json -Depth 20)
    # Text-only allowlist excludes models, binaries, archives and arbitrary files.
    # Refuse reparse points so scrubbing never follows a link outside OutDir.
    $files = @(Get-ChildItem -LiteralPath $out -Recurse -File | Where-Object { $_.Extension -in @('.json','.csv','.log','.txt') })
    foreach ($file in $files) {
        $node=$file
        while ($node -and $node.FullName -ne $out) {
            if ($node.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Report directory must not contain links.' }
            if ($node -is [IO.FileInfo]) { $node=$node.Directory } else { $node=$node.Parent }
            if ($node -and $node.FullName -eq [IO.Path]::GetPathRoot($out)) { break }
        }
        Save-Text $file.FullName ([IO.File]::ReadAllText($file.FullName))
    }
    $zip=Join-Path $root ('report_B_'+[Environment]::MachineName+'_'+(Get-Date -Format 'yyyyMMdd-HHmm')+'.zip')
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipStream=[IO.File]::Open($zip,[IO.FileMode]::Create)
    $archive=New-Object IO.Compression.ZipArchive($zipStream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            $relative=$file.FullName.Substring($out.TrimEnd('\').Length+1).Replace('\','/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,$file.FullName,$relative) | Out-Null
        }
    } finally { $archive.Dispose() }
    Write-Host ''
    Write-Host 'Device B results (FAIL items are included in the zip):'
    foreach ($result in $summary.steps) { Write-Host ('{0,-8} {1,-20} {2}' -f $result.status.ToUpper(),$result.name,$result.summary) }
    Write-Host (Scrub ("Send this file to ADMIN: " + $zip))
}
exit 0
