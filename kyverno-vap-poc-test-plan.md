# Kyverno → VAP PoC — Test Plan

Run this at a terminal. Every step: **commands → expected → record what happened.**

Background, mechanism and references: [kyverno-vap-complete-guide.md](kyverno-vap-complete-guide.md).

**Policies under test:** `disallow-host-ports` (primary), `disallow-host-process` (second).
**Cluster:** AKS · **Owner:** _TBC_ · **Date run:** _____

---

## Before you start

- Non-production cluster. While Kyverno is down, every *unconverted* policy is unenforced.
- Pause GitOps, or it will scale Kyverno back up mid-test:
  ```bash
  flux suspend helmrelease kyverno -n kyverno    # or: argocd app set kyverno --sync-policy none
  ```
- Silence Kyverno alerting. Keep the restore command in a second terminal.

```bash
mkdir -p vap-poc/{baseline,policies,tests,results} && cd vap-poc
```

---

## Step 0 — Baseline

```bash
kubectl version -o yaml | grep -A3 serverVersion
helm list -n kyverno

# Which API version is served? Every manifest below depends on this.
kubectl api-resources --api-group=policies.kyverno.io

# Replica counts - you need these to restore
kubectl get deploy -n kyverno -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas

# The problem statement
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o custom-columns='NAME:.metadata.name,FAILUREPOLICY:.webhooks[*].failurePolicy'
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o yaml > baseline/webhooks.yaml

# Current state of our two policies
kubectl get vpol disallow-host-ports disallow-host-process \
  -o custom-columns=NAME:.metadata.name,ACTIONS:.spec.validationActions,GENERATED:.status.generated
```

**Expected:** `failurePolicy: Ignore` on most webhooks. `GENERATED` false or empty — that is the gap.

| Record | Value |
|---|---|
| Kubernetes version | |
| Kyverno / chart version | |
| `ValidatingPolicy` API version (`v1alpha1` or `v1`) | |
| `failurePolicy` | |
| Kyverno replica counts | |

---

## Step 1 — Prove the gap exists

**Proves:** a Kyverno outage turns enforcement off.

```bash
kubectl create ns vap-poc

cat > tests/violating-deployment.yaml << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gap-test
  namespace: vap-poc
spec:
  replicas: 1
  selector: { matchLabels: { app: gap-test } }
  template:
    metadata:
      labels: { app: gap-test }
    spec:
      containers:
        - name: nginx
          image: nginx
          ports:
            - containerPort: 80
              hostPort: 8080          # ← THE VIOLATION
EOF

# PoC-scoped copy of the real policy. Scoped to vap-poc so this cannot touch prod.
cat > tests/poc-baseline-deny.yaml << 'EOF'
apiVersion: policies.kyverno.io/v1alpha1   # ← use what Step 0 reported
kind: ValidatingPolicy
metadata:
  name: poc-disallow-host-ports
spec:
  validationActions: [Deny]
  # No autogen block - exactly like upstream. That is the point.
  matchConstraints:
    resourceRules:
      - apiGroups:   [""]
        apiVersions: [v1]
        operations:  [CREATE, UPDATE]
        resources:   [pods]
    namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: vap-poc
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
      message: Use of host ports is disallowed.
EOF

kubectl apply -f tests/poc-baseline-deny.yaml && sleep 10

# 1. Blocked while Kyverno is UP
kubectl apply -f tests/violating-deployment.yaml
```

**Expected:** denied by `validate.kyverno.svc`.

> ⚠️ **If it is CREATED instead**, pod-controller autogen does *not* default on. That is a finding —
> record it, then add `autogen: {podControllers: {controllers: [deployments]}}` to the PoC copy and
> re-run so the demo has something to lose. Either answer settles the Step 3 question early.

```bash
# 2. Take Kyverno down
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/component=admission-controller \
  -n kyverno --timeout=120s
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno

# 3. Try again — THE GAP
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step1-the-gap.txt

# 4. Restore and clean up
kubectl delete deploy gap-test -n vap-poc
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=<baseline>
kubectl delete -f tests/poc-baseline-deny.yaml     # MUST NOT survive into Step 5
```

**Expected:** webhooks gone; deployment **created**. Capture that terminal output — it is half the argument.

| Result | |
|---|---|
| 1.1 Denied, Kyverno up | ☐ |
| 1.2 Created, Kyverno down | ☐ |
| Autogen default observed | ON / OFF |

---

## Step 2 — Confirm the machinery works

**Proves:** the VAP engine is live and Kyverno is allowed to generate.

```bash
# Kyverno RBAC - both must say yes
kubectl auth can-i create validatingadmissionpolicies \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
kubectl auth can-i create validatingadmissionpolicybindings \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller

# Flags in effect
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep -i admissionpolicy

# Does the API server's VAP engine work at all, independently of Kyverno?
kubectl create namespace vap-smoketest
cat > tests/vap-smoketest.yaml <<'EOF'
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: vap-smoketest
spec:
  failurePolicy: Fail
  matchConstraints:
    resourceRules:
      - apiGroups: [""]
        apiVersions: ["v1"]
        operations: ["CREATE"]
        resources: ["configmaps"]
  validations:
    - expression: "false"
      message: "VAP smoketest - the admission plugin IS active"
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicyBinding
metadata:
  name: vap-smoketest-binding
spec:
  policyName: vap-smoketest
  validationActions: [Deny]
  matchResources:
    namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: vap-smoketest
EOF
kubectl apply -f tests/vap-smoketest.yaml && sleep 10
kubectl -n vap-smoketest create configmap probe --from-literal=k=v

kubectl delete -f tests/vap-smoketest.yaml && kubectl delete ns vap-smoketest
```

**Expected:** both `can-i` → `yes`; ConfigMap **denied**, message mentions `ValidatingAdmissionPolicy`.
**If the ConfigMap is created, stop the PoC** and raise it with the platform team.

| Result | |
|---|---|
| 2.1 RBAC yes/yes | ☐ |
| 2.2 Smoketest denied | ☐ |

---

## Step 3 — CEL portability probe

**Proves:** our upstream policies' CEL actually compiles in the API server. Thirty seconds, no Kyverno involved.

Upstream uses optional syntax (`?.`, `.orValue()`) and concatenates container lists. Kyverno's CEL
engine supports both. The **API server** is a different CEL environment — find out before converting.

```bash
cat <<'EOF' | kubectl apply --dry-run=server -f -
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: cel-portability-probe
spec:
  failurePolicy: Fail
  matchConstraints:
    resourceRules:
      - apiGroups: [""]
        apiVersions: [v1]
        operations: [CREATE]
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
      message: probe
EOF
```

| Output | Meaning |
|---|---|
| `...created (server dry run)` | Both compile. Convert as written |
| `undeclared reference` / `no matching overload` on `orValue` | Optional library unavailable — rewrite using `has()` guards |
| Type error naming `EphemeralContainer` | Concatenation rejected — split into separate `.all()` clauses |

**Result:** ______________________  (paste the exact output — if it fails, the cost applies to the whole upstream pod-security library)

---

## Step 4 — Convert `disallow-host-ports`

**Proves:** the mechanism works end to end. Audit mode first.

Upstream ships **no `autogen` block** and matches **pods only**. Both must change:

```yaml
apiVersion: policies.kyverno.io/v1alpha1   # ← use what Step 0 reported
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports
spec:
  validationActions: [Audit]              # ALWAYS explicit - unset means Deny
  evaluation:
    background:
      enabled: true
  autogen:                                # ← ADDED. Upstream has none.
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []                     # MUST be explicit, or generation is skipped
  matchConstraints:
    resourceRules:                        # ← ADDED. Was: pods only.
      - apiGroups:   [""]
        apiVersions: [v1]
        operations:  [CREATE, UPDATE]
        resources:   [pods]
      - apiGroups:   [apps]
        apiVersions: [v1]
        operations:  [CREATE, UPDATE]
        resources:   [deployments, statefulsets, daemonsets, replicasets]
      - apiGroups:   [batch]
        apiVersions: [v1]
        operations:  [CREATE, UPDATE]
        resources:   [jobs, cronjobs]
  variables:
    # ← ADDED. Hand-written replacement for podControllers autogen.
    #   CronJob MUST be tested first - its spec has jobTemplate, not template.
    - name: podSpec
      expression: |-
        has(object.spec.jobTemplate)
          ? object.spec.jobTemplate.spec.template.spec
          : (has(object.spec.template) ? object.spec.template.spec : object.spec)
    - name: allContainers                 # upstream, object.spec -> variables.podSpec
      expression: |-
        variables.podSpec.containers +
         variables.podSpec.?initContainers.orValue([]) +
         variables.podSpec.?ephemeralContainers.orValue([])
  validations:
    - expression: |-                      # upstream, unchanged
        variables.allContainers.all(container,
          container.?ports.orValue([]).all(port, port.?hostPort.orValue(0) == 0))
      message: Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort must either be unset or set to `0`.
```

> **Two traps.** (1) Adding `validatingAdmissionPolicy.enabled: true` *without* `controllers: []`
> generates nothing — the autogen default blocks it. (2) Skipping the `matchConstraints` enumeration
> still converts cleanly and reports `generated: true`, but the policy then only sees bare Pods.
> `replicasets` is a deliberate widening — Kyverno's default autogen set does not include it.

```bash
POL=disallow-host-ports

kubectl get vpol $POL -o jsonpath='{.status.generated}'
# If false, why?
kubectl get vpol $POL -o jsonpath='{.status.conditionStatus.conditions}' | jq

kubectl get validatingadmissionpolicy vpol-$POL -o yaml
kubectl get validatingadmissionpolicybinding vpol-$POL-binding -o yaml
```

**Expected:** `generated: true`; `vpol-disallow-host-ports` and `-binding` exist; VAP is
`failurePolicy: Fail`; binding is `[Audit]`.
⚠️ `status.conditionStatus.ready` can be `true` while `generated` is `false`. Only `generated` counts.

**Negative test — prove the trap is real:**

```bash
kubectl patch vpol $POL --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":["deployments"]}}}}'
sleep 15
kubectl get vpol $POL -o jsonpath='{.status.generated}'          # EXPECT: false
kubectl get validatingadmissionpolicy vpol-$POL                  # EXPECT: NotFound - deleted
kubectl patch vpol $POL --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":[]}}}}'   # revert
```

Then repeat the whole step for **`disallow-host-process`** — identical shape, identical edit, same
`matchConstraints` and `podSpec`.

| Result | |
|---|---|
| 4.1 `generated: true` | ☐ |
| 4.2 VAP + binding exist | ☐ |
| 4.3 `failurePolicy: Fail` | ☐ |
| 4.4 podControllers trap: VAP deleted | ☐ |
| 4.5 `disallow-host-process` generated | ☐ |

---

## Step 5 — The resilience test (headline result)

**Proves:** converted policies keep working with Kyverno completely down.

> ⚠️ **Run the dry-run sweep first.** `disallow-host-ports` is cluster-wide. Flipping to `Deny`
> enforces on every namespace, including `UPDATE` to workloads that already use host ports — that
> wedges in-flight rollouts, not just new deploys.

```bash
for f in tests/real-workloads/*.yaml; do
  printf '%-60s' "$f"
  if kubectl apply -f "$f" --dry-run=server >/dev/null 2>&1; then echo "ADMITTED"; else echo "DENIED  <-- INVESTIGATE"; fi
done | tee results/step5-blast-radius.txt
```

**Expected:** all ADMITTED. Investigate every DENIED before continuing.

```bash
POL=disallow-host-ports

# 1. Flip to Deny
kubectl patch vpol $POL --type=merge -p '{"spec":{"validationActions":["Deny"]}}'
sleep 10
kubectl get validatingadmissionpolicybinding vpol-$POL-binding \
  -o jsonpath='{.spec.validationActions}'                      # EXPECT: ["Deny"]

# 2. Blocks while Kyverno is UP
kubectl apply -f tests/violating-deployment.yaml               # EXPECT: denied

# 3. Take Kyverno FULLY down - all four controllers
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
kubectl get pods -n kyverno                                    # EXPECT: none
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding

# 4. THE TEST
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step5-the-proof.txt

# 5. Control - an UNCONVERTED policy, same outage window
kubectl apply -f tests/violating-resource-for-unconverted-policy.yaml
# EXPECT: admitted. This delta shows the gain came from VAP.

# 6. Restore
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done
```

**Expected output of step 4:**

```
The deployments "gap-test" is invalid: : ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding' denied request: Use of host ports is disallowed.
The field spec.containers[*].ports[*].hostPort must either be unset or set to `0`.
```

The denial comes from the **API server**, with Kyverno's webhooks gone. That is the proof.

| Result | |
|---|---|
| 5.1 Blast-radius sweep all ADMITTED | ☐ |
| 5.2 Webhooks gone, VAP still present | ☐ |
| **5.3 Denied by the API server, Kyverno down** | ☐ |
| 5.4 Unconverted policy admitted (control) | ☐ |
| Webhook re-registration time (= recovery window) | ____ s |

---

## Step 6 — Do exceptions survive the outage?

**Proves:** existing exemptions still work with Kyverno down. This is the ticket's main question.

```yaml
apiVersion: policies.kyverno.io/v1alpha1   # ← verify on cluster
kind: PolicyException
metadata:
  name: skip-important-tool
  namespace: <our-exception-namespace>
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: skip-important-tool          # MUST be unique per policy
      expression: "object.metadata.name.startsWith('important-tool')"
```

> ⚠️ **`startsWith`, not `==`.** The converted policy matches Deployments, ReplicaSets *and* Pods —
> named `important-tool`, `important-tool-7d4f9c8b6d`, `important-tool-7d4f9c8b6d-x2knp`. An `==`
> exception exempts only the Deployment: `kubectl apply` succeeds, then the Pods are denied. That
> reads as a pass. **Any real exception matching a controller name by equality has this hole** —
> audit for them before converting:
> ```bash
> kubectl get polex -A -o json | jq -r '
>   .items[] as $e | $e.spec.matchConditions[]? |
>   select(.expression | test("object\\.metadata\\.name\\s*==")) |
>   "\($e.metadata.namespace)/\($e.metadata.name)\t\(.expression)"'
> ```

```bash
kubectl apply -f tests/exception.yaml && sleep 15

# THE MECHANISM CHECK - if this is empty, nothing else here will work
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq | tee results/step6-vap-matchconditions.json
```

**Expected:** `[{"name":"skip-important-tool","expression":"!(object.metadata.name.startsWith('important-tool'))"}]`
— the exemption physically inside the native object. **Save this for the write-up.**

```bash
# Exempt workload, Kyverno UP (control)
sed 's/name: gap-test/name: important-tool/; s/app: gap-test/app: important-tool/' \
  tests/violating-deployment.yaml > tests/important-tool-deployment.yaml
kubectl apply -f tests/important-tool-deployment.yaml           # EXPECT: allowed
kubectl get pods -n vap-poc -l app=important-tool               # EXPECT: a Running pod
kubectl delete deploy important-tool -n vap-poc

# Kyverno FULLY DOWN
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s

kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq        # EXPECT: condition STILL present

# TEST A - exempt workload
kubectl apply -f tests/important-tool-deployment.yaml 2>&1 | tee results/step6-exempt-down.txt
kubectl get pods -n vap-poc -l app=important-tool
# TEST B - non-exempt, same window
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step6-nonexempt-down.txt

# Restore
kubectl delete deploy important-tool -n vap-poc --ignore-not-found
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done
kubectl rollout status deploy/kyverno-admission-controller -n kyverno --timeout=300s
```

**Expected:** A = **allowed, pod Running**. B = **still denied**.
Both matter — A alone could pass because the policy was inactive; B rules that out.

**Repeat under a hard crash** (webhooks stay registered — different API-server code path):

```bash
kubectl -n kyverno set image deploy/kyverno-admission-controller \
  kyverno=mcr.microsoft.com/oss/kubernetes/pause:doesnotexist
kubectl -n kyverno get pods -w        # wait for 0 ready, Ctrl-C
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno  # STILL present
kubectl apply -f tests/important-tool-deployment.yaml   # EXPECT: allowed
kubectl apply -f tests/violating-deployment.yaml        # EXPECT: denied
kubectl -n kyverno rollout undo deploy/kyverno-admission-controller
```

**Exception created *during* an outage — expect this to fail closed:**

```bash
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl apply -f tests/brand-new-exception.yaml
kubectl apply -f tests/newly-exempted-violating-deployment.yaml
# EXPECT: BLOCKED - the VAP knows nothing about it
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=<baseline>
```

**This inverts today's failure mode.** Today an outage lets everything through. After conversion,
converted policies enforce strictly and exemptions are frozen. Runbook change.

**Replay the real production exceptions:**

```bash
kubectl get polex -A -o wide | tee results/step6-exceptions.txt

cat > tests/check-exempt-workloads.sh <<'EOF'
#!/usr/bin/env bash
for f in tests/real-exempt-workloads/*.yaml; do
  printf '%-60s' "$(basename "$f")"
  if kubectl apply -f "$f" --dry-run=server >/dev/null 2>&1; then echo "ADMITTED"; else echo "DENIED  <-- REGRESSION"; fi
done
EOF
chmod +x tests/check-exempt-workloads.sh

./tests/check-exempt-workloads.sh | tee results/step6-healthy.txt
# ...scale Kyverno to 0, then:
./tests/check-exempt-workloads.sh | tee results/step6-outage.txt
diff results/step6-healthy.txt results/step6-outage.txt && echo "NO REGRESSION"
```

**Expected:** both runs 100% ADMITTED, diff empty.

| Result | |
|---|---|
| 6.1 Negated condition present in VAP | ☐ |
| 6.2 Exempt allowed + pods Running, Kyverno up | ☐ |
| **6.3 Exempt allowed, all 4 controllers at 0** | ☐ |
| **6.4 Non-exempt still denied, same window** | ☐ |
| 6.5 Exempt allowed under hard crash | ☐ |
| 6.6 Non-exempt denied under hard crash | ☐ |
| 6.7 New exception during outage → blocked | ☐ |
| 6.8 Real exempt workloads, diff empty | ☐ |
| Exceptions using `name ==` found | ____ |

---

## Step 7 — Rollback

**Proves:** we can undo this safely. Rehearse before rolling out widely.

```bash
# Per policy
kubectl patch vpol <name> --type=merge \
  -p '{"spec":{"autogen":{"validatingAdmissionPolicy":{"enabled":false}}}}'

# Cluster-wide
kubectl delete validatingadmissionpolicy -l app.kubernetes.io/managed-by=kyverno
kubectl delete validatingadmissionpolicybinding -l app.kubernetes.io/managed-by=kyverno

# Test cleanup
kubectl delete ns vap-poc --ignore-not-found
kubectl delete polex -n <exception-ns> skip-important-tool --ignore-not-found

# Resume GitOps
flux resume helmrelease kyverno -n kyverno
```

**Expected:** VAP deleted, Kyverno webhook re-registers. **Confirm it re-registered** before declaring rollback done.

| Result | |
|---|---|
| 7.1 Webhook re-registered | ☐ |

---

## Headline numbers for the write-up

| | |
|---|---|
| Policies convertible | ___ of ___ (___%) |
| Enforcement with Kyverno down, before | **none** |
| Enforcement with Kyverno down, after | converted policies **hold** |
| Exceptions honoured during outage | ☐ yes ☐ no |
| Recovery window (webhook re-registration) | ____ s |
| CEL portability probe | ☐ pass ☐ fail |

**The two terminal captures that make the argument:** `results/step1-the-gap.txt` (created, no
enforcement) next to `results/step5-the-proof.txt` (denied by the API server, Kyverno gone).
