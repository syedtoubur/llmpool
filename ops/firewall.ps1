[CmdletBinding()]
param([switch]$Apply,[switch]$Coordinator,[switch]$Check,[switch]$Remove,[switch]$RemoveBroad,[string[]]$Names)
$ErrorActionPreference = 'Stop'
$names = @('llmpool-node-8443', 'llmpool-coordinator-8444')
if ($Apply -or $Remove -or $RemoveBroad) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Output 'Administrator rights are required. Open PowerShell as Administrator and run this command again.'; exit 1 }
}
if ($Apply) {
    $adapter = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -like '*Tailscale*' } | Select-Object -First 1
    $wanted = @(@{ Name=$names[0]; Port='8443' }); if ($Coordinator) { $wanted += @{ Name=$names[1]; Port='8444' } }
    foreach ($rule in $wanted) {
        Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        $params = @{Name=$rule.Name; DisplayName=$rule.Name; Direction='Inbound'; Action='Allow'; Protocol='TCP'; LocalPort=$rule.Port; RemoteAddress='100.64.0.0/10'; Profile='Any'}
        if ($adapter) { $params.InterfaceAlias = $adapter.Name }
        New-NetFirewallRule @params | Out-Null
    }
    if ($adapter) { Write-Output "Firewall rules applied for requested ports, limited to Tailscale interface '$($adapter.Name)'." } else { Write-Output 'Firewall rules applied for requested ports; no Tailscale adapter was found, so no interface restriction was added.' }
    exit 0
}
if ($Check) {
    try {
        $fw = New-Object -ComObject HNetCfg.FwPolicy2
        $profiles = @(
            [pscustomobject]@{Name='Domain'; Enabled=[bool]$fw.FirewallEnabled(1); DefaultInboundAction=$(if ($fw.DefaultInboundAction(1) -eq 0) {'Block'} else {'Allow'})}
            [pscustomobject]@{Name='Private'; Enabled=[bool]$fw.FirewallEnabled(2); DefaultInboundAction=$(if ($fw.DefaultInboundAction(2) -eq 0) {'Block'} else {'Allow'})}
            [pscustomobject]@{Name='Public'; Enabled=[bool]$fw.FirewallEnabled(4); DefaultInboundAction=$(if ($fw.DefaultInboundAction(4) -eq 0) {'Block'} else {'Allow'})}
        )
        $rules = @()
        $ownerRules = @(Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction Stop | ForEach-Object { [pscustomobject]@{DisplayName=[string]$_.DisplayName; InstanceID=[string]$_.InstanceID; Owner=[string]$_.Owner} })
        foreach ($r in $fw.Rules) {
            if ($r.Enabled -and $r.Direction -eq 1 -and $r.Action -eq 1) { $rules += [pscustomobject]@{Name=[string]$r.Name; Protocol=[int]$r.Protocol; LocalPorts=[string]$r.LocalPorts; RemoteAddresses=[string]$r.RemoteAddresses; ApplicationName=[string]$r.ApplicationName; ServiceName=[string]$r.serviceName; Profiles=[int]$r.Profiles; InterfaceTypes=[string]$r.InterfaceTypes} }
        }
        ConvertTo-Json -InputObject ([pscustomobject]@{profiles=$profiles; rules=$rules; owner_rules=$ownerRules; error=$null}) -Depth 6 -Compress
    } catch { ConvertTo-Json -InputObject ([pscustomobject]@{profiles=@(); rules=@(); owner_rules=@(); error="Firewall query failed; administrator rights or firewall access may be required: $($_.Exception.Message)"}) -Compress }
    exit 0
}
if ($RemoveBroad) {
    $fw=New-Object -ComObject HNetCfg.FwPolicy2; $targets=@($fw.Rules | Where-Object {$_.Name -in $Names})
    Write-Output 'Disabling named broad rules:'
    foreach($r in $targets){Write-Output $r.Name; $r.Enabled=$false}
    Write-Output ("Disabled {0} matching rules." -f $targets.Count); exit 0
}
if ($Remove) { Get-NetFirewallRule -Name 'llmpool-*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; Write-Output 'llmpool firewall rules removed.'; exit 0 }
Write-Output 'Choose -Apply, -Check, or -Remove.'; exit 2
