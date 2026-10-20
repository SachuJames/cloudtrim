"""Command line entry point."""

import argparse
import json
import sys

from cloudtrim import __version__, analyze, apply, diff, pricing, prometheus, report, snapshot


def build_parser():
    p = argparse.ArgumentParser(
        prog="cloudtrim",
        description="Find wasted Kubernetes resources and price the difference.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-file", metavar="PATH", help="read a snapshot JSON file")
    src.add_argument(
        "--from-cluster",
        action="store_true",
        help="read requests, limits and current usage via kubectl",
    )
    src.add_argument(
        "--from-prometheus",
        metavar="URL",
        help="read usage history and requests from a Prometheus base URL "
        "(needs kube-state-metrics for the requests)",
    )
    p.add_argument(
        "--windows",
        default="24h,7d",
        help="prometheus windows to fetch, comma list (default: 24h,7d)",
    )
    p.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="output format (default: text)",
    )
    p.add_argument(
        "--region",
        default="us-east-1",
        help="pricing region (default: us-east-1); builtins: "
        "us-east-1, us-west-2, eu-west-1, ap-south-1",
    )
    p.add_argument(
        "--pricing-file",
        metavar="PATH",
        help="JSON file with vcpu_hour and gib_hour to override built-in rates",
    )
    p.add_argument(
        "--discount",
        type=float,
        default=1.0,
        metavar="F",
        help="multiply all prices by F, for savings plans or reserved "
        "pricing (default: 1.0)",
    )
    p.add_argument(
        "--headroom",
        type=float,
        default=analyze.HEADROOM,
        metavar="F",
        help=f"suggested request is p95 times F (default: {analyze.HEADROOM})",
    )
    p.add_argument(
        "--over-factor",
        type=float,
        default=analyze.OVER_FACTOR,
        metavar="F",
        help=f"request over F times p95 counts as over (default: {analyze.OVER_FACTOR})",
    )
    p.add_argument(
        "--tight-ratio",
        type=float,
        default=analyze.TIGHT_RATIO,
        metavar="F",
        help=f"p95 above F of the request counts as tight (default: {analyze.TIGHT_RATIO})",
    )
    p.add_argument(
        "--namespace",
        action="append",
        default=[],
        metavar="NS",
        help="only look at this namespace (repeatable)",
    )
    p.add_argument(
        "--selector",
        action="append",
        default=[],
        metavar="K=V",
        help="only look at containers whose labels match (repeatable)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="print a dry-run snippet with the suggested values instead "
        "of the report; nothing is applied",
    )
    p.add_argument(
        "--apply-format",
        choices=["values", "kustomize", "patch"],
        default="values",
        help="apply snippet shape (default: values)",
    )
    p.add_argument(
        "--diff",
        metavar="PATH",
        help="compare a --from-file snapshot against this baseline snapshot",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="never color the text report (color is tty-only anyway)",
    )
    p.add_argument(
        "--fail-on-waste-pct",
        type=float,
        metavar="N",
        help="exit 1 when waste exceeds N percent of requested cost (CI gating)",
    )
    p.add_argument("--version", action="version", version=f"cloudtrim {__version__}")
    return p


def _parse_selectors(raw):
    selectors = []
    for item in raw:
        if "=" not in item:
            raise ValueError(f"bad selector '{item}': expected key=value")
        key, value = item.split("=", 1)
        selectors.append((key, value))
    return selectors


def _load_source(args):
    if args.from_file:
        snap = snapshot.load(args.from_file)
        snap.source = f"file {args.from_file}"
        return snap
    if args.from_cluster:
        return snapshot.from_cluster()
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    if not windows:
        raise snapshot.SnapshotError("--windows needs at least one window")
    snap = prometheus.from_prometheus(args.from_prometheus, windows=windows)
    snap.source = f"prometheus {args.from_prometheus}"
    return snap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        selectors = _parse_selectors(args.selector)
    except ValueError as exc:
        print(f"cloudtrim: {exc}", file=sys.stderr)
        return 2
    if args.diff and not args.from_file:
        print("cloudtrim: --diff needs --from-file for the current snapshot",
              file=sys.stderr)
        return 2
    try:
        snap = _load_source(args)
        baseline = snapshot.load(args.diff) if args.diff else None
    except snapshot.SnapshotError as exc:
        print(f"cloudtrim: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"cloudtrim: file not found: {exc.filename}", file=sys.stderr)
        return 2
    try:
        rates = pricing.make_pricing(args.region, args.pricing_file, args.discount)
    except (ValueError, KeyError, OSError) as exc:
        print(f"cloudtrim: bad pricing input: {exc}", file=sys.stderr)
        return 2

    config = analyze.Config(
        headroom=args.headroom,
        over_factor=args.over_factor,
        tight_ratio=args.tight_ratio,
    )
    if args.namespace or selectors:
        snap.containers = snapshot.apply_filters(
            snap.containers, namespaces=args.namespace, selectors=selectors
        )
    cluster_report = analyze.analyze(snap, rates, config)

    if args.apply:
        print(apply.render(cluster_report, args.apply_format))
        return 0

    if baseline is not None:
        comparison = diff.compare(analyze.analyze(baseline, rates, config),
                                  cluster_report)
        if args.format == "json":
            data = json.loads(report.to_json(cluster_report))
            data["diff"] = diff.to_dict(comparison)
            print(json.dumps(data, indent=2))
        elif args.format == "markdown":
            print(diff.markdown(comparison))
        else:
            print(diff.text(comparison))
    else:
        color = args.format == "text" and sys.stdout.isatty() and not args.no_color
        rendered = {"text": lambda r: report.text(r, color=color),
                    "markdown": report.markdown,
                    "json": report.to_json}[args.format](cluster_report)
        print(rendered)

    if args.fail_on_waste_pct is not None:
        if 100 * cluster_report.waste_fraction > args.fail_on_waste_pct:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
