import json

import pytest

from cloudtrim.cli import main


SNAP = {
    "cluster": "test",
    "captured_at": "2026-10-18T00:00:00Z",
    "containers": [
        {
            "namespace": "shop",
            "pod": "api-x",
            "container": "api",
            "cpu_request_m": 1000,
            "mem_request_mi": 1024,
            "samples": [{"cpu_m": 200, "mem_mi": 300}] * 10,
        },
        {
            "namespace": "shop",
            "pod": "web-x",
            "container": "web",
            "cpu_request_m": 200,
            "mem_request_mi": 256,
            "samples": [{"cpu_m": 100, "mem_mi": 120}] * 10,
        },
    ],
}


@pytest.fixture
def snap_file(tmp_path):
    f = tmp_path / "snap.json"
    f.write_text(json.dumps(SNAP))
    return str(f)


def test_text_mode(snap_file, capsys):
    assert main(["--from-file", snap_file]) == 0
    out = capsys.readouterr().out
    assert "api-x/api" in out
    assert "waste $" in out


def test_json_mode_parses_and_has_totals(snap_file, capsys):
    assert main(["--from-file", snap_file, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["container_count"] == 2
    assert data["summary"]["waste_monthly_usd"] > 0
    assert data["summary"]["cpu_request_m"] == 1200
    assert data["containers"][0]["verdict"] == "over"


def test_markdown_mode_renders_table(snap_file, capsys):
    assert main(["--from-file", snap_file, "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert "| namespace | workload |" in out
    assert "| shop | api-x/api |" in out


def test_fail_on_waste_pct(snap_file):
    # waste here is well over 10 percent of requested
    assert main(["--from-file", snap_file, "--fail-on-waste-pct", "10"]) == 1
    assert main(["--from-file", snap_file, "--fail-on-waste-pct", "99"]) == 0


def test_missing_file_is_a_clean_error(capsys):
    assert main(["--from-file", "/nope/does-not-exist.json"]) == 2
    assert "file not found" in capsys.readouterr().err


def test_malformed_snapshot_is_a_clean_error(tmp_path, capsys):
    f = tmp_path / "bad.json"
    f.write_text('{"containers": [{"namespace": "a"}]}')
    assert main(["--from-file", str(f)]) == 2
    assert "containers[0]" in capsys.readouterr().err
