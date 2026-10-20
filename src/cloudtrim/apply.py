"""Dry-run snippets for the suggestions.

--apply prints one of these and stops. Nothing here talks to a cluster;
the point is to hand a human (or a GitOps repo) a starting diff instead
of a report they have to retype.
"""

import json
from itertools import groupby

HEADER = "cloudtrim dry run: suggested values only, nothing was applied"


def _cpu(v):
    return f"{v:.0f}m"


def _mem(v):
    if v >= 1024 and v % 1024 == 0:
        return f"{v / 1024:.0f}Gi"
    return f"{v:.0f}Mi"


def _flagged(c):
    return c.verdict in ("over", "tight")


def _resources(c, suggested=True):
    if suggested:
        requests = {"cpu": _cpu(c.cpu.suggested), "memory": _mem(c.mem.suggested)}
        limits = {
            "cpu": _cpu(c.cpu.suggested_limit),
            "memory": _mem(c.mem.suggested_limit),
        }
    else:
        requests = {"cpu": _cpu(c.cpu.request), "memory": _mem(c.mem.request)}
        limits = {}
        if c.cpu_limit_m is not None:
            limits["cpu"] = _cpu(c.cpu_limit_m)
        if c.mem_limit_mi is not None:
            limits["memory"] = _mem(c.mem_limit_mi)
    out = {"requests": requests}
    if limits:
        out["limits"] = limits
    return out


def _flagged_containers(report):
    flagged = [c for c in report.containers if _flagged(c) and c.samples > 0]
    return sorted(flagged, key=lambda c: (c.namespace, c.pod, c.container))


def render_values(report):
    lines = [f"# {HEADER}"]
    flagged = _flagged_containers(report)
    if not flagged:
        lines.append("# no changes suggested")
        return "\n".join(lines)
    for namespace, group in groupby(flagged, key=lambda c: c.namespace):
        lines.append(f"{namespace}:")
        for pod, pod_group in groupby(group, key=lambda c: c.pod):
            lines.append(f"  {pod}:")
            for c in pod_group:
                res = _resources(c)
                lines.append(f"    {c.container}:")
                lines.append("      requests:")
                lines.append(f"        cpu: \"{res['requests']['cpu']}\"")
                lines.append(f"        memory: \"{res['requests']['memory']}\"")
                lines.append("      limits:")
                lines.append(f"        cpu: \"{res['limits']['cpu']}\"")
                lines.append(f"        memory: \"{res['limits']['memory']}\"")
    return "\n".join(lines)


def render_kustomize(report):
    lines = [
        f"# {HEADER}",
        "# --- kustomization.yaml ---",
        "apiVersion: kustomize.config.k8s.io/v1beta1",
        "kind: Kustomization",
        "resources:",
        "  - all.yaml",
        "patches:",
        "  - path: cloudtrim-patch.yaml",
        "# --- cloudtrim-patch.yaml ---",
        "# Pod names below come from the snapshot, hash suffix and all.",
        "# Point each entry at the workload that owns the pod (a Deployment,",
        "# usually) in your own manifests, and drop the suffix there.",
    ]
    flagged = _flagged_containers(report)
    if not flagged:
        lines.append("# no changes suggested")
        return "\n".join(lines)
    for i, (pod_key, group) in enumerate(
        groupby(flagged, key=lambda c: (c.namespace, c.pod))
    ):
        if i:
            lines.append("---")
        namespace, pod = pod_key
        lines += [
            "apiVersion: v1",
            "kind: Pod",
            "metadata:",
            f"  name: {pod}",
            f"  namespace: {namespace}",
            "spec:",
            "  containers:",
        ]
        for c in group:
            res = _resources(c)
            lines.append(f"  - name: {c.container}")
            lines.append("    resources:")
            lines.append(
                f"      requests: {{cpu: \"{res['requests']['cpu']}\", "
                f"memory: \"{res['requests']['memory']}\"}}"
            )
            lines.append(
                f"      limits: {{cpu: \"{res['limits']['cpu']}\", "
                f"memory: \"{res['limits']['memory']}\"}}"
            )
    return "\n".join(lines)


def render_patch(report):
    """One merge patch per pod.

    A merge patch replaces the whole containers list, so every container
    of the pod is in the patch: flagged ones with their suggestions, the
    rest with the values they already have.
    """
    flagged = _flagged_containers(report)
    entries = []
    if flagged:
        flagged_pods = {(c.namespace, c.pod) for c in flagged}
        pods = {}
        for c in report.containers:
            if (c.namespace, c.pod) in flagged_pods and c.samples > 0:
                pods.setdefault((c.namespace, c.pod), []).append(c)
        for (namespace, pod), group in sorted(pods.items()):
            containers = [
                {
                    "name": c.container,
                    "resources": _resources(c, suggested=_flagged(c)),
                }
                for c in group
            ]
            entries.append(
                {
                    "namespace": namespace,
                    "pod": pod,
                    "merge_patch": {"spec": {"containers": containers}},
                }
            )
    return f"# {HEADER}\n" + json.dumps(entries, indent=2)


def render(report, fmt):
    return {"values": render_values, "kustomize": render_kustomize,
            "patch": render_patch}[fmt](report)
