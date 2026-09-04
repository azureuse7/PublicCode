# Inspecting hostPath / hostPort policy violations

Commands for pulling the container name, image, hostPath volumes and host ports out of a
pod (or the workload that owns it), for writing up Kyverno policy violations and exceptions.

Each container's `volumeMounts` are mapped back to the pod's `hostPath` volumes, so you get
the actual host paths **per container** rather than the flat list the policy report gives you.

Requires `kubectl`, `jq` and `column`.

---

## 1. Table output

Drop this into your shell profile:

```bash
kpol() {
  local pod="$1" ns="${2:-default}"
  kubectl get pod "$pod" -n "$ns" -o json | jq -r '
    def orelse(d): if . == "" then d else . end;
    (.spec.template.spec // .spec) as $s
    | ([$s.volumes[]? | select(.hostPath) | {(.name): .hostPath.path}] | add // {}) as $hp
    | [ ($s.initContainers[]? + {kind:"init"}), ($s.containers[]? + {kind:"container"}) ]
    | [["KIND","CONTAINER","IMAGE","HOSTPATH -> MOUNTPATH","HOSTPORT"]]
      + map([ .kind, .name, .image,
              ([.volumeMounts[]? | select($hp[.name]) | "\($hp[.name]) -> \(.mountPath)"] | join(" ; ") | orelse("-")),
              ([.ports[]? | select(.hostPort) | "\(.hostPort)/\(.protocol // "TCP")"] | join(",") | orelse("-")) ])
    | .[] | @tsv' | column -t -s $'\t'
}
```

Usage:

```bash
kpol csi-blob-node-x7k2p kube-system
```

---

## 2. Block output

For `csi-blob-node` the hostPath column gets very wide, so this format reads better when
you're pasting into an exception or a writeup:

```bash
kubectl get pod "$POD" -n "$NS" -o json | jq -r '
  def orelse(d): if . == "" then d else . end;
  (.spec.template.spec // .spec) as $s
  | ([$s.volumes[]? | select(.hostPath) | {(.name): .hostPath.path}] | add // {}) as $hp
  | (($s.initContainers[]? + {kind:"init"}), ($s.containers[]? + {kind:"container"}))
  | "[\(.kind)] \(.name)\n  image: \(.image)\n  hostPaths:\n"
    + ([.volumeMounts[]? | select($hp[.name])
        | "    - \($hp[.name])  ->  \(.mountPath)\(if .readOnly then "  (ro)" else "" end)"]
       | join("\n") | orelse("    (none)"))
    + "\n  hostPorts: "
    + ([.ports[]? | select(.hostPort) | "\(.hostPort)/\(.protocol // "TCP")"] | join(", ") | orelse("none")) + "\n"'
```

---

## Notes

### Runs against the workload too

The `(.spec.template.spec // .spec)` line means the same script works unchanged against the
owning object. Usually what you want here, since it's a DaemonSet and you don't have to hunt
for a live pod name:

```bash
kubectl get ds csi-blob-node -n kube-system -o json | jq -r '<same script>'
```

Works for Deployments and StatefulSets as well.

### hostNetwork and host ports

`csi-blob-node` runs with `hostNetwork: true`, so `hostPort` is often unset even though the
ports still bind on the node. `disallow-host-ports` only checks
`spec.containers[*].ports[*].hostPort`, so the command above matches what the policy sees.

For the full picture, drop the `select(.hostPort)` filter and print `"\(.containerPort)"`
instead.

### Init containers

Init containers are included and tagged `init`, since they trip the same policies as regular
containers — `install-blobfuse-proxy` is the one doing most of the hostPath mounting on
`csi-blob-node`.
