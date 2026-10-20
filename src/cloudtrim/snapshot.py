"""Load and validate a usage snapshot.

A snapshot is one JSON file: the cluster name, when it was captured, and a
list of containers with what they requested and their usage samples.
cloudtrim ships three ways to build one (a file you already have, a live
kubectl read, a Prometheus history) but everything downstream only ever
sees this shape.

A container's usage can arrive as several labeled windows ("24h", "7d",
...). File and kubectl snapshots carry a single flat sample list, which
the rest of the code reads as one window labeled "snapshot" (or by its
time span, when the samples carry timestamps).
"""

import json
from dataclasses import dataclass, field


class SnapshotError(ValueError):
    pass


@dataclass
class Sample:
    cpu_m: float
    mem_mi: float
    t: float | None = None  # epoch seconds, when the source provides it


@dataclass
class UsageWindow:
    label: str
    samples: list = field(default_factory=list)
    span_seconds: float | None = None  # time the samples cover, when known


@dataclass
class Container:
    namespace: str
    pod: str
    container: str
    cpu_request_m: float
    mem_request_mi: float
    cpu_limit_m: float | None = None
    mem_limit_mi: float | None = None
    samples: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    windows: list = field(default_factory=list)

    def usage_windows(self):
        """All usage windows for this container, oldest data first."""
        if self.windows:
            return self.windows
        span = None
        stamps = [s.t for s in self.samples if s.t is not None]
        if len(stamps) >= 2:
            span = max(stamps) - min(stamps)
        return [UsageWindow("snapshot", list(self.samples), span)]


@dataclass
class Snapshot:
    cluster: str
    captured_at: str
    containers: list = field(default_factory=list)
    source: str = "file"


def _num(entry, key, where, required=True):
    if key not in entry or entry[key] is None:
        if required:
            raise SnapshotError(f"{where}: missing '{key}'")
        return None
    value = entry[key]
    # bool is an int subclass; a JSON true here is a data bug, not a number
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SnapshotError(f"{where}: '{key}' must be a number")
    return float(value)


def _parse_container(entry, index):
    where = f"containers[{index}]"
    if not isinstance(entry, dict):
        raise SnapshotError(f"{where}: expected an object")
    for key in ("namespace", "pod", "container"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise SnapshotError(f"{where}: '{key}' must be a non-empty string")
    raw_samples = entry.get("samples", [])
    if not isinstance(raw_samples, list):
        raise SnapshotError(f"{where}: 'samples' must be a list")
    samples = []
    for j, raw in enumerate(raw_samples):
        swhere = f"{where}.samples[{j}]"
        if not isinstance(raw, dict):
            raise SnapshotError(f"{swhere}: expected an object")
        samples.append(
            Sample(
                cpu_m=_num(raw, "cpu_m", swhere),
                mem_mi=_num(raw, "mem_mi", swhere),
                t=_num(raw, "t", swhere, required=False),
            )
        )
    labels = entry.get("labels", {})
    if not isinstance(labels, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in labels.items()
    ):
        raise SnapshotError(f"{where}: 'labels' must be an object of strings")
    return Container(
        namespace=entry["namespace"],
        pod=entry["pod"],
        container=entry["container"],
        cpu_request_m=_num(entry, "cpu_request_m", where),
        mem_request_mi=_num(entry, "mem_request_mi", where),
        cpu_limit_m=_num(entry, "cpu_limit_m", where, required=False),
        mem_limit_mi=_num(entry, "mem_limit_mi", where, required=False),
        samples=samples,
        labels=dict(labels),
    )


def loads(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SnapshotError("snapshot must be a JSON object")
    if not isinstance(data.get("containers"), list):
        raise SnapshotError("snapshot needs a 'containers' list")
    return Snapshot(
        cluster=str(data.get("cluster", "unknown")),
        captured_at=str(data.get("captured_at", "")),
        containers=[_parse_container(e, i) for i, e in enumerate(data["containers"])],
    )


def load(path):
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read())


def apply_filters(containers, namespaces=None, selectors=None):
    """Keep only containers matching every filter the caller asked for.

    namespaces is a list of names; selectors a list of (key, value) pairs
    matched against each container's labels. No filters, no change.
    """
    out = list(containers)
    if namespaces:
        keep = set(namespaces)
        out = [c for c in out if c.namespace in keep]
    for key, value in selectors or []:
        out = [c for c in out if c.labels.get(key) == value]
    return out


def from_cluster():
    """Build a snapshot from a live cluster through kubectl.

    Requests and limits come from the pod spec, usage from metrics-server.
    That gives exactly one sample per container: right now, not a history.
    """
    import shutil
    import subprocess
    from datetime import datetime, timezone

    if shutil.which("kubectl") is None:
        raise SnapshotError(
            "kubectl not found on PATH; install it or use --from-file with a snapshot"
        )

    def run(args):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SnapshotError(
                f"{' '.join(args)} failed: {proc.stderr.strip() or 'no error output'}"
            )
        return proc.stdout

    pods = json.loads(run(["kubectl", "get", "pods", "-A", "-o", "json"]))
    top = run(["kubectl", "top", "pods", "-A", "--containers"])

    usage = {}
    for line in top.splitlines()[1:]:
        parts = line.split()
        if len(parts) != 5:
            continue
        namespace, pod, container, cpu, mem = parts
        usage[(namespace, pod, container)] = (
            _parse_cpu(cpu),
            _parse_mem(mem),
        )

    containers = []
    for item in pods.get("items", []):
        meta = item.get("metadata", {})
        namespace, pod = meta.get("namespace", ""), meta.get("name", "")
        pod_labels = {
            str(k): str(v)
            for k, v in (meta.get("labels") or {}).items()
        }
        for spec in item.get("spec", {}).get("containers", []):
            name = spec.get("name", "")
            res = spec.get("resources", {})
            requests = res.get("requests", {})
            limits = res.get("limits", {})
            if "cpu" not in requests and "memory" not in requests:
                continue
            samples = []
            if (namespace, pod, name) in usage:
                cpu_m, mem_mi = usage[(namespace, pod, name)]
                samples.append(Sample(cpu_m=cpu_m, mem_mi=mem_mi))
            containers.append(
                Container(
                    namespace=namespace,
                    pod=pod,
                    container=name,
                    cpu_request_m=_parse_cpu(requests.get("cpu", "0")),
                    mem_request_mi=_parse_mem(requests.get("memory", "0")),
                    cpu_limit_m=_parse_cpu(limits["cpu"]) if "cpu" in limits else None,
                    mem_limit_mi=_parse_mem(limits["memory"]) if "memory" in limits else None,
                    samples=samples,
                    labels=pod_labels,
                )
            )
    return Snapshot(
        cluster="live (kubectl)",
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        containers=containers,
        source="kubectl",
    )


def _parse_cpu(text):
    # kubectl prints millicores as "250m" and whole cores as "2"
    text = str(text).strip()
    if text.endswith("m"):
        return float(text[:-1])
    return float(text) * 1000


_MEM_UNITS = {"Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0, "Ti": 1024.0 * 1024}


def _parse_mem(text):
    text = str(text).strip()
    for unit, factor in _MEM_UNITS.items():
        if text.endswith(unit):
            return float(text[: -len(unit)]) * factor
    if text.endswith("k"):
        return float(text[:-1]) * 1000 / (1024 * 1024)
    # plain bytes
    return float(text) / (1024 * 1024)
