# VAP Proof of Concept — Findings & Test Results

**Status:** In Progress
**Audience:** Platform / Security team
**Kyverno docs ref:** [ValidatingPolicy — 1.15](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/)

---

## 1. Syntax clarification — `ValidatingPolicy` vs `ClusterPolicy`

A common source of confusion during the PoC:

| Property | `ClusterPolicy` (`kyverno.io/v1`) | `ValidatingPolicy` (`policies.kyverno.io/v1alpha1`) |
|---|---|---|
| CEL syntax | Opt-in via `validate.cel:` | CEL by default — no special key needed |
| VAP generation | Not supported | Opt-in via `spec.autogen.validatingAdmissionPolicy.enabled: true` |
| Exception support | `PolicyException` | `PolicyException` (compiled into VAP `matchConditions`) |
| Background scan | Yes | Configurable via `spec.evaluation.background.enabled` |

**Key point:** `ValidatingPolicy` was built specifically to mirror and extend Kubernetes
`ValidatingAdmissionPolicy`. It uses CEL natively — you do not need a `validate.cel:` stanza to
instruct it to use CEL, it already does. To get Kyverno to auto-generate the native Kubernetes
`ValidatingAdmissionPolicy` (and its corresponding `ValidatingAdmissionPolicyBinding`), add the
`autogen` block under `spec` as shown in §2 below.

> **API version note:** the Kyverno 1.15 docs reference `policies.kyverno.io/v1alpha1`. Verify
> the exact version your cluster serves with `kubectl api-resources | grep validatingpolicy`
> before applying manifests.

---

## 2. Policy manifest — `disallow-host-ports` with VAP generation

```yaml
# manifests/vpol-vap-enabled.yaml
apiVersion: policies.kyverno.io/v1alpha1   # verify against your cluster
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports
  annotations:
    policies.kyverno.io/title: Disallow hostPorts
    policies.kyverno.io/category: Pod Security Standards (Baseline)
    policies.kyverno.io/severity: medium
    policies.kyverno.io/subject: Pod
spec:
  autogen:
    validatingAdmissionPolicy:
      enabled: true        # instructs Kyverno to generate a native VAP
    podControllers:
      controllers: []      # must be empty — VAP handles controller kinds via matchConstraints
  validationActions:
    - Deny
  evaluation:
    background:
      enabled: true
  matchConstraints:
    resourceRules:
      - apiGroups: [""]
        apiVersions: [v1]
        operations: [CREATE, UPDATE]
        resources: [pods]
  variables:
    - name: allContainers
      expression: |-
        object.spec.containers +
          object.spec.?initContainers.orValue([]) +
          object.spec.?ephemeralContainers.orValue([])
  validations:
    - expression: |-
        variables.allContainers.all(container,
          container.?ports.orValue([]).all(port, port.?hostPort.orValue(0) == 0))
      message: >-
        Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort
        must either be unset or set to `0`.
```

---

## 3. Test 1 — Control: generation is off by default

Confirm that a `ValidatingPolicy` without the `autogen` block does **not** generate a VAP.

```bash
kubectl get vpol disallow-host-ports \
  -o jsonpath='{.status.generated}{"\n"}'
```

Expected: `false`

```bash
kubectl describe vpol disallow-host-ports | grep -i -A2 message
```

Expected output contains: `skip generating ValidatingAdmissionPolicy: not enabled`

---

## 4. Test 2 — VAP generation with autogen enabled

```bash
kubectl apply -f manifests/vpol-vap-enabled.yaml
sleep 15

# 1. Check generation status on the ValidatingPolicy
kubectl get vpol disallow-host-ports \
  -o jsonpath='{.status.generated}{"\n"}'

# 2. Confirm the VAP exists
kubectl get validatingadmissionpolicy vpol-disallow-host-ports

# 3. Confirm the binding exists
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding
```

### Inspection checklist — verify three things in the generated VAP

```bash
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o yaml
```

| # | What to check | Expected |
|---|---|---|
| 1 | CEL expression in `.spec.validations[].expression` | Identical to the source policy |
| 2 | `.spec.failurePolicy` | `Fail` |
| 3 | `.metadata.ownerReferences[0].name` | `disallow-host-ports` — deleting the `ValidatingPolicy` cascades to the VAP |

---

## 5. Test 3 — Exceptions survive Kyverno outage

**Objective:** answer the question the team actually asked — do existing `PolicyException`s still
work against a generated VAP when Kyverno is down?

### How it works

Kyverno compiles a `PolicyException`'s `matchConditions` into the generated VAP as a **negated
`matchCondition`**. Once compiled in, the condition lives inside the Kubernetes apiserver — Kyverno
is no longer in the path.

> The `matchConditions[].name` must be unique per policy. It becomes the `matchCondition` name
> inside the VAP.

### 5.1 Exception manifest

```yaml
# manifests/exception.yaml
apiVersion: policies.kyverno.io/v1alpha1   # verify against your cluster
kind: PolicyException
metadata:
  name: allow-host-ports-monitoring
  namespace: <exception-namespace>          # replace before applying
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring
      expression: "object.metadata.name.startsWith('node-exporter')"
```

### 5.2 Exempt Pod manifest

This Pod **must** violate the policy (it uses `hostPort: 9100`) **and** match the exception.
If it didn't violate, it would be admitted regardless and the test would prove nothing.

```yaml
# manifests/exempt-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: node-exporter-test
  namespace: vap-poc
spec:
  containers:
    - name: exporter
      image: nginx:1.27
      ports:
        - containerPort: 9100
          hostPort: 9100
```

### 5.3 Apply exception and verify it reached the VAP

Bring Kyverno back up first — only Kyverno can compile the exception into the VAP.

```bash
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done

kubectl wait --for=condition=available deploy -l app.kubernetes.io/part-of=kyverno \
  -n kyverno --timeout=300s

kubectl apply -f manifests/exception.yaml
sleep 15

kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq
```

**Expected output** — the exception appears as a negated `matchCondition` inside the VAP:

```json
[
  {
    "name": "allow-host-ports-monitoring",
    "expression": "!(object.metadata.name.startsWith('node-exporter'))"
  }
]
```

### 5.4 Verify behaviour with Kyverno running

```bash
kubectl apply -f manifests/exempt-pod.yaml   # expect: created
kubectl apply -f manifests/bad-pod.yaml      # expect: denied
```

### 5.5 Take Kyverno down and repeat

```bash
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found

for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done

kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno \
  -n kyverno --timeout=180s
```

**THE TEST — zero Kyverno pods running:**

```bash
kubectl apply -f manifests/exempt-pod.yaml   # expect: STILL created
kubectl apply -f manifests/bad-pod.yaml      # expect: STILL denied
```

**Both halves matter.** The first confirms exceptions survive outage; the second confirms the
policy hasn't simply stopped enforcing.

### 5.6 Restore Kyverno

```bash
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
```

### Result

| Test | Expected | Actual | Pass / Fail |
|---|---|---|---|
| Exempt Pod admitted with Kyverno up | `created` | | ⬜ |
| Non-exempt Pod denied with Kyverno up | `denied` | | ⬜ |
| Exempt Pod admitted with Kyverno down | `created` | | ⬜ |
| Non-exempt Pod denied with Kyverno down | `denied` | | ⬜ |

---

## 6. Which policies fit VAP's execution model?

### Commands we ran

> **TODO:** paste the commands run here and their output.

---

## 7. Confluence action item

> Document the following in Confluence:
> - Whether `PolicyException` objects continue to work against a generated `ValidatingAdmissionPolicy`
>   (answer: **yes**, Kyverno compiles them as negated `matchConditions` into the VAP, so they
>   survive Kyverno outage)
> - The `autogen.podControllers.controllers: []` requirement (Gotcha — must be empty or autogen
>   conflicts with VAP's `matchConstraints`)
> - The `matchConditions[].name` uniqueness constraint per policy
> - API version to use in your cluster (`kubectl api-resources | grep validatingpolicy`)
