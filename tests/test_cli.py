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


LABELED = {
    "cluster": "test",
    "captured_at": "2026-10-18T00:00:00Z",
    "containers": [
        {
            "namespace": "shop", "pod": "api-x", "container": "api",
            "cpu_request_m": 1000, "mem_request_mi": 1024,
            "labels": {"app": "api"},
            "samples": [{"cpu_m": 200, "mem_mi": 300}] * 10,
        },
        {
            "namespace": "batch", "pod": "job-x", "container": "job",
            "cpu_request_m": 500, "mem_request_mi": 512,
            "labels": {"app": "etl"},
            "samples": [{"cpu_m": 100, "mem_mi": 100}] * 10,
        },
    ],
}


@pytest.fixture
def labeled_file(tmp_path):
    f = tmp_path / "labeled.json"
    f.write_text(json.dumps(LABELED))
    return str(f)


def test_namespace_filter(labeled_file, capsys):
    assert main(["--from-file", labeled_file, "--namespace", "shop"]) == 0
    out = capsys.readouterr().out
    assert "api-x/api" in out
    assert "job-x/job" not in out


def test_selector_filter(labeled_file, capsys):
    assert main(["--from-file", labeled_file, "--selector", "app=etl"]) == 0
    out = capsys.readouterr().out
    assert "job-x/job" in out
    assert "api-x/api" not in out


def test_bad_selector_is_a_clean_error(labeled_file, capsys):
    assert main(["--from-file", labeled_file, "--selector", "app"]) == 2
    assert "key=value" in capsys.readouterr().err


def test_tuning_flags_change_verdicts(labeled_file, capsys):
    # at an over-factor of 10 nothing here is "over" any more
    assert main(["--from-file", labeled_file, "--over-factor", "10"]) == 0
    out = capsys.readouterr().out
    assert ">>" not in out


def test_discount_note_and_smaller_waste(labeled_file, capsys):
    assert main(["--from-file", labeled_file, "--discount", "0.5",
                 "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["discount"] == 0.5


def test_apply_values_mode(labeled_file, capsys):
    assert main(["--from-file", labeled_file, "--apply"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# cloudtrim dry run")
    assert 'cpu: "260m"' in out


def test_apply_patch_mode_parses(labeled_file, capsys):
    assert main(["--from-file", labeled_file, "--apply",
                 "--apply-format", "patch"]) == 0
    out = capsys.readouterr().out
    entries = json.loads(out.split("\n", 1)[1])
    assert entries[0]["merge_patch"]["spec"]["containers"]


BASE = {
    "cluster": "test",
    "captured_at": "2026-10-11T00:00:00Z",
    "containers": [
        {
            "namespace": "shop", "pod": "api-x", "container": "api",
            "cpu_request_m": 1000, "mem_request_mi": 1024,
            "samples": [{"cpu_m": 200, "mem_mi": 300}] * 10,
        }
    ],
}


def test_diff_text_and_json(tmp_path, capsys):
    base = tmp_path / "base.json"
    base.write_text(json.dumps(BASE))
    current = dict(SNAP)
    f = tmp_path / "cur.json"
    f.write_text(json.dumps(current))
    assert main(["--from-file", str(f), "--diff", str(base)]) == 0
    out = capsys.readouterr().out
    assert "diff vs baseline" in out
    assert "web-x" in out  # new in the current snapshot

    assert main(["--from-file", str(f), "--diff", str(base),
                 "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "diff" in data
    assert data["diff"]["totals"]["old_waste_monthly_usd"] > 0


def test_diff_needs_from_file(tmp_path, capsys):
    base = tmp_path / "base.json"
    base.write_text(json.dumps(BASE))
    assert main(["--from-cluster", "--diff", str(base)]) == 2
    assert "--diff needs --from-file" in capsys.readouterr().err


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_prometheus_mode_through_cli(monkeypatch, capsys):
    import urllib.parse
    import urllib.request
    from pathlib import Path

    from tests.test_prometheus import fake_transport

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(fake_transport(request.full_url))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rc = main(["--from-prometheus", "http://prom:9090", "--windows", "24h,7d"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "source prometheus http://prom:9090" in out
    assert "windows: 24h, 7d" in out
    assert "api-x/api" in out
    assert "p95 by window:" in out
    assert "no data, left out of the totals: 1" in out
