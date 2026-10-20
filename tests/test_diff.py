import json

from cloudtrim.analyze import analyze
from cloudtrim.diff import compare, text, to_dict
from cloudtrim.pricing import make_pricing
from cloudtrim.snapshot import loads


def _snap(containers, at="2026-10-18T00:00:00Z"):
    return loads(json.dumps({"cluster": "t", "captured_at": at,
                             "containers": containers}))


def _c(pod, samples, cpu=1000, mem=1024, ns="shop"):
    return {
        "namespace": ns, "pod": pod, "container": pod,
        "cpu_request_m": cpu, "mem_request_mi": mem,
        "samples": [{"cpu_m": c, "mem_mi": m} for c, m in samples],
    }


def _reports():
    pricing = make_pricing("us-east-1")
    baseline = analyze(
        _snap(
            [
                _c("api", [(200, 300)] * 10),          # over, then fixed
                _c("gone", [(100, 100)] * 10),          # removed later
                _c("same", [(600, 600)] * 10, cpu=800),  # never changes
            ],
            at="2026-10-11T00:00:00Z",
        ),
        pricing,
    )
    current = analyze(
        _snap(
            [
                _c("api", [(200, 300)] * 10, cpu=300, mem=400),
                _c("same", [(600, 600)] * 10, cpu=800),
                _c("fresh", [(50, 60)] * 10, cpu=500, mem=512),
            ]
        ),
        pricing,
    )
    return baseline, current


def test_compare_changed_new_gone():
    baseline, current = _reports()
    result = compare(baseline, current)
    by_status = {}
    for change in result.changes:
        by_status.setdefault(change.status, []).append(change.container)
    assert by_status["changed"] == ["api"]
    assert by_status["new"] == ["fresh"]
    assert by_status["gone"] == ["gone"]
    assert result.unchanged == 1
    changed = [c for c in result.changes if c.status == "changed"][0]
    assert changed.old_verdict == "over"
    assert changed.new_verdict == "ok"
    assert changed.old_waste > changed.new_waste


def test_compare_totals_math():
    baseline, current = _reports()
    result = compare(baseline, current)
    assert result.old_waste_monthly == baseline.waste_cost_monthly
    assert result.new_waste_monthly == current.waste_cost_monthly
    assert result.delta == current.waste_cost_monthly - baseline.waste_cost_monthly
    assert result.delta < 0  # the fix saved money
    out = text(result)
    assert "down $" in out
    assert "2026-10-11" in out


def test_compare_to_dict():
    baseline, current = _reports()
    data = to_dict(compare(baseline, current))
    assert data["totals"]["delta_monthly_usd"] < 0
    statuses = {c["container"]: c["status"] for c in data["changes"]}
    assert statuses == {"api": "changed", "gone": "gone", "fresh": "new"}
