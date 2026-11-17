import json
from types import SimpleNamespace

from node_agent.memory import MIB, AdapterInfo, classify, format_luid, parse_luid_key, sum_phys_instances, usable_for, probe


def test_luid_and_phys_sum():
    key=format_luid(0x137a5,0)
    assert key=="luid_0x000137a5_0x00000000"
    assert parse_luid_key(key)==(0x137a5,0)
    assert sum_phys_instances({key+"_phys_0":2,key+"_phys_1":3})=={key:5}


def test_classification():
    assert classify(0x10de,0)=="cuda"
    assert classify(0x8086,512*MIB)=="dgpu"
    assert classify(0x8086,1)=="igpu"
    assert classify(0,0,False)=="cpu"


def test_usable_clamps_reserve_and_budget():
    assert usable_for("cuda",free=1024*MIB)==512*MIB
    assert usable_for("dgpu",dedicated=1024*MIB)==512*MIB
    assert usable_for("igpu",shared=3000*MIB,available=4000*MIB,reserve_ram_mb=1000)==3000*MIB
    assert usable_for("cpu",available=100*MIB,reserve_ram_mb=200)==0
    assert usable_for("cpu",available=4096*MIB,reserve_ram_mb=0,memory_budget_mb=512)==512*MIB


def setup_probe(monkeypatch, adapters, usage, available=4*MIB, total=8*MIB, nvml=()):
    monkeypatch.setattr("node_agent.memory.enumerate_dxgi",lambda:adapters)
    monkeypatch.setattr("node_agent.memory.pdh_usage",lambda:usage)
    monkeypatch.setattr("node_agent.memory.nvml_memories",lambda:list(nvml))
    monkeypatch.setattr("node_agent.memory.psutil.virtual_memory",lambda:SimpleNamespace(available=available,total=total))


def test_cpu_without_hardware_adapter(monkeypatch):
    setup_probe(monkeypatch, [], {}, available=3*MIB, total=8*MIB)
    r=probe(reserve_ram_mb=1)
    assert (r.kind,r.total_bytes,r.used_bytes,r.usable_bytes)==("cpu",8*MIB,5*MIB,2*MIB)
    assert r.limited_by=="system_ram_reserve" and r.adapters==[]


def test_igpu_total_used_and_limiter(monkeypatch):
    a=AdapterInfo("Intel",0x8086,1,128*MIB,0,4096*MIB,"luid_0x00000000_0x00000001",0)
    setup_probe(monkeypatch,[a],{a.luid_key:{"dedicated_used":10*MIB,"shared_used":900*MIB}},available=423*MIB,total=8*1024*MIB)
    r=probe()
    assert a.total_bytes==4224*MIB
    assert a.used_bytes==910*MIB
    assert r.usable_bytes==0 and r.limited_by=="system_ram_reserve"
    assert r.available_ram_bytes==423*MIB and r.reserve_bytes==2048*MIB


def test_limited_by_all_values(monkeypatch):
    from node_agent.memory import _usable_and_limit
    assert _usable_and_limit("igpu",shared=100*MIB,available=1000*MIB,reserve_ram_mb=0)[1]=="gpu_memory"
    assert _usable_and_limit("igpu",shared=1000*MIB,available=100*MIB,reserve_ram_mb=200)[1]=="system_ram_reserve"
    assert _usable_and_limit("igpu",shared=1000*MIB,available=2000*MIB,reserve_ram_mb=0,memory_budget_mb=10)[1]=="budget"
    assert AdapterInfo("x",0,0,0,0,0,"x",0).limited_by=="none"


def test_nvidia_uses_mocked_pynvml(monkeypatch):
    import sys
    fake=SimpleNamespace(nvmlInit=lambda:None,nvmlDeviceGetCount=lambda:1,
        nvmlDeviceGetHandleByIndex=lambda i:"handle",nvmlDeviceGetName=lambda h:b"NVIDIA",
        nvmlDeviceGetMemoryInfo=lambda h:SimpleNamespace(free=2*MIB,total=8*MIB))
    monkeypatch.setitem(sys.modules,"pynvml",fake)
    from node_agent.memory import nvml_memories
    assert nvml_memories()==[("NVIDIA",2*MIB,8*MIB)]
    a=AdapterInfo("NVIDIA",0x10de,1,8*MIB,0,0,"nvidia",0)
    setup_probe(monkeypatch,[a],{},nvml=[("NVIDIA",2*MIB,8*MIB)])
    r=probe(reserve_ram_mb=0)
    assert (r.total_bytes,r.used_bytes,r.method,r.limited_by)==(8*MIB,6*MIB,"nvml","gpu_memory")


def test_pdh_missing_uses_powershell_and_real_json_paths(monkeypatch):
    import builtins, subprocess
    from node_agent.memory import pdh_usage
    key="luid_0x00000000_0x000137a5"
    rows=[
        {"Path":rf"\\hostname\gpu adapter memory({key}_phys_0)\shared usage","CookedValue":12},
        {"Path":rf"\\hostname\gpu adapter memory({key}_phys_1)\shared usage","CookedValue":8},
        {"Path":rf"\\hostname\gpu adapter memory({key}_phys_0)\dedicated usage","CookedValue":3},
    ]
    result=SimpleNamespace(stdout=json.dumps(rows))
    monkeypatch.setattr(subprocess,"run",lambda *a,**k:result)
    real=builtins.__import__
    def importing(name,*a,**kw):
        if name=="win32pdh": raise ImportError()
        return real(name,*a,**kw)
    monkeypatch.setattr(builtins,"__import__",importing)
    assert pdh_usage()=={key:{"shared_used":20,"dedicated_used":3}}
