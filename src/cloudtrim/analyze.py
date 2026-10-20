"""The right-sizing math.

Each resource (CPU, memory) gets its own verdict, because a container can
be wasting CPU and starved on memory at the same time:

  over    the request is more than over_factor times the p95 usage
  tight   p95 usage is above tight_ratio of the request
  ok      everything else

The suggested request is p95 with headroom on top, rounded up to a step a
human would actually write in a manifest. The suggested limit is the
suggested request times limit_factor.

Usage can arrive as several windows (Prometheus gives a short and a long
one). The window covering the most time decides the verdict; the others
are kept for context, and a short window running far hotter than the
long one marks the container bursty.
"""

import math
from dataclasses import dataclass, field

OVER_FACTOR = 2.0
TIGHT_RATIO = 0.85
HEADROOM = 1.3
LIMIT_FACTOR = 1.5
BURST_FACTOR = 1.5

CPU_FLOOR_M = 10
MEM_FLOOR_MI = 16
CPU_STEP_M = 10
MEM_STEP_MI = 16


@dataclass(frozen=True)
class Config:
    headroom: float = HEADROOM
    over_factor: float = OVER_FACTOR
    tight_ratio: float = TIGHT_RATIO
    limit_factor: float = LIMIT_FACTOR


DEFAULT_CONFIG = Config()


def percentile(values, pct):
    """Nearest-rank percentile. pct is 0 to 100."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(pct / 100 * len(ordered))
    return ordered[max(0, min(rank, len(ordered)) - 1)]


def _round_up(value, step, floor):
    return max(floor, math.ceil(value / step) * step)


@dataclass
class ResourceVerdict:
    request: float
    p95: float
    peak: float
    verdict: str
    suggested: float
    suggested_limit: float
    waste: float  # request minus suggested, only when over; 0 otherwise


def judge(request, samples, step, floor, cfg=DEFAULT_CONFIG):
    if not samples:
        return ResourceVerdict(
            request, 0.0, 0.0, "no-data", request,
            _round_up(request * cfg.limit_factor, step, floor), 0.0,
        )
    p95 = percentile(samples, 95)
    peak = max(samples)
    if request <= 0:
        return ResourceVerdict(
            request, p95, peak, "no-request", request,
            _round_up(request * cfg.limit_factor, step, floor), 0.0,
        )
    if p95 > 0 and request > cfg.over_factor * p95:
        suggested = _round_up(p95 * cfg.headroom, step, floor)
        suggested = min(suggested, request)
        return ResourceVerdict(
            request, p95, peak, "over", suggested,
            _round_up(suggested * cfg.limit_factor, step, floor),
            request - suggested,
        )
    if p95 >= cfg.tight_ratio * request:
        suggested = _round_up(p95 * cfg.headroom, step, floor)
        return ResourceVerdict(
            request, p95, peak, "tight", suggested,
            _round_up(suggested * cfg.limit_factor, step, floor), 0.0,
        )
    return ResourceVerdict(
        request, p95, peak, "ok", request,
        _round_up(request * cfg.limit_factor, step, floor), 0.0,
    )


@dataclass
class WindowUsage:
    label: str
    samples: int
    span_seconds: float | None
    cpu: ResourceVerdict
    mem: ResourceVerdict


@dataclass
class ContainerReport:
    namespace: str
    pod: str
    container: str
    labels: dict
    windows: list
    primary: int  # index into windows of the window that decided the verdict
    bursty: bool = False
    waste_cost_monthly: float = 0.0
    growth_cost_monthly: float = 0.0  # extra cost if tight suggestions are applied

    @property
    def main(self):
        return self.windows[self.primary]

    @property
    def cpu(self):
        return self.main.cpu

    @property
    def mem(self):
        return self.main.mem

    @property
    def samples(self):
        return self.main.samples

    @property
    def verdict(self):
        parts = {self.cpu.verdict, self.mem.verdict}
        for name in ("over", "tight", "no-data", "no-request"):
            if name in parts:
                return name
        return "ok"


@dataclass
class ClusterReport:
    cluster: str
    captured_at: str
    source: str
    pricing_region: str
    pricing_as_of: str
    discount: float = 1.0
    containers: list = field(default_factory=list)
    cpu_request_m: float = 0.0
    cpu_suggested_m: float = 0.0
    mem_request_mi: float = 0.0
    mem_suggested_mi: float = 0.0
    waste_cost_monthly: float = 0.0
    growth_cost_monthly: float = 0.0
    current_cost_monthly: float = 0.0
    no_data_count: int = 0  # containers left out of the totals above
    total_samples: int = 0  # samples across every window of every container
    span_seconds: float | None = None  # longest window span seen

    @property
    def waste_fraction(self):
        """Waste cost as a share of the current requested cost."""
        if self.current_cost_monthly <= 0:
            return 0.0
        return self.waste_cost_monthly / self.current_cost_monthly

    @property
    def window_labels(self):
        labels = []
        for c in self.containers:
            for w in c.windows:
                if w.label not in labels:
                    labels.append(w.label)
        return labels

    @property
    def notes(self):
        out = []
        if self.waste_cost_monthly > 0:
            out.append(
                "rightsized pods do not shrink the nodes under them; "
                "unallocated node capacity is a separate cost"
            )
        return out


def _pick_primary(windows):
    """The window covering the most time decides. Ties go to the later one."""
    best = 0
    for i, w in enumerate(windows):
        if (w.span_seconds or 0) >= (windows[best].span_seconds or 0):
            best = i
    return best


def _is_bursty(windows, primary):
    if len(windows) < 2:
        return False
    long = windows[primary]
    short = min(windows, key=lambda w: w.span_seconds or 0)
    if short is long:
        return False
    for a, b in ((short.cpu, long.cpu), (short.mem, long.mem)):
        if b.p95 > 0 and a.p95 > BURST_FACTOR * b.p95:
            return True
    return False


def analyze(snapshot, pricing, config=None):
    cfg = config or DEFAULT_CONFIG
    report = ClusterReport(
        cluster=snapshot.cluster,
        captured_at=snapshot.captured_at,
        source=snapshot.source,
        pricing_region=pricing.region,
        pricing_as_of=pricing.as_of,
        discount=pricing.discount,
    )
    for c in snapshot.containers:
        windows = []
        for w in c.usage_windows():
            windows.append(
                WindowUsage(
                    label=w.label,
                    samples=len(w.samples),
                    span_seconds=w.span_seconds,
                    cpu=judge(
                        c.cpu_request_m,
                        [s.cpu_m for s in w.samples],
                        CPU_STEP_M,
                        CPU_FLOOR_M,
                        cfg,
                    ),
                    mem=judge(
                        c.mem_request_mi,
                        [s.mem_mi for s in w.samples],
                        MEM_STEP_MI,
                        MEM_FLOOR_MI,
                        cfg,
                    ),
                )
            )
            report.total_samples += len(w.samples)
            if w.span_seconds is not None and (
                report.span_seconds is None or w.span_seconds > report.span_seconds
            ):
                report.span_seconds = w.span_seconds
        primary = _pick_primary(windows)
        entry = ContainerReport(
            namespace=c.namespace,
            pod=c.pod,
            container=c.container,
            labels=dict(c.labels),
            windows=windows,
            primary=primary,
            bursty=_is_bursty(windows, primary),
        )
        report.containers.append(entry)
        if entry.samples == 0:
            # nothing measured, nothing claimed: out of every total
            report.no_data_count += 1
            continue
        cpu, mem = entry.cpu, entry.mem
        entry.waste_cost_monthly = pricing.monthly(cpu.waste, mem.waste)
        entry.growth_cost_monthly = pricing.monthly(
            max(0.0, cpu.suggested - cpu.request) if cpu.verdict == "tight" else 0.0,
            max(0.0, mem.suggested - mem.request) if mem.verdict == "tight" else 0.0,
        )
        report.cpu_request_m += c.cpu_request_m
        report.cpu_suggested_m += cpu.suggested
        report.mem_request_mi += c.mem_request_mi
        report.mem_suggested_mi += mem.suggested
        report.waste_cost_monthly += entry.waste_cost_monthly
        report.growth_cost_monthly += entry.growth_cost_monthly
    report.current_cost_monthly = pricing.monthly(
        report.cpu_request_m, report.mem_request_mi
    )
    report.containers.sort(
        key=lambda e: (e.waste_cost_monthly, e.cpu.waste + e.mem.waste), reverse=True
    )
    return report
