from dataclasses import dataclass

REGISTRY = []

@dataclass
class Result:
    status: str
    detail: str

def check(check_id, category, title, why, fix):
    def register(fn):
        fn.metadata = dict(id=check_id, category=category, title=title, why=why, fix=fix)
        REGISTRY.append(fn)
        return fn
    return register
