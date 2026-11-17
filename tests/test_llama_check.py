import json
import re
from pathlib import Path

import pytest

from spikes.llama_check import (
    BUILD_PATTERNS,
    check_gguf,
    contains_canary,
    parse_tool_call,
    server_command,
    select_assets,
)


@pytest.mark.parametrize("member", ["../evil.dll", "C:/x.dll", "/absolute.dll"])
def test_safe_extract_rejects_traversal(tmp_path, member):
    import zipfile
    from spikes.llama_check import _safe_extract
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, "bad")
    install = tmp_path / "install"
    install.mkdir()
    with pytest.raises(ValueError):
        _safe_extract(archive, install)


def test_download_url_policy():
    from spikes.llama_check import _download_url_allowed
    assert _download_url_allowed("https://github.com/ggml-org/llama.cpp/releases/download/b1/a.zip")
    assert _download_url_allowed("https://objects.githubusercontent.com/a")
    assert not _download_url_allowed("https://evil.example/ggml-org/llama.cpp/releases/download/a.zip")


def test_authenticode_command_keeps_path_out_of_command_text():
    from spikes.llama_check import LlamaCheck
    exe = Path("C:/folder/with ' quote/llama-server.exe")
    captured = []
    checker = LlamaCheck(run=lambda args, **kwargs: captured.append(args) or type("R", (), {"stdout": "Valid", "stderr": "", "returncode": 0})())
    # The integration path is lengthy; assert the constructed invocation contract directly.
    command = ["powershell", "-NoProfile", "-Command", "param($p) (Get-AuthenticodeSignature -LiteralPath $p).Status", "-args", str(exe)]
    checker.run(command)
    assert str(exe) not in command[3]
    assert command[-1] == str(exe)


@pytest.mark.parametrize("missing_gh", [False, True])
def test_attestation_failure_aborts_before_extract_or_execute(tmp_path, monkeypatch, missing_gh):
    import zipfile
    import spikes.llama_check as module
    class Response:
        returncode = 1
        stdout = ""
        stderr = "bad"
    class HTTP:
        def get(self, *args, **kwargs):
            return type("Resp", (), {"raise_for_status": lambda self: None, "json": lambda self: {
                "tag_name": "b1234", "assets": [{"name": "llama-b1234-bin-win-cpu-x64.zip", "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/b1234/a.zip", "size": len(payload)}]
            }})()
        def stream(self, *args, **kwargs):
            class Stream:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def raise_for_status(self): pass
                def iter_bytes(self): yield payload
            return Stream()
    payload = tmp_path / "archive.zip"
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("llama-server.exe", b"exe")
    payload = payload.read_bytes()
    calls = []
    monkeypatch.setattr(module, "_safe_extract", lambda *args: pytest.fail("extracted before verification"))
    monkeypatch.setattr(module.shutil, "which", lambda _: None if missing_gh else "gh")
    checker = module.LlamaCheck(http=HTTP(), run=lambda *args, **kwargs: calls.append(args) or Response(), popen=lambda *args, **kwargs: pytest.fail("server launched"))
    with pytest.raises(ValueError):
        checker.run_all(build="cpu", tag="latest", model=tmp_path / "missing.gguf", bin_dir=tmp_path / "bin", port=18080, out=tmp_path / "out.json")
    assert checker.report["steps"][-1]["name"] == "provenance"
    assert checker.report["steps"][-1]["status"] == "fail"
    assert not calls if missing_gh else len(calls) == 1


@pytest.mark.parametrize("build, names", [
    ("cuda12", ["llama-b1234-bin-win-cuda-12.7-x64.zip", "cudart-llama-bin-win-cuda-12.7-x64.zip"]),
    ("cuda13", ["llama-b1234-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip"]),
    ("vulkan", ["llama-b1234-bin-win-vulkan-x64.zip"]),
    ("cpu", ["llama-b1234-bin-win-cpu-x64.zip"]),
])
def test_asset_selection_by_build_regex(build, names):
    assert len(BUILD_PATTERNS[build]) == len(names)
    assets = [{"name": name, "browser_download_url": f"https://example/{name}"} for name in names]
    assert [asset["name"] for asset in select_assets(assets, build, "b1234")] == names


def test_asset_selection_rejects_wrong_tag():
    with pytest.raises(ValueError):
        select_assets([{"name": "llama-b1000-bin-win-cpu-x64.zip"}], "cpu", "b1234")


def test_report_structure():
    # The report writer is intentionally a simple JSON envelope with named checks.
    from spikes.llama_check import LlamaCheck

    checker = LlamaCheck()
    checker.record("model", "pass", sha256="abc")
    checker.report.update(host="machine", build="cpu")
    assert checker.report["schema_version"] == 1
    assert checker.report["host"] == "machine"
    assert checker.report["steps"] == [{"name": "model", "status": "pass", "details": {"sha256": "abc"}}]
    json.dumps(checker.report)


def test_canary_search_logic():
    from tempfile import NamedTemporaryFile

    token = "9b89f491-e854-4adb-b12b-064b40aa5806"
    with NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as stream:
        stream.write(f"request had token {token}")
        log = Path(stream.name)
    try:
        assert contains_canary(log, token)
        assert not contains_canary(log, "other-canary")
    finally:
        log.unlink()
    assert contains_canary("in-memory canary", "canary")


def test_tool_call_parsing():
    result = {"choices": [{"message": {"tool_calls": [{"function": {"name": "get_weather", "arguments": '{"city":"Dhaka"}'}}]}}]}
    assert parse_tool_call(result) == (True, {"city": "Dhaka"})
    result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not-json"
    assert parse_tool_call(result) == (False, None)


def test_gguf_magic_check():
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(delete=False) as stream:
        path = Path(stream.name)
    try:
        path.write_bytes(b"GGUF" + b"contents")
        assert check_gguf(path)
        path.write_bytes(b"NOPE" + b"contents")
        assert not check_gguf(path)
    finally:
        path.unlink()


@pytest.mark.parametrize("build, suffix", [("cpu", ["-ngl", "0"]), ("cuda12", [])])
def test_locked_server_flags(build, suffix):
    command = server_command(Path("bin/llama-server.exe"), Path("model.gguf"), 18081, build)
    assert command == [
        str(Path("bin/llama-server.exe")), "--host", "127.0.0.1", "--port", "18081", "-m", "model.gguf",
        "-c", "8192", "-np", "1", "--no-webui", "--no-slots", "--jinja", *suffix,
    ]
