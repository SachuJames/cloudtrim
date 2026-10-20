import json

from cloudtrim.analyze import analyze
from cloudtrim.apply import render
from cloudtrim.pricing import make_pricing
from cloudtrim.snapshot import loads


def _report():
    snap = loads(
        json.dumps(
            {
                "cluster": "t",
                "containers": [
                    {
                        "namespace": "shop", "pod": "api-x", "container": "api",
                        "cpu_request_m": 1000, "mem_request_mi": 1024,
                        "cpu_limit_m": 2000, "mem_limit_mi": 2048,
                        "samples": [{"cpu_m": 200, "mem_mi": 300}] * 10,
                    },
                    {
                        "namespace": "shop", "pod": "api-x", "container": "sidecar",
                        "cpu_request_m": 100, "mem_request_mi": 128,
                        "samples": [{"cpu_m": 60, "mem_mi": 70}] * 10,
                    },
                    {
                        "namespace": "shop", "pod": "web-x", "container": "web",
                        "cpu_request_m": 200, "mem_request_mi": 256,
                        "samples": [{"cpu_m": 100, "mem_mi": 130}] * 10,
                    },
                ],
            }
        )
    )
    return analyze(snap, make_pricing("us-east-1"))


def test_values_groups_and_suggests():
    out = render(_report(), "values")
    assert out.startswith("# cloudtrim dry run")
    assert "shop:" in out
    assert "api-x:" in out
    assert 'cpu: "260m"' in out  # api suggestion, p95 200 times headroom
    assert 'cpu: "390m"' in out  # its suggested limit, 1.5x on a 10m step
    # web is fine at its current size, so it gets no snippet
    assert "web-x" not in out


def test_kustomize_shape():
    out = render(_report(), "kustomize")
    assert "kind: Kustomization" in out
    assert "cloudtrim-patch.yaml" in out
    assert "name: api-x" in out
    assert "hash suffix" in out


def test_patch_is_merge_patch_per_pod():
    out = render(_report(), "patch")
    entries = json.loads(out.split("\n", 1)[1])
    assert len(entries) == 1  # only api-x has a flagged container
    entry = entries[0]
    assert entry["namespace"] == "shop" and entry["pod"] == "api-x"
    containers = entry["merge_patch"]["spec"]["containers"]
    # merge patch replaces the list, so the unflagged sidecar rides along
    # with the values it already has
    by_name = {c["name"]: c["resources"] for c in containers}
    assert by_name["api"]["requests"] == {"cpu": "260m", "memory": "400Mi"}
    assert by_name["sidecar"]["requests"] == {"cpu": "100m", "memory": "128Mi"}
