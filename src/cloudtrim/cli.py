"""Command line entry point."""

import argparse
import sys

from cloudtrim import __version__, analyze, pricing, report, snapshot


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
    p.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="output format (default: text)",
    )
    p.add_argument(
        "--region",
        default="us-east-1",
        help="pricing region (default: us-east-1); builtins: us-east-1, ap-south-1",
    )
    p.add_argument(
        "--pricing-file",
        metavar="PATH",
        help="JSON file with vcpu_hour and gib_hour to override built-in rates",
    )
    p.add_argument(
        "--fail-on-waste-pct",
        type=float,
        metavar="N",
        help="exit 1 when waste exceeds N percent of requested cost (CI gating)",
    )
    p.add_argument("--version", action="version", version=f"cloudtrim {__version__}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        snap = (
            snapshot.load(args.from_file)
            if args.from_file
            else snapshot.from_cluster()
        )
    except snapshot.SnapshotError as exc:
        print(f"cloudtrim: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"cloudtrim: file not found: {args.from_file}", file=sys.stderr)
        return 2
    try:
        rates = pricing.make_pricing(args.region, args.pricing_file)
    except (ValueError, KeyError, OSError) as exc:
        print(f"cloudtrim: bad pricing input: {exc}", file=sys.stderr)
        return 2

    cluster_report = analyze.analyze(snap, rates)
    rendered = {"text": report.text, "markdown": report.markdown, "json": report.to_json}[
        args.format
    ](cluster_report)
    print(rendered)

    if args.fail_on_waste_pct is not None:
        if 100 * cluster_report.waste_fraction > args.fail_on_waste_pct:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
