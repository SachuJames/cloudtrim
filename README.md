<div align="center">

<img src="docs/assets/banner.svg" alt="cloudtrim" width="100%"/>

[![CI](https://github.com/SachuJames/cloudtrim/actions/workflows/ci.yml/badge.svg)](https://github.com/SachuJames/cloudtrim/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SachuJames/cloudtrim)](https://github.com/SachuJames/cloudtrim/releases)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

**[Release v0.1.0](https://github.com/SachuJames/cloudtrim/releases/tag/v0.1.0)** · **[Image on GHCR](https://github.com/SachuJames/cloudtrim/pkgs/container/cloudtrim)**

Your cluster knows what each pod requested. It also knows what each pod
actually used. cloudtrim puts the two side by side, suggests request
values that fit the real numbers, and tells you roughly what the
difference costs you every month.

</div>

## Quickstart

```
pipx install git+https://github.com/SachuJames/cloudtrim.git
cloudtrim --from-file examples/sample-snapshot.json
```

That runs against a small sample cluster that ships with the repo,
twelve containers across three namespaces, a few of them badly
over-provisioned. This is the tool's real output:

```
    platform worker-8d2f6a4c9-h3j5/worker cpu   4,000m p95    700m -> 910m     mem      8Gi p95 2,100Mi -> 2,736Mi      over waste   $165.28
        shop checkout-5c8a7e2d1-m4k8/checkout cpu   2,000m p95    380m -> 500m     mem      2Gi p95   600Mi -> 784Mi        over waste    $67.60
        shop api-6d9f8c5b4-x2v1/api       cpu   1,000m p95    225m -> 300m     mem      1Gi p95   495Mi -> 656Mi        over waste    $29.52
    platform report-3e7b1d5f8-s6r4/report cpu     500m p95     90m -> 120m     mem      1Gi p95   300Mi -> 400Mi        over waste    $19.90
        shop token-4a9c2e8b6-t5n7/token   cpu     100m p95     40m -> 60m      mem    128Mi p95    55Mi -> 80Mi         over waste     $1.93
       batch exporter-7a4c8e2b5-n8q1/exporter cpu      50m p95     12m -> 20m      mem     64Mi p95    20Mi -> 32Mi         over waste     $1.41
        shop web-7f4b9d6c8-p9q3/web       cpu     500m p95    325m -> 500m     mem    512Mi p95   400Mi -> 512Mi          ok waste     $0.00
        shop auth-6b3d5f9a7-z8w2/auth     cpu     250m p95    228m -> 300m     mem    256Mi p95   180Mi -> 256Mi       tight waste     $0.00
    platform scheduler-9c5a3b7e2-v1x9/scheduler cpu     100m p95     88m -> 120m     mem    128Mi p95    70Mi -> 128Mi       tight waste     $0.00
       batch etl-2f8d4c6a1-b7m3/etl       cpu   1,500m p95  1,250m -> 1,500m   mem      3Gi p95 1,900Mi -> 3Gi            ok waste     $0.00
       batch cache-5b1e9d3f7-k2p6/redis   cpu     200m p95    150m -> 200m     mem    512Mi p95   470Mi -> 624Mi       tight waste     $0.00
       batch ingest-1d6f3a9c4-w4t8/ingest cpu     800m p95    700m -> 910m     mem      1Gi p95   600Mi -> 1Gi         tight waste     $0.00

12 containers | cpu 11,000m -> 5,440m | mem 17,984Mi -> 10,304Mi | waste $285.64/mo (49.7% of requested)
note: tight containers would add $7.75/mo if their suggestions were applied
pricing: estimates only, on-demand list price, us-east-1, as of 2026-10
```

$285 a month going to containers that asked for far more than they use.
One command, no account, no cloud bill involved.

## How it decides

CPU and memory are judged separately, because a container can waste CPU
and starve on memory at the same time.

- **over**: the request is more than twice the p95 usage. The suggestion
  is p95 plus 30 percent headroom, rounded up to a manifest-sensible step
  (10m of CPU, 16Mi of memory), floored at 10m/16Mi, and never raised
  above the current request.
- **tight**: p95 usage sits above 85 percent of the request. A raise to
  p95 times 1.3 is suggested, but it is reported as extra cost, not
  folded silently into the savings.
- **ok**: close enough to leave alone.

p95 comes from your samples with a simple nearest-rank percentile. The
quality of every answer here is set by the quality of those samples.

## The cost model

Each region has a per vCPU-hour and per GiB-hour rate built in, and the
monthly number is just the hourly cost times 730 hours. The built-ins are
on-demand list-price approximations for us-east-1 and ap-south-1, dated
2026-10. They are there to make the waste concrete, not to predict your
bill.

Bring your own rates any time:

```
cloudtrim --from-file snapshot.json --region ap-south-1
cloudtrim --from-file snapshot.json --pricing-file my-rates.json
```

A pricing file is JSON with `vcpu_hour`, `gib_hour`, and optionally
`as_of`.

## Feeding it data

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
      "samples": [{"cpu_m": 210, "mem_mi": 480}]
    }
  ]
}
```

Limits are optional. Malformed entries fail fast and name the entry, so a
bad exporter feed does not silently poison the numbers.

**A live cluster** goes through kubectl:

```
cloudtrim --from-cluster
```

Requests and limits come from the pod spec, usage from metrics-server.
That yields exactly one sample per container, right now, not a week of
history. The report says the sample count per container, take the
single-sample answers as a first look, not a verdict.

## Reports and CI gating

Text goes to stdout by default. The other formats:

```
cloudtrim --from-file snapshot.json --format json > report.json
cloudtrim --from-file snapshot.json --format markdown   # paste into an issue
```

In CI, gate on the waste share of requested cost:

```
cloudtrim --from-file snapshot.json --fail-on-waste-pct 25
```

Exits 1 when waste is over 25 percent, so a PR that doubles a request
gets caught the same way a failing test does.

## Honest limits

The answers are only as good as the samples. A snapshot from an idle
weekend will tell you to cut everything; a snapshot from a deploy storm
will tell you to raise everything. The kubectl mode sees one moment in
time.

The dollars are estimates. On-demand list prices in two regions, dated,
nothing more. Reserved instances, savings plans, spot pricing, and the
fact that bins are packed on nodes all move the real number.

And the obvious one: cloudtrim reads and reports. It never touches a
cluster. Every suggestion is a line in a report for a human to apply.

## Layout

```
src/cloudtrim/
  cli.py       argument parsing and the exit codes
  snapshot.py  snapshot validation, plus the kubectl reader
  analyze.py   p95, verdicts, and the suggestions
  pricing.py   built-in rates and --pricing-file
  report.py    text, markdown, and JSON output
tests/         the suite, 26 cases
examples/      sample-snapshot.json, the quickstart cluster
```

No runtime dependencies, standard library only.

## Install

PyPI is coming under the name `cloudtrim-cli` (the plain name belongs to
someone else's package). Until then:

```
pipx install git+https://github.com/SachuJames/cloudtrim.git
```

or download the wheel from the
[v0.1.0 release](https://github.com/SachuJames/cloudtrim/releases/tag/v0.1.0)
and `pip install cloudtrim_cli-0.1.0-py3-none-any.whl`. A container image
is on GHCR as `ghcr.io/sachujames/cloudtrim:v0.1.0`.

## License

MIT. See [LICENSE](LICENSE).
