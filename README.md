<div align="center">

<img src="docs/assets/banner.svg" alt="cloudtrim" width="100%"/>

[![CI](https://github.com/SachuJames/cloudtrim/actions/workflows/ci.yml/badge.svg)](https://github.com/SachuJames/cloudtrim/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SachuJames/cloudtrim)](https://github.com/SachuJames/cloudtrim/releases)
[![PyPI](https://img.shields.io/pypi/v/cloudtrim-cli)](https://pypi.org/project/cloudtrim-cli/)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

**[Release v0.2.1](https://github.com/SachuJames/cloudtrim/releases/tag/v0.2.1)** · **[PyPI](https://pypi.org/project/cloudtrim-cli/)** · **[Image on GHCR](https://github.com/SachuJames/cloudtrim/pkgs/container/cloudtrim)**

Your cluster knows what each pod requested. It also knows what each pod
actually used. cloudtrim puts the two side by side, suggests request
values that fit the real numbers, and tells you roughly what the
difference costs you every month.

</div>

## Quickstart

```
pipx install cloudtrim-cli
cloudtrim --from-file examples/sample-snapshot.json
```

That runs against a small sample cluster that ships with the repo,
twelve containers across three namespaces, a few of them badly
over-provisioned. This is the tool's real output:

```
prod-eks-1 · source file examples/sample-snapshot.json · windows: snapshot · 120 samples · span unknown

platform
>> worker-8d2f6a4c9-h3j5/worker       cpu   4,000m p95    700m -> 910m     mem      8Gi p95 2,100Mi -> 2,736Mi      over waste   $165.28
     suggested limits: cpu 1,370m, mem 4,112Mi
>> report-3e7b1d5f8-s6r4/report       cpu     500m p95     90m -> 120m     mem      1Gi p95   300Mi -> 400Mi        over waste    $19.90
     suggested limits: cpu 180m, mem 608Mi
 ! scheduler-9c5a3b7e2-v1x9/scheduler cpu     100m p95     88m -> 120m     mem    128Mi p95    70Mi -> 128Mi       tight waste     $0.00
     suggested limits: cpu 180m, mem 192Mi
  subtotal waste $185.17/mo

shop
>> checkout-5c8a7e2d1-m4k8/checkout   cpu   2,000m p95    380m -> 500m     mem      2Gi p95   600Mi -> 784Mi        over waste    $67.60
     suggested limits: cpu 750m, mem 1,184Mi
>> api-6d9f8c5b4-x2v1/api             cpu   1,000m p95    225m -> 300m     mem      1Gi p95   495Mi -> 656Mi        over waste    $29.52
     suggested limits: cpu 450m, mem 992Mi
>> token-4a9c2e8b6-t5n7/token         cpu     100m p95     40m -> 60m      mem    128Mi p95    55Mi -> 80Mi         over waste     $1.93
     suggested limits: cpu 90m, mem 128Mi
   web-7f4b9d6c8-p9q3/web             cpu     500m p95    325m -> 500m     mem    512Mi p95   400Mi -> 512Mi          ok waste     $0.00
 ! auth-6b3d5f9a7-z8w2/auth           cpu     250m p95    228m -> 300m     mem    256Mi p95   180Mi -> 256Mi       tight waste     $0.00
     suggested limits: cpu 450m, mem 384Mi
  subtotal waste $99.05/mo

batch
>> exporter-7a4c8e2b5-n8q1/exporter   cpu      50m p95     12m -> 20m      mem     64Mi p95    20Mi -> 32Mi         over waste     $1.41
     suggested limits: cpu 30m, mem 48Mi
   etl-2f8d4c6a1-b7m3/etl             cpu   1,500m p95  1,250m -> 1,500m   mem      3Gi p95 1,900Mi -> 3Gi            ok waste     $0.00
 ! cache-5b1e9d3f7-k2p6/redis         cpu     200m p95    150m -> 200m     mem    512Mi p95   470Mi -> 624Mi       tight waste     $0.00
     suggested limits: cpu 300m, mem 944Mi
 ! ingest-1d6f3a9c4-w4t8/ingest       cpu     800m p95    700m -> 910m     mem      1Gi p95   600Mi -> 1Gi         tight waste     $0.00
     suggested limits: cpu 1,370m, mem 1,536Mi
  subtotal waste $1.41/mo

12 containers | cpu 11,000m -> 5,440m | mem 17,984Mi -> 10,304Mi | waste $285.64/mo (49.7% of requested)
note: tight containers would add $7.75/mo if their suggestions were applied
note: rightsized pods do not shrink the nodes under them; unallocated node capacity is a separate cost
pricing: estimates only, on-demand list price, us-east-1, as of 2026-10
```

$285 a month going to containers that asked for far more than they use.
One command, no account, no cloud bill involved. The `>>` lines are
where the money is; `!` marks the tight ones. On a terminal they are
colored too (`--no-color` turns that off).

## How it decides

CPU and memory are judged separately, because a container can waste CPU
and starve on memory at the same time.

- **over**: the request is more than twice the p95 usage. The suggestion
  is p95 plus 30 percent headroom, rounded up to a manifest-sensible
  step (10m of CPU, 16Mi of memory), floored at 10m/16Mi, and never
  raised above the current request.
- **tight**: p95 usage sits above 85 percent of the request. A raise to
  p95 times 1.3 is suggested, but it is reported as extra cost, not
  folded silently into the savings.
- **ok**: close enough to leave alone.
- **no-data**: no samples at all. These get their own section at the
  end of the report and are left out of every total, because a
  container nobody measured should not dilute the numbers.

Every suggestion comes with a suggested limit: the suggested request
times 1.5.

The defaults are a starting point, not a doctrine. Risk tolerance
varies, so all three knobs move:

```
cloudtrim --from-file snapshot.json --headroom 1.5 --over-factor 3 --tight-ratio 0.8
```

When usage arrives in more than one window (Prometheus mode, below),
the longest window decides the verdict. The shorter one is printed next
to it, and when its p95 runs far above the long window's, the container
is called out as bursty: a workload that spikes is a different
right-sizing problem than one that is steady.

## Feeding it data

Three ways in, in increasing order of how much history they carry.

**Prometheus** is the one with real history:

```
cloudtrim --from-prometheus http://localhost:9090
```

It pulls CPU and memory usage as range queries over two windows, the
last 24 hours at a 5 minute step and the last 7 days at a 30 minute
step (`--windows 7d` fetches just one, `--windows 24h,7d,30d` is the
shape if you want more). The requests come from kube-state-metrics
(`kube_pod_container_resource_requests`), so that exporter needs to be
scraped by your Prometheus; if the requests series are missing,
cloudtrim says so and stops rather than guessing. The first line of
the report states the source, the windows, the sample count, and the
span covered, so you can see how much evidence a verdict stands on.

**A snapshot file** is one JSON object with the cluster name, a
timestamp, and a list of containers:

```json
{
  "cluster": "prod-eks-1",
  "captured_at": "2026-10-18T09:30:00Z",
  "containers": [
    {
      "namespace": "shop", "pod": "api-6d9f8c5b4-x2v1", "container": "api",
      "cpu_request_m": 1000, "mem_request_mi": 1024,
      "cpu_limit_m": 2000, "mem_limit_mi": 2048,
      "labels": {"app": "api"},
      "samples": [{"cpu_m": 210, "mem_mi": 480, "t": 1791859200}]
    }
  ]
}
```

Limits and labels are optional; a sample's `t` (epoch seconds) is too,
and when samples carry timestamps the report can state the span they
cover. Malformed entries fail fast and name the entry, so a bad
exporter feed does not silently poison the numbers. If you need
snapshots written for you, `examples/exporter/` has a small script
plus a CronJob that writes one hourly from kubectl and metrics-server.

**A live cluster** goes through kubectl:

```
cloudtrim --from-cluster
```

Requests and limits come from the pod spec, usage from metrics-server.
That yields exactly one sample per container, right now, not a week of
history. Take single-sample answers as a first look, not a verdict.

Filters narrow any of the three sources down before the math:

```
cloudtrim --from-prometheus http://localhost:9090 --namespace shop
cloudtrim --from-file snapshot.json --selector app=api --selector team=payments
```

(Selector filtering matches container labels. File snapshots carry the
labels written into them, kubectl mode uses the pod's labels, and
Prometheus mode uses whatever labels the series carry.)

## Getting values out: the dry run

A report is advice; `--apply` turns it into a starting diff. It prints
a snippet and stops. Nothing is applied, ever.

```
cloudtrim --from-file snapshot.json --apply                      # Helm-style values
cloudtrim --from-file snapshot.json --apply --apply-format kustomize
cloudtrim --from-file snapshot.json --apply --apply-format patch
```

- **values**: suggested requests and limits grouped by namespace, pod,
  and container, as YAML.
- **kustomize**: a kustomization skeleton plus a patch file with one
  entry per flagged pod. Pod names come from the snapshot, hash suffix
  and all; point each entry at the workload that owns the pod.
- **patch**: a JSON merge patch per pod. Merge patches replace the
  whole containers list, so every container of the pod is included,
  flagged ones with their suggestions and the rest with what they
  already have.

## Watching it move: diff

```
cloudtrim --from-file today.json --diff last-week.json
```

Per container: the verdict then and now, the waste then and now.
Containers only on one side are marked new or gone, and the last line
is the totals delta. With `--format json` the comparison rides along
in a `diff` section of the normal report.

## The cost model

Each region has a per vCPU-hour and per GiB-hour rate built in, and the
monthly number is just the hourly cost times 730 hours. The built-ins
are on-demand list-price approximations for us-east-1, us-west-2,
eu-west-1, and ap-south-1, dated 2026-10. They are there to make the
waste concrete, not to predict your bill.

Pay less than list? Say how much less:

```
cloudtrim --from-file snapshot.json --discount 0.6
```

That multiplies every price by 0.6, the rough shape of a savings plan
or reserved discount, and the pricing line in the report says the
factor out loud. Or bring your own rates entirely:

```
cloudtrim --from-file snapshot.json --pricing-file my-rates.json
```

A pricing file is JSON with `vcpu_hour`, `gib_hour`, and optionally
`as_of`.

## Reports and CI

Text goes to stdout by default, grouped by namespace with a subtotal
each. The other formats:

```
cloudtrim --from-file snapshot.json --format json > report.json
cloudtrim --from-file snapshot.json --format markdown   # paste into an issue
```

The JSON is versioned (`"version": "2"`) and carries per-container
windows, suggested limits, per-namespace rollups, and the totals, so
other tools can build on it.

In CI, gate on the waste share of requested cost:

```
cloudtrim --from-file snapshot.json --fail-on-waste-pct 25
```

Exits 1 when waste is over 25 percent, so a PR that doubles a request
gets caught the same way a failing test does. For GitHub Actions there
is an action in this repo:

```yaml
- uses: SachuJames/cloudtrim@v0.2.0
  with:
    snapshot: snapshots/prod.json
    fail-on-waste-pct: "25"
```

For CI and air-gapped setups in general, the container image is the
recommended route: `ghcr.io/sachujames/cloudtrim:v0.2.0`, no Python
setup on the runner at all.

## Honest limits

The answers are only as good as the samples. A snapshot from an idle
weekend will tell you to cut everything; a snapshot from a deploy storm
will tell you to raise everything. Prometheus mode widens the window,
but it is still bounded by what your Prometheus kept: default
retention is often 15 days, and the steps (5 minutes, 30 minutes)
smooth over spikes shorter than themselves, so very spiky workloads
look calmer than they are.

The dollars are estimates. On-demand list prices in four regions,
dated, nothing more. Reserved instances, savings plans (the
`--discount` factor is a blunt stand-in for both), spot pricing, and
the fact that bins are packed on nodes all move the real number.
Rightsizing pods does not shrink the nodes under them, either; the
report says that out loud when it finds waste.

And the obvious one: cloudtrim reads and reports. It never touches a
cluster. Every suggestion is a line in a report, or a dry-run snippet,
for a human to apply.

## Layout

```
src/cloudtrim/
  cli.py         argument parsing and the exit codes
  snapshot.py    snapshot validation, plus the kubectl reader
  prometheus.py  range-query reader for usage history and requests
  analyze.py     p95, verdicts, and the suggestions
  pricing.py     built-in rates, --pricing-file, --discount
  report.py      text, markdown, and JSON output
  apply.py       the --apply dry-run snippets
  diff.py        comparing two snapshots
tests/           the suite, 72 cases, Prometheus covered with recorded fixtures
examples/        sample-snapshot.json, and the exporter CronJob example
action.yml       the GitHub Action
```

No runtime dependencies, standard library only.

## Install

On PyPI as `cloudtrim-cli` (the plain name belongs to someone else's
package):

```
pipx install cloudtrim-cli
```

or `pip install cloudtrim-cli`. The wheel is also attached to the
[v0.2.1 release](https://github.com/SachuJames/cloudtrim/releases/tag/v0.2.1),
and a container image is on GHCR as `ghcr.io/sachujames/cloudtrim:v0.2.1`.

## License

MIT. See [LICENSE](LICENSE).
