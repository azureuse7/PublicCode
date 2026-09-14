# Pod policy-violation CSV — pure kubectl (no jq)

Pulls container name, image, hostPath (mapped to its mount path), and host port out of a
pod, using only `kubectl -o go-template`. No jq, no extra binaries — kubectl bundles
[sprig](https://github.com/Masterminds/sprig) for `go-template` output, which is what makes
the volume-name → hostPath join possible.

Requires **kubectl 1.18+** (basically any cluster in current use).

---

## The function

Paste this into your terminal once, or add it to `~/.bashrc` / `~/.zshrc` so it's always
available:

```bash
kcsv() {
  local pod="$1" ns="${2:-default}" out="${3:-policy-violations.csv}"
  kubectl get pod "$pod" -n "$ns" -o go-template='
{{- $hp := dict -}}
{{- range .spec.volumes -}}
{{- if .hostPath -}}{{- $_ := set $hp .name .hostPath.path -}}{{- end -}}
{{- end -}}
{{- print "Type,Container,Image,HostPath,MountPath,ReadOnly,HostPort" -}}
{{- range $c := .spec.initContainers -}}
{{- $ports := "" -}}
{{- range $c.ports -}}{{- if .hostPort -}}{{- if eq $ports "" -}}{{- $ports = printf "%v/%s" .hostPort (default "TCP" .protocol) -}}{{- else -}}{{- $ports = printf "%s %v/%s" $ports .hostPort (default "TCP" .protocol) -}}{{- end -}}{{- end -}}{{- end -}}
{{- $any := false -}}
{{- range $c.volumeMounts -}}
{{- $path := index $hp .name -}}
{{- if $path -}}{{- $any = true -}}{{- $ro := "no" -}}{{- if .readOnly -}}{{- $ro = "yes" -}}{{- end -}}{{- printf "\ninit,%s,%s,%s,%s,%s,%s" $c.name $c.image $path .mountPath $ro $ports -}}{{- end -}}
{{- end -}}
{{- if not $any -}}{{- printf "\ninit,%s,%s,,,,%s" $c.name $c.image $ports -}}{{- end -}}
{{- end -}}
{{- range $c := .spec.containers -}}
{{- $ports := "" -}}
{{- range $c.ports -}}{{- if .hostPort -}}{{- if eq $ports "" -}}{{- $ports = printf "%v/%s" .hostPort (default "TCP" .protocol) -}}{{- else -}}{{- $ports = printf "%s %v/%s" $ports .hostPort (default "TCP" .protocol) -}}{{- end -}}{{- end -}}{{- end -}}
{{- $any := false -}}
{{- range $c.volumeMounts -}}
{{- $path := index $hp .name -}}
{{- if $path -}}{{- $any = true -}}{{- $ro := "no" -}}{{- if .readOnly -}}{{- $ro = "yes" -}}{{- end -}}{{- printf "\ncontainer,%s,%s,%s,%s,%s,%s" $c.name $c.image $path .mountPath $ro $ports -}}{{- end -}}
{{- end -}}
{{- if not $any -}}{{- printf "\ncontainer,%s,%s,,,,%s" $c.name $c.image $ports -}}{{- end -}}
{{- end -}}
{{- print "\n" -}}' > "$out"
  echo "wrote $out ($(( $(wc -l < "$out") - 1 )) rows)"
}
```

## Usage

Call it with the pod name and namespace — that's the only part you fill in:

```bash
---
kind: Pod
apiVersion: v1
metadata:
  name: sample-app-vault-read
  labels:
    app: sample
    vault-sidecar-injector: enabled
  annotations:
    vault.hashicorp.com/agent-inject: "true"
    vault.hashicorp.com/agent-inject-secret-test: "secrets/CLUSTER_NAME-bdd-test"
    vault.hashicorp.com/namespace: "KMAAS_NAMESPACE"
    vault.hashicorp.com/auth-path: "auth/VAULT_AUTH_PATH"
    vault.hashicorp.com/role: "CLUSTER_NAME-bdd-role"
    vault.hashicorp.com/agent-inject-token: "true"
    vault.hashicorp.com/agent-service-account-token-volume-name: "vault-token"
    vault.hashicorp.com/tls-skip-verify: "true"
    vault.hashicorp.com/log-level: "debug"
spec:
  serviceAccountName: bdd-serviceaccount
  automountServiceAccountToken: false
  volumes:
    - name: vault-token
      projected:
        sources:
          - serviceAccountToken:
              path: token
              expirationSeconds: 600
  containers:
    - name: sample-app-vault-read
      image: REGISTRY_DOCKER/nginx:1.27.3
      imagePullPolicy: IMAGE_PULL_POLICY
      command: ["/bin/bash"]
      args: ["-c", "cat /vault/secrets/test; sleep 3000"]
      resources:
        requests:
          memory: "100Mi"
          cpu: "100m"
        limits:
          memory: "100Mi"
          cpu: "100m"
  imagePullSecrets:
    - name: artifactory-sync
  nodeSelector:
    kubernetes.io/os: linux
  tolerations:
    - key: "CriticalAddonsOnly"
      operator: "Exists"
      effect: "NoSchedule"
```

Example:

```bash
kcsv csi-blob-node-x7k2p kube-system
```

Writes `policy-violations.csv` in the current directory. Pass a third argument for a
specific filename:

```bash
kcsv csi-blob-node-x7k2p kube-system csi-blob-node.csv
```

If you don't know the exact pod name (DaemonSet pods get a random suffix):

```bash
kubectl get pods -n kube-system | grep csi-blob-node
```

## Output shape

One row per container × hostPath mount, so it filters and sorts properly once opened in
Excel or a spreadsheet — not one crammed cell per container.

```
Type,Container,Image,HostPath,MountPath,ReadOnly,HostPort
init,install-blobfuse-proxy,mcr.microsoft.com/...blob-csi:v1.24.2,/usr,/host/usr,no,
container,liveness-probe,mcr.microsoft.com/...livenessprobe:v2.12.0,/var/lib/kubelet/plugins/blob.csi.azure.com,/csi,no,29633/TCP
container,blob,mcr.microsoft.com/...blob-csi:v1.24.2,/lib/modules,/lib/modules,yes,
```

- **Init containers** are included and tagged `init` — on `csi-blob-node`,
  `install-blobfuse-proxy` does most of the `/usr`, `/etc`, `/opt` mounting.
- A container with **no hostPath** still gets a row, with blank fields, so nothing silently
  drops out of the report.
- Multiple host ports on one container are space-separated in a single cell (e.g.
  `80/TCP 53/UDP`) — see caveats below for why.

## Caveats

- **No CSV field quoting.** Unlike jq's `@csv`, a raw go-template can't escape commas
  inside a field. Not an issue for container/image names (Kubernetes forbids commas
  there), but if a hostPath itself ever contained a comma, that row would misalign. Host
  ports are joined with a space rather than a comma for the same reason.
- **Targets a Pod directly.** To run it against the DaemonSet itself (no live pod name
  needed), replace every `.spec.` in the template with `.spec.template.spec.` and run
  `kubectl get ds csi-blob-node -n kube-system` instead of `kubectl get pod`.

## Quick terminal preview

Before opening in a spreadsheet:

```bash
column -t -s, policy-violations.csv
```
---
kind: Pod
apiVersion: v1
metadata:
  name: sample-app-vault-read
  labels:
    app: sample
    vault-sidecar-injector: enabled
  annotations:
    vault.hashicorp.com/agent-inject: "true"
    vault.hashicorp.com/agent-inject-secret-test: "secrets/CLUSTER_NAME-bdd-test"
    vault.hashicorp.com/namespace: "KMAAS_NAMESPACE"
    vault.hashicorp.com/auth-path: "auth/VAULT_AUTH_PATH"
    vault.hashicorp.com/role: "CLUSTER_NAME-bdd-role"
    vault.hashicorp.com/agent-inject-token: "true"
    vault.hashicorp.com/agent-service-account-token-volume-name: "vault-token"
    vault.hashicorp.com/tls-skip-verify: "true"
    vault.hashicorp.com/log-level: "debug"
spec:
  serviceAccountName: bdd-serviceaccount
  automountServiceAccountToken: false
  volumes:
    - name: vault-token
      projected:
        sources:
          - serviceAccountToken:
              path: token
              expirationSeconds: 600
  containers:
    - name: sample-app-vault-read
      image: REGISTRY_DOCKER/nginx:1.27.3
      imagePullPolicy: IMAGE_PULL_POLICY
      command: ["/bin/bash"]
      args: ["-c", "cat /vault/secrets/test; sleep 3000"]
      resources:
        requests:
          memory: "100Mi"
          cpu: "100m"
        limits:
          memory: "100Mi"
          cpu: "100m"
  imagePullSecrets:
    - name: artifactory-sync
  nodeSelector:
    kubernetes.io/os: linux
  tolerations:
    - key: "CriticalAddonsOnly"
      operator: "Exists"
      effect: "NoSchedule"
