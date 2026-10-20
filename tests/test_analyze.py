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
