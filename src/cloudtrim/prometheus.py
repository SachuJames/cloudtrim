"""Read usage history from Prometheus.

Two range queries per window (CPU rate, memory working set) plus one
instant query for the requests, which live in kube-state-metrics. No
kube-state-metrics, no requests, and without requests there is nothing
to judge usage against, so that case is a plain error, not a guess.

The HTTP layer is one function that takes a URL and returns parsed JSON.
Tests pass in a fake one fed by recorded responses, so the whole client
is exercisable without a server.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from cloudtrim.snapshot import (
    Container,
    Sample,
    Snapshot,
    SnapshotError,
    UsageWindow,
)

CPU_QUERY = (
    "sum by (namespace, pod, container) "
    '(rate(container_cpu_usage_seconds_total{container!=""}[5m])) * 1000'
)
MEM_QUERY = (
    "sum by (namespace, pod, container) "
    '(container_memory_working_set_bytes{container!=""}) / 1048576'
)
REQUEST_CPU_QUERY = 'kube_pod_container_resource_requests{resource="cpu"}'
REQUEST_MEM_QUERY = 'kube_pod_container_resource_requests{resource="memory"}'

# labels that describe the series itself, not the workload
_DROP_LABELS = {"namespace", "pod", "container", "resource", "unit"}

_WINDOW_RE = re.compile(r"^(\d+)([mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def parse_window(spec):
    """'24h' -> ('24h', 86400). Raises SnapshotError on anything else."""
    match = _WINDOW_RE.match(spec.strip())
    if not match:
        raise SnapshotError(
            f"bad window '{spec}': use forms like 30m, 24h, 7d"
        )
    return spec.strip(), int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def step_for(seconds):
    # fine steps for short windows, coarser for long ones: range queries
    # get expensive, and a 30m step still shows the shape of a week
    return 300 if seconds <= 48 * 3600 else 1800


def http_get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "cloudtrim"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise SnapshotError(f"prometheus request failed: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SnapshotError(f"prometheus sent back non-JSON: {exc}") from exc


def _call(transport, base, path, params):
    url = base.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    payload = transport(url)
    if not isinstance(payload, dict) or payload.get("status") != "success":
        detail = ""
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("message") or ""
        raise SnapshotError(f"prometheus query failed: {detail or 'unknown error'}")
    return payload.get("data") or {}


def _key(metric):
    return (metric.get("namespace", ""), metric.get("pod", ""), metric.get("container", ""))


def _labels(metric):
    return {k: v for k, v in metric.items() if k not in _DROP_LABELS}


def _instant_requests(transport, base, query):
    """{(ns, pod, container): (value, labels)} from an instant query."""
    data = _call(transport, base, "/api/v1/query", {"query": query})
    out = {}
    for series in data.get("result", []):
        metric = series.get("metric", {})
        value = series.get("value") or [0, "0"]
        try:
            out[_key(metric)] = (float(value[1]), _labels(metric))
        except (TypeError, ValueError):
            continue
    return out


def _range_series(transport, base, query, start, end, step):
    """{(ns, pod, container): (labels, {ts: value})} from a range query."""
    data = _call(
        transport,
        base,
        "/api/v1/query_range",
        {"query": query, "start": start, "end": end, "step": step},
    )
    out = {}
    for series in data.get("result", []):
        points = {}
        for ts, raw in series.get("values", []):
            try:
                points[float(ts)] = float(raw)
            except (TypeError, ValueError):
                continue
        out[_key(series.get("metric", {}))] = (_labels(series.get("metric", {})), points)
    return out


def from_prometheus(base_url, windows=("24h", "7d"), transport=None, now=None):
    """Build a snapshot from a Prometheus base URL.

    transport is a callable taking a full URL and returning the parsed
    JSON body; the default does a real HTTP GET.
    """
    transport = transport or http_get
    now = time.time() if now is None else now

    cpu_requests = _instant_requests(transport, base_url, REQUEST_CPU_QUERY)
    mem_requests = _instant_requests(transport, base_url, REQUEST_MEM_QUERY)
    if not cpu_requests and not mem_requests:
        raise SnapshotError(
            "no container requests found in prometheus; "
            "kube-state-metrics may not be installed"
        )

    window_objs = {}
    labels_by_key = {}
    for key, (_, labels) in list(cpu_requests.items()) + list(mem_requests.items()):
        labels_by_key.setdefault(key, labels)

    parsed_windows = [parse_window(w) for w in windows]
    for label, seconds in parsed_windows:
        step = step_for(seconds)
        start, end = now - seconds, now
        cpu_series = _range_series(transport, base_url, CPU_QUERY, start, end, step)
        mem_series = _range_series(transport, base_url, MEM_QUERY, start, end, step)
        keys = set(cpu_series) | set(mem_series)
        per_key = {}
        for key in keys:
            for source in (cpu_series, mem_series):
                if key in source:
                    labels_by_key.setdefault(key, source[key][0])
            cpu_points = cpu_series.get(key, ({}, {}))[1]
            mem_points = mem_series.get(key, ({}, {}))[1]
            stamps = sorted(set(cpu_points) | set(mem_points))
            samples = [
                Sample(
                    cpu_m=cpu_points.get(ts, 0.0),
                    mem_mi=mem_points.get(ts, 0.0),
                    t=ts,
                )
                for ts in stamps
            ]
            per_key[key] = UsageWindow(label, samples, float(seconds))
        window_objs[label] = per_key

    all_keys = set(cpu_requests) | set(mem_requests)
    for per_key in window_objs.values():
        all_keys |= set(per_key)

    containers = []
    for key in sorted(all_keys):
        namespace, pod, container = key
        containers.append(
            Container(
                namespace=namespace,
                pod=pod,
                container=container,
                cpu_request_m=cpu_requests.get(key, (0.0, {}))[0] * 1000,
                mem_request_mi=mem_requests.get(key, (0.0, {}))[0] / 1048576,
                labels=labels_by_key.get(key, {}),
                windows=[
                    window_objs[label].get(
                        key, UsageWindow(label, [], float(seconds))
                    )
                    for label, seconds in parsed_windows
                ],
            )
        )
    return Snapshot(
        cluster=base_url,
        captured_at=datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"
        ),
        containers=containers,
        source="prometheus",
    )
