# PoC: ValidatingAdmissionPolicy Generation from Kyverno ValidatingPolicy

**Worked example:** upstream `disallow-host-ports` (Pod Security Standards — Baseline)
**Platform:** ECP · AKS · Kubernetes 1.36 ("Haru")
**Policy type:** `ValidatingPolicy` (VPOL) — `policies.kyverno.io`
**Kyverno:** latest (1.18.x — confirm in §0)
**Status:** Draft — complete the Result boxes as you run each section
**Owner:** _TBC_

---

## How to use this document

Every section is **independently runnable** and has the same shape:

> **Objective** — what this section proves
> **Setup** — what must already be true
> **Run** — commands to execute
> **Expect** — the output that means it worked
> **Pass criteria** — the single condition that decides pass/fail
> **Result** — fill this in

Sections §0–§2 are cluster setup and must run first. §3 onward can be run in order or picked individually. Manifests referenced throughout are in **Appendix A** — save them once before starting.

**Time estimate:** ~2 hours for a full run, ~30 minutes for §0–§7 (the core resilience proof).

---

## 1. Background

### 1.1 The problem

Kyverno enforces policy through a **ValidatingWebhookConfiguration**. The API server makes a network call to the Kyverno admission controller Service on every matching request. That call has a `failurePolicy`, and on ECP it is `Ignore` for most resources:

| Kyverno state | API server behaviour | Result |
|---|---|---|
| Healthy | Calls webhook, gets allow/deny | Policy enforced |
| Pods down / Service unreachable / TLS expired | Webhook call fails → **skipped** | **Request admitted. Zero enforcement.** |

Worse: when the admission controller terminates cleanly it **removes its own webhook configurations**, so there isn't even a failing webhook — the resource types drop out of the admission chain entirely.

### 1.2 The hypothesis

A **ValidatingAdmissionPolicy** is evaluated by the API server's own in-process CEL admission plugin. No webhook, no Service, no network hop, no TLS, no pod.

> A ValidatingPolicy with `spec.autogen.validatingAdmissionPolicy.enabled: true` produces a VAP + VAPBinding that keeps enforcing when Kyverno is completely down — and existing PolicyExceptions keep applying.

### 1.3 The example policy

`disallow-host-ports` is a good PoC subject: pure CEL, no external data, no image checks, self-contained on the incoming object. It's also representative — it's an upstream PSS Baseline policy, so whatever we learn applies to the rest of that family.

It also exposes the single biggest gotcha in this whole exercise (see §8), which is exactly why it's worth using rather than a synthetic policy.

### 1.4 Correcting the ticket

The ticket asks for policies to carry:

```yaml
validate:
  cel:
    generate: true
```

That field does not exist for this policy type. The equivalent for a ValidatingPolicy is:

```yaml
spec:
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []        # MUST be empty — see §11.1
```

Both parts are required. Worth correcting on the ticket before the team picks it up.

---

## 2. Reference: expected translation behaviour

Established from the Kyverno controller source and its conformance suite. **Each item is what the tests below are checking against, not a substitute for testing** — the version we run may differ.

### 2.1 Three conditions for generation

| # | Condition | Message in `.status` if unmet |
|---|---|---|
| 1 | Kyverno SA has RBAC on `validatingadmissionpolicies` **and** `...bindings` | `insufficient permissions to generate ValidatingAdmissionPolicies` |
| 2 | `spec.autogen.validatingAdmissionPolicy.enabled: true` | `skip generating ValidatingAdmissionPolicy: not enabled.` |
| 3 | Pod-controller autogen produced no configs (`podControllers.controllers: []`) | `skip generating ValidatingAdmissionPolicy: pod controllers autogen is enabled.` |

⚠️ **`ready` ≠ `generated`.** A policy that skipped generation still reports `status.conditionStatus.ready: true`. Only `status.generated` is meaningful.

### 2.2 Naming

| Object | Name |
|---|---|
| ValidatingAdmissionPolicy | `vpol-disallow-host-ports` |
| ValidatingAdmissionPolicyBinding | `vpol-disallow-host-ports-binding` |

### 2.3 Field translation

| VPOL field | Generated VAP |
|---|---|
| `spec.matchConstraints` | `spec.matchConstraints` — verbatim |
| `spec.matchConditions` | `spec.matchConditions` — then exception conditions appended |
| `spec.validations` | `spec.validations` — with `exceptions.*` substitutions |
| `spec.variables` | `spec.variables` — then exception variables appended |
| `spec.auditAnnotations` | `spec.auditAnnotations` |
| `spec.validationActions` | binding `spec.validationActions` — **defaults to `[Deny]` if unset** |
| — | `spec.failurePolicy: Fail` (VAP API default, not read from the VPOL) |
| `spec.evaluation.*` | *not translated* — VAP is admission-only |
| `paramKind` / `paramRef` | *appears unwired for VPOL* — n/a for this policy |

### 2.4 Exception translation

| PolicyException field | Becomes in the VAP |
|---|---|
| `matchConditions[].expression` | A `matchConditions` entry, **negated**: `!(<original>)`, same `name` |
| `images[]` | A `variables` entry `allowedImages` (CEL list literal) |
| `allowedValues[]` | A `variables` entry `allowedValues` (CEL list literal) |
| `exceptions.allowedImages` in a validation | String-substituted to `variables.allowedImages` |
| `reportResult`, priority label | *not translated* |

VAP `matchConditions` are ANDed, so multiple exceptions compose as `!(ex1) && !(ex2)` — correct "exclude if any matches" semantics.

### 2.5 The webhook drops the policy once a VAP exists

Kyverno registers a VPOL in its webhook only when `AdmissionEnabled() && !status.Generated`. A successfully generated policy is removed from the webhook entirely — no double enforcement, but enforcement then lives **only** in the API server.

---

## §0 — Environment baseline

> **Objective** — record the starting state; capture the `failurePolicy` that makes this PoC necessary.
> **Setup** — cluster access, `kubectl`, `jq`.

**Run**

```bash
mkdir -p vap-poc && cd vap-poc

kubectl version -o yaml | grep -A3 serverVersion | tee 00-k8s-version.txt
kubectl get deploy -n kyverno \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,IMAGE:.spec.template.spec.containers[0].image' \
  | tee 00-kyverno-deploys.txt
helm list -n kyverno | tee 00-helm.txt

# Which API version does this cluster serve? Use it in every manifest below.
kubectl api-resources --api-group=policies.kyverno.io | tee 00-api-versions.txt

# THE PROBLEM STATEMENT — capture this
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o custom-columns='NAME:.metadata.name,FAILUREPOLICY:.webhooks[*].failurePolicy' \
  | tee 00-webhook-failurepolicy.txt
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno -o yaml \
  > 00-baseline-webhooks.yaml

# Flags in effect
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | tee 00-admission-flags.txt

# Inventory
kubectl get vpol -o wide | tee 00-vpols.txt
kubectl get polex -A -o yaml > 00-exceptions.yaml
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding | tee 00-existing-vaps.txt

kubectl create ns vap-poc
```

**Expect** — at least one Kyverno webhook with `failurePolicy: Ignore`; no existing `vpol-*` VAPs.

**Pass criteria** — baseline captured, replica counts recorded (needed to restore in §7).

**Result** — K8s version: ______ · Kyverno version: ______ · API version served: ______ · failurePolicy: ______ · Admission controller replicas: ______

---

## §1 — Prove the fail-open gap

> **Objective** — demonstrate that policy enforcement disappears entirely when Kyverno goes down. This is the "before" half of the demo.
> **Setup** — §0 complete. Requires a policy already in Deny mode; if `disallow-host-ports` is currently Audit, temporarily use any existing Deny-mode VPOL, or run this section again after §6.

**Run**

```bash
kubectl apply -f manifests/bad-pod.yaml
# Note the result

REPLICAS=$(kubectl get deploy kyverno-admission-controller -n kyverno -o jsonpath='{.spec.replicas}')
echo "baseline replicas: $REPLICAS"

kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/component=admission-controller \
  -n kyverno --timeout=120s

# The webhooks have gone entirely
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno

kubectl delete pod bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-pod.yaml | tee 01-gap-result.txt

# Restore
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=$REPLICAS
kubectl wait --for=condition=available deploy/kyverno-admission-controller -n kyverno --timeout=180s
```

**Expect**

```
pod/bad-pod created
```

…with no webhook configurations present.

**Pass criteria** — a violating Pod is **admitted** while Kyverno is down. Gap confirmed.

**Result** — ⬜ Pass ⬜ Fail · Notes: ______

---

## §2 — Enable VAP generation cluster-wide

> **Objective** — satisfy condition 1 of 3 (flags + RBAC). This changes nothing on its own; generation is opt-in per policy.
> **Setup** — §0 complete.

**Run** — apply via the normal Terraform/Helm path:

```yaml
features:
  generateValidatingAdmissionPolicy:
    enabled: true
  validatingAdmissionPolicyReports:
    enabled: true
  policyExceptions:
    enabled: true
    namespace: "<our-exception-namespace>"   # or "*"
```

> Recent chart versions default the first two to `true`. Set them explicitly anyway so intent is in Git, and verify against our pinned chart version.

```bash
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' \
  | grep -i admissionpolicy

kubectl auth can-i create validatingadmissionpolicies \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
kubectl auth can-i create validatingadmissionpolicybindings \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
```

**Expect** — `--generateValidatingAdmissionPolicy=true` present; both `auth can-i` return `yes`.

If RBAC is missing:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kyverno:generate-validatingadmissionpolicy
  labels:
    app.kubernetes.io/component: admission-controller
    app.kubernetes.io/instance: kyverno
    app.kubernetes.io/part-of: kyverno
rules:
  - apiGroups: ["admissionregistration.k8s.io"]
    resources: ["validatingadmissionpolicies", "validatingadmissionpolicybindings"]
    verbs: ["create", "update", "delete", "list"]
```

**Pass criteria** — both `auth can-i` checks return `yes`.

**Result** — ⬜ Pass ⬜ Fail · Notes: ______

---

## §3 — Control test: the policy as-published generates nothing

> **Objective** — prove that the upstream policy, applied unmodified, produces **no** VAP. This is the control for §4 and stops anyone assuming the cluster-wide flag was sufficient.
> **Setup** — §2 complete.

**Run**

```bash
kubectl apply -f manifests/vpol-disallow-host-ports-asis.yaml
sleep 15

kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
kubectl get vpol disallow-host-ports -o jsonpath='{.status.conditionStatus.ready}{"\n"}'
kubectl describe vpol disallow-host-ports | grep -i -A2 "message" | tee 03-skip-reason.txt
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
```

**Expect**

```
false
true
Message: skip generating ValidatingAdmissionPolicy: not enabled.
Error from server (NotFound): validatingadmissionpolicies.admissionregistration.k8s.io "vpol-disallow-host-ports" not found
```

**Pass criteria** — `status.generated` is `false`, no VAP exists, **and `ready` is `true` despite that**. The second half is the point: `ready` is not a usable health signal.

**Result** — generated: ______ · ready: ______ · skip reason: ______ · ⬜ Pass ⬜ Fail

---

## §4 — Enable generation on the policy

> **Objective** — satisfy conditions 2 and 3, and confirm the VAP + binding appear (**Acceptance Criterion 2**).
> **Setup** — §3 complete.

**Run**

```bash
kubectl apply -f manifests/vpol-disallow-host-ports-vap.yaml
sleep 15

kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding
```

The only change versus §3 is this block:

```yaml
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []
```

**Expect**

```
true
NAME                       VALIDATIONS   PARAMKIND   AGE
vpol-disallow-host-ports   1             <unset>     15s

NAME                               POLICYNAME                 PARAMREF   AGE
vpol-disallow-host-ports-binding   vpol-disallow-host-ports   <unset>    15s
```

**Pass criteria** — `status.generated: true` and both objects exist.

**Result** — ⬜ Pass ⬜ Fail · Time to generate: ______s

---

## §5 — Inspect the generated VAP

> **Objective** — verify the translation is faithful and record what was and wasn't carried across.
> **Setup** — §4 passed.

**Run**

```bash
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o yaml | tee 05-generated-vap.yaml
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding -o yaml | tee 05-generated-vapb.yaml

# Targeted checks
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o jsonpath='{.spec.failurePolicy}{"\n"}'
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o jsonpath='{.metadata.ownerReferences}' | jq
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o jsonpath='{.spec.matchConstraints.resourceRules}' | jq
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o jsonpath='{.spec.variables}' | jq
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding -o jsonpath='{.spec.validationActions}{"\n"}'
```

**Expect** — approximately:

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: vpol-disallow-host-ports
  labels:
    app.kubernetes.io/managed-by: kyverno
  ownerReferences:
    - apiVersion: policies.kyverno.io/v1
      kind: ValidatingPolicy
      name: disallow-host-ports
spec:
  failurePolicy: Fail
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
      message: Use of host ports is disallowed. ...
```

Binding: `validationActions: [Audit]`.

**Checklist**

| Check | Expected | Actual |
|---|---|---|
| `spec.failurePolicy` | `Fail` | |
| `ownerReferences` → ValidatingPolicy | present | |
| `matchConstraints` matches the VPOL | identical | |
| `matchConstraints` includes `pods/ephemeralcontainers` | **no** (see below) | |
| `variables.allContainers` preserved | identical CEL | |
| `evaluation.background` reflected anywhere | **no** — VAP is admission-only | |
| Binding `validationActions` | `[Audit]` | |

Two things to note in the write-up:

- **`pods/ephemeralcontainers` is not added.** `matchConstraints` is copied verbatim, so updates via the ephemeral-containers subresource aren't matched. For *this* policy the practical impact is nil, because the Kubernetes API forbids `ports` on ephemeral containers — the `ephemeralContainers` term in `allContainers` can never produce a violation. For other policies in the family it may matter.
- **`evaluation.background.enabled: true` is not translated.** Background scanning stays with Kyverno's reports controller (tested in §10) and stops when Kyverno stops.

**Pass criteria** — `failurePolicy: Fail`, ownerReference present, matchConstraints and variables identical to source.

**Result** — ⬜ Pass ⬜ Fail · Notes: ______

---

## §6 — Admission behaviour: Audit, then Deny

> **Objective** — confirm the VAP actually evaluates, and establish what users see in each mode.
> **Setup** — §4 passed.

### 6a — Audit mode (as shipped)

**Run**

```bash
kubectl delete pod bad-pod good-pod bad-pod-init -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-pod.yaml
kubectl apply -f manifests/good-pod.yaml
```

**Expect** — both created. With `validationActions: [Audit]` only, the violation is recorded in the **API server audit log** (annotation key `validation.policy.admission.k8s.io/validation_failure`) and **nothing is returned to the client** — no warning, no error.

> **Recommendation for rollout:** use `validationActions: [Audit, Warn]` during the soak phase. `Warn` surfaces a client-side warning so teams see the violation before we flip to Deny. `[Audit]` alone is invisible at the terminal.

### 6b — Warn mode

```bash
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"validationActions":["Audit","Warn"]}}'
sleep 10
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding \
  -o jsonpath='{.spec.validationActions}{"\n"}'

kubectl delete pod bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-pod.yaml
```

**Expect** — Pod created, plus:

```
Warning: Validation failed for ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding': Use of host ports is disallowed. ...
pod/bad-pod created
```

### 6c — Deny mode

```bash
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"validationActions":["Deny"]}}'
sleep 10
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding \
  -o jsonpath='{.spec.validationActions}{"\n"}'   # EXPECT: ["Deny"]

kubectl delete pod bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-pod.yaml        # EXPECT: denied
kubectl apply -f manifests/good-pod.yaml       # EXPECT: created
kubectl apply -f manifests/bad-pod-init.yaml   # EXPECT: denied (initContainers term works)
```

**Expect** — for `bad-pod`, approximately:

```
The pods "bad-pod" is invalid: : ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding' denied request:
Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort
must either be unset or set to `0`.
```

Note the message comes from the API server, **not** `validate.kyverno.svc`. Record the exact wording — runbooks and support docs will need updating.

### 6d — The webhook no longer carries this policy

```bash
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno -o json \
  | jq '.items[].webhooks[] | {name, rules: .rules}' | tee 06-webhook-after.json
diff <(jq -S . 06-webhook-after.json) <(yq -o=json '.items[].webhooks[] | {name, rules}' 00-baseline-webhooks.yaml | jq -S .) || true
```

**Expect** — `v1 pods` removed from the Kyverno webhook rules relative to baseline (assuming no other policy still matches Pods), and **exactly one** denial message in 6c — not one from the API server plus one from Kyverno.

**Pass criteria** — bad Pod denied, good Pod allowed, init-container Pod denied, single denial message.

**Result** — 6a ⬜ · 6b ⬜ · 6c ⬜ · 6d ⬜ · Exact denial text: ______

---

## §7 — The resilience test

> **Objective** — the headline result. Prove enforcement survives a total Kyverno outage.
> **Setup** — §6c complete (policy in Deny mode).

**Run**

```bash
BASE_ADM=$(kubectl get deploy kyverno-admission-controller -n kyverno -o jsonpath='{.spec.replicas}')
BASE_BG=$(kubectl get deploy kyverno-background-controller -n kyverno -o jsonpath='{.spec.replicas}')
BASE_REP=$(kubectl get deploy kyverno-reports-controller -n kyverno -o jsonpath='{.spec.replicas}')
BASE_CLN=$(kubectl get deploy kyverno-cleanup-controller -n kyverno -o jsonpath='{.spec.replicas}')
echo "$BASE_ADM $BASE_BG $BASE_REP $BASE_CLN" | tee 07-baseline-replicas.txt

# 1. Confirm it blocks while Kyverno is UP
kubectl delete pod bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-pod.yaml        # EXPECT: denied

# 2. Take Kyverno FULLY down
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
kubectl get pods -n kyverno

# 3. Webhooks gone, VAP survives
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding | tee 07-vap-survives.txt

# 4. THE TEST
kubectl apply -f manifests/bad-pod.yaml | tee 07-RESULT.txt
kubectl apply -f manifests/good-pod.yaml
```

**Expect**

```
The pods "bad-pod" is invalid: : ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding' denied request:
Use of host ports is disallowed. ...
```

…while `good-pod` is created normally. **This is the proof.** Capture the terminal output.

```bash
# 5. Control test — an UNCONVERTED policy in the same outage window
kubectl apply -f manifests/violating-resource-for-unconverted-policy.yaml
# EXPECT: admitted — demonstrates the delta

# 6. Restore
kubectl scale deploy kyverno-admission-controller  -n kyverno --replicas=$BASE_ADM
kubectl scale deploy kyverno-background-controller -n kyverno --replicas=$BASE_BG
kubectl scale deploy kyverno-reports-controller    -n kyverno --replicas=$BASE_REP
kubectl scale deploy kyverno-cleanup-controller    -n kyverno --replicas=$BASE_CLN
kubectl wait --for=condition=available deploy -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=300s
```

**Pass criteria** — violating Pod **denied by the API server** with zero Kyverno pods running; unconverted-policy control resource **admitted** in the same window.

**Result** — ⬜ Pass ⬜ Fail · Denial source: ______

### 7b — Additional failure modes (optional)

| Scenario | How | Watch for |
|---|---|---|
| Node/AZ loss | Cordon + drain Kyverno's nodes | Same result, more realistic |
| Network partition | Deny-all NetworkPolicy on the Kyverno Service | Webhook *times out* rather than vanishing — does `Ignore` still admit? |
| TLS cert expiry | Delete the Kyverno TLS secret | Webhook errors; VAP unaffected |
| Cluster restart | Restart with no Kyverno pods | VAP active from the first request |

---

## §8 — Pod-controller coverage gap ⚠️

> **Objective** — quantify what we lose by having to set `podControllers.controllers: []`. **This is the most important finding in the PoC after §7.**
> **Setup** — §6c complete (Deny mode), Kyverno up.

### Why this happens

`disallow-host-ports` matches `pods` only. Upstream PSS policies rely on Kyverno's pod-controller autogen to extend that to Deployments, StatefulSets, DaemonSets, Jobs and CronJobs. But autogen and VAP generation are **mutually exclusive** — so a converted policy sees only bare Pod requests.

### 8a — Observe the gap

**Run**

```bash
kubectl apply -f manifests/bad-deployment.yaml
kubectl get deploy bad-deploy -n vap-poc
sleep 20
kubectl get pods -n vap-poc -l app=bad-deploy
kubectl get replicaset -n vap-poc -l app=bad-deploy \
  -o jsonpath='{.items[0].status.conditions}' | jq
kubectl get events -n vap-poc --field-selector reason=FailedCreate | tee 08-events.txt
```

**Expect** — the **Deployment is created successfully**. Its ReplicaSet then fails to create Pods, with a `FailedCreate` event naming the VAP.

**This is the finding:** enforcement holds (no violating Pod ever runs), but it surfaces in the wrong place. Instead of *"your Deployment was rejected"* at `kubectl apply`, the user gets a Deployment that silently never rolls out and has to go digging in ReplicaSet events. For a platform team fielding tickets, that's a materially worse experience than today.

**Pass criteria** — Deployment admitted, Pods blocked, `FailedCreate` event present. Record the event text.

**Result** — Deployment admitted? ______ · Pods blocked? ______ · Event text: ______

### 8b — Option: extend `matchConstraints` to cover controllers

If pod-level-only enforcement isn't acceptable, the CEL has to resolve the pod spec per kind, because `object.spec.containers` only exists on a Pod.

**Run**

```bash
kubectl apply -f manifests/vpol-disallow-host-ports-workloads.yaml
sleep 15
kubectl get vpol disallow-host-ports-workloads -o jsonpath='{.status.generated}{"\n"}'
kubectl get validatingadmissionpolicy vpol-disallow-host-ports-workloads -o yaml \
  | tee 08-workloads-vap.yaml

kubectl delete deploy bad-deploy -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-deployment.yaml   # EXPECT: denied at the Deployment
kubectl apply -f manifests/bad-cronjob.yaml      # EXPECT: denied at the CronJob
kubectl apply -f manifests/good-deployment.yaml  # EXPECT: created
```

The key addition is a `podSpec` variable that branches on kind:

```yaml
variables:
  - name: podSpec
    expression: >-
      has(object.spec.jobTemplate)
        ? object.spec.jobTemplate.spec.template.spec
        : (has(object.spec.template) ? object.spec.template.spec : object.spec)
  - name: allContainers
    expression: >-
      variables.podSpec.containers +
      variables.podSpec.?initContainers.orValue([]) +
      variables.podSpec.?ephemeralContainers.orValue([])
```

**Expect** — the Deployment itself is denied at `kubectl apply`, restoring today's UX.

**Trade-offs to record:**
- The CEL diverges from upstream, so `sync-upstream.py` no longer gives us the policy for free — we own a fork of the expression.
- Matching `pods`, `deployments` **and** `replicasets` means one Deployment rollout is evaluated three times. Harmless for correctness, noisy for audit/reporting volume.
- Every policy in the PSS family needs the same treatment. Estimate the effort across the full candidate list before committing.

**Pass criteria** — Deployment and CronJob both denied at their own admission request.

**Result** — ⬜ Pass ⬜ Fail · Recommendation: ⬜ accept pod-level only ⬜ extend matchConstraints

---

## §9 — Exceptions (Acceptance Criterion 3)

> **Objective** — answer the ticket's central question: do PolicyExceptions still work against a generated VAP, including during an outage?
> **Setup** — §6c complete, `--enablePolicyException=true`.

### 9a — Single exception translates and applies

**Run**

```bash
kubectl apply -f manifests/exception-monitoring.yaml
sleep 15

kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq | tee 09-vap-matchconditions.json

kubectl apply -f manifests/exempt-pod.yaml   # node-exporter-test, uses hostPort 9100
kubectl apply -f manifests/bad-pod.yaml      # still violating, not exempt
```

**Expect**

```json
[
  {
    "name": "allow-host-ports-monitoring",
    "expression": "!(object.metadata.name.startsWith('node-exporter'))"
  }
]
```

…with `node-exporter-test` **created** and `bad-pod` **denied**.

**Pass criteria** — exception appears in the VAP as a negated match condition; exempt Pod allowed, non-exempt Pod still denied.

**Result** — ⬜ Pass ⬜ Fail

### 9b — Exception survives a Kyverno outage 🎯

**Run**

```bash
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found

for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s

kubectl apply -f manifests/exempt-pod.yaml | tee 09-exception-during-outage.txt
kubectl apply -f manifests/bad-pod.yaml
```

**Expect** — `node-exporter-test` **created**, `bad-pod` **denied**. The exclusion is compiled into the VAP, so it holds without Kyverno.

**Pass criteria** — exempt Pod allowed AND non-exempt Pod denied, with zero Kyverno pods running. **This is the answer for the Confluence page.**

**Result** — ⬜ Pass ⬜ Fail

### 9c — Exceptions freeze during an outage ⚠️

**Run** — still with Kyverno down:

```bash
kubectl apply -f manifests/exception-second.yaml
kubectl apply -f manifests/exempt-pod-2.yaml | tee 09-stale-exception.txt
```

**Expect** — the new exception is accepted as an API object, but the Pod is **denied**: the VAP has no knowledge of it, because only Kyverno compiles exceptions into the VAP.

**Operational consequence:** during a Kyverno outage, exception grants are frozen and newly-exempted workloads are *blocked*. This inverts today's failure mode (fail-open) to fail-closed for that specific case. Must go in the runbook.

```bash
# Restore, then confirm the new exception lands
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=$(cat 07-baseline-replicas.txt | awk '{print $1}')
done
sleep 30
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o jsonpath='{.spec.matchConditions}' | jq
```

**Pass criteria** — new exception NOT honoured while down; honoured within ~30s of recovery.

**Result** — ⬜ Pass ⬜ Fail · Time to reconcile after recovery: ______s

### 9d — Multiple exceptions compose

**Run**

```bash
kubectl apply -f manifests/exception-second.yaml   # distinct matchCondition name
sleep 15
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq
```

**Expect** — both conditions present, both negated. Because VAP match conditions are ANDed, the result is `!(ex1) && !(ex2)` — a Pod matching either is excluded. Verify both exempt Pods are admitted.

**Pass criteria** — both exceptions present and both effective.

**Result** — ⬜ Pass ⬜ Fail

### 9e — Duplicate matchCondition names ⚠️ (expected to break)

**Run**

```bash
kubectl apply -f manifests/exception-collision.yaml   # reuses name: allow-host-ports-monitoring
sleep 15

kubectl get vpol disallow-host-ports -o jsonpath='{.status}' | jq
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o yaml | tee 09-collision-vap.yaml
kubectl logs -n kyverno -l app.kubernetes.io/component=admission-controller --tail=200 \
  | grep -i "matchcondition\|duplicate\|invalid" | tee 09-collision-logs.txt
```

**Expect** — VAP match-condition names must be unique, so this should fail: either the VAP update is rejected and the object goes stale, or Kyverno logs an error. **Record exactly what happens** — a silently stale VAP is much worse than a loud failure.

**If it breaks:** we need a naming convention (e.g. `matchConditions[].name` must equal the PolicyException's own `metadata.name`) enforced by a Kyverno policy on PolicyException resources. Draft that as a follow-up.

**Pass criteria** — behaviour documented, and if it fails, the failure is visible in `status` or logs rather than silent.

**Result** — ⬜ Fails loudly ⬜ Fails silently ⬜ Handled gracefully · Notes: ______

### 9f — Exception feature matrix

| Feature | Expected | Result | Notes |
|---|---|---|---|
| `matchConditions` on `object` | Translates (negated) | | 9a |
| `matchConditions` on `namespaceObject` | Translates | | |
| Multiple exceptions, distinct names | Compose via AND | | 9d |
| Multiple exceptions, **same name** | Likely invalid | | 9e |
| `images[]` → `variables.allowedImages` | Translates | | n/a for this policy |
| `allowedValues[]` → `variables.allowedValues` | Translates | | n/a for this policy |
| `reportResult` | Not translated | | |
| Exception honoured with Kyverno down | **Honoured** | | 9b |
| Exception created during outage | **Not applied → blocked** | | 9c |

---

## §10 — Reports and observability

> **Objective** — find out what monitoring we lose when a policy leaves the webhook.
> **Setup** — §4 passed, Kyverno up.

**Run**

```bash
# Does background scanning still produce reports? (the policy sets evaluation.background.enabled: true)
kubectl get polr -n vap-poc
kubectl get polr -n vap-poc -o yaml | grep -A6 "policy: disallow-host-ports" | tee 10-polr.txt
kubectl get polr -n vap-poc -o jsonpath='{.items[*].results[*].source}{"\n"}'

# Admission-time results
kubectl get polr -n vap-poc -o yaml | grep -B2 -A6 "process:"

# Kyverno metrics — do converted policies still emit?
kubectl port-forward -n kyverno svc/kyverno-svc-metrics 8000:8000 &
sleep 3
curl -s localhost:8000/metrics | grep disallow-host-ports | tee 10-metrics.txt
kill %1
```

**Expect** — background scanning should still work (the reports controller reads policies directly, not through the webhook), so `PolicyReport` entries for `disallow-host-ports` should still appear. Admission-time metrics are the question mark: since the policy no longer passes through the Kyverno webhook, `kyverno_admission_*` counters may no longer cover it.

**Pass criteria** — record honestly whether (a) background reports still appear, (b) admission counters still appear. Then check whether our Dynatrace DQL dashboards still show this policy.

> If admission metrics disappear, that's a **monitoring gap to close before production rollout** — API-server denials land in the API server audit log, not Kyverno metrics. Given §2.5, this is a likely regression rather than a hypothetical one.

**Result** — Background reports: ______ · Admission metrics: ______ · Dynatrace visibility: ______

---

## §11 — Negative and guardrail tests

### 11a — The `podControllers` trap ⚠️

> **Objective** — show that adding pod-controller autogen silently deletes the VAP and reverts to fail-open.

```bash
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":["deployments"]}}}}'
sleep 20

kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
kubectl describe vpol disallow-host-ports | grep -i "pod controllers autogen"
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno -o json \
  | jq '.items[].webhooks[].rules'

# Revert
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":[]}}}}'
sleep 20
kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
```

**Expect** — `generated: false`, skip message present, **VAP deleted**, `v1 pods` re-added to the webhook. Then `true` again after reverting.

**Pass criteria** — the VAP is deleted and enforcement returns to the fail-open webhook. Measure the gap between VAP deletion and webhook re-registration if you can.

**Result** — ⬜ Pass ⬜ Fail · Transition window observed: ______

### 11b — Deleting the policy removes enforcement

```bash
kubectl delete vpol disallow-host-ports
sleep 10
kubectl get validatingadmissionpolicy vpol-disallow-host-ports         # EXPECT: NotFound
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding
kubectl apply -f manifests/vpol-disallow-host-ports-vap.yaml           # restore
```

**Expect** — both garbage-collected via `ownerReferences`. Enforcement inherits the policy's lifecycle; a VAP is not an independent safety net.

**Result** — ⬜ Pass ⬜ Fail

### 11c — Manual edits are reverted

```bash
kubectl patch validatingadmissionpolicy vpol-disallow-host-ports --type=json \
  -p '[{"op":"replace","path":"/spec/validations/0/message","value":"tampered"}]'
sleep 20
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.validations[0].message}{"\n"}'
```

**Expect** — reverted to the policy's message. Confirms Kyverno owns the object.

**Result** — ⬜ Pass ⬜ Fail

### 11d — Suggested alert

Once converted policies exist, alert on the silent-revert condition:

```bash
kubectl get vpol -o json | jq -r '
  .items[]
  | select(.spec.autogen.validatingAdmissionPolicy.enabled == true)
  | select((.status.generated // false) == false)
  | "DEGRADED: \(.metadata.name) — VAP enabled but not generated"'
```

Any policy in this state has silently fallen back to the fail-open webhook. Wire this into Dynatrace or a CronJob check. **Do not alert on `status.conditionStatus.ready`** — §3 showed it reports `true` in exactly this situation.

---

## §12 — Candidate audit (Acceptance Criterion 1)

> **Objective** — produce the register of which policies can convert.
> **Setup** — §2 complete.

**Run**

```bash
# Opt-in status and pod-controller dependency across the estate
kubectl get vpol -o json | jq -r '
  .items[] | [
    .metadata.name,
    (.spec.autogen.validatingAdmissionPolicy.enabled // false | tostring),
    (.spec.autogen.podControllers.controllers // [] | length | tostring),
    (.spec.validationActions // ["UNSET->DENY"] | join(",")),
    (.status.generated // false | tostring)
  ] | @tsv' | column -t -N NAME,VAP_ENABLED,PODCTRL,ACTIONS,GENERATED

# Hard blockers: Kyverno CEL libraries not available in the API server
kubectl get vpol -o json | jq -r '
  .items[] as $p |
  ($p.spec.validations[]?.expression, $p.spec.variables[]?.expression, $p.spec.matchConditions[]?.expression)
  | select(test("resource\\.(Get|List)|http\\.|verifyImage|verifyAttestation|globalContext|images\\."))
  | $p.metadata.name' | sort -u

# JSON-mode or background-only policies
kubectl get vpol -o json | jq -r '
  .items[] | select(.spec.evaluation.mode == "JSON" or .spec.evaluation.admission.enabled == false)
  | .metadata.name'

# Policies with UNSET validationActions (these default to Deny — audit them)
kubectl get vpol -o json | jq -r '
  .items[] | select(has("spec") and (.spec.validationActions == null)) | .metadata.name'

# Exception matchCondition name collisions (see §9e) — run BEFORE converting anything
kubectl get polex -A -o json | jq -r '
  .items[] as $e | $e.spec.policyRefs[]? as $ref |
  ($e.spec.matchConditions[]? | "\($ref.name)\t\(.name)\t\($e.metadata.namespace)/\($e.metadata.name)")' \
  | sort | awk -F'\t' '{k=$1"\t"$2; c[k]++; d[k]=d[k]" "$3}
      END {for (i in c) if (c[i]>1) print "COLLISION:", i, "→", d[i]}'
```

**Disqualifiers**

| Feature | Why it blocks conversion |
|---|---|
| Kyverno CEL libraries (`resource.Get/List`, `http.*`, image verification, global context) | Not in the API server CEL environment |
| `evaluation.mode: JSON` | No admission path |
| `evaluation.admission.enabled: false` | Background-only |
| `NamespacedValidatingPolicy` | VAP is cluster-scoped |
| Relies on `autogen.podControllers` | Mutually exclusive — needs the §8b rewrite |

**Deliverable — candidate register**

| Policy | Source | Convertible | Blockers | Rewrite needed | Exceptions |
|---|---|---|---|---|---|
| disallow-host-ports | upstream PSS | ✅ | podControllers | §8b (optional) | 1 |
| … | | | | | |

Headline: `X of Y convertible (Z%)`.

**Result** — ______ of ______ convertible

---

## §13 — CI validation

```bash
kyverno version
kyverno apply ./policies/disallow-host-ports.yaml --resource ./manifests/bad-pod.yaml
kyverno apply ./policies/disallow-host-ports.yaml --resource ./manifests/good-pod.yaml
kyverno test ./test/
kubectl apply --dry-run=server -f ./generated-vaps/
```

Add two chart-level guards so the traps can't recur:

```
{{- if and .autogen.validatingAdmissionPolicy .autogen.podControllers }}
{{- fail "podControllers and validatingAdmissionPolicy are mutually exclusive" }}
{{- end }}
{{- if not .validationActions }}
{{- fail "validationActions must be set explicitly (unset defaults to Deny)" }}
{{- end }}
```

---

## §14 — Rollback

```bash
# Per policy
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"validatingAdmissionPolicy":{"enabled":false}}}}'
# Kyverno deletes the VAP and re-registers the webhook

# Cluster-wide
helm upgrade kyverno ... --set features.generateValidatingAdmissionPolicy.enabled=false
kubectl delete validatingadmissionpolicy -l app.kubernetes.io/managed-by=kyverno
kubectl delete validatingadmissionpolicybinding -l app.kubernetes.io/managed-by=kyverno

# Clean up PoC artefacts
kubectl delete ns vap-poc
kubectl delete polex -n <exception-ns> allow-host-ports-monitoring allow-host-ports-second --ignore-not-found
```

**Verify** the webhook re-registers before declaring rollback complete.

---

## 15. Results summary

| § | Test | Expected | Result | Status |
|---|---|---|---|---|
| 0 | Baseline captured | failurePolicy recorded | | ⬜ |
| 1 | Fail-open gap | Violating Pod admitted, Kyverno down | | ⬜ |
| 2 | Flags + RBAC | Both `can-i` = yes | | ⬜ |
| 3 | Policy as-published | `generated: false`, `ready: true` | | ⬜ |
| 4 | VAP + binding generated | `generated: true`, both exist | | ⬜ |
| 5 | Translation faithful | failurePolicy Fail, owner ref, CEL identical | | ⬜ |
| 6a | Audit mode | Created, no client output | | ⬜ |
| 6b | Warn mode | Created + warning | | ⬜ |
| 6c | Deny mode | Denied by API server | | ⬜ |
| 6d | Webhook drops policy | `pods` removed, single message | | ⬜ |
| **7** | **Enforcement with Kyverno down** | **Denied by API server** | | ⬜ |
| 7 | Control: unconverted policy | Admitted | | ⬜ |
| 8a | Pod-controller gap | Deployment admitted, Pods blocked | | ⬜ |
| 8b | Extended matchConstraints | Deployment denied directly | | ⬜ |
| 9a | Exception translates | Negated matchCondition in VAP | | ⬜ |
| **9b** | **Exception honoured, Kyverno down** | **Exempt allowed, other denied** | | ⬜ |
| 9c | Exception created during outage | Blocked (stale VAP) | | ⬜ |
| 9d | Multiple exceptions | Both negated, ANDed | | ⬜ |
| 9e | Duplicate condition names | Expected to break | | ⬜ |
| 10 | Background reports | Still produced | | ⬜ |
| 10 | Admission metrics / Dynatrace | Likely gap | | ⬜ |
| 11a | podControllers trap | VAP deleted, webhook returns | | ⬜ |
| 11b | Delete policy | VAP garbage-collected | | ⬜ |
| 11c | Manual edit | Reverted | | ⬜ |
| 12 | Candidate register | X of Y convertible | | ⬜ |

---

## 16. Known limitations and risks

### Functional
- **Coverage is partial.** Only self-contained CEL checks convert; anything using Kyverno CEL libraries, external data, or image verification stays webhook-only and fail-open.
- **Pod-controller autogen is unavailable** for converted policies. Either accept pod-level enforcement with a degraded UX (§8a) or fork the CEL to handle template paths (§8b).
- **`validationActions` defaults to Deny** when unset. Guard this in the chart.
- **Generated VAPs are always `failurePolicy: Fail`.** A bad CEL expression blocks admission with no webhook fallback.
- **`[Audit]` alone is invisible** to users. Use `[Audit, Warn]` during soak.
- **Exceptions freeze during an outage** and fail *closed* for newly-exempted workloads.
- **Exception naming is load-bearing** — duplicate `matchCondition` names risk an invalid or stale VAP.
- **`pods/ephemeralcontainers` is not matched.** Immaterial for this policy (the API forbids ports on ephemeral containers), possibly material elsewhere.

### Operational
- **Converted policies leave the Kyverno webhook.** Good for latency and message clarity; means any generation failure silently reverts to fail-open.
- **VAP lifecycle is bound to the policy** via ownerReference.
- **Error message format changes** — update runbooks and support docs with the §6c text.
- **Monitoring likely regresses** — verify Dynatrace before rollout.
- **Alert on `status.generated`, never `ready`.**

---

## 17. Confluence page outline

1. **Summary** — one paragraph, two numbers: N of M policies convertible; converted policies survive a total Kyverno outage *with their exceptions intact*.
2. **The gap** — §1 output, verbatim.
3. **The fix** — §7 output, verbatim. The two terminal captures side by side are the whole argument.
4. **Exception compatibility** — §9 matrix. *This is what the ticket specifically asks to document.* Lead with the good news (exceptions survive), then the two caveats: name collisions and the outage freeze.
5. **The pod-controller constraint** — §8, its own section. Biggest authoring change and the main UX regression.
6. **Candidate register** — §12.
7. **Limitations** — §16.
8. **Recommendation** — phased: Audit+Warn conversions first, close the monitoring gap, then Deny.
9. **Runbook deltas** — new error format, exception freeze during outage, alert on `status.generated`.

---

## 18. Acceptance criteria traceability

| AC | Covered by | Evidence |
|---|---|---|
| All CEL-capable policies identified and listed as VAP candidates | §12 | Candidate register |
| Helm override added per policy; Kyverno generates VAP + VAPBinding | §2, §3, §4 | `status.generated: true`; `vpol-*` objects; §3 proves the override is what does it |
| Existing PolicyExceptions tested against generated VAPs | §9 | Exception matrix, §9b outage test |

---

## Appendix A — Manifests

Save these under `manifests/`. Replace `policies.kyverno.io/v1` with whatever `kubectl api-resources --api-group=policies.kyverno.io` reports (§0).

### `vpol-disallow-host-ports-asis.yaml` — upstream, unmodified (§3)

```yaml
apiVersion: policies.kyverno.io/v1
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports
  annotations:
    policies.kyverno.io/title: Disallow hostPorts in ValidatingPolicy
    policies.kyverno.io/category: Pod Security Standards (Baseline)
    policies.kyverno.io/severity: medium
    policies.kyverno.io/subject: Pod
    policies.kyverno.io/minversion: 1.14.0
    kyverno.io/kubernetes-version: "1.30+"
    policies.kyverno.io/description: >-
      Access to host ports allows potential snooping of network traffic and should not be
      allowed, or at minimum restricted to a known list. This policy ensures the `hostPort`
      field is unset or set to `0`.
spec:
  validationActions:
    - Audit
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

### `vpol-disallow-host-ports-vap.yaml` — VAP generation enabled (§4)

Identical to the above **plus** the `autogen` block:

```yaml
spec:
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []          # MUST be empty
  validationActions:
    - Audit
  # ... rest unchanged
```

### `vpol-disallow-host-ports-workloads.yaml` — controller coverage (§8b)

```yaml
apiVersion: policies.kyverno.io/v1
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports-workloads
spec:
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []
  validationActions:
    - Deny
  matchConstraints:
    resourceRules:
      - apiGroups: [""]
        apiVersions: [v1]
        operations: [CREATE, UPDATE]
        resources: [pods]
      - apiGroups: ["apps"]
        apiVersions: [v1]
        operations: [CREATE, UPDATE]
        resources: [deployments, statefulsets, daemonsets, replicasets]
      - apiGroups: ["batch"]
        apiVersions: [v1]
        operations: [CREATE, UPDATE]
        resources: [jobs, cronjobs]
  variables:
    - name: podSpec
      expression: >-
        has(object.spec.jobTemplate)
          ? object.spec.jobTemplate.spec.template.spec
          : (has(object.spec.template) ? object.spec.template.spec : object.spec)
    - name: allContainers
      expression: >-
        variables.podSpec.containers +
        variables.podSpec.?initContainers.orValue([]) +
        variables.podSpec.?ephemeralContainers.orValue([])
  validations:
    - expression: |-
        variables.allContainers.all(container,
          container.?ports.orValue([]).all(port, port.?hostPort.orValue(0) == 0))
      message: >-
        Use of host ports is disallowed. The hostPort field must either be unset or set to `0`.
```

### Test resources

`bad-pod.yaml`
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: bad-pod
  namespace: vap-poc
spec:
  containers:
    - name: nginx
      image: nginx:1.27
      ports:
        - containerPort: 80
          hostPort: 8080
```

`good-pod.yaml`
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: good-pod
  namespace: vap-poc
spec:
  containers:
    - name: nginx
      image: nginx:1.27
      ports:
        - containerPort: 80
```

`bad-pod-init.yaml` — exercises the `initContainers` term
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: bad-pod-init
  namespace: vap-poc
spec:
  initContainers:
    - name: init
      image: busybox:1.36
      command: ["sh", "-c", "sleep 1"]
      ports:
        - containerPort: 9000
          hostPort: 9000
  containers:
    - name: nginx
      image: nginx:1.27
```

`bad-deployment.yaml`
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bad-deploy
  namespace: vap-poc
spec:
  replicas: 1
  selector:
    matchLabels: { app: bad-deploy }
  template:
    metadata:
      labels: { app: bad-deploy }
    spec:
      containers:
        - name: nginx
          image: nginx:1.27
          ports:
            - containerPort: 80
              hostPort: 8081
```

`good-deployment.yaml` — same as above with the `hostPort` line removed and `name: good-deploy`.

`bad-cronjob.yaml`
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: bad-cronjob
  namespace: vap-poc
spec:
  schedule: "0 0 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: nginx
              image: nginx:1.27
              ports:
                - containerPort: 80
                  hostPort: 8082
```

`exempt-pod.yaml`
```yaml
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

`exempt-pod-2.yaml` — same shape, `name: ingress-metrics-test`, `hostPort: 9101`.

### Exceptions

`exception-monitoring.yaml`
```yaml
apiVersion: policies.kyverno.io/v1
kind: PolicyException
metadata:
  name: allow-host-ports-monitoring
  namespace: <our-exception-namespace>
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring        # unique per policy — see §9e
      expression: "object.metadata.name.startsWith('node-exporter')"
```

`exception-second.yaml`
```yaml
apiVersion: policies.kyverno.io/v1
kind: PolicyException
metadata:
  name: allow-host-ports-second
  namespace: <our-exception-namespace>
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-second            # DIFFERENT name
      expression: "object.metadata.name.startsWith('ingress-metrics')"
```

`exception-collision.yaml` — deliberately reuses a name, for §9e
```yaml
apiVersion: policies.kyverno.io/v1
kind: PolicyException
metadata:
  name: allow-host-ports-collision
  namespace: <our-exception-namespace>
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring        # SAME name as exception-monitoring
      expression: "object.metadata.name.startsWith('collision-test')"
```

---

## Appendix B — References

- Kyverno — ValidatingPolicy: https://kyverno.io/docs/policy-types/validating-policy/
- Kyverno — Policy Exceptions (CEL): https://kyverno.io/docs/guides/exceptions/
- Kyverno — Migrating to CEL Policies: https://kyverno.io/docs/guides/migration-to-cel/
- Kyverno — Configuring Kyverno (container flags): https://kyverno.io/docs/installation/customization/
- Kyverno — CEL Libraries: https://kyverno.io/docs/policy-types/cel-libraries/
- Kyverno conformance suite: `test/conformance/chainsaw/generate-validating-admission-policy/validatingpolicy/`
- Kyverno generation logic: `pkg/controllers/admissionpolicygenerator/generate-vap.go`, `pkg/admissionpolicy/builder.go`
- Kubernetes — ValidatingAdmissionPolicy: https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/
- Kubernetes — MutatingAdmissionPolicy (GA in 1.36): https://kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/
- Kubernetes v1.36 — manifest-based admission control: https://kubernetes.io/blog/2026/05/04/kubernetes-v1-36-manifest-based-admission-control/
- Kyverno issue #13722 — VAP generation skipped when podControllers autogen enabled: https://github.com/kyverno/kyverno/issues/13722
