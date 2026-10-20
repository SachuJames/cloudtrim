import json

import pytest

from cloudtrim import snapshot
from cloudtrim.snapshot import SnapshotError


def test_loads_minimal():
    snap = snapshot.loads(
        json.dumps(
            {
                "cluster": "c1",
                "captured_at": "2026-10-18T00:00:00Z",
                "containers": [
                    {
                        "namespace": "a",
                        "pod": "p",
                        "container": "c",
                        "cpu_request_m": 100,
                        "mem_request_mi": 128,
                        "samples": [{"cpu_m": 10, "mem_mi": 20}],
                    }
                ],
            }
        )
    )
    assert snap.cluster == "c1"
    assert snap.containers[0].samples[0].cpu_m == 10.0
    assert snap.containers[0].cpu_limit_m is None


def test_bad_json():
    with pytest.raises(SnapshotError):
        snapshot.loads("{not json")


def test_missing_containers():
    with pytest.raises(SnapshotError):
        snapshot.loads('{"cluster": "c1"}')


def test_missing_request_names_the_index():
    bad = json.dumps(
        {
            "containers": [
                {
                    "namespace": "a",
                    "pod": "p",
                    "container": "c",
                    "mem_request_mi": 128,
                    "samples": [],
                }
            ]
        }
    )
    with pytest.raises(SnapshotError, match=r"containers\[0\]"):
        snapshot.loads(bad)


def test_non_number_rejected():
    bad = json.dumps(
        {
            "containers": [
                {
                    "namespace": "a",
                    "pod": "p",
                    "container": "c",
                    "cpu_request_m": "lots",
                    "mem_request_mi": 128,
                    "samples": [],
                }
            ]
        }
    )
    with pytest.raises(SnapshotError, match="cpu_request_m"):
        snapshot.loads(bad)


def test_bad_sample_entry():
    bad = json.dumps(
        {
            "containers": [
                {
                    "namespace": "a",
                    "pod": "p",
                    "container": "c",
                    "cpu_request_m": 100,
                    "mem_request_mi": 128,
                    "samples": [{"cpu_m": 10}],
                }
            ]
        }
    )
    with pytest.raises(SnapshotError, match="samples"):
        snapshot.loads(bad)


def test_quantity_parsing():
    assert snapshot._parse_cpu("250m") == 250
    assert snapshot._parse_cpu("2") == 2000
    assert snapshot._parse_mem("512Mi") == 512
    assert snapshot._parse_mem("2Gi") == 2048
    assert snapshot._parse_mem("1024Ki") == 1
    assert snapshot._parse_mem(str(1024 * 1024)) == 1
