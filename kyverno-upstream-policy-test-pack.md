# Kyverno Upstream Policy — Test Pack

Test manifests for every upstream policy under `kyverno-policies/files/upstream/`, written in the pod format from `deny-2-disallowed.yaml`.

Each policy gets:

- a **DENY** manifest — should be **rejected** by the admission webhook
- an **ALLOW** manifest — should be **accepted** (control case, proves the policy isn't blanket-blocking)

Target namespace: `beats`. Pull secret: `artifactory-sync`.

---

## 1. Before you start

### 1.1 Confirm the policies are loaded and in Enforce mode

The estate uses `ValidatingPolicy` (VPOL), not `ClusterPolicy`:

```bash
kubectl get vpol
kubectl get vpol -o custom-columns=NAME:.metadata.name,ACTION:.spec.validationActions
```

Anything in `Audit` will **not** reject the DENY pods — it will create them and emit a `PolicyReport` instead. Check reports with:

```bash
kubectl get policyreport -n beats
kubectl get polr -n beats -o yaml | grep -A5 'result: fail'
```

### 1.2 Check for exceptions that would mask a result

The `beats` namespace is self-service for exceptions, so a stale `PolicyException` can make a policy look broken when it's actually being deliberately skipped:

```bash
kubectl get polex -n beats
kubectl get polex -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,POLICIES:.spec.exceptions[*].policyName
```

### 1.3 Use server-side dry run

Every test below is designed to run with `--dry-run=server`. This sends the object through the full admission chain (including Kyverno) but **never persists it** — so nothing is scheduled, nothing pulls an image, and there's no cleanup.

```bash
kubectl apply -f pe-deny-host-ports-01.yaml --dry-run=server
```

Expected shape of a working deny:

```
Error from server: error when creating "pe-deny-host-ports-01.yaml":
admission webhook "vpol.validate.kyverno.svc-fail" denied the request:
disallow-host-ports: Use of host ports is disallowed. ...
```

Expected shape of a working allow:

```
pod/pe-allow-host-ports-01 created (server dry run)
```

> **Because of dry-run, the `image:` value is never pulled.** Substitute your own mirrored tag anyway so the manifests stay usable if you ever want to apply them for real. Placeholder used throughout: `sam.jfrog.io/<repo>/filebeat:<tag>`.

### 1.4 Offline alternative

If you'd rather not touch a live cluster (useful for the tests that depend on feature gates — see §4.4):

```bash
kyverno apply files/upstream/pod-security-vpol/ --resource ./tests/
```

---

## 2. Results tracker

| # | Policy | Set | Deny result | Allow result | Notes |
|---|--------|-----|-------------|--------------|-------|
| 1 | `disallow-host-ports` | baseline | | | |
| 2 | `disallow-host-process` | baseline | | | needs `hostNetwork: true` |
| 3 | `disallow-privileged-containers` | baseline | | | trips 2 policies |
| 4 | `disallow-proc-mount` | baseline | | | feature-gate dependent |
| 5 | `disallow-selinux` | baseline | | | |
| 6 | `restrict-sysctls` | baseline | | | |
| 7 | `disallow-privilege-escalation` | restricted | | | |
| 8 | `require-run-as-non-root-user` | restricted | | | |
| 9 | `require-run-as-nonroot` | restricted | | | |
| 10 | `restrict-seccomp-strict` | restricted | | | |
| 11 | `disallow-secrets-from-env-vars` | other | | | |
| 12 | `restrict-sa-automount-sa-token` | other | | | check matched kind |
| 13 | `restrict-binding-clusteradmin` | other | | | **not a Pod** |
| 14 | `restrict-binding-system-groups` | other | | | **not a Pod** |
| 15 | `restrict-clusterrole-nodesproxy` | other | | | **not a Pod** |
| 16 | `restrict-escalation-verbs-roles` | other | | | **not a Pod** |
| 17 | `restrict-secret-role-verbs` | other | | | **not a Pod** |
| 18 | `restrict-wildcard-resources` | other | | | **not a Pod** |
| 19 | `restrict-wildcard-verbs` | other | | | **not a Pod** |

---

## 3. The clean baseline pod

Every DENY manifest below is this pod plus **exactly one** violation, so the denial message is unambiguous about which policy fired. Every ALLOW manifest is this pod plus the compliant version of the same field.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-baseline-clean-00
  namespace: beats
  labels:
    test: kyverno-policy-check
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**Apply this one first.** If the clean pod is rejected, something other than your intended violation is firing and every result below will be misleading.

### Two deliberate changes from your original manifest

| Field | Yours | Here | Why |
|---|---|---|---|
| `spec.securityContext.runAsNonRoot` | `false` | `true` | Pod-level `false` is a latent trip-hazard for `require-run-as-nonroot` if a future container in the pod omits its own override |
| `seccompProfile` | absent | `RuntimeDefault` (both levels) | **Your original pod would fail `restrict-seccomp-strict`** — the restricted profile requires the type to be *explicitly* set, absent is a violation |

That second row is worth a look independently of this test run: if `deny-2-disallowed.yaml` is a fixture you use elsewhere, it's currently failing two policies rather than the one it's named for.

---

## 4. Pod Security — Baseline

### 4.1 `disallow-host-ports`

Blocks any container declaring `hostPort` (or requires it to be `0`).

**DENY** — `pe-deny-host-ports-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-host-ports-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-host-ports
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    ports:
    - name: metrics
      containerPort: 5066
      hostPort: 5066          # <-- VIOLATION
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — `pe-allow-host-ports-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-allow-host-ports-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-host-ports
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    ports:
    - name: metrics
      containerPort: 5066     # containerPort only — compliant
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

---

### 4.2 `disallow-host-process`

Blocks Windows HostProcess containers (`windowsOptions.hostProcess: true`).

> **Read this before running.** The Kubernetes API server validates HostProcess pods *before* the Kyverno webhook sees them, and it enforces two rules: `hostNetwork` must be `true`, and all containers must agree on the `hostProcess` setting. So the DENY pod has to carry `hostNetwork: true` to reach Kyverno at all. If you also have a `disallow-host-namespaces` policy loaded (not in the current tree — see §7), both will fire.

**DENY** — `pe-deny-host-process-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-host-process-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-host-process
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  nodeSelector:
    kubernetes.io/os: windows
  hostNetwork: true           # required by API validation for HostProcess
  securityContext:
    windowsOptions:
      hostProcess: true       # <-- VIOLATION
      runAsUserName: "NT AUTHORITY\\SYSTEM"
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      windowsOptions:
        hostProcess: true     # must match pod-level
```

**ALLOW** — `pe-allow-host-process-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-allow-host-process-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-host-process
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
      windowsOptions:
        hostProcess: false    # explicitly false — compliant
```

---

### 4.3 `disallow-privileged-containers`

Blocks `securityContext.privileged: true`.

> **This DENY pod necessarily violates two policies.** Kubernetes rejects `privileged: true` combined with `allowPrivilegeEscalation: false` as an invalid spec, so the manifest has to set escalation to `true` — which also trips `disallow-privilege-escalation` (§5.1). Expect both policy names in the denial message. That's correct behaviour, not a bug. Isolate it by testing this one against the baseline set alone via the Kyverno CLI if you need a clean single-policy result.

**DENY** — `pe-deny-privileged-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-privileged-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-privileged-containers
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: false
    runAsUser: 0
    runAsGroup: 0
    fsGroup: 10000
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: true              # <-- VIOLATION
      allowPrivilegeEscalation: true
      runAsNonRoot: false
      runAsUser: 0
      readOnlyRootFilesystem: false
```

**ALLOW** — `pe-allow-privileged-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-allow-privileged-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-privileged-containers
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false             # explicitly false — compliant
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

---

### 4.4 `disallow-proc-mount`

Requires `procMount` to be unset or `Default`.

> **This one may not reach Kyverno.** `procMount: Unmasked` depends on the `ProcMountType` feature gate, and the API server applies its own validation to it. On a cluster where the gate is off, you'll get an API-server rejection rather than a Kyverno denial — which tells you nothing about the policy. **If the error message doesn't name the webhook, verify this policy offline with `kyverno apply` instead** (§1.4). Worth confirming the gate state on your 1.36 clusters before you interpret the result.

**DENY** — `pe-deny-proc-mount-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-proc-mount-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-proc-mount
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      procMount: Unmasked           # <-- VIOLATION
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — `pe-allow-proc-mount-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-allow-proc-mount-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-proc-mount
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      procMount: Default            # explicit Default — compliant
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

---

### 4.5 `disallow-selinux`

`seLinuxOptions.type` must be one of `container_t`, `container_init_t`, `container_kvm_t`, `container_engine_t` (or unset), and `user`/`role` must be unset.

**DENY** — `pe-deny-selinux-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-selinux-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-selinux
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      seLinuxOptions:
        type: unconfined_t          # <-- VIOLATION (disallowed type)
        user: system_u              # <-- VIOLATION (user must be unset)
        role: system_r              # <-- VIOLATION (role must be unset)
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — `pe-allow-selinux-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-allow-selinux-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-selinux
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      seLinuxOptions:
        type: container_t           # permitted type, no user/role
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

---

### 4.6 `restrict-sysctls`

Only the PSS safe list is permitted: `kernel.shm_rmid_forced`, `net.ipv4.ip_local_port_range`, `net.ipv4.ip_unprivileged_port_start`, `net.ipv4.tcp_syncookies`, `net.ipv4.ping_group_range`, `net.ipv4.ip_local_reserved_ports`.

**DENY** — `pe-deny-sysctls-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-sysctls-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-sysctls
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
    sysctls:
    - name: kernel.msgmax           # <-- VIOLATION (unsafe sysctl)
      value: "65536"
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — `pe-allow-sysctls-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-allow-sysctls-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-sysctls
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
    sysctls:
    - name: net.ipv4.ip_local_port_range   # on the safe list — compliant
      value: "32768 60999"
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

---

## 5. Pod Security — Restricted

### 5.1 `disallow-privilege-escalation`

`allowPrivilegeEscalation` must be explicitly `false` on every container.

**DENY** — `pe-deny-priv-escalation-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-priv-escalation-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-privilege-escalation
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: true    # <-- VIOLATION
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — `pe-allow-priv-escalation-01.yaml`

Use the clean baseline pod from §3 with `name: pe-allow-priv-escalation-01`. Its `allowPrivilegeEscalation: false` is already the compliant case.

> Worth a second DENY variant here: **omit** `allowPrivilegeEscalation` entirely rather than setting it `true`. The restricted profile treats *absent* as a violation too, and that's the failure mode teams actually hit in the wild. If the omitted-field pod is accepted, your policy is only catching the explicit `true` case.

---

### 5.2 `require-run-as-non-root-user`

`runAsUser` must not be `0`.

**DENY** — `pe-deny-runasuser-zero-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-runasuser-zero-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: require-run-as-non-root-user
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 0                     # <-- VIOLATION (UID 0)
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — clean baseline pod from §3, `name: pe-allow-runasuser-zero-01` (`runAsUser: 10000`).

---

### 5.3 `require-run-as-nonroot`

`runAsNonRoot` must be `true` at pod or container level. Deliberately uses a non-zero UID so it doesn't overlap with §5.2.

**DENY** — `pe-deny-runasnonroot-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-runasnonroot-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: require-run-as-nonroot
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: false                # <-- VIOLATION (pod level)
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: false              # <-- VIOLATION (container level)
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**ALLOW** — clean baseline pod from §3, `name: pe-allow-runasnonroot-01`.

> A second useful DENY variant: pod-level `false` with the container-level field **omitted** (not `false`). Upstream `require-run-as-nonroot` accepts a container override of `true`, so an omitted container field should inherit the pod's `false` and be denied. This is exactly the shape of your original `deny-2-disallowed.yaml`, which sets pod `false` / container `true` — and therefore **passes** this policy. If you expected that fixture to be denied by this rule, it won't be.

---

### 5.4 `restrict-seccomp-strict`

`seccompProfile.type` must be explicitly `RuntimeDefault` or `Localhost`. Absent is a violation.

**DENY (explicit Unconfined)** — `pe-deny-seccomp-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-seccomp-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-seccomp-strict
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: Unconfined                 # <-- VIOLATION
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: Unconfined               # <-- VIOLATION
```

**DENY (omitted — the important one)** — `pe-deny-seccomp-02.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-seccomp-02
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-seccomp-strict
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    # seccompProfile deliberately absent   <-- VIOLATION under strict
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      # seccompProfile deliberately absent <-- VIOLATION under strict
```

**ALLOW** — clean baseline pod from §3, `name: pe-allow-seccomp-01`.

---

## 6. Other VPOL

### 6.1 `disallow-secrets-from-env-vars`

Blocks `env[].valueFrom.secretKeyRef` and `envFrom[].secretRef`. The referenced Secret does **not** need to exist — admission runs before the reference is resolved.

**DENY (secretKeyRef)** — `pe-deny-secret-env-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-secret-env-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-secrets-from-env-vars
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    env:
    - name: ES_PASSWORD
      valueFrom:
        secretKeyRef:                  # <-- VIOLATION
          name: elastic-credentials
          key: password
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**DENY (envFrom.secretRef)** — `pe-deny-secret-env-02.yaml`

Same pod, `name: pe-deny-secret-env-02`, with the `env:` block replaced by:

```yaml
    envFrom:
    - secretRef:                       # <-- VIOLATION
        name: elastic-credentials
```

Run both. Some policy revisions only check one of the two paths — that gap is the whole point of testing this pair.

**ALLOW** — `pe-allow-secret-env-01.yaml`

Same pod, `name: pe-allow-secret-env-01`, with:

```yaml
    env:
    - name: ES_HOST
      value: "https://elastic.internal:9200"
    envFrom:
    - configMapRef:
        name: filebeat-config
```

---

### 6.2 `restrict-sa-automount-sa-token`

The name suggests this targets **ServiceAccount**, but some revisions of this policy match Pods as well. Test both kinds and note which one actually fires — that answers a question you'll need for exception design anyway.

**DENY (Pod variant)** — `pe-deny-sa-automount-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-sa-automount-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-sa-automount-sa-token
spec:
  automountServiceAccountToken: true   # <-- VIOLATION
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
```

**DENY (ServiceAccount variant)** — `pe-deny-sa-automount-02.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pe-deny-sa-automount-02
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-sa-automount-sa-token
automountServiceAccountToken: true     # <-- VIOLATION
```

**ALLOW (ServiceAccount)** — `pe-allow-sa-automount-02.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pe-allow-sa-automount-02
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-sa-automount-sa-token
automountServiceAccountToken: false
```

**ALLOW (Pod)** — clean baseline pod from §3, `name: pe-allow-sa-automount-01` (already `false`).

---

## 7. RBAC policies — these cannot be tested with a Pod

Seven of the nine `other-vpol` policies match `Role`, `ClusterRole`, `RoleBinding`, and `ClusterRoleBinding` — not `Pod`. A pod manifest will sail past them regardless of what it contains, so a "test passed" from a pod tells you nothing. The manifests below are the correct kinds.

> ### Two cautions
>
> 1. **Always use `--dry-run=server` for this section.** If any of these policies is in `Audit` rather than `Enforce`, applying for real would create a genuine `cluster-admin` binding.
> 2. **RBAC escalation prevention may reject these before Kyverno sees them.** Kubernetes won't let you create a Role granting permissions you don't hold yourself, unless you have the `escalate` verb. If you get a `"attempt to grant extra privileges"` error naming your own user rather than the webhook, that's the API server, not the policy — retry with a sufficiently privileged identity or verify via `kyverno apply` offline.

### 7.1 `restrict-binding-clusteradmin`

**DENY** — `pe-deny-binding-clusteradmin-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: pe-deny-binding-clusteradmin-01
  labels:
    test: kyverno-policy-check
    policy: restrict-binding-clusteradmin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin                  # <-- VIOLATION
subjects:
- kind: ServiceAccount
  name: filebeat
  namespace: beats
```

**ALLOW** — `pe-allow-binding-clusteradmin-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: pe-allow-binding-clusteradmin-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-binding-clusteradmin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view                           # non-admin role — compliant
subjects:
- kind: ServiceAccount
  name: filebeat
  namespace: beats
```

### 7.2 `restrict-binding-system-groups`

**DENY** — `pe-deny-binding-system-groups-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: pe-deny-binding-system-groups-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-binding-system-groups
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- kind: Group
  name: system:masters                 # <-- VIOLATION
  apiGroup: rbac.authorization.k8s.io
```

**ALLOW** — `pe-allow-binding-system-groups-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: pe-allow-binding-system-groups-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-binding-system-groups
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- kind: ServiceAccount
  name: filebeat
  namespace: beats
```

> Check whether the policy also blocks `system:serviceaccounts:<ns>` groups. Your self-service PolicyException RBAC model binds on exactly that group pattern — if this policy matches it, your own exception RoleBindings would be denied. Add a third test with `kind: Group, name: system:serviceaccounts:beats` to find out.

### 7.3 `restrict-clusterrole-nodesproxy`

**DENY** — `pe-deny-nodesproxy-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pe-deny-nodesproxy-01
  labels:
    test: kyverno-policy-check
    policy: restrict-clusterrole-nodesproxy
rules:
- apiGroups: [""]
  resources: ["nodes/proxy"]           # <-- VIOLATION
  verbs: ["get", "list"]
```

**ALLOW** — `pe-allow-nodesproxy-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pe-allow-nodesproxy-01
  labels:
    test: kyverno-policy-check
    policy: restrict-clusterrole-nodesproxy
rules:
- apiGroups: [""]
  resources: ["nodes"]                 # nodes without /proxy — compliant
  verbs: ["get", "list"]
```

### 7.4 `restrict-escalation-verbs-roles`

**DENY** — `pe-deny-escalation-verbs-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pe-deny-escalation-verbs-01
  labels:
    test: kyverno-policy-check
    policy: restrict-escalation-verbs-roles
rules:
- apiGroups: ["rbac.authorization.k8s.io"]
  resources: ["clusterroles", "roles"]
  verbs: ["bind", "escalate"]          # <-- VIOLATION
- apiGroups: [""]
  resources: ["users", "groups", "serviceaccounts"]
  verbs: ["impersonate"]               # <-- VIOLATION
```

**ALLOW** — `pe-allow-escalation-verbs-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pe-allow-escalation-verbs-01
  labels:
    test: kyverno-policy-check
    policy: restrict-escalation-verbs-roles
rules:
- apiGroups: ["rbac.authorization.k8s.io"]
  resources: ["clusterroles", "roles"]
  verbs: ["get", "list", "watch"]      # read-only — compliant
```

### 7.5 `restrict-secret-role-verbs`

**DENY** — `pe-deny-secret-role-verbs-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pe-deny-secret-role-verbs-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-secret-role-verbs
rules:
- apiGroups: [""]
  resources: ["secrets"]               # <-- VIOLATION
  verbs: ["get", "list", "watch"]
```

**ALLOW** — `pe-allow-secret-role-verbs-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pe-allow-secret-role-verbs-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-secret-role-verbs
rules:
- apiGroups: [""]
  resources: ["configmaps"]            # not secrets — compliant
  verbs: ["get", "list", "watch"]
```

### 7.6 `restrict-wildcard-resources`

**DENY** — `pe-deny-wildcard-resources-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pe-deny-wildcard-resources-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-wildcard-resources
rules:
- apiGroups: [""]
  resources: ["*"]                     # <-- VIOLATION
  verbs: ["get", "list"]
```

**ALLOW** — `pe-allow-wildcard-resources-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pe-allow-wildcard-resources-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-wildcard-resources
rules:
- apiGroups: [""]
  resources: ["pods", "configmaps"]    # explicit list — compliant
  verbs: ["get", "list"]
```

> Also worth testing `apiGroups: ["*"]` as a separate DENY. Several revisions of this policy check resources and apiGroups independently.

### 7.7 `restrict-wildcard-verbs`

**DENY** — `pe-deny-wildcard-verbs-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pe-deny-wildcard-verbs-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-wildcard-verbs
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["*"]                         # <-- VIOLATION
```

**ALLOW** — `pe-allow-wildcard-verbs-01.yaml`

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pe-allow-wildcard-verbs-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: restrict-wildcard-verbs
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]      # explicit verbs — compliant
```

---

## 8. Appendix — hostPath

Your example fixture is named `pe-deny-disallowed-hostpath-46`, but **`disallow-host-path` is not in the baseline folder** in the tree you sent. Neither is `disallow-capabilities` or `disallow-host-namespaces`. Either they're vendored somewhere else (a custom or merged policy — your `baseline-privileged-workloads` VPOL covers hostNetwork/hostPID), or the sync script didn't pick them up.

Worth checking before the test run, since the hostPath rule is one of the two PSS Baseline policies in your VAP PoC:

```bash
grep -rl "hostPath\|host-path" files/upstream/
kubectl get vpol | grep -E 'host-path|capabilities|host-namespaces'
```

If it exists, here's the test in your format:

**DENY** — `pe-deny-hostpath-01.yaml`

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pe-deny-hostpath-01
  namespace: beats
  labels:
    test: kyverno-policy-check
    policy: disallow-host-path
spec:
  automountServiceAccountToken: false
  imagePullSecrets:
  - name: artifactory-sync
  tolerations:
  - operator: Exists
  securityContext:
    runAsNonRoot: true
    runAsUser: 10000
    runAsGroup: 10000
    fsGroup: 10000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: filebeat
    image: sam.jfrog.io/<repo>/filebeat:<tag>
    command: ["sleep", "300"]
    volumeMounts:
    - name: badpath
      mountPath: /host/var/log
    securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 10000
      runAsGroup: 10000
      capabilities:
        drop: ["ALL"]
      readOnlyRootFilesystem: false
      seccompProfile:
        type: RuntimeDefault
  volumes:
  - name: badpath
    hostPath:                          # <-- VIOLATION
      path: /var/log
      type: Directory
```

**ALLOW** — `pe-allow-hostpath-01.yaml`

Same pod, `name: pe-allow-hostpath-01`, with the volume replaced by:

```yaml
  volumes:
  - name: badpath
    emptyDir: {}                       # non-host volume — compliant
```

> Note your original fixture declares `volumeMounts` referencing `badpath` but the `volumes:` block was cut off at line 34. If the volume is missing entirely, the API server rejects the pod for an unresolved mount reference before Kyverno is consulted — worth confirming that fixture is complete.

---

## 9. Running the whole set

```bash
# Split this file into individual manifests first, or paste each block into its own file.
# Then:

for f in pe-deny-*.yaml; do
  echo "=== $f ==="
  kubectl apply -f "$f" --dry-run=server 2>&1 | tail -3
  echo
done

for f in pe-allow-*.yaml; do
  echo "=== $f ==="
  kubectl apply -f "$f" --dry-run=server 2>&1 | tail -3
  echo
done
```

Read it as: every `pe-deny-*` should produce a webhook error naming its policy, every `pe-allow-*` should produce `created (server dry run)`.

Three failure modes to watch for, in order of how often they catch people out:

1. **A deny is accepted** — policy is in `Audit`, or a `PolicyException` in `beats` covers it, or the policy isn't loaded at all. Check §1.1 and §1.2 before assuming the policy logic is wrong.
2. **An allow is rejected** — a different policy is firing. Read the webhook message for the policy name; it's usually `restrict-seccomp-strict` catching an omitted field.
3. **The error doesn't name a webhook** — that's the API server rejecting the spec before admission (see §4.2, §4.4, §7). Fall back to `kyverno apply`.

## 10. Cleanup

Not needed if you stuck to `--dry-run=server`. If you applied anything for real:

```bash
kubectl delete pod,serviceaccount -n beats -l test=kyverno-policy-check
kubectl delete role,rolebinding -n beats -l test=kyverno-policy-check
kubectl delete clusterrole,clusterrolebinding -l test=kyverno-policy-check
```
