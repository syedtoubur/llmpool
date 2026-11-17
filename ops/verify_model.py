"""Verify a local GGUF against a catalogue entry."""

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    message: str


CHUNK_SIZE = 8 * 1024 * 1024


def verify(path: str | Path, entry: dict, progress: Callable[[int], None] | None = None) -> VerifyResult:
    path = Path(path)
    if path.suffix.lower() != ".gguf":
        return VerifyResult(False, "File must have a .gguf extension")
    try:
        actual_size = path.stat().st_size
        if actual_size != entry["size_bytes"]:
            return VerifyResult(False, f"Size mismatch: expected {entry['size_bytes']} bytes, got {actual_size}")
        with path.open("rb") as handle:
            magic = handle.read(4)
            if magic != b"GGUF":
                return VerifyResult(False, "File does not have GGUF magic")
            digest = hashlib.sha256()
            digest.update(magic)
            bytes_hashed = len(magic)
            if progress is not None:
                progress(bytes_hashed)
            while chunk := handle.read(CHUNK_SIZE):
                digest.update(chunk)
                bytes_hashed += len(chunk)
                if progress is not None:
                    progress(bytes_hashed)
    except OSError as exc:
        return VerifyResult(False, f"Could not read file: {exc}")
    actual_hash = digest.hexdigest()
    if actual_hash != entry["sha256"]:
        return VerifyResult(False, f"SHA-256 mismatch: expected {entry['sha256']}, got {actual_hash}")
    return VerifyResult(True, "OK")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    import json
    catalogue = json.loads(args.catalogue.read_text(encoding="utf-8"))
    entry = next((model for model in catalogue["models"] if model["model_id"] == args.model_id), None)
    if entry is None:
        raise SystemExit(f"Model ID not found in catalogue: {args.model_id}")
    result = verify(args.path, entry)
    if not result.ok:
        raise SystemExit(result.message)
    print(result.message)


if __name__ == "__main__":
    main()
