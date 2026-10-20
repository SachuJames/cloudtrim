#!/bin/sh
# Build one cloudtrim snapshot JSON from the pod spec and metrics-server.
#
# Needs kubectl (with a kubeconfig that can read every namespace) and jq.
# Each run captures a single sample per container: right now, not a
# history. The CronJob in this directory runs it hourly into a volume;
# keep the files and you have a history you can --diff. For real history
# without the file juggling, cloudtrim --from-prometheus is the better
# answer.
#
# Usage: snapshot.sh [output path]   (default: snapshot.json)
set -eu

out="${1:-snapshot.json}"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
epoch="$(date +%s)"
cluster="${CT_CLUSTER:-live}"

pods="$(mktemp)"
top="$(mktemp)"
trap 'rm -f "$pods" "$top"' EXIT

kubectl get pods -A -o json > "$pods"

# kubectl top prints a header, then:
# NAMESPACE  POD  NAME(container)  CPU(cores)  MEMORY(bytes)
kubectl top pods -A --containers | awk '
  NR > 1 {
    printf "%s{\"ns\":\"%s\",\"pod\":\"%s\",\"container\":\"%s\",\"cpu\":\"%s\",\"mem\":\"%s\"}",
      (n++ ? "," : ""), $1, $2, $3, $4, $5
  }
  END { print "" }
' > "$top"
usage="[$(cat "$top")]"

jq -n \
  --arg cluster "$cluster" \
  --arg now "$now" \
  --argjson epoch "$epoch" \
  --argjson usage "$usage" \
  --slurpfile pods "$pods" '
  def cpu_m:
    if endswith("m") then (rtrimstr("m") | tonumber)
    else (tonumber * 1000) end;
  def mem_mi:
    if endswith("Gi") then (rtrimstr("Gi") | tonumber * 1024)
    elif endswith("Mi") then (rtrimstr("Mi") | tonumber)
    elif endswith("Ki") then (rtrimstr("Ki") | tonumber / 1024)
    else (tonumber / 1048576) end;
  ($usage | map({key: (.ns + "/" + .pod + "/" + .container), value: .})
     | from_entries) as $use
  | {
      cluster: $cluster,
      captured_at: $now,
      containers: [
        $pods[0].items[] as $pod
        | $pod.spec.containers[]
        | select(.resources.requests.cpu or .resources.requests.memory)
        | ($use[($pod.metadata.namespace + "/" + $pod.metadata.name + "/" + .name)]
             // null) as $u
        | {
            namespace: $pod.metadata.namespace,
            pod: $pod.metadata.name,
            container: .name,
            cpu_request_m: ((.resources.requests.cpu // "0") | cpu_m),
            mem_request_mi: ((.resources.requests.memory // "0") | mem_mi),
            cpu_limit_m: (if .resources.limits.cpu
                          then (.resources.limits.cpu | cpu_m) else null end),
            mem_limit_mi: (if .resources.limits.memory
                           then (.resources.limits.memory | mem_mi) else null end),
            labels: ($pod.metadata.labels // {}),
            samples: (if $u
                      then [{cpu_m: ($u.cpu | cpu_m),
                             mem_mi: ($u.mem | mem_mi), t: $epoch}]
                      else [] end)
          }
      ]
    }
' > "$out"

echo "wrote $out"
