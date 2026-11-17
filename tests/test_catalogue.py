import hashlib
import json

import httpx
import respx

from ops.add_model import add_model
from ops.verify_model import verify


def test_add_model_uses_lfs_and_card_license():
    spec = {"model_id": "demo", "repo": "org/demo", "file_pattern": "*Q4_K_M.gguf", "roles": ["worker"]}
    with respx.mock:
        respx.get("https://huggingface.co/api/models/org/demo/tree/main").mock(return_value=httpx.Response(200, json=[{"path": "demo.Q4_K_M.gguf", "size": 7, "lfs": {"oid": "a" * 64, "size": 123}}]))
        respx.get("https://huggingface.co/api/models/org/demo").mock(return_value=httpx.Response(200, json={"cardData": {"license": "apache-2.0"}}))
        entry = add_model(httpx.Client(), spec)
    assert entry["sha256"] == "a" * 64
    assert entry["size_bytes"] == 123
    assert entry["license"] == "apache-2.0"


def test_verify_cases(tmp_path):
    payload = b"GGUF" + b"model data"
    good = tmp_path / "model.gguf"
    good.write_bytes(payload)
    entry = {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    assert verify(good, entry).ok
    assert not verify(good, {**entry, "sha256": "0" * 64}).ok
    bad_magic = tmp_path / "bad.gguf"
    bad_magic.write_bytes(b"NOPE" + payload[4:])
    assert "magic" in verify(bad_magic, {"size_bytes": len(payload), "sha256": hashlib.sha256(bad_magic.read_bytes()).hexdigest()}).message
    wrong_ext = tmp_path / "model.bin"
    wrong_ext.write_bytes(payload)
    assert "extension" in verify(wrong_ext, entry).message
    assert "Size mismatch" in verify(good, {**entry, "size_bytes": 1}).message


def test_verify_hashes_multiple_chunks_and_reports_progress(tmp_path, monkeypatch):
    import ops.verify_model as verify_model

    payload = b"GGUF" + b"0123456789"
    path = tmp_path / "chunks.gguf"
    path.write_bytes(payload)
    monkeypatch.setattr(verify_model, "CHUNK_SIZE", 4, raising=False)
    progress = []

    result = verify(path, {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}, progress.append)

    assert result.ok
    assert progress[-1] == len(payload)
    assert len(progress) > 2


def test_verify_rejects_hash_mismatch_in_last_byte(tmp_path):
    payload = b"GGUF" + b"model data"
    path = tmp_path / "last-byte.gguf"
    path.write_bytes(payload)
    wrong_hash = bytearray(hashlib.sha256(payload).digest())
    wrong_hash[-1] ^= 1

    result = verify(path, {"size_bytes": len(payload), "sha256": bytes(wrong_hash).hex()})

    assert not result.ok
    assert "SHA-256 mismatch" in result.message


def test_verify_extension_is_case_insensitive_but_rejects_suffixes(tmp_path):
    payload = b"GGUF" + b"model data"
    entry = {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    uppercase = tmp_path / "model.GGUF"
    uppercase.write_bytes(payload)
    executable = tmp_path / "model.gguf.exe"
    executable.write_bytes(payload)
    binary = tmp_path / "model.bin"
    binary.write_bytes(payload)

    assert verify(uppercase, entry).ok
    assert "extension" in verify(executable, entry).message
    assert "extension" in verify(binary, entry).message
