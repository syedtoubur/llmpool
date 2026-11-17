"""Log memory probe samples to CSV or print a one-shot JSON report."""
import argparse, csv, json, socket, time
from dataclasses import asdict
from pathlib import Path
import psutil
from node_agent.memory import probe

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--interval",type=float,default=2); p.add_argument("--duration",type=float,default=60)
    p.add_argument("--out",default=None); p.add_argument("--once",action="store_true"); p.add_argument("--json",action="store_true")
    args=p.parse_args(); report=probe()
    if args.once and args.json:
        print(json.dumps(asdict(report),indent=2)); return
    if args.once:
        limited = {"gpu_memory": "GPU memory", "system_ram_reserve": f"the {report.reserve_bytes//(1024*1024)} MB RAM reserve kept for the owner (only {report.available_ram_bytes//(1024*1024)} MB RAM is free)", "budget": "the configured memory budget", "none": "no memory limit"}[report.limited_by]
        print(f"Usable for models: {report.usable_bytes//(1024*1024)} MB, limited by {limited}.")
        return
    path=Path(args.out or f"reports/memprobe-{socket.gethostname()}.csv"); path.parent.mkdir(parents=True,exist_ok=True)
    end=time.monotonic()+(0 if args.once else args.duration)
    with path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f); writer.writerow(["timestamp","adapter","kind","dedicated_used","dedicated_total","shared_used","shared_total","psutil_available","usable"])
        while True:
            report=probe(); available=psutil.virtual_memory().available
            targets=report.adapters or [None]
            for a in targets:
                name=a.name if a else "CPU"; kind=a.kind if a else "cpu"
                du=a.dedicated_used if a else 0; dt=a.dedicated_video_memory if a else report.total_bytes
                su=a.shared_used if a else 0; st=a.shared_system_memory if a else report.total_bytes
                usable=a.usable_bytes if a else report.usable_bytes
                print(f"{name:28} {kind:5} dedicated {du}/{dt} shared {su}/{st} available {available} usable {usable}")
                writer.writerow([time.time(),name,kind,du,dt,su,st,available,usable])
            f.flush()
            if args.once or time.monotonic()>=end: break
            time.sleep(max(0,args.interval))

if __name__ == "__main__": main()
