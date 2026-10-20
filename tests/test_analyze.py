import pytest

from cloudtrim.analyze import judge, percentile


def test_percentile_nearest_rank():
    vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(vals, 95) == 100
    assert percentile(vals, 50) == 50
    assert percentile(vals, 0) == 10
    assert percentile([7], 95) == 7
    assert percentile([], 95) == 0.0


def test_over_provisioned_cpu():
    v = judge(1000, [200.0] * 10, 10, 10)
    assert v.verdict == "over"
    assert v.suggested == 260  # 200 * 1.3, already on a 10m step
    assert v.waste == 740


def test_over_provisioned_mem():
    v = judge(1024, [300.0] * 10, 16, 16)
    assert v.verdict == "over"
    assert v.suggested == 400  # 390 rounded up to a 16Mi step
    assert v.waste == 624


def test_tight():
    v = judge(250, [228.0] * 10, 10, 10)
    assert v.verdict == "tight"
    assert v.waste == 0.0
    assert v.suggested == 300  # raise to p95 * 1.3
    assert v.suggested > v.request


def test_ok():
    v = judge(500, [300.0] * 10, 10, 10)
    assert v.verdict == "ok"
    assert v.suggested == 500
    assert v.waste == 0.0


def test_no_samples():
    v = judge(500, [], 10, 10)
    assert v.verdict == "no-data"
    assert v.suggested == 500


def test_single_sample():
    v = judge(1000, [100.0], 10, 10)
    assert v.p95 == 100.0
    assert v.verdict == "over"


def test_suggestion_floors():
    v = judge(100, [3.0] * 5, 10, 10)
    assert v.verdict == "over"
    assert v.suggested == 10  # CPU floor

    v = judge(64, [4.0] * 5, 16, 16)
    assert v.suggested == 16  # memory floor


def test_suggestion_stays_below_request_when_over():
    v = judge(1000, [197.0] * 10, 10, 10)
    assert v.verdict == "over"
    assert v.suggested == 260  # 197 * 1.3 = 256.1, rounded up to a 10m step
    assert v.suggested < v.request


from cloudtrim.analyze import Config, analyze
from cloudtrim.pricing import make_pricing
from cloudtrim.snapshot import loads
import json


def test_config_changes_the_verdict():
    samples = [400.0] * 10
    # 1000 is 2.5x the p95: over at the default factor, fine at 3.0
    assert judge(1000, samples, 10, 10).verdict == "over"
    assert judge(1000, samples, 10, 10, Config(over_factor=3.0)).verdict == "ok"
    # a bigger headroom raises the suggestion
    v = judge(1000, samples, 10, 10, Config(headroom=2.0))
    assert v.suggested == 800


def test_tight_ratio_is_configurable():
    samples = [800.0] * 10
    assert judge(1000, samples, 10, 10).verdict == "ok"
    assert judge(1000, samples, 10, 10, Config(tight_ratio=0.7)).verdict == "tight"


def test_suggested_limit_is_one_and_a_half_times():
    v = judge(1000, [200.0] * 10, 10, 10)
    assert v.suggested == 260
    assert v.suggested_limit == 390  # 260 * 1.5, on a 10m step
    v = judge(1024, [300.0] * 10, 16, 16)
    assert v.suggested == 400
    assert v.suggested_limit == 608  # 600 rounded up to a 16Mi step


def _snapshot(containers):
    return loads(json.dumps({"cluster": "t", "containers": containers}))


def _container(samples, cpu=1000, mem=1024, **extra):
    base = {
        "namespace": "shop",
        "pod": "api-x",
        "container": "api",
        "cpu_request_m": cpu,
        "mem_request_mi": mem,
        "samples": [{"cpu_m": c, "mem_mi": m} for c, m in samples],
    }
    base.update(extra)
    return base


def test_no_data_containers_are_left_out_of_totals():
    snap = _snapshot(
        [
            _container([(200, 300)] * 10),
            _container([], cpu=4000, mem=8192, pod="ghost-x", container="ghost"),
        ]
    )
    report = analyze(snap, make_pricing("us-east-1"))
    assert report.no_data_count == 1
    assert report.cpu_request_m == 1000  # the ghost's 4000m is not in here
    assert report.mem_request_mi == 1024
    ghost = [c for c in report.containers if c.container == "ghost"][0]
    assert ghost.verdict == "no-data"
    assert ghost.waste_cost_monthly == 0.0


def test_multi_window_primary_and_bursty():
    from cloudtrim.snapshot import Container, Sample, Snapshot, UsageWindow

    long_samples = [Sample(cpu_m=100.0, mem_mi=200.0)] * 20
    hot_samples = [Sample(cpu_m=400.0, mem_mi=210.0)] * 20
    c = Container(
        namespace="shop", pod="api-x", container="api",
        cpu_request_m=500, mem_request_mi=512,
        windows=[
            UsageWindow("24h", hot_samples, 86400),
            UsageWindow("7d", long_samples, 604800),
        ],
    )
    snap = Snapshot(cluster="t", captured_at="", containers=[c], source="prometheus")
    report = analyze(snap, make_pricing("us-east-1"))
    entry = report.containers[0]
    assert entry.main.label == "7d"  # the long window decides
    assert entry.cpu.p95 == 100.0
    by_label = {w.label: w for w in entry.windows}
    assert by_label["24h"].cpu.p95 == 400.0
    assert entry.bursty is True  # 24h p95 is 4x the 7d one
    assert report.total_samples == 40
    assert report.span_seconds == 604800
    assert report.window_labels == ["24h", "7d"]


def test_steady_container_is_not_bursty():
    from cloudtrim.snapshot import Container, Sample, Snapshot, UsageWindow

    samples = [Sample(cpu_m=100.0, mem_mi=200.0)] * 20
    c = Container(
        namespace="shop", pod="api-x", container="api",
        cpu_request_m=500, mem_request_mi=512,
        windows=[
            UsageWindow("24h", list(samples), 86400),
            UsageWindow("7d", list(samples), 604800),
        ],
    )
    snap = Snapshot(cluster="t", captured_at="", containers=[c], source="prometheus")
    entry = analyze(snap, make_pricing("us-east-1")).containers[0]
    assert entry.bursty is False
