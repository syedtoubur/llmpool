import ipaddress, json, subprocess
from pathlib import Path
import psutil
from security.registry import Result, check

RISKY={3389:'Windows Remote Desktop',445:'Windows file sharing (SMB)',11434:'Ollama API',8080:'web service',5900:'VNC remote desktop',22:'SSH remote login'}

def _port_covers(value, target):
    if value in ('', '*', 'Any'): return True
    for item in value.split(','):
        item=item.strip()
        if item==str(target): return True
        if '-' in item:
            try:
                lo,hi=(int(x) for x in item.split('-',1))
                if lo<=target<=hi:return True
            except ValueError:pass
    return False

def _remote_is_tailnet(values):
    accepted={'100.64.0.0/10','100.64.0.0/255.192.0.0'}
    return bool(values) and all(v.strip() in accepted for v in values)

def _broad_rule(rule, owner_rules=()):
    if int(rule.get('Protocol',0)) not in (6,256): return False
    ports=str(rule.get('LocalPorts',''))
    if not (_port_covers(ports,8443) or _port_covers(ports,8444)): return False
    remotes=[x.strip() for x in str(rule.get('RemoteAddresses','*')).split(',')]
    if _remote_is_tailnet(remotes): return False
    app=str(rule.get('ApplicationName','') or '').strip()
    service=str(rule.get('ServiceName','') or '').strip()
    owners=[x for x in owner_rules if x.get('DisplayName')==rule.get('Name')]
    if owners and all(str(x.get('Owner','')).strip() for x in owners): return False
    family=_indirect_package_family(str(rule.get('Name','')))
    matches=[x for x in owner_rules if family and str(x.get('InstanceID','')).startswith(family)]
    if matches and any(str(x.get('Owner','')).strip() for x in matches): return False
    if not app and not service: return True
    return bool(app) and app.lower().replace('/','\\').endswith(('\\python.exe','\\pythonw.exe','\\llama-server.exe','\\uvicorn.exe'))

def _rule_program(rule):
    return rule.get('ApplicationName') or 'any program'

def _indirect_package_family(name):
    if not name.startswith('@{') or '?' not in name: return None
    parts=name[2:].split('?',1)[0].rstrip('}').rsplit('_',4)
    if len(parts)!=5:return None
    return f'{parts[0]}_{parts[4]}' if parts[0] and parts[4] else None

@check('local_bindings','network','Local service bindings','Private APIs must stay on this computer and tailnet services must not bind publicly.','Bind local APIs to 127.0.0.1; bind 8443/8444 to the device tailnet IP.')
def local_bindings(args):
    try: conns=psutil.net_connections(kind='tcp')
    except Exception as e: return Result('FAIL',f'Could not inspect listening sockets: {e}')
    found=[]; failures=[]
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr: continue
        ip,port=c.laddr.ip,c.laddr.port
        try: proc=psutil.Process(c.pid).name() if c.pid else 'unknown process'
        except Exception: proc='unknown process'
        found.append(f'{ip}:{port} ({proc})')
        if (18080<=port<=18099 or port in (9000,11500)) and ip not in ('127.0.0.1','::1'): failures.append(f'{ip}:{port} ({proc})')
        if port in (8443,8444):
            try: ok=ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10')
            except ValueError: ok=False
            if not ok: failures.append(f'{ip}:{port} ({proc})')
    if failures:return Result('FAIL','Unsafe listeners: '+', '.join(failures))
    if not found:return Result('SKIP','No pool ports are listening, so local binding checks were skipped.')
    return Result('PASS','All checked service listeners use the required addresses.')

@check('risky_listeners','network','Risky public listeners','Remote access services should not listen on every network interface.','Restrict these services to loopback or the tailnet and firewall them.')
def risky_listeners(args):
    try: conns=psutil.net_connections(kind='tcp')
    except Exception as e:return Result('FAIL',f'Could not inspect listening sockets: {e}')
    hits=[]
    for c in conns:
        if c.status==psutil.CONN_LISTEN and c.laddr and c.laddr.port in RISKY and c.laddr.ip in ('0.0.0.0','::'):
            p=c.laddr.port; hits.append(f'{p} is {RISKY[p]} ({c.laddr.ip})')
    return Result('WARN','Broad listeners: '+', '.join(hits)) if hits else Result('PASS','No listed risky services listen on all interfaces.')

@check('firewall_rules','network','Windows firewall rules','Inbound pool access must be limited to tailnet addresses.','Run ops/firewall.ps1 -Apply (add -Coordinator on the coordinator).')
def firewall_rules(args):
    script=Path(__file__).parents[2]/'ops'/'firewall.ps1'
    try:p=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Check'],capture_output=True,text=True)
    except Exception as e:return Result('FAIL',f'Could not query Windows Firewall: {e}')
    try:data=json.loads(p.stdout)
    except Exception:return Result('FAIL','Firewall check returned invalid JSON or could not run: '+(p.stderr.strip() or 'query failed'))
    errors=[]; accepted={'100.64.0.0/10','100.64.0.0/255.192.0.0'}
    if data.get('error'):
        return Result('FAIL',f"Could not read Windows Firewall state: {data['error']}")
    for profile in ('Domain','Private','Public'):
        rows=[x for x in data.get('profiles',[]) if x.get('Name')==profile]
        if not rows: errors.append(f'Could not read Windows Firewall profile {profile}')
        elif any(x.get('Enabled') not in (True,'True') or x.get('DefaultInboundAction') not in ('Block','NotConfigured') for x in rows):errors.append(f'Firewall profile {profile} is disabled or does not block inbound traffic')
    if 'rules' in data:
        data['llmpool_rules']=[]; data['broad_rules']=[]
    for raw in data.get('rules',[]):
        protocol=int(raw.get('Protocol',0)); proto='TCP' if protocol==6 else ('Any' if protocol==256 else str(protocol))
        inbound=raw.get('Direction',1) in (1,'1','Inbound')
        allow=raw.get('Action',1) in (1,'1','Allow')
        entry={'Name':raw.get('Name'),'Enabled':True,'Direction':'Inbound' if inbound else 'Outbound','Action':'Allow' if allow else 'Block','Protocol':proto,'LocalPort':raw.get('LocalPorts',''),'RemoteAddress':[x.strip() for x in str(raw.get('RemoteAddresses','')).split(',') if x.strip()]}
        if str(raw.get('Name','')).startswith('llmpool-'): data['llmpool_rules'].append(entry)
        if _broad_rule(raw,data.get('owner_rules',[])): data['broad_rules'].append({'Name':raw.get('Name'),'DisplayName':raw.get('Name'),'Program':_rule_program(raw)})
    required={'llmpool-node-8443':'8443'}
    if args.coordinator:required['llmpool-coordinator-8444']='8444'
    for name,port in required.items():
        rows=[x for x in data.get('llmpool_rules',[]) if x.get('Name')==name]
        if len(rows)!=1:errors.append(f'{name} is missing or duplicated');continue
        x=rows[0]
        for key,val in [('Enabled',True),('Direction','Inbound'),('Action','Allow'),('Protocol','TCP')]:
            if str(x.get(key)).lower()!=str(val).lower():errors.append(f'{name} has incorrect {key}')
        if str(x.get('LocalPort'))!=port:errors.append(f'{name} has incorrect LocalPort')
        remote=x.get('RemoteAddress',[]); remote=[remote] if isinstance(remote,str) else remote
        if len(remote)!=1 or remote[0] not in accepted:errors.append(f'{name} RemoteAddress must be exactly the tailnet range')
    broad=[x for x in data.get('broad_rules',[]) if x.get('Name') not in required]
    if broad:
        for x in broad:
            errors.append(f"Windows has a rule {x.get('DisplayName') or x.get('Name','(unnamed)')} that lets anyone on any network reach {x.get('Program') or 'the application'} on any port; this bypasses the pool's tailnet-only rule. Fix: delete it in Windows Defender Firewall > Inbound Rules, or run ops/firewall.ps1 -RemoveBroad (admin)")
    return Result('FAIL','; '.join(errors)) if errors else Result('PASS','Firewall profiles block inbound by default and required llmpool rules are tailnet-only.')

@check('tailscale_up','network','Tailnet connection','The machine must be connected to the private tailnet to serve pool traffic.','Install Tailscale, sign in to the pool tailnet, then run this check again.')
def tailscale_up(args):
    import shutil
    exe=shutil.which('tailscale') or r'C:\Program Files\Tailscale\tailscale.exe'
    if not shutil.which('tailscale') and not Path(exe).exists():return Result('FAIL','Tailscale is not installed (not found on PATH or at the standard install location).')
    try:
        status=json.loads(subprocess.run([exe,'status','--json'],capture_output=True,text=True,check=True).stdout)
    except Exception as e:return Result('FAIL',f'Tailscale is installed but could not report status: {e}')
    if status.get('BackendState')!='Running':return Result('FAIL',f"Tailscale is installed but not logged in or stopped (BackendState: {status.get('BackendState','unknown')}).")
    try:ipout=subprocess.run([exe,'ip','-4'],capture_output=True,text=True,check=True).stdout.strip().splitlines()[0]
    except Exception as e:return Result('FAIL',f'Tailscale is running but could not provide its IP: {e}')
    try:ok=ipaddress.ip_address(ipout) in ipaddress.ip_network('100.64.0.0/10')
    except ValueError:ok=False
    if not ok:return Result('FAIL',f'Tailscale IP {ipout} is outside 100.64.0.0/10.')
    features=[]
    if status.get('Self',{}).get('SSHEnabled'):features.append('Tailscale SSH is enabled and allows remote shell access')
    if status.get('ExitNodeStatus'):features.append('exit-node use is enabled and routes traffic through another device')
    if status.get('Self',{}).get('AdvertisedRoutes'):features.append('subnet-router routes are advertised and widen network access')
    return Result('WARN' if features else 'PASS',f"Connected at {ipout}. "+('; '.join(features) if features else 'Tailscale SSH, exit-node, and subnet-router features are off.'))
