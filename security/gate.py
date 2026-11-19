import argparse
import importlib
import json
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from security.registry import REGISTRY, Result

def load_checks():
    for module in ('security.checks.net_local', 'security.checks.net_peer'):
        importlib.import_module(module)

def run_checks(args):
    load_checks()
    results = []
    for fn in REGISTRY:
        if fn.__module__.endswith('net_peer') and not args.peer:
            continue
        try: result = fn(args)
        except Exception as exc: result = Result('FAIL', f'Check could not complete: {exc}')
        results.append({**fn.metadata, **asdict(result)})
    return results

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--peer'); p.add_argument('--peer-is-coordinator', action='store_true'); p.add_argument('--lan-peer')
    p.add_argument('--coordinator', action='store_true')
    p.add_argument('--json-out', default='reports/gate-host.json')
    args = p.parse_args(argv)
    results = run_checks(args)
    if not results:
        results = [{'id':'gate','category':'security','title':'Security gate','why':'Checks must execute to establish security.','fix':'Restore registered checks.','status':'FAIL','detail':'No checks ran'}]
    print('STATUS  CHECK              RESULT / WHY / FIX')
    for r in results:
        print(f"{r['status']:<7} {r['id']:<18} {r['title']}: {r['detail']} Why: {r['why']} Fix: {r['fix']}")
    out = Path(args.json_out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(results, indent=2), encoding='utf-8')
    return int(any(r['status'] == 'FAIL' for r in results))

if __name__ == '__main__':
    raise SystemExit(main())
