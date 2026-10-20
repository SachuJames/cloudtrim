import pytest

from cloudtrim import prometheus
from cloudtrim.analyze import analyze
from cloudtrim.pricing import make_pricing
from cloudtrim.snapshot import SnapshotError
from promfake import empty_requests_transport, fake_transport

NOW = 1791859200.0


@pytest.fixture
def snap():
    return prometheus.from_prometheus(
        "http://prom:9090", transport=fake_transport, now=NOW
    )


def _by_name(snap):
    return {c.container: c for c in snap.containers}


def test_series_joined_into_containers(snap):
    by_name = _by_name(snap)
    assert set(by_name) == {"api", "web", "idle", "job"}
    api = by_name["api"]
    assert api.cpu_request_m == 1000  # 1 core in the requests series
    assert api.mem_request_mi == 1024  # bytes converted to MiB
    assert api.labels.get("app") == "api"
    assert snap.source == "prometheus"


def test_window_p95s_computed(snap):
    api = _by_name(snap)["api"]
    windows = {w.label: w for w in api.usage_windows()}
    assert set(windows) == {"24h", "7d"}
    assert len(windows["24h"].samples) == 3
    cpu_24h = sorted(s.cpu_m for s in windows["24h"].samples)
    assert cpu_24h == [200.0, 210.0, 220.0]
    report = analyze(snap, make_pricing("us-east-1"))
    entry = [c for c in report.containers if c.container == "api"][0]
    by_label = {w.label: w for w in entry.windows}
    assert by_label["24h"].cpu.p95 == 220
    assert by_label["7d"].cpu.p95 == 200
    assert entry.main.label == "7d"  # long window decides
    assert entry.verdict == "over"  # 1000m requested against a 200m p95


def test_bursty_flagged_from_windows(snap):
    report = analyze(snap, make_pricing("us-east-1"))
    web = [c for c in report.containers if c.container == "web"][0]
    # 24h p95 420m against a 7d p95 of 100m
    assert web.bursty is True


def test_requests_without_usage_is_no_data(snap):
    report = analyze(snap, make_pricing("us-east-1"))
    idle = [c for c in report.containers if c.container == "idle"][0]
    assert idle.verdict == "no-data"
    assert report.no_data_count == 1
    assert report.cpu_request_m == 1500  # api 1000 + web 500, idle left out


def test_usage_without_requests_is_no_request(snap):
    report = analyze(snap, make_pricing("us-east-1"))
    job = [c for c in report.containers if c.container == "job"][0]
    assert job.cpu.request == 0
    assert job.verdict == "no-request"


def test_missing_requests_series_is_a_plain_error():
    with pytest.raises(SnapshotError, match="kube-state-metrics"):
        prometheus.from_prometheus(
            "http://prom:9090", transport=empty_requests_transport, now=NOW
        )


def test_parse_window():
    assert prometheus.parse_window("24h") == ("24h", 86400)
    assert prometheus.parse_window("7d") == ("7d", 604800)
    assert prometheus.parse_window("30m") == ("30m", 1800)
    with pytest.raises(SnapshotError):
        prometheus.parse_window("a while")


def test_single_window_fetch(snap):
    one = prometheus.from_prometheus(
        "http://prom:9090", windows=("7d",), transport=fake_transport, now=NOW
    )
    api = _by_name(one)["api"]
    assert [w.label for w in api.usage_windows()] == ["7d"]
