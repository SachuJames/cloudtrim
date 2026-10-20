"""Render a ClusterReport as text, markdown, or JSON."""

import json

_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _fmt_cpu(m):
    return f"{m:,.0f}m"


def _fmt_mem(mi):
    if mi >= 1024 and mi % 1024 == 0:
        return f"{mi / 1024:,.0f}Gi"
    return f"{mi:,.0f}Mi"


def _fmt_money(x):
    return f"${x:,.2f}"


def _fmt_span(seconds):
    if seconds is None:
        return "span unknown"
    if seconds >= 86400:
        days = seconds / 86400
        return f"span {days:,.0f}d" if days == int(days) else f"span {days:.1f}d"
    if seconds >= 3600:
        hours = seconds / 3600
        return f"span {hours:,.0f}h" if hours == int(hours) else f"span {hours:.1f}h"
    return f"span {seconds / 60:.0f}m"


def _marker(verdict):
    if verdict == "over":
        return ">>"
    if verdict == "tight":
        return " !"
    return "  "


def _flagged(c):
    return c.cpu.verdict in ("over", "tight") or c.mem.verdict in ("over", "tight")


def _by_namespace(containers):
    groups = {}
    for c in containers:
        groups.setdefault(c.namespace, []).append(c)
    return groups


def _header(report):
    windows = ", ".join(report.window_labels) or "snapshot"
    return (
        f"{report.cluster} · source {report.source} · windows: {windows} · "
        f"{report.total_samples} samples · {_fmt_span(report.span_seconds)}"
    )


def _pricing_line(report):
    line = (
        f"pricing: estimates only, on-demand list price, {report.pricing_region}, "
        f"as of {report.pricing_as_of}"
    )
    if report.discount != 1.0:
        line += f", discount factor {report.discount:g} applied"
    return line


def _container_line(c, color=False):
    workload = f"{c.pod}/{c.container}"
    line = (
        f"{_marker(c.verdict)} {workload:<34} "
        f"cpu {_fmt_cpu(c.cpu.request):>8} p95 {_fmt_cpu(c.cpu.p95):>7} "
        f"-> {_fmt_cpu(c.cpu.suggested):<8} "
        f"mem {_fmt_mem(c.mem.request):>8} p95 {_fmt_mem(c.mem.p95):>7} "
        f"-> {_fmt_mem(c.mem.suggested):<8} "
        f"{c.verdict:>8} waste {_fmt_money(c.waste_cost_monthly):>9}"
    )
    if color and c.verdict == "over":
        return f"{_RED}{line}{_RESET}"
    if color and c.verdict == "tight":
        return f"{_YELLOW}{line}{_RESET}"
    return line


def _sub_lines(c):
    out = []
    if _flagged(c):
        out.append(
            f"     suggested limits: cpu {_fmt_cpu(c.cpu.suggested_limit)}, "
            f"mem {_fmt_mem(c.mem.suggested_limit)}"
        )
    if len(c.windows) > 1:
        bits = " · ".join(
            f"{w.label} cpu {_fmt_cpu(w.cpu.p95)} mem {_fmt_mem(w.mem.p95)}"
            for w in c.windows
        )
        out.append(f"     p95 by window: {bits}")
    if c.bursty:
        out.append("     note: bursty, the short window runs far above the long one")
    return out


def text(report, color=False):
    lines = [_header(report), ""]
    with_data = [c for c in report.containers if c.samples > 0]
    no_data = [c for c in report.containers if c.samples == 0]
    for namespace, group in _by_namespace(with_data).items():
        lines.append(namespace)
        for c in group:
            lines.append(_container_line(c, color))
            lines.extend(_sub_lines(c))
        subtotal = sum(c.waste_cost_monthly for c in group)
        lines.append(f"  subtotal waste {_fmt_money(subtotal)}/mo")
        lines.append("")
    if no_data:
        lines.append(f"no data, left out of the totals: {len(no_data)}")
        for c in no_data:
            lines.append(
                f"  {c.namespace} {c.pod}/{c.container} "
                f"cpu {_fmt_cpu(c.cpu.request)} mem {_fmt_mem(c.mem.request)} (0 samples)"
            )
        lines.append("")
    summary = (
        f"{len(with_data)} containers | cpu {_fmt_cpu(report.cpu_request_m)} "
        f"-> {_fmt_cpu(report.cpu_suggested_m)} | mem {_fmt_mem(report.mem_request_mi)} "
        f"-> {_fmt_mem(report.mem_suggested_mi)} | "
        f"waste {_fmt_money(report.waste_cost_monthly)}/mo "
        f"({100 * report.waste_fraction:.1f}% of requested)"
    )
    if report.no_data_count:
        summary += f" | {report.no_data_count} no-data excluded"
    lines.append(summary)
    if report.growth_cost_monthly > 0:
        lines.append(
            f"note: tight containers would add "
            f"{_fmt_money(report.growth_cost_monthly)}/mo if their suggestions "
            "were applied"
        )
    for note in report.notes:
        lines.append(f"note: {note}")
    lines.append(_pricing_line(report))
    return "\n".join(lines)


def markdown(report):
    out = [
        f"## cloudtrim: {report.cluster}",
        "",
        f"Source {report.source} · windows: {', '.join(report.window_labels)} · "
        f"{report.total_samples} samples · {_fmt_span(report.span_seconds)}",
        "",
        "| namespace | workload | cpu req | cpu p95 | cpu suggested | cpu limit | mem req | mem p95 | mem suggested | mem limit | verdict | waste/mo |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in report.containers:
        verdict = c.verdict + (" (bursty)" if c.bursty else "")
        out.append(
            f"| {c.namespace} | {c.pod}/{c.container} | {_fmt_cpu(c.cpu.request)} | "
            f"{_fmt_cpu(c.cpu.p95)} | {_fmt_cpu(c.cpu.suggested)} | "
            f"{_fmt_cpu(c.cpu.suggested_limit)} | {_fmt_mem(c.mem.request)} | "
            f"{_fmt_mem(c.mem.p95)} | {_fmt_mem(c.mem.suggested)} | "
            f"{_fmt_mem(c.mem.suggested_limit)} | {verdict} | "
            f"{_fmt_money(c.waste_cost_monthly)} |"
        )
    counted = len(report.containers) - report.no_data_count
    out += [
        "",
        f"**{counted} containers**, waste "
        f"{_fmt_money(report.waste_cost_monthly)}/mo "
        f"({100 * report.waste_fraction:.1f}% of requested). "
        f"Pricing is an estimate: on-demand list price, {report.pricing_region}, "
        f"as of {report.pricing_as_of}"
        + (f", discount factor {report.discount:g} applied." if report.discount != 1.0 else "."),
    ]
    if report.no_data_count:
        out.append(
            f"{report.no_data_count} container(s) had no samples and were "
            "left out of the totals."
        )
    for note in report.notes:
        out.append(f"Note: {note}")
    return "\n".join(out)


def _container_json(c):
    return {
        "namespace": c.namespace,
        "pod": c.pod,
        "container": c.container,
        "labels": c.labels,
        "verdict": c.verdict,
        "bursty": c.bursty,
        "samples": c.samples,
        "windows": {
            w.label: {
                "samples": w.samples,
                "span_seconds": w.span_seconds,
                "cpu_p95_m": w.cpu.p95,
                "mem_p95_mi": w.mem.p95,
            }
            for w in c.windows
        },
        "cpu": {
            "request_m": c.cpu.request,
            "p95_m": c.cpu.p95,
            "peak_m": c.cpu.peak,
            "suggested_m": c.cpu.suggested,
            "suggested_limit_m": c.cpu.suggested_limit,
            "waste_m": c.cpu.waste,
        },
        "mem": {
            "request_mi": c.mem.request,
            "p95_mi": c.mem.p95,
            "peak_mi": c.mem.peak,
            "suggested_mi": c.mem.suggested,
            "suggested_limit_mi": c.mem.suggested_limit,
            "waste_mi": c.mem.waste,
        },
        "waste_monthly_usd": round(c.waste_cost_monthly, 2),
        "growth_monthly_usd": round(c.growth_cost_monthly, 2),
    }


def _namespaces_json(report):
    out = {}
    for namespace, group in _by_namespace(report.containers).items():
        counted = [c for c in group if c.samples > 0]
        out[namespace] = {
            "containers": len(counted),
            "no_data": len(group) - len(counted),
            "cpu_request_m": sum(c.cpu.request for c in counted),
            "cpu_suggested_m": sum(c.cpu.suggested for c in counted),
            "mem_request_mi": sum(c.mem.request for c in counted),
            "mem_suggested_mi": sum(c.mem.suggested for c in counted),
            "waste_monthly_usd": round(
                sum(c.waste_cost_monthly for c in counted), 2
            ),
            "growth_monthly_usd": round(
                sum(c.growth_cost_monthly for c in counted), 2
            ),
        }
    return out


def to_json(report):
    return json.dumps(
        {
            "version": "2",
            "cluster": report.cluster,
            "captured_at": report.captured_at,
            "source": report.source,
            "pricing_region": report.pricing_region,
            "pricing_as_of": report.pricing_as_of,
            "discount": report.discount,
            "windows": report.window_labels,
            "total_samples": report.total_samples,
            "span_seconds": report.span_seconds,
            "containers": [_container_json(c) for c in report.containers],
            "namespaces": _namespaces_json(report),
            "notes": report.notes,
            "summary": {
                "container_count": len(report.containers) - report.no_data_count,
                "no_data_count": report.no_data_count,
                "cpu_request_m": report.cpu_request_m,
                "cpu_suggested_m": report.cpu_suggested_m,
                "mem_request_mi": report.mem_request_mi,
                "mem_suggested_mi": report.mem_suggested_mi,
                "waste_monthly_usd": round(report.waste_cost_monthly, 2),
                "waste_fraction": round(report.waste_fraction, 4),
                "growth_monthly_usd": round(report.growth_cost_monthly, 2),
            },
        },
        indent=2,
    )
