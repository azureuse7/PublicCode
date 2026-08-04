# Spike — VAP Conversion & Kyverno Outage Resilience

> Can Kyverno auto-generate native `ValidatingAdmissionPolicy` objects from `ValidatingPolicy` manifests — and do existing exceptions and enforcement survive a full Kyverno outage?

| | |
|---|---|
| **Date** | 2026-08-04 |
| **Status** | In Progress |
| **Policy tested** | disallow-host-ports |
| **Ref** | [Kyverno 1.15 — ValidatingPolicy docs](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/) |

---

## 01 — Syntax Clarity
### Two CRDs, one goal — know which you're writing

The `validate.cel:` stanza belongs to a classic **ClusterPolicy** (`kyverno.io/v1`). The newer **ValidatingPolicy** (`policies.kyverno.io/v1alpha1`) is CEL-native by default — no opt-in key needed. To get Kyverno to emit a native `ValidatingAdmissionPolicy`, add the `autogen` block shown in §02.

| Property | `ClusterPolicy` (kyverno.io/v1) | `ValidatingPolicy` (policies.kyverno.io/v1alpha1) |
|---|---|---|
| CEL syntax | Opt-in via `validate.cel:` | Default — no key needed |
| VAP generation | Not supported | Opt-in via `autogen.validatingAdmissionPolicy.enabled` |
| Exception support | `PolicyException` | `PolicyException` — compiled into VAP `matchConditions` |
| Background scan | Yes | Configurable via `spec.evaluation.background.enabled` |

> **Verify first:** confirm the API version your cluster serves before applying any manifest.
> ```bash
> kubectl api-resources | grep validatingpolicy
> ```

---

## 02 — The Policy
### disallow-host-ports with VAP generation enabled

```yaml
# manifests/vpol-vap-enabled.yaml
# verify apiVersion against your cluster
apiVersion: policies.kyverno.io/v1alpha1
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports
  annotations:
    policies.kyverno.io/title: Disallow hostPorts
    policies.kyverno.io/severity: medium
spec:
  autogen:
    validatingAdmissionPolicy:
      enabled: true        # instructs Kyverno to generate a native VAP
    podControllers:
      controllers: []      # must be empty — VAP handles kinds via matchConstraints
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
          container.?ports.orValue([]).all(port,
            port.?hostPort.orValue(0) == 0))
      message: >-
        Use of host ports is disallowed. spec.containers[*].ports[*].hostPort
        must be unset or 0.
```

---

## 03 — Test 1
### Control — generation is off by default

Confirms that a `ValidatingPolicy` without the `autogen` block does not generate a VAP.

```bash
kubectl get vpol disallow-host-ports \
  -o jsonpath='{.status.generated}{"\n"}'

kubectl describe vpol disallow-host-ports | grep -i -A2 message
```

**Expected:**
```
false
skip generating ValidatingAdmissionPolicy: not enabled
```

---

## 04 — Test 2
### VAP generation — apply and verify

```bash
kubectl apply -f manifests/vpol-vap-enabled.yaml
sleep 15

# generation status
kubectl get vpol disallow-host-ports \
  -o jsonpath='{.status.generated}{"\n"}'

# VAP and binding exist
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding
```

### Inspection checklist — verify three things in the generated VAP

```bash
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o yaml
```

- [ ] **CEL is identical** — `.spec.validations[].expression` matches the source policy exactly
- [ ] **Failure policy is Fail** — `.spec.failurePolicy: Fail`
- [ ] **Owner reference present** — `.metadata.ownerReferences[0].name: disallow-host-ports` — deleting the `ValidatingPolicy` cascades to the VAP

---

## 05 — Test 3
### Exceptions survive Kyverno outage

Kyverno compiles a `PolicyException`'s `matchConditions` into the generated VAP as a **negated `matchCondition`**. Once compiled, the condition lives inside the Kubernetes apiserver — Kyverno is no longer in the path.

> **Note:** `matchConditions[].name` must be unique per policy — it becomes the `matchCondition` name inside the generated VAP.

### Exception manifest

Exempts any pod named `node-exporter*` — a realistic case since node exporters legitimately use host ports.

```yaml
# manifests/exception.yaml
apiVersion: policies.kyverno.io/v1alpha1
kind: PolicyException
metadata:
  name: allow-host-ports-monitoring
  namespace: <exception-namespace>      # replace before applying
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring
      expression: "object.metadata.name.startsWith('node-exporter')"
```

### Exempt pod manifest

Must **both** violate the policy (uses `hostPort: 9100`) and match the exception. If it didn't violate, admission would pass regardless and the test would prove nothing.

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

### Step 1 — apply exception and verify it reached the VAP

Bring Kyverno up first — only Kyverno can compile the exception into the VAP.

```bash
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
kubectl wait --for=condition=available deploy \
  -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=300s

kubectl apply -f manifests/exception.yaml
sleep 15

kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq
```

**Expected — exception as negated `matchCondition` inside the VAP:**

```json
[
  {
    "name": "allow-host-ports-monitoring",
    "expression": "!(object.metadata.name.startsWith('node-exporter'))"
  }
]
```

### Step 2 — verify with Kyverno running

```bash
kubectl apply -f manifests/exempt-pod.yaml   # expect: created
kubectl apply -f manifests/bad-pod.yaml      # expect: denied
```

### Step 3 — take Kyverno down and repeat (the real test)

```bash
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found

for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod \
  -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s

# zero Kyverno pods running — VAP enforcement must hold
kubectl apply -f manifests/exempt-pod.yaml   # expect: STILL created
kubectl apply -f manifests/bad-pod.yaml      # expect: STILL denied
```

### Step 4 — restore

```bash
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
```

### Results

| # | Test case | Expected | Actual | Status |
|---|---|---|---|---|
| 1 | Exempt pod admitted — Kyverno up | `created` | | ⬜ Pending |
| 2 | Non-exempt pod denied — Kyverno up | `denied` | | ⬜ Pending |
| 3 | Exempt pod admitted — Kyverno **down** | `created` | | ⬜ Pending |
| 4 | Non-exempt pod denied — Kyverno **down** | `denied` | | ⬜ Pending |

---

## 06 — Triage
### Which policies fit VAP's execution model?

> **TODO:** Paste the commands run and their output here. See `kyverno-vap-conversion-triage.md` for the full triage runbook.

---

## 07 — Actions
### What to document and decide

| # | Action | Owner | Done |
|---|---|---|---|
| A1 | **Document in Confluence — exception behaviour.** `PolicyException`s continue to work against a generated VAP. Kyverno compiles them as negated `matchConditions` inside the VAP, so they survive a full Kyverno outage. | | ⬜ |
| A2 | **Document the `podControllers.controllers: []` gotcha.** Must be empty when autogen is enabled — a non-empty value conflicts with VAP's `matchConstraints` and can cause silent generation failure or wrong coverage. | | ⬜ |
| A3 | **Document `matchConditions[].name` uniqueness.** Names must be unique per policy — they become the `matchCondition` names inside the generated VAP. | | ⬜ |
| A4 | **Pin the correct API version per cluster.** Run `kubectl api-resources \| grep validatingpolicy` and record the result. Manifests should reference the version the cluster actually serves. | | ⬜ |
| A5 | **Complete the triage section (§06).** Run commands from the triage runbook, paste results, and confirm which policies are candidates for conversion. | | ⬜ |
| A6 | **Fill in the Test 3 results table.** Record actual vs expected for all four cases before closing the spike. | | ⬜ |
