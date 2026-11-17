import asyncio, json, socket, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from security.registry import Result
from security.checks.net_peer import evaluate_scan, scan_ports
from security.checks.net_local import local_bindings, risky_listeners, firewall_rules, tailscale_up, _port_covers, _remote_is_tailnet, _broad_rule, _indirect_package_family

def conn(ip,port): return SimpleNamespace(status='LISTEN',laddr=SimpleNamespace(ip=ip,port=port),pid=None)
def bind(items):
    with patch('security.checks.net_local.psutil.net_connections',return_value=items):return local_bindings(None)

@pytest.mark.parametrize('ip,port,status',[('127.0.0.1',18080,'PASS'),('::1',9000,'PASS'),('0.0.0.0',18080,'FAIL'),('192.168.1.5',9000,'FAIL'),('100.101.2.3',8443,'PASS'),('0.0.0.0',8443,'FAIL'),('::',8443,'FAIL')])
def test_bindings(ip,port,status): assert bind([conn(ip,port)]).status==status
def test_no_pool_listener_skips(): assert bind([]).status=='SKIP'
@pytest.mark.parametrize('port',[3389,445,11434,8080,5900,22])
def test_risky_listeners(port):
    with patch('security.checks.net_local.psutil.net_connections',return_value=[conn('0.0.0.0',port)]):
        r=risky_listeners(None); assert r.status=='WARN' and str(port) in r.detail

def firewall_data(rule=None,broad=None,disabled=None):
    rule=rule or dict(Name='llmpool-node-8443',Enabled=True,Direction='Inbound',Action='Allow',Protocol='TCP',LocalPort='8443',RemoteAddress=['100.64.0.0/10'])
    return dict(profiles=[dict(Name=n,Enabled=(n!=disabled),DefaultInboundAction='Block') for n in ('Domain','Private','Public')],llmpool_rules=[rule],broad_rules=broad or [])
def check_fw(data,coordinator=False):
    with patch('security.checks.net_local.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(data),stderr='')):return firewall_rules(SimpleNamespace(coordinator=coordinator))
def test_firewall_valid_and_missing():
    assert check_fw(firewall_data()).status=='PASS'
    assert check_fw(firewall_data(rule=None) | {'llmpool_rules':[]}).status=='FAIL'
@pytest.mark.parametrize('change',[{'Enabled':False},{'LocalPort':'80'},{'RemoteAddress':['Any']},{'RemoteAddress':['100.64.0.0/10','Any']}])
def test_firewall_invalid_rule(change):
    row=firewall_data()['llmpool_rules'][0]|change; assert check_fw(firewall_data(row)).status=='FAIL'
def test_firewall_broad_and_profile():
    assert check_fw(firewall_data(broad=[{'DisplayName':'python.exe rule','Name':'python'}])).status=='FAIL'
    assert check_fw(firewall_data(disabled='Public')).status=='FAIL'
def test_com_shaped_json_and_port_remote_parsing():
    shaped=firewall_data(); shaped['rules']=[dict(Name='llmpool-node-8443',Protocol=6,LocalPorts='8443',RemoteAddresses='100.64.0.0/10')]; shaped.pop('llmpool_rules'); assert check_fw(shaped).status=='PASS'
    for val in ('*','','8443','8000-9000','80,443,8443'): assert _port_covers(val,8443)
    assert _remote_is_tailnet(['100.64.0.0/10']) and _remote_is_tailnet(['100.64.0.0/255.192.0.0'])
    assert _remote_is_tailnet(['100.64.0.0/10','100.64.0.0/255.192.0.0'])
    assert not _remote_is_tailnet(['100.64.0.0/10','*'])
def test_program_broad_classification():
    assert check_fw(firewall_data(broad=[{'Name':'python','DisplayName':'Python listener','Program':'python.exe'}])).status=='FAIL'
    assert check_fw(firewall_data(broad=[])).status=='PASS'
    assert check_fw(firewall_data(broad=[{'Name':'empty','DisplayName':'Unscoped listener','Program':'any program'}])).status=='FAIL'
def test_unreadable_firewall_is_not_reported_disabled():
    r=check_fw({'error':'Access denied','profiles':[],'llmpool_rules':[],'broad_rules':[]})
    assert r.status=='FAIL' and 'Could not read' in r.detail and 'disabled' not in r.detail
def test_broad_rule_classifier():
    base=dict(Protocol=6,LocalPorts='*',RemoteAddresses='*',ApplicationName='C:\\Python\\python.exe',ServiceName='')
    assert _broad_rule(base)
    assert not _broad_rule(base|{'ApplicationName':'C:\\Apps\\chrome.exe'})
    assert _broad_rule(base|{'ApplicationName':'','ServiceName':''})
    uwp=base|{'Name':'Game Bar','ApplicationName':'','ServiceName':''}
    assert not _broad_rule(uwp,[{'DisplayName':'Game Bar','Owner':'S-1-5-21-user'}])
    assert _broad_rule(uwp,[{'DisplayName':'Game Bar','Owner':'S-1-5-21-user'},{'DisplayName':'Game Bar','Owner':''}])
    assert _broad_rule(uwp,[])
    examples=[('@{Microsoft.Todos_0.179.8901.0_x64__8wekyb3d8bbwe?ms-resource://x}','Microsoft.Todos_8wekyb3d8bbwe'),('@{Microsoft.SecHealthUI_1000.29680.1000.0_x64__8wekyb3d8bbwe?ms-resource://x}','Microsoft.SecHealthUI_8wekyb3d8bbwe'),('@{MicrosoftWindows.Client.CBS_1000.26100.385.0_x64__cw5n1h2txyewy?ms-resource://x}','MicrosoftWindows.Client.CBS_cw5n1h2txyewy')]
    for name,family in examples:
        assert _indirect_package_family(name)==family
        assert not _broad_rule(uwp|{'Name':name},[{'InstanceID':family+'S-1-5-21-1','Owner':'S-1-5-21-1'}])

@pytest.mark.skipif(sys.platform!='win32',reason='PowerShell firewall COM integration requires Windows')
def test_firewall_ps_check_emits_json_on_windows():
    script=Path(__file__).parents[1]/'ops'/'firewall.ps1'
    p=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Check'],capture_output=True,text=True)
    obj=json.loads(p.stdout)
    assert {'profiles','rules','error'}<=set(obj)
    assert obj['error'] is None or isinstance(obj['error'],str) and obj['error'].strip()
def test_coordinator_requires_8444():
    data=firewall_data(); assert check_fw(data,True).status=='FAIL'
    data['llmpool_rules'].append(dict(Name='llmpool-coordinator-8444',Enabled=True,Direction='Inbound',Action='Allow',Protocol='TCP',LocalPort='8444',RemoteAddress=['100.64.0.0/10']))
    assert check_fw(data,True).status=='PASS'

def test_tailscale_causes_and_fallback():
    with patch('shutil.which',return_value=None),patch('pathlib.Path.exists',return_value=False): assert 'not installed' in tailscale_up(None).detail
    args=SimpleNamespace(stdout=json.dumps({'BackendState':'Stopped'}))
    with patch('shutil.which',return_value='tailscale'),patch('security.checks.net_local.subprocess.run',return_value=args): assert 'not logged in or stopped' in tailscale_up(None).detail
    outputs=[SimpleNamespace(stdout=json.dumps({'BackendState':'Running'})),SimpleNamespace(stdout='10.1.1.1\n')]
    with patch('shutil.which',return_value='tailscale'),patch('security.checks.net_local.subprocess.run',side_effect=outputs): assert 'outside' in tailscale_up(None).detail
    outputs=[SimpleNamespace(stdout=json.dumps({'BackendState':'Running','Self':{'SSHEnabled':True,'AdvertisedRoutes':['10/8']},'ExitNodeStatus':{'ID':'x'}})),SimpleNamespace(stdout='100.101.2.3\n')]
    with patch('shutil.which',return_value=None),patch('pathlib.Path.exists',return_value=True),patch('security.checks.net_local.subprocess.run',side_effect=outputs) as run:
        r=tailscale_up(None); assert r.status=='WARN' and 'SSH' in r.detail and 'exit-node' in r.detail and 'subnet-router' in r.detail
        assert all('tailscale.exe' in str(c.args[0][0]) for c in run.call_args_list)

def test_scan_evaluation():
    assert evaluate_scan([8443]).status=='PASS'; assert evaluate_scan([8443,8444],True).status=='PASS'; assert evaluate_scan([8443,22]).status=='FAIL'
def test_loopback_scan():
    a=socket.socket();a.bind(('127.0.0.1',0));a.listen()
    try: assert asyncio.run(scan_ports('127.0.0.1',[a.getsockname()[1]],timeout=.1))==[a.getsockname()[1]]
    finally:a.close()
def test_subprocess_gate_registers_expected_checks(tmp_path):
    out=tmp_path/'gate.json'; p=subprocess.run([sys.executable,'-m','security.gate','--json-out',str(out)],capture_output=True,text=True)
    ids={r['id'] for r in json.loads(out.read_text())}; assert {'local_bindings','risky_listeners','firewall_rules','tailscale_up'}<=ids
def test_zero_checks_fail(tmp_path):
    from security import gate
    gate.REGISTRY.clear()
    with patch.object(gate,'load_checks'): assert gate.main(['--json-out',str(tmp_path/'none.json')])==1
    assert json.loads((tmp_path/'none.json').read_text())[0]['detail']=='No checks ran'
