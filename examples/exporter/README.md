# Snapshot exporter (example)

cloudtrim judges whatever you feed it, so somebody has to write the
snapshot. This is the small version of that somebody.

- `snapshot.sh` reads the pod spec and `kubectl top`, and writes one
  snapshot JSON: requests, limits, pod labels, and one usage sample per
  container. It needs `kubectl` with read access to every namespace,
  plus `jq`.
- `cronjob.yaml` runs it hourly: a ServiceAccount with get/list on pods
  and the metrics API, the script mounted from a ConfigMap (create it
  with the command below), and a 1Gi volume that keeps the latest
  snapshot at `/data/snapshot.json`.

```
kubectl create configmap cloudtrim-exporter \
  --from-file=snapshot.sh -n platform
kubectl apply -f cronjob.yaml
```

Then, whenever you like:

```
kubectl cp platform/<exporter-pod>:/data/snapshot.json ./snapshot.json
cloudtrim --from-file snapshot.json
cloudtrim --from-file snapshot.json --diff last-week.json
```

Honest notes: each run still captures a single moment, so an hourly
file is an hourly sample, not a peak. Keeping older snapshots under
different names is what makes `--diff` useful; the CronJob as written
overwrites one file. And this example has not been run against a live
cluster from cloudtrim's own repo (there is no cluster in its test
setup); the script itself is plain sh and jq, easy to dry-run by hand
with `snapshot.sh /tmp/out.json`.

If you already run Prometheus, skip all of this:
`cloudtrim --from-prometheus` reads the history directly.
