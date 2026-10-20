"""Compare two snapshots: what changed, what appeared, what went away.

Both sides go through the same analyzer with the same pricing, so the
delta is apples to apples. A container counts as changed when its
verdict moved or its monthly waste moved by more than a cent.
"""

from dataclasses import dataclass, field

from cloudtrim.report import _fmt_money


@dataclass
class Change:
    namespace: str
    pod: str
    container: str
    status: str  # changed, new, gone
    old_verdict: str | None
    new_verdict: str | None
    old_waste: float
    new_waste: float


@dataclass
class Comparison:
    baseline_at: str
    current_at: str
    changes: list = field(default_factory=list)
    unchanged: int = 0
    old_waste_monthly: float = 0.0
    new_waste_monthly: float = 0.0

    @property
    def delta(self):
        return self.new_waste_monthly - self.old_waste_monthly


def _index(report):
    return {(c.namespace, c.pod, c.container): c for c in report.containers}


def compare(baseline, current):
    old, new = _index(baseline), _index(current)
    result = Comparison(
        baseline_at=baseline.captured_at,
        current_at=current.captured_at,
        old_waste_monthly=baseline.waste_cost_monthly,
        new_waste_monthly=current.waste_cost_monthly,
    )
    for key in sorted(set(old) | set(new)):
        namespace, pod, container = key
        before, after = old.get(key), new.get(key)
        if before is None:
            result.changes.append(
                Change(namespace, pod, container, "new", None,
                       after.verdict, 0.0, after.waste_cost_monthly)
            )
        elif after is None:
            result.changes.append(
                Change(namespace, pod, container, "gone",
                       before.verdict, None, before.waste_cost_monthly, 0.0)
            )
        elif (
            before.verdict != after.verdict
            or abs(before.waste_cost_monthly - after.waste_cost_monthly) > 0.01
        ):
            result.changes.append(
                Change(namespace, pod, container, "changed",
                       before.verdict, after.verdict,
                       before.waste_cost_monthly, after.waste_cost_monthly)
            )
        else:
            result.unchanged += 1
    return result


_MARKERS = {"changed": ">>", "new": " +", "gone": " -"}


def _line(change):
    old_v = change.old_verdict or "-"
    new_v = change.new_verdict or "-"
    return (
        f"{_MARKERS[change.status]} {change.namespace} "
        f"{change.pod}/{change.container} {old_v} -> {new_v} "
        f"· waste {_fmt_money(change.old_waste)} -> {_fmt_money(change.new_waste)}"
    )


def _totals_line(comparison):
    delta = comparison.delta
    if delta < 0:
        trend = f"down {_fmt_money(-delta)}"
    elif delta > 0:
        trend = f"up {_fmt_money(delta)}"
    else:
        trend = "no change"
    return (
        f"totals: waste {_fmt_money(comparison.old_waste_monthly)}/mo -> "
        f"{_fmt_money(comparison.new_waste_monthly)}/mo ({trend})"
    )


def text(comparison):
    lines = [f"diff vs baseline captured {comparison.baseline_at or 'unknown'}"]
    lines.extend(_line(c) for c in comparison.changes)
    if comparison.unchanged:
        lines.append(f"   {comparison.unchanged} unchanged")
    lines.append(_totals_line(comparison))
    return "\n".join(lines)


def markdown(comparison):
    out = [
        f"## cloudtrim diff vs baseline {comparison.baseline_at or 'unknown'}",
        "",
        "| namespace | workload | was | now | waste was | waste now |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in comparison.changes:
        out.append(
            f"| {c.namespace} | {c.pod}/{c.container} | {c.old_verdict or 'new'} "
            f"| {c.new_verdict or 'gone'} | {_fmt_money(c.old_waste)} "
            f"| {_fmt_money(c.new_waste)} |"
        )
    out += ["", _totals_line(comparison)]
    return "\n".join(out)


def to_dict(comparison):
    return {
        "baseline_captured_at": comparison.baseline_at,
        "current_captured_at": comparison.current_at,
        "changes": [
            {
                "namespace": c.namespace,
                "pod": c.pod,
                "container": c.container,
                "status": c.status,
                "old_verdict": c.old_verdict,
                "new_verdict": c.new_verdict,
                "old_waste_monthly_usd": round(c.old_waste, 2),
                "new_waste_monthly_usd": round(c.new_waste, 2),
            }
            for c in comparison.changes
        ],
        "unchanged": comparison.unchanged,
        "totals": {
            "old_waste_monthly_usd": round(comparison.old_waste_monthly, 2),
            "new_waste_monthly_usd": round(comparison.new_waste_monthly, 2),
            "delta_monthly_usd": round(comparison.delta, 2),
        },
    }
