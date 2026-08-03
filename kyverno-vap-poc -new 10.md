# PoC: Proving Kyverno-generated ValidatingAdmissionPolicies survive a Kyverno outage

**Example policy:** upstream `disallow-host-ports` (Pod Security Standards — Baseline)
**Environment:** AKS · Kubernetes 1.36 · Kyverno 1.18.x
**Runtime:** ~30 minutes
**Owner:** _TBC_

---

## What we're proving

Kyverno enforces policy through a **webhook**. That webhook has `failurePolicy: Ignore`, and when the Kyverno pods stop, Kyverno *deletes its own webhook configuration*. Result: **no Kyverno = no enforcement.**

A **ValidatingAdmissionPolicy (VAP)** is evaluated by the API server itself. No webhook, no network call, no pod.

> **Claim:** a ValidatingPolicy with VAP generation enabled keeps enforcing when Kyverno is completely down — and its PolicyExceptions keep working too.

Six tests prove or disprove it:

| Test | Proves |
|---|---|
| 1 | Today: Kyverno down = violating resources admitted |
| 2 | VAP generation can be switched on |
| 3 | Kyverno generates a VAP + Binding from the policy |
| 4 | The VAP enforces, and enforces *selectively* |
| **5** | **The VAP still enforces with Kyverno fully down** ← the proof |
| **6** | **Exceptions still work with Kyverno fully down** |

Each test creates only the manifests it needs, at the point it needs them, so it's clear what each one is for.

---

## Before you start

**Check which API version this cluster serves.** The `policies.kyverno.io` group has moved through `v1alpha1` → `v1beta1` → `v1`, so don't assume:

```bash
kubectl api-resources --api-group=policies.kyverno.io
```

Use whatever it reports in every manifest below.

**Set up a workspace and record the baseline.** You need the replica counts to restore Kyverno after Tests 1, 5 and 6:

```bash
mkdir -p vap-poc/manifests && cd vap-poc
kubectl create ns vap-poc

# The failurePolicy that makes this PoC necessary — capture this for the write-up
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o custom-columns='NAME:.metadata.name,FAILUREPOLICY:.webhooks[*].failurePolicy'

# Write these down
kubectl get deploy -n kyverno -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas'
```

Everything below assumes 1 replica per controller; adjust if yours differ.

---

## Test 1 — Prove the gap

**Objective:** show that today, enforcement disappears completely when Kyverno stops.

### The policy

We need the upstream policy running in its normal, webhook-enforced form — **no VAP involved yet**. This is the control: whatever we observe here is the current production behaviour.

Two deliberate choices:
- **`validationActions: [Deny]`** rather than the upstream `[Audit]`. Audit produces no output at the terminal at all, so a gap would be invisible. We need a visible block.
- **No `autogen` block.** Without it Kyverno generates nothing, which is exactly the state we're comparing against.

```bash
cat > manifests/vpol-baseline.yaml << 'EOF'
apiVersion: policies.kyverno.io/v1
kind: ValidatingPolicy
metadata:
  name: disallow-host-ports
  annotations:
    policies.kyverno.io/title: Disallow hostPorts
    policies.kyverno.io/category: Pod Security Standards (Baseline)
    policies.kyverno.io/severity: medium
    policies.kyverno.io/subject: Pod
    policies.kyverno.io/description: >-
      Access to host ports allows potential snooping of network traffic and should not be
      allowed, or at minimum restricted to a known list. This policy ensures the `hostPort`
      field is unset or set to `0`.
spec:
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
EOF
```

### The violating Pod

The smallest thing that breaks this specific policy: a Pod with `hostPort` set to a non-zero value. That's the only field the CEL inspects.

This same file is reused in Tests 4, 5 and 6 unchanged — keeping the input constant is what makes the before/after comparison meaningful.

```bash
cat > manifests/bad-pod.yaml << 'EOF'
apiVersion: v1
kind: Pod
metadata: { name: bad-pod, namespace: vap-poc }
spec:
  containers:
    - name: nginx
      image: nginx:1.27
      ports:
        - containerPort: 80
          hostPort: 8080
EOF
```

### Run

```bash
kubectl apply -f manifests/vpol-baseline.yaml
sleep 10

kubectl apply -f manifests/bad-pod.yaml
# Expect: denied by validate.kyverno.svc

# Take Kyverno down
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/component=admission-controller \
  -n kyverno --timeout=120s

# The webhook has gone entirely — not failing, gone
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno

kubectl apply -f manifests/bad-pod.yaml
```

**Expect**

```
pod/bad-pod created
```

**Pass** — the identical violating Pod that was denied 30 seconds ago is now admitted.

**Restore**

```bash
kubectl delete pod bad-pod -n vap-poc
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=1
kubectl wait --for=condition=available deploy/kyverno-admission-controller -n kyverno --timeout=180s
```

**Result:** ⬜ Pass ⬜ Fail

---

## Test 2 — Turn on VAP generation

**Objective:** satisfy the cluster-level prerequisites. No manifests needed — this is Helm values plus an RBAC check.

Add to the Kyverno Helm values and redeploy:

```yaml
features:
  generateValidatingAdmissionPolicy:
    enabled: true
  validatingAdmissionPolicyReports:
    enabled: true
  policyExceptions:
    enabled: true
    namespace: "<exception-namespace>"   # or "*"
```

### Run

```bash
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep -i admissionpolicy

kubectl auth can-i create validatingadmissionpolicies \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
kubectl auth can-i create validatingadmissionpolicybindings \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
```

**Expect** — `--generateValidatingAdmissionPolicy=true` present, both `can-i` return `yes`.

**Pass** — both `can-i` checks return `yes`.

### If either returns `no`

Kyverno can't create objects it has no permission for, and this failure shows up later as a confusing "not generated" with no obvious cause. Create the ClusterRole — the labels matter, they're what makes Kyverno's aggregated role pick it up:

```bash
cat > manifests/rbac-vap.yaml << 'EOF'
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
EOF

kubectl apply -f manifests/rbac-vap.yaml
```

Then re-run the `can-i` checks.

**Result:** ⬜ Pass ⬜ Fail

---

## Test 3 — Generate the VAP

**Objective:** get Kyverno to produce a VAP + Binding, and prove that the policy change is what does it.

### Why a second policy file

This is the *same policy* as Test 1 with one block added. Keeping it as a separate file rather than patching in place means you can `diff` the two and the delta is your evidence for the write-up — it's the exact change that would go into the Helm chart override.

```bash
cat > manifests/vpol-vap-enabled.yaml << 'EOF'
apiVersion: policies.kyverno.io/v1
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
      enabled: true          # opt in to VAP generation
    podControllers:
      controllers: []        # must be empty — see Gotcha 2
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
EOF

diff manifests/vpol-baseline.yaml manifests/vpol-vap-enabled.yaml
```

> The ticket asks for `validate: cel: generate: true`. That field doesn't exist for this policy type — the `autogen` block above is the real equivalent, and **both** lines are required. Worth correcting on the ticket.

### Run

```bash
# Control: the policy from Test 1 is still applied and generates nothing
kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
# Expect: false
kubectl describe vpol disallow-host-ports | grep -i -A2 message
# Expect: skip generating ValidatingAdmissionPolicy: not enabled.

# Now apply the version with autogen enabled
kubectl apply -f manifests/vpol-vap-enabled.yaml
sleep 15

kubectl get vpol disallow-host-ports -o jsonpath='{.status.generated}{"\n"}'
kubectl get validatingadmissionpolicy vpol-disallow-host-ports
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding
```

**Expect**

```
true
NAME                       VALIDATIONS   PARAMKIND   AGE
vpol-disallow-host-ports   1             <unset>     15s

NAME                               POLICYNAME                 PARAMREF   AGE
vpol-disallow-host-ports-binding   vpol-disallow-host-ports   <unset>    15s
```

Note the `vpol-` prefix — that's the naming convention for VAPs generated from a ValidatingPolicy.

**Pass** — `status.generated: true` and both objects exist.

### Worth capturing

```bash
kubectl get validatingadmissionpolicy vpol-disallow-host-ports -o yaml
```

Check three things for the write-up: the CEL is identical to the source policy, `failurePolicy` is `Fail`, and there's an `ownerReferences` entry pointing back at the ValidatingPolicy (which means deleting the policy deletes the VAP).

**Result:** ⬜ Pass ⬜ Fail

---

## Test 4 — The VAP enforces, selectively

**Objective:** confirm the VAP actually evaluates — and that it discriminates rather than blocking everything.

### Why we need a compliant Pod

If the CEL expression were broken (say it always evaluated false), *every* Pod would be denied and that would look identical to "the policy works". A Pod that's identical except for the `hostPort` line proves the VAP is reading the right field.

```bash
cat > manifests/good-pod.yaml << 'EOF'
apiVersion: v1
kind: Pod
metadata: { name: good-pod, namespace: vap-poc }
spec:
  containers:
    - name: nginx
      image: nginx:1.27
      ports:
        - containerPort: 80
EOF
```

### Run

```bash
kubectl get validatingadmissionpolicybinding vpol-disallow-host-ports-binding \
  -o jsonpath='{.spec.validationActions}{"\n"}'
# Expect: ["Deny"]

kubectl delete pod bad-pod good-pod -n vap-poc --ignore-not-found
kubectl apply -f manifests/bad-pod.yaml     # expect: denied
kubectl apply -f manifests/good-pod.yaml    # expect: created
```

**Expect**

```
The pods "bad-pod" is invalid: : ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding' denied request:
Use of host ports is disallowed. The field spec.containers[*].ports[*].hostPort
must either be unset or set to `0`.

pod/good-pod created
```

The denial names the **ValidatingAdmissionPolicy**, not `validate.kyverno.svc`. Kyverno removes a policy from its webhook once a VAP exists, so you should see exactly one message — if you see two, the webhook is still evaluating it as well and something is off.

**Pass** — bad Pod denied by the API server, good Pod created.

**Result:** ⬜ Pass ⬜ Fail · Exact message: ______

---

## Test 5 — Enforcement survives Kyverno being down 🎯

**Objective:** the headline result.

**No new manifests.** That's deliberate — this test reruns Test 1 byte-for-byte. Same Pod, same outage, only the VAP has changed. Anything else you introduce here weakens the comparison.

### Run

```bash
kubectl delete pod bad-pod good-pod -n vap-poc --ignore-not-found

# Take Kyverno FULLY down — all four controllers
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
kubectl get pods -n kyverno

# Webhooks gone, VAP survives
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding

# THE TEST
kubectl apply -f manifests/bad-pod.yaml
kubectl apply -f manifests/good-pod.yaml
```

**Expect** — no Kyverno pods, no Kyverno webhooks, and:

```
The pods "bad-pod" is invalid: : ValidatingAdmissionPolicy 'vpol-disallow-host-ports'
with binding 'vpol-disallow-host-ports-binding' denied request:
Use of host ports is disallowed. ...

pod/good-pod created
```

**Pass** — violating Pod denied by the API server with zero Kyverno pods running; compliant Pod still created.

Capture this terminal output. Put it next to the Test 1 output in the write-up — same manifest, same outage, opposite outcome. That's the whole argument.

Leave Kyverno down and go into Test 6.

**Result:** ⬜ Pass ⬜ Fail

---

## Test 6 — Exceptions survive too

**Objective:** answer the question the ticket actually asks — do existing PolicyExceptions still work against a generated VAP?

### The exception

Kyverno compiles a PolicyException's `matchConditions` into the generated VAP as a **negated** condition. This one exempts anything named `node-exporter*` — a realistic case, since node exporters legitimately need host ports.

The `matchConditions[].name` matters: it becomes the match-condition name inside the VAP, and those must be unique per policy (see Gotcha 6).

```bash
cat > manifests/exception.yaml << 'EOF'
apiVersion: policies.kyverno.io/v1
kind: PolicyException
metadata:
  name: allow-host-ports-monitoring
  namespace: <exception-namespace>
spec:
  policyRefs:
    - name: disallow-host-ports
      kind: ValidatingPolicy
  matchConditions:
    - name: allow-host-ports-monitoring
      expression: "object.metadata.name.startsWith('node-exporter')"
EOF
```

### The exempt Pod

This Pod must **violate the policy** (it uses `hostPort: 9100`) *and* match the exception. If it didn't violate, it would be admitted anyway and the test would prove nothing.

```bash
cat > manifests/exempt-pod.yaml << 'EOF'
apiVersion: v1
kind: Pod
metadata: { name: node-exporter-test, namespace: vap-poc }
spec:
  containers:
    - name: exporter
      image: nginx:1.27
      ports:
        - containerPort: 9100
          hostPort: 9100
EOF
```

### Run

```bash
# Bring Kyverno back — only Kyverno can compile the exception into the VAP
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
kubectl wait --for=condition=available deploy -l app.kubernetes.io/part-of=kyverno \
  -n kyverno --timeout=300s

kubectl apply -f manifests/exception.yaml
sleep 15

# Did it reach the VAP?
kubectl get validatingadmissionpolicy vpol-disallow-host-ports \
  -o jsonpath='{.spec.matchConditions}' | jq
```

**Expect** — the exception appears as a negated match condition:

```json
[
  {
    "name": "allow-host-ports-monitoring",
    "expression": "!(object.metadata.name.startsWith('node-exporter'))"
  }
]
```

```bash
# Works with Kyverno up
kubectl apply -f manifests/exempt-pod.yaml    # expect: created
kubectl apply -f manifests/bad-pod.yaml       # expect: denied

# Take Kyverno down again
kubectl delete pod node-exporter-test bad-pod -n vap-poc --ignore-not-found
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s

# THE TEST
kubectl apply -f manifests/exempt-pod.yaml    # expect: STILL created
kubectl apply -f manifests/bad-pod.yaml       # expect: STILL denied
```

**Pass** — exempt Pod allowed **and** non-exempt Pod denied, with zero Kyverno pods running. Both halves matter: the first shows exceptions survive, the second shows the policy hasn't simply stopped working.

**Restore**

```bash
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=1
done
```

**Result:** ⬜ Pass ⬜ Fail

---

## Results

| # | Test | Expected | Actual | Status |
|---|---|---|---|---|
| 1 | Gap: Kyverno down, no VAP | Violating Pod admitted | | ⬜ |
| 2 | Flags + RBAC | Both `can-i` = yes | | ⬜ |
| 3 | VAP + Binding generated | `generated: true` | | ⬜ |
| 4 | VAP enforces selectively | Bad denied, good created | | ⬜ |
| **5** | **Enforces with Kyverno down** | **Denied by API server** | | ⬜ |
| **6** | **Exception works with Kyverno down** | **Exempt allowed, other denied** | | ⬜ |

---

## Things that will trip you up

**1. `ready` does not mean `generated`.** A policy that failed to generate a VAP still reports `status.conditionStatus.ready: true`. Only check `status.generated`. If you build an alert, use this:

```bash
kubectl get vpol -o json | jq -r '
  .items[]
  | select(.spec.autogen.validatingAdmissionPolicy.enabled == true)
  | select((.status.generated // false) == false)
  | "DEGRADED: \(.metadata.name) — VAP enabled but not generated"'
```

Anything listed here has silently fallen back to the fail-open webhook.

**2. `podControllers` and VAP generation are mutually exclusive.** Setting `podControllers.controllers` to anything non-empty makes Kyverno skip generation — and if a VAP already exists, it **deletes it**. Worth demonstrating live:

```bash
kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":["deployments"]}}}}'
sleep 20
kubectl get validatingadmissionpolicy vpol-disallow-host-ports   # NotFound

kubectl patch vpol disallow-host-ports --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":[]}}}}'
```

**3. `validationActions` behaviour.** Unset defaults to `[Deny]` — there is no safe default. `[Audit]` alone produces *no* client output at all (audit log only), which is why Test 1 uses Deny. Use `[Audit, Warn]` during a soak phase so people actually see warnings.

**4. This policy only covers bare Pods.** Because `podControllers` must be empty, the VAP matches `pods` only. To see what that means, create a violating Deployment:

```bash
cat > manifests/bad-deployment.yaml << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata: { name: bad-deploy, namespace: vap-poc }
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
EOF

kubectl apply -f manifests/bad-deployment.yaml   # created!
kubectl get pods -n vap-poc -l app=bad-deploy    # none
kubectl get events -n vap-poc --field-selector reason=FailedCreate
```

Enforcement holds — no violating Pod ever runs — but the user sees a Deployment that never rolls out instead of a clear rejection. To fix it, cover the controllers explicitly (Appendix A), at the cost of forking the upstream CEL.

**5. Exceptions freeze during an outage.** Kyverno compiles exceptions into the VAP. An exception created *while Kyverno is down* won't reach the VAP, so the workload stays blocked until Kyverno recovers. Worth one line in the runbook.

**6. Exception match-condition names must be unique per policy.** Two exceptions on the same policy that reuse a `matchConditions[].name` collide inside the VAP. Check the existing estate before converting anything:

```bash
kubectl get polex -A -o json | jq -r '
  .items[] as $e | $e.spec.policyRefs[]? as $ref |
  ($e.spec.matchConditions[]? | "\($ref.name)\t\(.name)\t\($e.metadata.namespace)/\($e.metadata.name)")' \
  | sort | awk -F'\t' '{k=$1"\t"$2; c[k]++; d[k]=d[k]" "$3}
      END {for (i in c) if (c[i]>1) print "COLLISION:", i, "→", d[i]}'
```

---

## Which policies can convert?

A policy is a candidate if its CEL only reads the incoming object. Sort the estate:

```bash
kubectl get vpol -o json | jq -r '
  .items[] | [
    .metadata.name,
    (.spec.autogen.validatingAdmissionPolicy.enabled // false | tostring),
    (.spec.autogen.podControllers.controllers // [] | length | tostring),
    (.spec.validationActions // ["UNSET->DENY"] | join(",")),
    (.status.generated // false | tostring)
  ] | @tsv' | column -t -N NAME,VAP_ENABLED,PODCTRL,ACTIONS,GENERATED

# Hard blockers — Kyverno CEL libraries aren't available to the API server
kubectl get vpol -o json | jq -r '
  .items[] as $p |
  ($p.spec.validations[]?.expression, $p.spec.variables[]?.expression)
  | select(test("resource\\.(Get|List)|http\\.|verifyImage|verifyAttestation|globalContext"))
  | $p.metadata.name' | sort -u
```

Anything using external lookups, image verification, `evaluation.mode: JSON`, or `NamespacedValidatingPolicy` cannot convert and stays on the fail-open webhook.

---

## Cleanup

```bash
kubectl delete ns vap-poc
kubectl delete vpol disallow-host-ports --ignore-not-found
kubectl delete polex allow-host-ports-monitoring -n <exception-namespace> --ignore-not-found

# VAP and Binding are garbage-collected with the policy via ownerReferences — verify
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding
```

To roll back the feature entirely:

```bash
helm upgrade kyverno ... --set features.generateValidatingAdmissionPolicy.enabled=false
```

Then confirm the Kyverno webhook re-registers before calling it done.

---

## Appendix A — Optional: covering pod controllers

Only needed if pod-level-only enforcement (Gotcha 4) isn't acceptable. `object.spec.containers` exists only on a Pod, so the CEL has to resolve the pod spec per kind before it can check containers:

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

Trade-offs: the CEL diverges from upstream so you own a fork of the expression; and matching `pods`, `deployments` and `replicasets` means one rollout is evaluated three times.

---

## References

- Kyverno — ValidatingPolicy: https://kyverno.io/docs/policy-types/validating-policy/
- Kyverno — Policy Exceptions: https://kyverno.io/docs/guides/exceptions/
- Kyverno — CEL Libraries: https://kyverno.io/docs/policy-types/cel-libraries/
- Kyverno — Configuring Kyverno: https://kyverno.io/docs/installation/customization/
- Kubernetes — ValidatingAdmissionPolicy: https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/
- Kyverno issue #13722 — VAP generation skipped when podControllers autogen enabled: https://github.com/kyverno/kyverno/issues/13722
