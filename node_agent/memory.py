"""System-wide memory probe using DXGI, PDH, NVML and psutil."""
from __future__ import annotations
import ctypes, json, re, subprocess, sys
from dataclasses import dataclass
import psutil

MIB = 1024 * 1024

@dataclass
class AdapterInfo:
    name: str
    vendor_id: int
    device_id: int
    dedicated_video_memory: int
    dedicated_system_memory: int
    shared_system_memory: int
    luid_key: str
    flags: int
    dedicated_used: int = 0
    shared_used: int = 0
    kind: str = "igpu"
    usable_bytes: int = 0
    total_bytes: int = 0
    used_bytes: int = 0
    method: str = "pdh+dxgi"
    limited_by: str = "none"

@dataclass
class MemoryReport:
    kind: str
    usable_bytes: int
    total_bytes: int
    used_bytes: int
    method: str
    adapters: list[AdapterInfo]
    limited_by: str = "none"
    available_ram_bytes: int = 0
    reserve_bytes: int = 0

def format_luid(high: int, low: int) -> str:
    return "luid_0x%08x_0x%08x" % (high & 0xffffffff, low & 0xffffffff)

def parse_luid_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"luid_0x([0-9a-fA-F]{8})_0x([0-9a-fA-F]{8})", key)
    if not m: raise ValueError(key)
    return int(m[1], 16), int(m[2], 16)

def sum_phys_instances(values: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for path, value in values.items():
        m = re.fullmatch(r"(luid_0x[0-9a-f]{8}_0x[0-9a-f]{8})_phys_\d+", path, re.I)
        if m: result[m[1].lower()] = result.get(m[1].lower(), 0) + int(value)
    return result

def classify(vendor_id: int, dedicated_video_memory: int, hardware: bool = True) -> str:
    if not hardware: return "cpu"
    if vendor_id == 0x10DE: return "cuda"
    return "dgpu" if dedicated_video_memory >= 512*MIB else "igpu"

def usable_for(kind: str, *, free=0, dedicated=0, dedicated_used=0, shared=0, shared_used=0,
               available=0, reserve_ram_mb=2048, memory_budget_mb=None) -> int:
    if kind == "cuda": n = free - 512*MIB
    elif kind == "dgpu": n = dedicated - dedicated_used - 512*MIB
    elif kind == "igpu": n = min(shared-shared_used, available-reserve_ram_mb*MIB)
    else: n = available-reserve_ram_mb*MIB
    n = max(0, n)
    return min(n, memory_budget_mb*MIB) if memory_budget_mb is not None else n

def _usable_and_limit(kind: str, *, free=0, dedicated=0, dedicated_used=0, shared=0, shared_used=0,
                      available=0, reserve_ram_mb=2048, memory_budget_mb=None) -> tuple[int, str]:
    reserve = reserve_ram_mb * MIB
    if kind == "cuda": constraints = [(free - 512*MIB, "gpu_memory")]
    elif kind == "dgpu": constraints = [(dedicated - dedicated_used - 512*MIB, "gpu_memory")]
    elif kind == "igpu": constraints = [(shared-shared_used, "gpu_memory"), (available-reserve, "system_ram_reserve")]
    else: constraints = [(available-reserve, "system_ram_reserve")]
    amount, reason = min(constraints, key=lambda item: item[0])
    if memory_budget_mb is not None and memory_budget_mb*MIB < amount:
        amount, reason = memory_budget_mb*MIB, "budget"
    return max(0, amount), reason if amount > 0 else reason

def enumerate_dxgi() -> list[AdapterInfo]:
    if sys.platform != "win32": return []
    from ctypes import wintypes
    class LUID(ctypes.Structure): _fields_=[("LowPart",wintypes.DWORD),("HighPart",wintypes.LONG)]
    class Desc(ctypes.Structure): _fields_=[("Description",wintypes.WCHAR*128),("VendorId",wintypes.UINT),("DeviceId",wintypes.UINT),("SubSysId",wintypes.UINT),("Revision",wintypes.UINT),("DedicatedVideoMemory",ctypes.c_size_t),("DedicatedSystemMemory",ctypes.c_size_t),("SharedSystemMemory",ctypes.c_size_t),("AdapterLuid",LUID),("Flags",wintypes.UINT)]
    # dxgi.h order: IUnknown slots 0-2, IDXGIFactory slots 3-11, EnumAdapters1=12;
    # IDXGIAdapter1 inherits IDXGIObject (0-6), IDXGIAdapter (7-9), GetDesc1=10.
    dll=ctypes.WinDLL("dxgi"); factory=ctypes.c_void_p()
    iid=(ctypes.c_byte*16)(0x78,0xae,0x0a,0x77,0x6f,0xf2,0xba,0x4d,0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87)
    create=dll.CreateDXGIFactory1; create.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]; create.restype=ctypes.c_long
    if create(iid,ctypes.byref(factory))<0: raise OSError("CreateDXGIFactory1 failed")
    def method(obj,index,restype,args):
        table=ctypes.cast(obj,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype,ctypes.c_void_p,*args)(table[index])
    release=method(factory,2,wintypes.ULONG,[]); enum=method(factory,12,ctypes.c_long,[wintypes.UINT,ctypes.POINTER(ctypes.c_void_p)])
    found=[]; i=0
    try:
        while True:
            ptr=ctypes.c_void_p()
            if enum(factory,i,ctypes.byref(ptr)) != 0: break
            try:
                desc=Desc()
                if method(ptr,10,ctypes.c_long,[ctypes.POINTER(Desc)])(ptr,ctypes.byref(desc))<0:
                    i+=1
                    continue
                if desc.Flags&2 or desc.VendorId==0x1414: i+=1; continue
                found.append(AdapterInfo(desc.Description,desc.VendorId,desc.DeviceId,desc.DedicatedVideoMemory,desc.DedicatedSystemMemory,desc.SharedSystemMemory,format_luid(desc.AdapterLuid.HighPart,desc.AdapterLuid.LowPart),desc.Flags))
            finally: method(ptr,2,wintypes.ULONG,[])(ptr)
            i+=1
    finally: release(factory)
    return found

def pdh_usage() -> dict[str,dict[str,int]]:
    try:
        import win32pdh
        q=win32pdh.OpenQuery(); counters={}
        try:
            for label in ("Dedicated Usage","Shared Usage"):
                _,paths=win32pdh.ExpandWildCardPath(None,"\\GPU Adapter Memory(*)\\"+label,0)
                for p in paths: counters[(label,p)]=win32pdh.AddCounter(q,p)
            win32pdh.CollectQueryData(q); result={}
            for (label,path),counter in counters.items():
                _,v=win32pdh.GetFormattedCounterValue(counter,win32pdh.PDH_FMT_LARGE)
                m=re.search(r"(luid_0x[0-9a-f]{8}_0x[0-9a-f]{8})_phys_\d+",path,re.I)
                if m:
                    key=m[1].lower(); field="dedicated_used" if label.startswith("Dedicated") else "shared_used"
                    result.setdefault(key,{})[field]=result.setdefault(key,{}).get(field,0)+int(v)
            return result
        finally: win32pdh.CloseQuery(q)
    except Exception:
        cmd="$c=Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage'; $c.CounterSamples | Select Path,CookedValue | ConvertTo-Json -Compress"
        rows=json.loads(subprocess.run(["powershell","-NoProfile","-Command",cmd],capture_output=True,text=True,check=True).stdout or "[]")
        if isinstance(rows,dict): rows=[rows]
        result={}
        for row in rows:
            m=re.search(r"luid_0x[0-9a-f]{8}_0x[0-9a-f]{8}",row["Path"],re.I)
            if m:
                key=m[0].lower(); field="dedicated_used" if "dedicated usage" in row["Path"].lower() else "shared_used"
                result.setdefault(key,{})[field]=result.setdefault(key,{}).get(field,0)+int(row["CookedValue"])
        return result

def nvml_memories():
    try:
        import pynvml
        pynvml.nvmlInit(); result=[]
        for i in range(pynvml.nvmlDeviceGetCount()):
            h=pynvml.nvmlDeviceGetHandleByIndex(i); name=pynvml.nvmlDeviceGetName(h)
            result.append((name.decode(errors="replace") if isinstance(name,bytes) else str(name),pynvml.nvmlDeviceGetMemoryInfo(h).free,pynvml.nvmlDeviceGetMemoryInfo(h).total))
        return result
    except Exception: return []

def probe(reserve_ram_mb: int=2048,memory_budget_mb: int|None=None) -> MemoryReport:
    adapters=[a for a in enumerate_dxgi() if not a.flags&2 and a.vendor_id!=0x1414]
    usage=pdh_usage(); available=psutil.virtual_memory().available; nvml=nvml_memories(); ni=0
    for a in adapters:
        u=usage.get(a.luid_key,{}); a.dedicated_used=u.get("dedicated_used",0); a.shared_used=u.get("shared_used",0)
        a.kind=classify(a.vendor_id,a.dedicated_video_memory); free=a.dedicated_video_memory; total=free
        if a.kind=="cuda" and ni<len(nvml):
            # NVIDIA DXGI and NVML enumeration order is the device matching rule.
            _,free,total=nvml[ni]; ni+=1; a.method="nvml"
        a.usable_bytes,a.limited_by=_usable_and_limit(a.kind,free=free,dedicated=a.dedicated_video_memory,dedicated_used=a.dedicated_used,shared=a.shared_system_memory,shared_used=a.shared_used,available=available,reserve_ram_mb=reserve_ram_mb,memory_budget_mb=memory_budget_mb)
        a.total_bytes=total if a.kind=="cuda" else (a.dedicated_video_memory if a.kind=="dgpu" else a.dedicated_video_memory+a.shared_system_memory)
        a.used_bytes=max(0,total-free) if a.kind=="cuda" else (a.dedicated_used if a.kind=="dgpu" else a.dedicated_used+a.shared_used)
    if not adapters:
        vm=psutil.virtual_memory(); usable,limited=_usable_and_limit("cpu",available=available,reserve_ram_mb=reserve_ram_mb,memory_budget_mb=memory_budget_mb)
        return MemoryReport("cpu",usable,vm.total,vm.total-available,"psutil",[],limited,available,reserve_ram_mb*MIB)
    best=max(adapters,key=lambda x:x.usable_bytes)
    return MemoryReport(best.kind,best.usable_bytes,best.total_bytes,best.used_bytes,best.method if best.kind in ("cuda","dgpu") else "pdh+dxgi+psutil",adapters,best.limited_by,available,reserve_ram_mb*MIB)
