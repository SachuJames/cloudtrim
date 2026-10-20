import json

from cloudtrim.analyze import analyze
from cloudtrim.pricing import make_pricing
from cloudtrim.report import markdown, text, to_json
from cloudtrim.snapshot import loads


def _report(containers, **pricing_kw):
    snap = loads(json.dumps({"cluster": "t", "containers": containers}))
    return analyze(snap, make_pricing("us-east-1", **pricing_kw))


def _c(ns, pod, samples, cpu=1000, mem=1024, **extra):
    base = {
        "namespace": ns, "pod": pod, "container": pod.split("-")[0],
        "cpu_request_m": cpu, "mem_request_mi": mem,
        "samples": [{"cpu_m": c, "mem_mi": m} for c, m in samples],
    }
    base.update(extra)
    return base


def test_text_groups_by_namespace_with_markers():
    report = _report(
        [
            _c("shop", "api-x", [(200, 300)] * 10),
            _c("batch", "job-x", [(900, 900)] * 10),
        ]
    )
    out = text(report)
    assert "\nshop\n" in out
    assert "\nbatch\n" in out
    over_line = [ln for ln in out.splitlines() if "api-x" in ln][0]
    assert over_line.startswith(">>")
    assert "subtotal waste" in out
    assert "suggested limits:" in out


def test_text_header_shows_data_quality():
    report = _report([_c("shop", "api-x", [(200, 300)] * 10)])
    first = text(report).splitlines()[0]
    assert "source file" in first
    assert "10 samples" in first
    assert "windows: snapshot" in first


def test_no_data_section_and_summary_count():
    report = _report(
        [
            _c("shop", "api-x", [(200, 300)] * 10),
            _c("shop", "ghost-x", [], cpu=4000, mem=8192),
        ]
    )
    out = text(report)
    assert "no data, left out of the totals: 1" in out
    assert "1 no-data excluded" in out
    # the ghost is not inside the shop group
    shop_block = out.split("\nshop\n")[1].split("subtotal")[0]
    assert "ghost-x" not in shop_block


def test_packing_note_present_when_waste():
    report = _report([_c("shop", "api-x", [(200, 300)] * 10)])
    assert any("nodes under them" in n for n in report.notes)
    data = json.loads(to_json(report))
    assert data["notes"]


def test_json_v2_shape():
    report = _report(
        [
            _c("shop", "api-x", [(200, 300)] * 10, labels={"app": "api"}),
            _c("batch", "job-x", [(900, 900)] * 10),
        ]
    )
    data = json.loads(to_json(report))
    assert data["version"] == "2"
    assert data["summary"]["container_count"] == 2  # old totals still there
    assert data["namespaces"]["shop"]["containers"] == 1
    assert data["namespaces"]["batch"]["waste_monthly_usd"] == 0
    entry = data["containers"][0]
    assert entry["labels"] == {"app": "api"}
    assert entry["cpu"]["suggested_limit_m"] == 390
    assert entry["mem"]["suggested_limit_mi"] == 608
    assert entry["windows"]["snapshot"]["cpu_p95_m"] == 200
    assert entry["samples"] == 10


def test_markdown_has_limit_columns():
    report = _report([_c("shop", "api-x", [(200, 300)] * 10)])
    out = markdown(report)
    assert "| cpu limit |" in out
    assert "390m" in out


def test_discount_shows_in_pricing_lines():
    report = _report([_c("shop", "api-x", [(200, 300)] * 10)], discount=0.6)
    assert "discount factor 0.6 applied" in text(report)
    data = json.loads(to_json(report))
    assert data["discount"] == 0.6
    plain = _report([_c("shop", "api-x", [(200, 300)] * 10)])
    assert "discount factor" not in text(plain)


def test_color_codes_only_when_asked():
    report = _report([_c("shop", "api-x", [(200, 300)] * 10)])
    assert "\033[" not in text(report)
    assert "\033[31m" in text(report, color=True)
