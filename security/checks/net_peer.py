import asyncio, socket, subprocess, ipaddress
from security.registry import Result, check

SERVICES={20:'ftp-data',21:'ftp',22:'ssh',23:'telnet',25:'smtp',53:'dns',80:'http',110:'pop3',143:'imap',443:'https',445:'smb',3389:'rdp',5900:'vnc',8080:'http-alt',8443:'llmpool-proxy',8444:'llmpool-coordinator',11434:'ollama'}

async def scan_ports(host, ports=range(1,65536), concurrency=500, timeout=.5, progress=None):
    sem=asyncio.Semaphore(concurrency); opened=[]; done=0; total=len(ports)
    async def probe(port):
        nonlocal done
        async with sem:
            try:
                _,w=await asyncio.wait_for(asyncio.open_connection(host,port),timeout)
                w.close(); await w.wait_closed(); opened.append(port)
            except (OSError,asyncio.TimeoutError): pass
            done+=1
            if progress and done%1000==0: progress(done,total)
    await asyncio.gather(*(probe(p) for p in ports))
    if progress: progress(total,total)
    return sorted(opened)

def evaluate_scan(open_ports, coordinator=False):
    expected={8443} | ({8444} if coordinator else set()); unexpected=set(open_ports)-expected
    if set(open_ports)==expected: return Result('PASS',f'Open ports match expected set {sorted(expected)}.')
    names=', '.join(f'{p}/{SERVICES.get(p,"unknown service")}' for p in sorted(unexpected)) or f'missing expected ports {sorted(expected-set(open_ports))}'
    return Result('FAIL',f'Unexpected or missing ports: {names}.')

@check('peer_port_scan','network','Peer tailnet ports','Only the node proxy, and coordinator API where applicable, should accept tailnet connections.','Close unexpected services and allow only TCP 8443 (plus 8444 on the coordinator).')
def peer_port_scan(args):
    try: source=subprocess.run(['tailscale','ip','-4'],capture_output=True,text=True,check=True).stdout.strip().splitlines()[0]
    except Exception: source='unknown tailnet IP'
    try: valid=ipaddress.ip_address(args.peer) in ipaddress.ip_network('100.64.0.0/10')
    except ValueError: valid=False
    if not valid:return Result('FAIL',f'Peer {args.peer} is outside 100.64.0.0/10; scan refused. Tested from {source} to {args.peer}.')
    ports=asyncio.run(scan_ports(args.peer,progress=lambda d,t: print(f'Port scan progress: {d}/{t}')))
    r=evaluate_scan(ports,args.peer_is_coordinator)
    r.detail+=f' Tested from {source} to {args.peer}.'
    return r

@check('lan_exposure','network','Peer LAN exposure','Pool ports must not be reachable through a LAN or public address.','Restrict 8443 and 8444 to the tailnet and block them on the LAN firewall.')
def lan_exposure(args):
    if not args.lan_peer: return Result('SKIP','No --lan-peer address was supplied.')
    async def probe(port):
        try:
            _,w=await asyncio.wait_for(asyncio.open_connection(args.lan_peer,port),.5); w.close(); await w.wait_closed(); return True
        except (OSError,asyncio.TimeoutError): return False
    opens=[p for p in (8443,8444) if asyncio.run(probe(p))]
    return Result('FAIL' if opens else 'PASS',f'Open on LAN/public address: {opens}' if opens else 'Ports 8443 and 8444 are closed on this address.')
