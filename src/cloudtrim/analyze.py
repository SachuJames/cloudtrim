"""The right-sizing math.

Each resource (CPU, memory) gets its own verdict, because a container can
be wasting CPU and starved on memory at the same time:

  over    the request is more than twice the p95 usage
  tight   p95 usage is above 85 percent of the request
  ok      everything else

The suggested request is p95 with 30 percent headroom, rounded up to a
step a human would actually write in a manifest.
"""

import math
from dataclasses import dataclass, field

OVER_FACTOR = 2.0
TIGHT_RATIO = 0.85
HEADROOM = 1.3

CPU_FLOOR_M = 10
MEM_FLOOR_MI = 16
CPU_STEP_M = 10
MEM_STEP_MI = 16


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
    waste: float  # request minus suggested, only when over; 0 otherwise


def judge(request, samples, step, floor):
    if not samples:
        return ResourceVerdict(request, 0.0, 0.0, "no-data", request, 0.0)
    p95 = percentile(samples, 95)
    peak = max(samples)
    if request <= 0:
        return ResourceVerdict(request, p95, peak, "no-request", request, 0.0)
    if p95 > 0 and request > OVER_FACTOR * p95:
        suggested = _round_up(p95 * HEADROOM, step, floor)
        suggested = min(suggested, request)
        return ResourceVerdict(request, p95, peak, "over", suggested, request - suggested)
    if p95 >= TIGHT_RATIO * request:
        suggested = _round_up(p95 * HEADROOM, step, floor)
        return ResourceVerdict(request, p95, peak, "tight", suggested, 0.0)
    return ResourceVerdict(request, p95, peak, "ok", request, 0.0)


@dataclass
class ContainerReport:
    namespace: str
    pod: str
    container: str
    samples: int
    cpu: ResourceVerdict
    mem: ResourceVerdict
    waste_cost_monthly: float = 0.0
    growth_cost_monthly: float = 0.0  # extra cost if tight suggestions are applied

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
    pricing_region: str
    pricing_as_of: str
    containers: list = field(default_factory=list)
    cpu_request_m: float = 0.0
    cpu_suggested_m: float = 0.0
    mem_request_mi: float = 0.0
    mem_suggested_mi: float = 0.0
    waste_cost_monthly: float = 0.0
    growth_cost_monthly: float = 0.0
    current_cost_monthly: float = 0.0

    @property
    def waste_fraction(self):
        """Waste cost as a share of the current requested cost."""
        if self.current_cost_monthly <= 0:
            return 0.0
        return self.waste_cost_monthly / self.current_cost_monthly


def analyze(snapshot, pricing):
    report = ClusterReport(
        cluster=snapshot.cluster,
        captured_at=snapshot.captured_at,
        pricing_region=pricing.region,
        pricing_as_of=pricing.as_of,
    )
    for c in snapshot.containers:
        cpu = judge(
            c.cpu_request_m, [s.cpu_m for s in c.samples], CPU_STEP_M, CPU_FLOOR_M
        )
        mem = judge(
            c.mem_request_mi, [s.mem_mi for s in c.samples], MEM_STEP_MI, MEM_FLOOR_MI
        )
        entry = ContainerReport(
            namespace=c.namespace,
            pod=c.pod,
            container=c.container,
            samples=len(c.samples),
            cpu=cpu,
            mem=mem,
        )
        entry.waste_cost_monthly = pricing.monthly(cpu.waste, mem.waste)
        entry.growth_cost_monthly = pricing.monthly(
            max(0.0, cpu.suggested - cpu.request) if cpu.verdict == "tight" else 0.0,
            max(0.0, mem.suggested - mem.request) if mem.verdict == "tight" else 0.0,
        )
        report.containers.append(entry)
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
