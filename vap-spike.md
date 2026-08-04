# Spike: Kyverno ValidatingPolicy → Native Kubernetes VAP Conversion
## Can we generate and sustain native ValidatingAdmissionPolicies from Kyverno, including exception and outage resilience?

---

| | |
|---|---|
| **Date** | 2026-08-04 |
| **Status** | In Progress |
| **Policy under test** | `disallow-host-ports` |
| **Kyverno ref** | [ValidatingPolicy — Kyverno 1.15 docs](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/) |
| **Kubernetes ref** | [ValidatingAdmissionPolicy — Kubernetes docs](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/) |

---

## Background & Motivation

Our current Kyverno setup enforces admission policies through the Kyverno webhook. If the Kyverno webhook is unavailable — due to a crash, upgrade, or misconfiguration — admission control falls back to the webhook's `failurePolicy`. In many clusters this is set to `Ignore`, which means **policies stop enforcing during outage windows**.

Kubernetes 1.30+ introduced [ValidatingAdmissionPolicy (VAP)](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/) as a GA feature. VAP runs [CEL (Common Expression Language)](https://kubernetes.io/docs/reference/using-api/cel/) expressions directly inside the API server. There is no webhook, no sidecar, no dependency on a running Kyverno instance. Policies enforced this way **survive a full Kyverno outage**.

Kyverno 1.14+ introduced [`ValidatingPolicy`](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/), a CEL-native CRD that can auto-generate native VAPs while keeping Kyverno-side features such as background scanning, policy reporting, and exception support.

This spike investigates whether Kyverno can automatically generate VAPs from `ValidatingPolicy` manifests — and whether the full policy lifecycle (enforcement, exceptions, background scanning) survives when Kyverno is brought down completely.

---

## Spike Goals

1. Understand the correct CRD and syntax to use when targeting VAP generation
2. Prove that Kyverno can auto-generate a native VAP from a `ValidatingPolicy` manifest
3. Confirm that existing `PolicyException` objects are compiled into the generated VAP
4. Prove that enforcement and exceptions both survive a full Kyverno outage
5. Identify which existing policies are candidates for conversion

---

## 01 — Syntax Clarity
### Understanding the difference between `ClusterPolicy` and `ValidatingPolicy`

**What this section covers:** Before writing any manifests, the team needs to understand which Kyverno CRD to use and why. There are two different resource types that look similar but behave very differently. Using the wrong one means VAP generation will never trigger, and the team will not get the outage-resilience benefit this spike is investigating.

**What we are trying to achieve:** Establish a clear, shared understanding of the two CRDs so every policy author targets the right one from day one.

---

The `validate.cel:` stanza is part of the classic **ClusterPolicy** ([`kyverno.io/v1`](https://kyverno.io/docs/kyverno-policies/)) — it is how you opt in to CEL inside the older resource type. It does not generate a native VAP.

The **ValidatingPolicy** ([`policies.kyverno.io/v1alpha1`](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/)) is a newer CRD built specifically to mirror Kubernetes VAP. It uses CEL by default — no opt-in key is needed. To instruct Kyverno to emit a native `ValidatingAdmissionPolicy` and its binding, add the `autogen` block to its `spec` (shown in §02).

| Property | `ClusterPolicy` (`kyverno.io/v1`) | `ValidatingPolicy` (`policies.kyverno.io/v1alpha1`) |
|---|---|---|
| CEL syntax | Opt-in via [`validate.cel:`](https://kyverno.io/docs/writing-policies/cel-based-validate/) | Default — no key needed |
| VAP auto-generation | Not supported | Opt-in via `autogen.validatingAdmissionPolicy.enabled: true` |
| Exception support | [`PolicyException`](https://kyverno.io/docs/writing-policies/exceptions/) | `PolicyException` — compiled into VAP [`matchConditions`](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions) |
| Background scan | Yes | Configurable via `spec.evaluation.background.enabled` |
| Outage resilience | No — relies on Kyverno webhook | Yes — VAP runs inside the API server ([ref](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#what-resources-make-a-policy)) |

> **Before applying any manifest:** verify the exact API version your cluster serves, as it is version-specific.
> ```bash
> kubectl api-resources | grep validatingpolicy
> ```

**Further reading:**
- [Kyverno ValidatingPolicy overview](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/)
- [Kubernetes VAP concepts](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
- [CEL in Kubernetes](https://kubernetes.io/docs/reference/using-api/cel/)

---

## 02 — The Policy Manifest
### Writing a `ValidatingPolicy` that generates a native VAP

**What this section covers:** The full manifest for the `disallow-host-ports` policy, written as a `ValidatingPolicy` with VAP generation enabled. This is the source of truth for the tests in §03–§05.

**What we are trying to achieve:** Produce a single policy manifest that Kyverno can apply as a `ValidatingPolicy` (for background scanning and reporting) while simultaneously generating a native `ValidatingAdmissionPolicy` that enforces the same rule at the API server level — independent of Kyverno.

---

```yaml
# manifests/vpol-vap-enabled.yaml
# Verify apiVersion against your cluster before applying
apiVersion: policies.kyverno.io/v1alpha1
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
      enabled: true        # Instructs Kyverno to generate a native VAP
    podControllers:
      controllers: []      # Must be empty — VAP coverage is set via matchConstraints below
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
        Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort
        must either be unset or set to 0.
```

**Key decisions in this manifest:**

| Field | Value | Why |
|---|---|---|
| `autogen.validatingAdmissionPolicy.enabled` | `true` | Triggers VAP generation — without this, Kyverno applies the policy via webhook only |
| `autogen.podControllers.controllers` | `[]` (empty) | Must be empty when autogen is enabled; a non-empty list conflicts with `matchConstraints` and can cause silent failures |
| `validationActions` | `Deny` | Maps to `[Deny]` on the generated binding — blocks admission rather than auditing. See [validationActions reference](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#validation-actions) |
| `evaluation.background.enabled` | `true` | Keeps Kyverno-side background scan running alongside the generated VAP |

**Further reading:**
- [Kyverno autogen for ValidatingPolicy](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/#auto-generated-policies)
- [VAP spec reference — `matchConstraints`](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#what-resources-make-a-policy)
- [CEL optional types (`?field`, `orValue`)](https://kubernetes.io/docs/reference/using-api/cel/#optional-types)

---

## 03 — Test 1: Control
### Confirming that VAP generation does not happen by default

**What this section covers:** A control test run before enabling autogen. Its purpose is to prove that applying a `ValidatingPolicy` without the `autogen` block does not produce a VAP — establishing a clean baseline before the main test.

**What we are trying to achieve:** Eliminate any doubt that a VAP might exist from a prior state. If this control passes, we know the VAP we see in §04 was caused by the `autogen` flag and nothing else.

---

Apply the policy **without** the `autogen` block first, then check its status.

```bash
# Check generation status on the ValidatingPolicy
kubectl get vpol disallow-host-ports \
  -o jsonpath='{.status.generated}{"\n"}'

# Check Kyverno's stated reason
kubectl describe vpol disallow-host-ports | grep -i -A2 message
```

**Expected output:**
```
false
skip generating ValidatingAdmissionPolicy: not enabled
```

If both lines match, the control is clean. Proceed to §04.

---

## 04 — Test 2: VAP Generation
### Proving that Kyverno generates a native VAP when autogen is enabled

**What this section covers:** Applying the manifest from §02 (with `autogen` enabled) and verifying that Kyverno produces a complete `ValidatingAdmissionPolicy` and its corresponding `ValidatingAdmissionPolicyBinding`.

**What we are trying to achieve:** Confirm that the generated VAP is a faithful copy of the source policy — same CEL expression, correct failure policy, and an owner reference back to the `ValidatingPolicy` so the lifecycle is managed as a pair. See [Kubernetes VAP lifecycle docs](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#creating-a-validatingadmissionpolicy).

---

```bash
kubectl apply -f manifests/vpol-vap-enabled.yaml
sleep 15

# 1. Generation status on the ValidatingPolicy
kubectl get vpol disallow-host-ports \
  -o jsonpath='{.status.generated}{"\n"}'

# 2. The VAP exists
kubectl get validatingadmissionpolicy vpol-disallow-host-ports

# 3. The binding exists
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding
```

### Inspect the generated VAP — three things to verify

```bash
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o yaml
```

These three checks confirm the generated VAP is correct and its lifecycle is tied to the source policy:

- [ ] **CEL expression is identical** — `.spec.validations[].expression` matches the source policy exactly. If it differs, enforcement behaviour will diverge from intent.
- [ ] **Failure policy is `Fail`** — `.spec.failurePolicy: Fail`. If this is `Ignore`, the policy silently stops enforcing during API server stress rather than blocking admission. See [failurePolicy reference](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#failure-policy).
- [ ] **Owner reference is present** — `.metadata.ownerReferences[0].name: disallow-host-ports`. This means deleting the `ValidatingPolicy` automatically deletes the VAP — no orphaned enforcement objects. See [Kubernetes owner references](https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/).

---

## 05 — Test 3: Outage Resilience & Exception Survival
### Proving that enforcement and exceptions hold when Kyverno is completely down

**What this section covers:** The core question of this spike. We bring Kyverno down to zero pods and verify that the `disallow-host-ports` policy continues to deny non-compliant pods, and that a `PolicyException` compiled into the VAP still allows the exempt pod.

**What we are trying to achieve:** Demonstrate that the generated VAP is truly independent of Kyverno at runtime. Once generated, it lives inside the Kubernetes API server and enforces on its own. This is the primary architectural benefit of the conversion: **policy enforcement that survives the Kyverno webhook going down**. The mechanism is explained in full detail in §06.

**How exceptions work in this model:** Kyverno compiles a `PolicyException`'s `matchConditions` into the generated VAP as a negated [`matchCondition`](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions). The exception is baked into the VAP at generation time — it does not require Kyverno to be running at admission time. See §06 for a step-by-step breakdown.

---

### Exception manifest

This exception exempts pods named `node-exporter*` — a realistic case, since node exporters legitimately use host ports. See [Kyverno PolicyException docs](https://kyverno.io/docs/writing-policies/exceptions/).

> **Important:** `matchConditions[].name` must be unique per policy. It becomes the [`matchCondition`](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions) name inside the generated VAP and duplicate names will be rejected.

```yaml
# manifests/exception.yaml
apiVersion: policies.kyverno.io/v1alpha1
kind: PolicyException
metadata:
  name: allow-host-ports-monitoring
  namespace: <exception-namespace>      # Replace with your namespace before applying
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring
      expression: "object.metadata.name.startsWith('node-exporter')"
```

### Exempt pod manifest

This pod **must both** violate the policy (uses `hostPort: 9100`) **and** match the exception. If it did not violate the policy, it would be admitted regardless and the test would prove nothing about exception behaviour.

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

### Step 1 — Apply the exception and verify it reached the VAP

Kyverno must be running for this step. Only Kyverno can compile the exception into the VAP — once compiled, Kyverno is no longer needed at admission time.

```bash
# Bring Kyverno up
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
kubectl wait --for=condition=available deploy \
  -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=300s

# Apply the exception
kubectl apply -f manifests/exception.yaml
sleep 15

# Verify the exception was compiled into the VAP
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq
```

**Expected — exception appears as a negated `matchCondition` inside the VAP:**

```json
[
  {
    "name": "allow-host-ports-monitoring",
    "expression": "!(object.metadata.name.startsWith('node-exporter'))"
  }
]
```

### Step 2 — Verify behaviour with Kyverno running (baseline)

```bash
kubectl apply -f manifests/exempt-pod.yaml   # Expect: created
kubectl apply -f manifests/bad-pod.yaml      # Expect: denied
```

### Step 3 — Take Kyverno down and repeat (the real test)

Both halves of this test matter equally. The first confirms exceptions survive outage; the second confirms the policy has not simply stopped enforcing.

```bash
# Clean up from Step 2
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found

# Scale Kyverno to zero
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod \
  -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s

# The actual test — zero Kyverno pods running
kubectl apply -f manifests/exempt-pod.yaml   # Expect: STILL created
kubectl apply -f manifests/bad-pod.yaml      # Expect: STILL denied
```

### Step 4 — Restore Kyverno

```bash
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
```

### Test Results

| # | Test case | Expected | Actual | Status |
|---|---|---|---|---|
| 1 | Exempt pod admitted — Kyverno **up** | `created` | | ⬜ Pending |
| 2 | Non-exempt pod denied — Kyverno **up** | `denied` | | ⬜ Pending |
| 3 | Exempt pod admitted — Kyverno **down** | `created` | | ⬜ Pending |
| 4 | Non-exempt pod denied — Kyverno **down** | `denied` | | ⬜ Pending |

---

## 06 — How Exemptions Still Work When Kyverno Is Down
### A detailed walkthrough of the compilation and enforcement model

**What this section covers:** The mechanism behind exception survival is non-obvious. This section explains exactly what happens in two phases — the compilation phase (requires Kyverno) and the enforcement phase (does not require Kyverno) — so the team has a clear mental model before rolling this out to other policies.

**What we are trying to achieve:** Give every team member a precise understanding of where the exception logic lives at runtime, what the API server actually evaluates, and what the one genuine limitation of this model is.

**Reference:** [Kubernetes VAP matchConditions](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions) · [Kyverno PolicyException with VAP](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/#exceptions) · [CEL language spec](https://github.com/google/cel-spec)

---

### The core insight

The exception is not evaluated by Kyverno at admission time. It is **compiled into the VAP by Kyverno** while it is running. By the time Kyverno goes down, the exception already lives as a field inside a Kubernetes-native object in etcd. The API server evaluates it entirely on its own.

---

### Phase 1 — Compilation (Kyverno must be running)

This is the only phase that requires Kyverno. It happens once, when you apply the `PolicyException`.

```
You apply a PolicyException
        │
        ▼
Kyverno's controller detects the new PolicyException object
        │
        ▼
Kyverno reads the matchConditions from the exception:
  expression: "object.metadata.name.startsWith('node-exporter')"
        │
        ▼
Kyverno patches the generated ValidatingAdmissionPolicy,
adding the expression — negated — as a matchCondition:

  spec:
    matchConditions:
      - name: allow-host-ports-monitoring
        expression: "!(object.metadata.name.startsWith('node-exporter'))"
        │
        ▼
The updated VAP is written back to etcd as a native Kubernetes object
```

At this point Kyverno's job is done. The exception is no longer a separate `PolicyException` object at admission time — it is a field inside the `ValidatingAdmissionPolicy`, stored in etcd, owned and evaluated by the Kubernetes API server itself.

---

### Phase 2 — Admission (Kyverno is completely down)

When a pod is submitted, the API server processes it through its [admission chain](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/). `ValidatingAdmissionPolicy` evaluation happens as a built-in step, entirely in-process — no network call, no sidecar.

```
Pod admission request arrives at kube-apiserver
        │
        ▼
API server runs registered admission webhooks
  — Kyverno webhook: no response (Kyverno is down, or scaled to zero)
  — Outcome depends on the webhook's failurePolicy (Ignore or Fail)
  — This step does NOT cover VAPs — they are separate
        │
        ▼
API server evaluates all ValidatingAdmissionPolicies
  — These are NOT webhooks
  — They run inside the API server as native CEL evaluation
  — No network. No sidecar. Pure in-process computation.
        │
        ▼
Step 1 — Does this pod match the VAP's matchConstraints?
  resourceRules: apiGroups=[""], resources=[pods], ops=[CREATE,UPDATE]
  → Yes for any Pod CREATE or UPDATE
        │
        ▼
Step 2 — Does this pod pass ALL matchConditions?
  Condition: !(object.metadata.name.startsWith('node-exporter'))
        │
        ├── Pod name is "node-exporter-test"
        │     startsWith('node-exporter') = true
        │     !(true) = false
        │     matchCondition FAILS
        │     → VAP skips validation entirely for this pod
        │     → Pod is ADMITTED  ✓
        │
        └── Pod name is "bad-pod"
              startsWith('node-exporter') = false
              !(false) = true
              matchCondition PASSES → proceed to Step 3
                      │
                      ▼
          Step 3 — Evaluate validations[] expressions
            variables.allContainers.all(container,
              container.?ports.orValue([]).all(port,
                port.?hostPort.orValue(0) == 0))
            → bad-pod has hostPort: 9100
            → expression returns false
            → VAP DENIES admission
            → Pod is REJECTED  ✗
```

---

### Why `matchConditions` is the right mechanism for exceptions

[`matchConditions`](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions) in a VAP act as a **pre-filter gate**, not a validation rule. The Kubernetes API server semantics are:

> *"Only run the `validations` block if **all** `matchConditions` evaluate to `true`."*

So when Kyverno compiles the exception as `!(original expression)`:

| Pod matches exception? | Negated condition result | VAP action | Outcome |
|---|---|---|---|
| Yes — is `node-exporter*` | `!(true)` = `false` | Skip validation | Admitted |
| No — is `bad-pod` | `!(false)` = `true` | Run validations | Enforced (denied if non-compliant) |

Kyverno cannot add an `OR` branch inside the `validations` expression for each exception — that would require rewriting your policy logic for every exception added. Instead it adds a `matchCondition` that makes the VAP opt **out** of evaluating excepted resources altogether. This approach is composable: each additional `PolicyException` adds one more negated `matchCondition` to the VAP, without touching the validation expressions. See [Kubernetes matchConditions reference](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions).

---

### What Kyverno being down actually means

Kyverno runs as a set of Kubernetes `Deployment`s. When they are scaled to zero:

| Component | Impact when Kyverno is down |
|---|---|
| Kyverno admission webhook | Stops responding — `ClusterPolicy` and webhook-backed `ValidatingPolicy` rules stop enforcing (subject to `failurePolicy`) |
| Kyverno background controller | Stops — no background scan, no `PolicyReport` updates |
| Generated `ValidatingAdmissionPolicy` | **Unaffected** — it is a Kubernetes-native object in etcd; the API server evaluates it on every matching admission request regardless of Kyverno's state |
| Compiled exception (`matchCondition`) | **Unaffected** — it is a field inside the VAP; the API server evaluates it in the same pass |

The VAP carries an `ownerReference` back to the `ValidatingPolicy`, but [owner references](https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/) only govern garbage collection. The API server does not check whether the owner is healthy before evaluating the VAP.

---

### The one genuine limitation

Updating or adding an exception **while Kyverno is down** does not take effect immediately.

```
New PolicyException applied while Kyverno is down
        │
        ▼
Kyverno controller is not running — cannot read the new object
        │
        ▼
VAP is not updated — old compiled state remains in etcd
        │
        ▼
Enforcement continues using the old exception list
        │
        ▼
When Kyverno comes back up, it reconciles all PolicyExceptions
and patches the VAP with any changes that occurred during the outage
```

This is the trade-off: you gain outage resilience for **enforcement**, but you lose the ability to make live exception changes during an outage. Teams should be aware that if an emergency exception is needed during a Kyverno outage, it requires restoring at least the Kyverno admission controller before the exception will take effect.

---

## 07 — Triage
### Identifying which existing policies are candidates for VAP conversion

**What this section covers:** Not every policy can be converted to a VAP. Policies that mutate resources, generate objects, call external services, or rely on cross-object lookups cannot run inside the API server's CEL engine. This section records the commands run to triage the existing policy set and their findings.

**What we are trying to achieve:** Produce a list of policies split into three buckets — convertible, needs rework, and not convertible — so the team can scope the conversion effort accurately. See `kyverno-vap-conversion-triage.md` for the full triage methodology and inventory schema.

**Reference:** [CEL language limitations in VAP](https://kubernetes.io/docs/reference/using-api/cel/#language-overview) · [Kyverno ValidatingPolicy limitations](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/#limitations)

---

> **TODO:** Paste the commands run and their output here.

---

## 08 — Actions & Follow-ups
### What needs to happen before this spike is closed

**What this section covers:** The concrete follow-up items that come out of this investigation — things to document, decisions to confirm, and gaps to fill before the team can consider this spike complete and move into conversion work.

---

| # | Action | Owner | Done |
|---|---|---|---|
| A1 | **Document exception behaviour in Confluence.** `PolicyException` objects continue to work against a generated VAP. Kyverno compiles them as negated [`matchConditions`](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions) inside the VAP, so they survive a full Kyverno outage and do not need to be recreated. | | ⬜ |
| A2 | **Document the `podControllers.controllers: []` requirement.** Must be empty when autogen is enabled. A non-empty value conflicts with the VAP's `matchConstraints` and causes silent generation failure or missing controller coverage. | | ⬜ |
| A3 | **Document the `matchConditions[].name` uniqueness constraint.** Names must be unique per policy — they become the `matchCondition` names inside the generated VAP. Duplicate names are rejected at admission time. | | ⬜ |
| A4 | **Document the emergency exception limitation.** New or updated `PolicyException` objects do not take effect while Kyverno is down. The Kyverno admission controller must be running to compile exception changes into the VAP. Capture this in runbooks and escalation procedures. | | ⬜ |
| A5 | **Pin the correct API version per cluster.** Run `kubectl api-resources \| grep validatingpolicy` on each target cluster and record the result. Manifests must reference the version the cluster actually serves — do not assume. | | ⬜ |
| A6 | **Complete the triage section (§07).** Run the triage commands, paste results, and confirm which policies are candidates for conversion. Size the rewrite backlog separately from the conversion work. | | ⬜ |
| A7 | **Fill in the Test 3 results table (§05).** Record actual vs expected for all four cases before closing the spike. | | ⬜ |
| A8 | **Decide: `ValidatingPolicy` vs raw VAP as the conversion target.** If the team is on Kyverno 1.14+, `ValidatingPolicy` with autogen may be a better long-term target than hand-authored VAPs — it keeps background scan, reporting, and exception support. This architectural decision should be made before conversion work begins. See [Kyverno ValidatingPolicy docs](https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/). | | ⬜ |

---

## Reference Links

| Resource | URL |
|---|---|
| Kyverno ValidatingPolicy (1.15) | https://release-1-15-0.kyverno.io/docs/policy-types/validating-policy/ |
| Kyverno PolicyException docs | https://kyverno.io/docs/writing-policies/exceptions/ |
| Kyverno CEL-based validate | https://kyverno.io/docs/writing-policies/cel-based-validate/ |
| Kubernetes ValidatingAdmissionPolicy | https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/ |
| VAP matchConditions | https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#match-conditions |
| VAP failurePolicy | https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#failure-policy |
| VAP validationActions | https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/#validation-actions |
| CEL in Kubernetes | https://kubernetes.io/docs/reference/using-api/cel/ |
| CEL optional types | https://kubernetes.io/docs/reference/using-api/cel/#optional-types |
| CEL language spec (Google) | https://github.com/google/cel-spec |
| Kubernetes owner references | https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/ |
| Kubernetes admission controllers | https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/ |
| Pod Security Standards (Baseline) | https://kubernetes.io/docs/concepts/security/pod-security-standards/ |
