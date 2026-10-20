"""Render a ClusterReport as text, markdown, or JSON."""

import json


def _fmt_cpu(m):
    return f"{m:,.0f}m"


def _fmt_mem(mi):
    if mi >= 1024 and mi % 1024 == 0:
        return f"{mi / 1024:,.0f}Gi"
    return f"{mi:,.0f}Mi"


def _fmt_money(x):
    return f"${x:,.2f}"


def _rows(report):
    for c in report.containers:
        yield {
            "namespace": c.namespace,
            "workload": f"{c.pod}/{c.container}",
            "cpu_request": _fmt_cpu(c.cpu.request),
            "cpu_p95": _fmt_cpu(c.cpu.p95),
            "cpu_suggested": _fmt_cpu(c.cpu.suggested),
            "mem_request": _fmt_mem(c.mem.request),
            "mem_p95": _fmt_mem(c.mem.p95),
            "mem_suggested": _fmt_mem(c.mem.suggested),
            "verdict": c.verdict,
            "waste_monthly": c.waste_cost_monthly,
            "samples": c.samples,
        }


def _summary(report):
    return {
        "cluster": report.cluster,
        "captured_at": report.captured_at,
        "pricing_region": report.pricing_region,
        "pricing_as_of": report.pricing_as_of,
        "containers": len(report.containers),
        "cpu_requested": _fmt_cpu(report.cpu_request_m),
        "cpu_suggested": _fmt_cpu(report.cpu_suggested_m),
        "mem_requested": _fmt_mem(report.mem_request_mi),
        "mem_suggested": _fmt_mem(report.mem_suggested_mi),
        "waste_monthly": report.waste_cost_monthly,
        "waste_fraction": report.waste_fraction,
        "growth_monthly": report.growth_cost_monthly,
    }


def text(report):
    lines = []
    for r in _rows(report):
        lines.append(
            f"{r['namespace']:>12} {r['workload']:<28} "
            f"cpu {r['cpu_request']:>8} p95 {r['cpu_p95']:>7} -> {r['cpu_suggested']:<8} "
            f"mem {r['mem_request']:>8} p95 {r['mem_p95']:>7} -> {r['mem_suggested']:<8} "
            f"{r['verdict']:>8} waste {_fmt_money(r['waste_monthly']):>9}"
        )
    s = _summary(report)
    lines.append("")
    lines.append(
        f"{s['containers']} containers | cpu {s['cpu_requested']} -> {s['cpu_suggested']} "
        f"| mem {s['mem_requested']} -> {s['mem_suggested']} | "
        f"waste {_fmt_money(s['waste_monthly'])}/mo "
        f"({100 * s['waste_fraction']:.1f}% of requested)"
    )
    if s["growth_monthly"] > 0:
        lines.append(
            f"note: tight containers would add {_fmt_money(s['growth_monthly'])}/mo "
            "if their suggestions were applied"
        )
    lines.append(
        f"pricing: estimates only, on-demand list price, {s['pricing_region']}, "
        f"as of {s['pricing_as_of']}"
    )
    return "\n".join(lines)


def markdown(report):
    out = [
        f"## cloudtrim: {report.cluster}",
        "",
        "| namespace | workload | cpu req | cpu p95 | cpu suggested | mem req | mem p95 | mem suggested | verdict | waste/mo |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in _rows(report):
        out.append(
            f"| {r['namespace']} | {r['workload']} | {r['cpu_request']} | {r['cpu_p95']} | "
            f"{r['cpu_suggested']} | {r['mem_request']} | {r['mem_p95']} | "
            f"{r['mem_suggested']} | {r['verdict']} | {_fmt_money(r['waste_monthly'])} |"
        )
    s = _summary(report)
    out += [
        "",
        f"**{s['containers']} containers**, waste "
        f"{_fmt_money(s['waste_monthly'])}/mo "
        f"({100 * s['waste_fraction']:.1f}% of requested). "
        f"Pricing is an estimate: on-demand list price, {s['pricing_region']}, "
        f"as of {s['pricing_as_of']}.",
    ]
    return "\n".join(out)


def to_json(report):
    rows = []
    for c, r in zip(report.containers, _rows(report)):
        rows.append(
            {
                "namespace": r["namespace"],
                "pod": c.pod,
                "container": c.container,
                "verdict": r["verdict"],
                "samples": r["samples"],
                "cpu": {
                    "request_m": c.cpu.request,
                    "p95_m": c.cpu.p95,
                    "peak_m": c.cpu.peak,
                    "suggested_m": c.cpu.suggested,
                    "waste_m": c.cpu.waste,
                },
                "mem": {
                    "request_mi": c.mem.request,
                    "p95_mi": c.mem.p95,
                    "peak_mi": c.mem.peak,
                    "suggested_mi": c.mem.suggested,
                    "waste_mi": c.mem.waste,
                },
                "waste_monthly_usd": round(c.waste_cost_monthly, 2),
                "growth_monthly_usd": round(c.growth_cost_monthly, 2),
            }
        )
    s = _summary(report)
    return json.dumps(
        {
            "cluster": s["cluster"],
            "captured_at": s["captured_at"],
            "pricing_region": s["pricing_region"],
            "pricing_as_of": s["pricing_as_of"],
            "containers": rows,
            "summary": {
                "container_count": s["containers"],
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
