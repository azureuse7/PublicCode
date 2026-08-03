# PoC: Generating a ValidatingAdmissionPolicy from a Kyverno ValidatingPolicy

**Worked example:** upstream `disallow-host-ports` (Pod Security Standards — Baseline)
**Platform:** AKS · Kubernetes 1.30+
**Status:** Draft — fill in the Result boxes as you go
**Owner:** _TBC_

---

## What we are proving

Kyverno enforces policy through a webhook. The API server calls it over the network, and that call
has a `failurePolicy` — `Ignore` for most resources. So:

| Kyverno state | Result |
|---|---|
| Healthy | Policy enforced |
| Pods down / Service unreachable / TLS expired | **Request admitted. Zero enforcement.** |

Worse, a clean shutdown makes Kyverno **delete its own webhooks**, so the resource types drop out of
the admission chain entirely.

A **ValidatingAdmissionPolicy (VAP)** is evaluated by the API server itself. No webhook, no Service,
no network hop, no pod.

> **Hypothesis:** a ValidatingPolicy with `spec.autogen.validatingAdmissionPolicy.enabled: true`
> produces a VAP that keeps enforcing when Kyverno is completely down — and existing
> PolicyExceptions keep applying.

**Ten steps, ~45 minutes.** Steps 1–7 are the core proof; 8–10 are the findings that matter for rollout.

> **Note on the ticket.** It asks for `validate.cel.generate: true`. That field does not exist for
> this policy type. The correct opt-in is `spec.autogen.validatingAdmissionPolicy.enabled: true`
> **plus** `spec.autogen.podControllers.controllers: []` — both parts. Worth correcting.

---

## Setup — create everything once

```bash
mkdir -p vap-poc/manifests && cd vap-poc
kubectl create ns vap-poc

cat > manifests/all.yaml <<'EOF'
---
apiVersion: v1
kind: Pod
metadata: { name: bad-pod, namespace: vap-poc }
spec:
  containers:
    - name: nginx
      image: nginx:1.27
      ports: [{ containerPort: 80, hostPort: 8080 }]
---
apiVersion: v1
kind: Pod
metadata: { name: good-pod, namespace: vap-poc }
spec:
  containers:
    - name: nginx
      image: nginx:1.27
      ports: [{ containerPort: 80 }]
---
apiVersion: v1
kind: Pod
metadata: { name: node-exporter-test, namespace: vap-poc }
spec:
  containers:
    - name: exporter
      image: nginx:1.27
      ports: [{ containerPort: 9100, hostPort: 9100 }]
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: bad-deploy, namespace: vap-poc }
spec:
  replicas: 1
  selector: { matchLabels: { app: bad-deploy } }
  template:
    metadata: { labels: { app: bad-deploy } }
    spec:
      containers:
        - name: nginx
          image: nginx:1.27
          ports: [{ containerPort: 80, hostPort: 8081 }]
EOF

# The upstream policy, unmodified
cat > manifests/policy-asis.yaml <<'EOF'
apiVersion: policies.kyverno.io/v1alpha1
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports
spec:
  validationActions: [Audit]
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
      message: Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort must either be unset or set to `0`.
EOF

# Same policy + the autogen block. This is the only difference.
sed 's|^spec:|spec:\n  autogen:\n    validatingAdmissionPolicy:\n      enabled: true\n    podControllers:\n      controllers: []|' \
  manifests/policy-asis.yaml > manifests/policy-vap.yaml

# The exception
cat > manifests/exception.yaml <<'EOF'
apiVersion: policies.kyverno.io/v1alpha1
kind: PolicyException
metadata:
  name: allow-host-ports-monitoring
  namespace: EXCEPTION_NAMESPACE
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring
      expression: "object.metadata.name.startsWith('node-exporter')"
EOF
sed -i 's/EXCEPTION_NAMESPACE/<our-exception-namespace>/' manifests/exception.yaml
```

> **Check the API version first.** If `kubectl api-resources --api-group=policies.kyverno.io` reports
> something other than `v1alpha1`, fix all the manifests in one go:
> ```bash
> sed -i 's|policies.kyverno.io/v1alpha1|policies.kyverno.io/v1|g' manifests/*.yaml
> ```

---

## Step 1 — Baseline

```bash
kubectl api-resources --api-group=policies.kyverno.io

# Replica counts - you need these to restore
kubectl get deploy -n kyverno \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas' | tee 01-replicas.txt

# THE PROBLEM STATEMENT - capture this
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o custom-columns='NAME:.metadata.name,FAILUREPOLICY:.webhooks[*].failurePolicy' \
  | tee 01-failurepolicy.txt
```

**Expect** — at least one webhook with `failurePolicy: Ignore`.

**Result** — API version: ______ · failurePolicy: ______ · admission-controller replicas: ______

---

## Step 2 — Prove the gap

> Needs a policy already in **Deny** mode. If nothing is, skip this step and come back after Step 6 —
> the "before" and "after" are the same test either way.

```bash
kubectl apply -f manifests/all.yaml          # note whether bad-pod is denied

kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/component=admission-controller \
  -n kyverno --timeout=120s

kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
kubectl delete pod bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/all.yaml | tee 02-gap.txt

# Restore
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=<baseline>
kubectl wait --for=condition=available deploy/kyverno-admission-controller -n kyverno --timeout=180s
```

**Expect** — no webhooks listed, and `pod/bad-pod created`.

**Pass** — a violating Pod is admitted while Kyverno is down. **Result** — ⬜ Pass ⬜ Fail

---

## Step 3 — Enable generation cluster-wide

Apply through the normal Helm/Terraform path:

```yaml
features:
  generateValidatingAdmissionPolicy:
    enabled: true
  validatingAdmissionPolicyReports:
    enabled: true
  policyExceptions:
    enabled: true
    namespace: "<our-exception-namespace>"
```

```bash
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep -i admissionpolicy

kubectl auth can-i create validatingadmissionpolicies \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
kubectl auth can-i create validatingadmissionpolicybindings \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
```

**Expect** — `--generateValidatingAdmissionPolicy=true` present; both `can-i` return `yes`.

If RBAC is missing:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kyverno:generate-validatingadmissionpolicy
  labels:
    app.kubernetes.io/part-of: kyverno
rules:
  - apiGroups: ["admissionregistration.k8s.io"]
    resources: ["validatingadmissionpolicies", "validatingadmissionpolicybindings"]
    verbs: ["create", "update", "delete", "list"]
```

**Pass** — both `can-i` = yes. **Result** — ⬜ Pass ⬜ Fail

---

## Step 4 — Control: the policy as-published generates nothing

This stops anyone assuming the cluster-wide flag was enough.

```bash
kubectl apply -f manifests/policy-asis.yaml && sleep 15

kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
kubectl get vpol disallow-host-ports -o jsonpath='{.status.conditionStatus.ready}{"\n"}'
kubectl describe vpol disallow-host-ports | grep -i -A2 "message"
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
```

**Expect**

```
false
true
Message: skip generating ValidatingAdmissionPolicy: not enabled.
Error from server (NotFound): ... "vpol-disallow-host-ports" not found
```

**Pass** — `generated: false`, no VAP — **and `ready: true` despite that**. That second half is the
point: ⚠️ **`ready` is not a health signal. Only `status.generated` is.**

**Result** — generated: ______ · ready: ______ · ⬜ Pass ⬜ Fail

---

## Step 5 — Turn generation on

The only change from Step 4 is the `autogen` block:

```yaml
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []        # MUST be empty, or generation is silently skipped
```

```bash
kubectl apply -f manifests/policy-vap.yaml && sleep 15

kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding

# Spot-check the translation
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.failurePolicy}{"\n"}'                       # EXPECT: Fail
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.metadata.ownerReferences[0].kind}{"\n"}'         # EXPECT: ValidatingPolicy
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding \
  -o jsonpath='{.spec.validationActions}{"\n"}'                   # EXPECT: ["Audit"]
```

**Expect**

```
true
NAME                       VALIDATIONS   PARAMKIND   AGE
vpol-disallow-host-ports   1             <unset>     15s

NAME                               POLICYNAME                 PARAMREF   AGE
vpol-disallow-host-ports-binding   vpol-disallow-host-ports   <unset>    15s
```

⚠️ **The generated VAP is always `failurePolicy: Fail`**, regardless of what the VPOL says. A bad CEL
expression blocks admission with no webhook to fall back on.

**Pass** — `generated: true`, both objects exist, `failurePolicy: Fail`, owner reference present.

**Result** — ⬜ Pass ⬜ Fail · Time to generate: ______s

---

## Step 6 — Prove the VAP actually denies

```bash
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"validationActions":["Deny"]}}'
sleep 10
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding \
  -o jsonpath='{.spec.validationActions}{"\n"}'      # EXPECT: ["Deny"]

kubectl delete pod bad-pod good-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/all.yaml                  # bad-pod denied, good-pod created

# Confirm the webhook no longer carries this policy
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno -o json \
  | jq '.items[].webhooks[] | {name, rules: .rules}'
```

**Expect**

```
The pods "bad-pod" is invalid: : ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding' denied request:
Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort
must either be unset or set to `0`.
```

The message comes from the **API server**, not `validate.kyverno.svc`. Record the exact wording —
runbooks need updating. Expect **one** denial, not two: a generated policy is removed from the
Kyverno webhook entirely.

> **Rollout tip:** use `validationActions: [Audit, Warn]` during the soak phase. `[Audit]` alone
> writes to the API server audit log and returns **nothing to the client** — users see no warning at
> all. `Warn` surfaces it at the terminal.

**Pass** — bad Pod denied by the API server, good Pod created, single denial message.

**Result** — ⬜ Pass ⬜ Fail · Exact denial text: ______

---

## Step 7 — The resilience test 🎯

**This is the headline result.**

```bash
# 1. It blocks while Kyverno is UP
kubectl delete pod bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/all.yaml                  # EXPECT: bad-pod denied

# 2. Take Kyverno FULLY down - all four controllers
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
kubectl get pods -n kyverno                          # EXPECT: no resources found

# 3. Webhooks gone, VAP survives
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding

# 4. THE TEST
kubectl delete pod bad-pod good-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/all.yaml 2>&1 | tee 07-PROOF.txt
```

**Expect** — `bad-pod` **denied by the API server** with zero Kyverno pods running; `good-pod` created
normally. **Capture this terminal output — it is the whole argument.**

```bash
# 5. Restore (use the numbers from Step 1)
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done
kubectl wait --for=condition=available deploy -l app.kubernetes.io/part-of=kyverno \
  -n kyverno --timeout=300s
```

**Pass** — violating Pod denied with Kyverno completely down.

**Result** — ⬜ Pass ⬜ Fail · Webhook re-registration time: ______s

> **Optional extra failure modes:** cordon+drain Kyverno's nodes; deny-all NetworkPolicy on the
> Kyverno Service (webhook *times out* rather than vanishing — does `Ignore` still admit?); delete
> the Kyverno TLS secret. All should leave the VAP unaffected.

---

## Step 8 — The pod-controller coverage gap ⚠️

**The most important finding after Step 7.** `disallow-host-ports` matches `pods` only. Upstream
relies on Kyverno's pod-controller autogen to extend that to Deployments, StatefulSets, DaemonSets,
Jobs and CronJobs — but autogen and VAP generation are **mutually exclusive**. A converted policy
therefore sees only bare Pod requests.

```bash
kubectl apply -f manifests/all.yaml            # includes bad-deploy
kubectl get deploy bad-deploy -n vap-poc
sleep 20
kubectl get pods -n vap-poc -l app=bad-deploy
kubectl get events -n vap-poc --field-selector reason=FailedCreate | tee 08-events.txt
```

**Expect** — the **Deployment is created successfully**; its ReplicaSet then fails to create Pods,
with a `FailedCreate` event naming the VAP.

**The finding:** enforcement holds — no violating Pod ever runs — but it surfaces in the wrong place.
Instead of *"your Deployment was rejected"* at `kubectl apply`, the user gets a Deployment that
silently never rolls out and has to dig through ReplicaSet events. For a team fielding tickets that
is materially worse than today.

**Pass** — Deployment admitted, Pods blocked, `FailedCreate` event present.

**Result** — Deployment admitted? ______ · Pods blocked? ______ · Event text: ______

### If pod-level-only is not acceptable

Extend `matchConstraints` to the controller kinds and resolve the pod spec per kind — `object.spec.containers`
only exists on a Pod:

```yaml
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
    - name: podSpec                    # CronJob MUST be tested first - it has jobTemplate, not template
      expression: >-
        has(object.spec.jobTemplate)
          ? object.spec.jobTemplate.spec.template.spec
          : (has(object.spec.template) ? object.spec.template.spec : object.spec)
    - name: allContainers              # upstream, with object.spec -> variables.podSpec
      expression: >-
        variables.podSpec.containers +
        variables.podSpec.?initContainers.orValue([]) +
        variables.podSpec.?ephemeralContainers.orValue([])
```

**Trade-offs to record:**
- The CEL diverges from upstream — we now own a fork of the expression, and upstream sync no longer
  gives us the policy for free.
- One Deployment rollout is evaluated three times (Deployment, ReplicaSet, Pod). Harmless for
  correctness, noisy for reporting volume.
- Every policy in the PSS family needs the same treatment. Cost this before committing.
- Once in Deny, `UPDATE` on existing workloads that already use host ports is blocked too — that can
  wedge an in-flight rollout, not just new deploys.

**Recommendation** — ⬜ accept pod-level only ⬜ extend matchConstraints

---

## Step 9 — Do exceptions survive the outage?

This is the ticket's central question.

```bash
kubectl apply -f manifests/exception.yaml && sleep 15

kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq | tee 09-matchconditions.json
```

**Expect** — the exception compiled into the VAP as a **negated** condition:

```json
[{ "name": "allow-host-ports-monitoring",
   "expression": "!(object.metadata.name.startsWith('node-exporter'))" }]
```

**This is the mechanism.** Once it is in the VAP, the API server applies it on its own.

```bash
# 9a - with Kyverno UP (control)
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/all.yaml
# EXPECT: node-exporter-test created, bad-pod denied

# 9b - with Kyverno FULLY DOWN
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s

kubectl apply -f manifests/all.yaml 2>&1 | tee 09-exception-outage.txt
# EXPECT: node-exporter-test STILL created, bad-pod STILL denied
```

**Both halves matter.** The exempt Pod being allowed is not enough on its own — if the policy were
inactive everything would be admitted and it would "pass" for the wrong reason. The non-exempt Pod
still being denied rules that out.

```bash
# 9c - an exception created DURING the outage (expected to fail closed)
sed 's/node-exporter/ingress-metrics/g; s/monitoring/second/g' manifests/exception.yaml \
  > manifests/exception-2.yaml
kubectl apply -f manifests/exception-2.yaml
kubectl run ingress-metrics-test -n vap-poc --image=nginx:1.27 \
  --overrides='{"spec":{"containers":[{"name":"c","image":"nginx:1.27","ports":[{"containerPort":9101,"hostPort":9101}]}]}}'
# EXPECT: DENIED - the VAP knows nothing about the new exception

# Restore
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done
sleep 30
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq    # both conditions now present
```

⚠️ **This inverts today's failure mode.** Today an outage lets everything through. After conversion,
converted policies enforce strictly and **exception grants are frozen** — teams cannot self-serve an
exemption until Kyverno recovers, and the workload stays blocked. Must go in the runbook.

> **Naming is load-bearing.** VAP `matchConditions` names must be unique. If two exceptions on the
> same policy reuse a name, the VAP goes invalid or stale. Check before converting:
> ```bash
> kubectl get polex -A -o json | jq -r '
>   .items[] as $e | $e.spec.policyRefs[]? as $ref |
>   ($e.spec.matchConditions[]? | "\($ref.name)\t\(.name)")' \
>   | sort | uniq -d
> ```

| Result | |
|---|---|
| 9.1 Exception present in VAP as negated condition | ⬜ |
| **9.2 Exempt Pod allowed, Kyverno down** | ⬜ |
| **9.3 Non-exempt Pod still denied, same window** | ⬜ |
| 9.4 New exception during outage → blocked | ⬜ |
| Duplicate condition names found | ______ |

---

## Step 10 — Negative tests and rollback

```bash
# The podControllers trap - adding it silently deletes the VAP
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":["deployments"]}}}}'
sleep 20
kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'   # EXPECT: false
kubectl get validatingadmissionpolicy vpol-disallow-host-ports                 # EXPECT: NotFound
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":[]}}}}'              # revert

# Deleting the policy removes enforcement (ownerReference garbage collection)
kubectl delete vpol disallow-host-ports && sleep 10
kubectl get validatingadmissionpolicy vpol-disallow-host-ports                 # EXPECT: NotFound
kubectl apply -f manifests/policy-vap.yaml                                     # restore

# Manual edits are reverted - you cannot hand-edit a VAP as break-glass
kubectl patch validatingadmissionpolicy vpol-disallow-host-ports --type=json \
  -p '[{"op":"replace","path":"/spec/validations/0/message","value":"tampered"}]'
sleep 20
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.validations[0].message}{"\n"}'    # EXPECT: reverted
```

**Rollback:**

```bash
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"validatingAdmissionPolicy":{"enabled":false}}}}'
kubectl delete validatingadmissionpolicy,validatingadmissionpolicybinding \
  -l app.kubernetes.io/managed-by=kyverno
kubectl delete ns vap-poc
kubectl delete polex -n <exception-ns> \
  allow-host-ports-monitoring allow-host-ports-second --ignore-not-found
```

**Verify the Kyverno webhook re-registers** before declaring rollback complete.

**Result** — podControllers trap ⬜ · policy delete ⬜ · manual edit reverted ⬜ · rollback ⬜

---

## Alert to add afterwards

Any policy in this state has silently fallen back to the fail-open webhook:

```bash
kubectl get vpol -o json | jq -r '
  .items[]
  | select(.spec.autogen.validatingAdmissionPolicy.enabled == true)
  | select((.status.generated // false) == false)
  | "DEGRADED: \(.metadata.name) - VAP enabled but not generated"'
```

**Do not alert on `status.conditionStatus.ready`** — Step 4 showed it reports `true` in exactly this
situation.

---

## Results summary

| Step | Test | Expected | Result |
|---|---|---|---|
| 1 | Baseline captured | `failurePolicy` recorded | ⬜ |
| 2 | Fail-open gap | Violating Pod admitted, Kyverno down | ⬜ |
| 3 | Flags + RBAC | Both `can-i` = yes | ⬜ |
| 4 | Policy as-published | `generated: false`, `ready: true` | ⬜ |
| 5 | VAP + binding generated | `generated: true`, both exist | ⬜ |
| 6 | Deny mode | Denied by the API server, one message | ⬜ |
| **7** | **Enforcement with Kyverno down** | **Denied by the API server** | ⬜ |
| 8 | Pod-controller gap | Deployment admitted, Pods blocked | ⬜ |
| 9.2 | **Exception honoured, Kyverno down** | **Exempt allowed** | ⬜ |
| 9.3 | Non-exempt denied, same window | Denied | ⬜ |
| 9.4 | Exception created during outage | Blocked | ⬜ |
| 10 | Negative tests + rollback | All as expected | ⬜ |

---

## Known limitations

- **Coverage is partial.** Only self-contained CEL converts. Anything using Kyverno's CEL libraries,
  external data, or image verification stays webhook-only and fail-open.
- **Pod-controller autogen is unavailable** for converted policies — accept pod-level enforcement
  with degraded UX, or fork the CEL (Step 8).
- **`validationActions` defaults to `Deny`** when unset. Guard it in the chart.
- **Generated VAPs are always `failurePolicy: Fail`.** Bad CEL blocks admission with no fallback.
- **`[Audit]` alone is invisible** to users. Use `[Audit, Warn]` during soak.
- **Exceptions freeze during an outage** and fail *closed* for newly-exempted workloads.
- **Exception naming is load-bearing** — duplicate `matchCondition` names risk an invalid VAP.
- **Converted policies leave the Kyverno webhook**, so any generation failure silently reverts to
  fail-open. Alert on `status.generated`, never `ready`.
- **Error message format changes** — update runbooks with the Step 6 text.
- **Monitoring likely regresses** — API-server denials land in the audit log, not Kyverno metrics.
  Verify dashboards before rollout.

---

## Write-up outline

1. **Summary** — one paragraph: converted policies survive a total Kyverno outage *with their
   exceptions intact*.
2. **The gap** — Step 2 output, verbatim.
3. **The fix** — Step 7 output, verbatim. The two terminal captures side by side are the argument.
4. **Exception compatibility** — Step 9. Lead with the good news, then the two caveats: name
   collisions and the outage freeze.
5. **The pod-controller constraint** — Step 8, its own section. Biggest authoring change and the main
   UX regression.
6. **Limitations** and **recommendation** — phased: Audit+Warn first, close the monitoring gap, then Deny.

---

## References

- [ValidatingPolicy](https://kyverno.io/docs/policy-types/validating-policy/)
- [Policy Exceptions](https://kyverno.io/docs/guides/exceptions/)
- [Kyverno CEL libraries](https://kyverno.io/docs/policy-types/cel-libraries/) — none of these work in a generated VAP
- [Kubernetes ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
- [Kyverno #13722](https://github.com/kyverno/kyverno/issues/13722) — VAP generation skipped when podControllers autogen is enabled
