# Proof of Concept: Kyverno-Generated ValidatingAdmissionPolicies (VAP)

**Kubernetes Version:** 1.36 (AKS / EKS)
**Kyverno Version:** 1.18.x (chart 3.x)
**Target Audience:** Platform Engineering & Security
**Status:** Draft for review

**Objective:** Evaluate whether Kyverno-generated native `ValidatingAdmissionPolicy` (VAP) resources can keep policy enforcement running during a Kyverno controller outage, determine which of our policies are eligible, and establish how `PolicyException` behaves under the VAP model.

---

## 1. Executive Summary & Problem Statement

### The problem

Kyverno enforces policy through admission webhooks. When the Kyverno admission controller is unavailable — pod crash, rollout, node drain, network partition, TLS cert rotation failure — the cluster ends up in one of two bad states, depending on how `failurePolicy` is configured:

| `failurePolicy` | Behaviour during Kyverno outage | Impact |
| --- | --- | --- |
| `Fail` (Kyverno default) | API server rejects every matched request | **Availability outage.** Deployments, scaling and reconciliation across the platform stall until Kyverno recovers. |
| `Ignore` | API server skips the webhook | **Silent control gap.** Non-compliant workloads are admitted with no record, until Kyverno recovers. |

Neither is acceptable for a shared platform. Today we manage this trade-off with namespace label selectors and fail-open/fail-closed splits, which is workable but leaves a window where either enforcement or availability is compromised.

### The proposed solution

Kubernetes has a native, in-process CEL admission controller — `ValidatingAdmissionPolicy` — that runs inside the API server. It has been enabled by default since Kubernetes 1.30, so it is available on our 1.36 AKS and EKS clusters without control-plane changes.

Kyverno can act as a translator: it reads eligible policies and generates the corresponding `ValidatingAdmissionPolicy` and `ValidatingAdmissionPolicyBinding` objects, then keeps them in sync.

* **No webhook or network dependency.** VAPs evaluate inside the API server process.
* **Survives controller outages.** Once generated, a VAP keeps enforcing whether or not Kyverno is running.
* **Single source of authorship.** Policies stay in our existing chart; the native objects are derived artefacts.

### What this does *not* solve

This is important to state up front, because it bounds the value of the whole exercise:

* Only **validation** translates. Mutation, generation and image-verification rules still depend entirely on the Kyverno webhook.
* Only **CEL-based** validation translates. Pattern-based, `deny`, `podSecurity`, `foreach` and `assert` rules do not.
* **Policy and exception changes do not apply during an outage.** The API server enforces whatever was generated last. A new policy or a new `PolicyException` applied while Kyverno is down is stored in etcd but not translated until Kyverno returns.
* **Reporting still depends on Kyverno.** Audit-mode results and PolicyReports come from the reports controller.

The realistic framing for the write-up: VAP converts a Kyverno outage from *"enforcement or availability breaks"* into *"policy changes are frozen"*. That is a significant improvement, not a removal of the dependency.

---

## 2. Prerequisites

1. Kubernetes 1.30+ with the `admissionregistration.k8s.io/v1` API served. (Confirm on AKS and EKS separately — no control-plane flags are needed at 1.36, but verify rather than assume.)
2. Kyverno admission controller running with `--generateValidatingAdmissionPolicy=true`.
3. Kyverno admission controller ServiceAccount granted permissions on `validatingadmissionpolicies` and `validatingadmissionpolicybindings`.
4. Kyverno reports controller running with `--validatingAdmissionPolicyReports=true` if we want VAP results in PolicyReports.
5. `PolicyException` support enabled (`--enablePolicyException=true` plus `--exceptionNamespace=...`). This is **off by default** — worth confirming against our current values before testing exception behaviour.

Verify the API is available:

```bash
kubectl api-resources | grep validatingadmissionpolic
```

---

## 3. Helm Configuration

Kyverno validates its own permissions at policy install time and reports if they are insufficient, so getting the RBAC block wrong produces a clear error rather than silent non-generation.

```yaml
features:
  generateValidatingAdmissionPolicy:
    enabled: true

  # Optional: surface VAP results in PolicyReports
  policyExceptions:
    enabled: true
    namespace: "${exception_namespace}"

admissionController:
  rbac:
    clusterRole:
      extraResources:
        - apiGroups:
            - admissionregistration.k8s.io
          resources:
            - validatingadmissionpolicies
            - validatingadmissionpolicybindings
          verbs:
            - create
            - update
            - delete
            - list
            - get
            - watch

reportsController:
  extraArgs:
    validatingAdmissionPolicyReports: "true"
```

> **Note:** confirm the exact value paths against the chart version pinned in `` before raising the PR — the `features.*` and `*.extraArgs` structures have shifted between 3.x minors. `helm show values kyverno/kyverno --version <pinned>` is the check.

Equivalent standalone ClusterRole, if we prefer to manage RBAC outside the chart:

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
  - apiGroups:
      - admissionregistration.k8s.io
    resources:
      - validatingadmissionpolicies
      - validatingadmissionpolicybindings
    verbs:
      - create
      - update
      - delete
      - list
```

---

## 4. Policy Eligibility Analysis

### Hard constraints

A Kyverno policy generates a VAP only if **all** of the following hold:

| Constraint | Detail |
| --- | --- |
| Validation only | Mutate, generate and `verifyImages` rules have no VAP equivalent. |
| `validate.cel` subrule | Pattern, `deny`, `podSecurity`, `foreach` and `assert` rules are not translated. |
| Cluster-scoped policy | VAPs are cluster-scoped resources, so only `ClusterPolicy` (not namespaced `Policy`) can generate them. |
| Match on resources only | Matching on `subjects`, `roles` or `clusterRoles` has no CEL equivalent in `matchConstraints`. |
| No engine-side context | `context` lookups, API calls, ConfigMap lookups and Kyverno-specific CEL libraries cannot run in the API server. |

### Custom policies

| Policy | Eligible | Notes |
| --- | --- | --- |
| `disallow-host-network` | Yes | Pure CEL expression |
| `disallow-host-ipc` | Yes | Pure CEL expression |
| `disallow-host-pid` | Yes | Pure CEL expression |
| `require-requests-limits` | Yes | Pure CEL expression |
| `restrict-image-registries` | Yes | CEL with `variables` — confirm no `context` lookup is used |
| `add-imagepullsecrets` | No | Mutation rule |
| `readonly-root-filesystem-mutation` | No | Mutation rule |

**Action:** this table needs to be completed against the actual rendered chart rather than from memory. Suggested one-liner to produce the candidate list:

```bash
kubectl get clusterpolicies -o json \
  | jq -r '.items[] | select([.spec.rules[] | has("validate")] | any)
           | .metadata.name + "\t" + ([.spec.rules[] | select(.validate.cel != null) | .name] | join(","))'
```

### Upstream policies (`best-practices-vpol`, `pod-security-vpol`)

These are `ValidatingPolicy` resources, not `ClusterPolicy`. **This matters:** `ClusterPolicy` is deprecated as of Kyverno 1.18, and `ValidatingPolicy` uses a different, explicit opt-in for VAP generation:

```yaml
apiVersion: policies.kyverno.io/v1
kind: ValidatingPolicy
metadata:
  name: disallow-capabilities
spec:
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers:
        - deployments
        - statefulsets
        - daemonsets
        - jobs
        - cronjobs
  # ...
```

So the PoC has **two generation paths to test**, not one:

1. `ClusterPolicy` + `validate.cel` + controller flag — the legacy path, covering our custom policies.
2. `ValidatingPolicy` + `spec.autogen.validatingAdmissionPolicy.enabled` — the strategic path, covering the vendored upstream sets.

Since `ClusterPolicy` is deprecated, path 2 should be treated as the target end state and path 1 as the migration bridge. Worth a follow-up spike on `kyverno migrate` for converting our custom policies.

---

## 5. Exception Mechanism Architecture

### How exceptions reach the API server

Native VAPs have no knowledge of Kyverno's `PolicyException` CRD. Kyverno bridges this at generation time: when a `PolicyException` matches a policy that generates a VAP, the excepted resources are written into the generated **`ValidatingAdmissionPolicy`'s `spec.matchConstraints.excludeResourceRules`** field.

Note the object: the exclusion lands on the **policy**, not on the binding. The binding only carries `spec.policyName` and `spec.validationActions`.

Example. Given this exception:

```yaml
apiVersion: kyverno.io/v2
kind: PolicyException
metadata:
  name: policy-exception
spec:
  exceptions:
    - policyName: disallow-host-path
      ruleNames:
        - host-path
  match:
    any:
      - resources:
          kinds:
            - Deployment
          names:
            - important-tool
          operations:
            - CREATE
            - UPDATE
```

Kyverno produces:

```yaml
spec:
  matchConstraints:
    resourceRules:
      - apiGroups: [apps]
        apiVersions: [v1]
        operations: [CREATE, UPDATE]
        resources: [deployments, statefulsets, replicasets, daemonsets]
    excludeResourceRules:
      - apiGroups: [apps]
        apiVersions: [v1]
        operations: [CREATE, UPDATE]
        resources: [deployments]
        resourceNames: [important-tool]
```

### Consequences for our exception model

This is the most significant finding for the ECP self-service exception design, and it needs explicit sign-off:

* **Exceptions become coarser.** `excludeResourceRules` supports groups, versions, operations, resources and resource names. It does **not** carry the `conditions{}` block, `podSecurity{}` controls, or CEL match conditions. Any exception relying on those degrades or fails to translate.
* **The exception surface becomes cluster-visible.** Excepted resource names appear in a cluster-scoped object readable by anyone with `get` on VAPs, which is a wider audience than the namespaced `PolicyException`.
* **Fine-grained CEL exceptions (`policies.kyverno.io` group) are engine-evaluated.** These are evaluated by Kyverno, not the API server. Whether they are reflected in the generated VAP at all is the single most important open question in this PoC — see Test 3.

### Behaviour during outages

| Scenario | Behaviour |
| --- | --- |
| Exception created **before** the outage, translated into the VAP | Continues to work. The API server reads the native object. |
| Exception created **during** the outage | CRD is stored, but no translation occurs. Enforcement continues without the exception until Kyverno recovers — the workload is **blocked**. |
| Exception deleted during the outage | Exclusion remains live in the VAP. The workload stays excepted until Kyverno recovers. |

The second and third rows are both risks worth calling out to the security team: an outage freezes the exception set in place in both directions.

---

## 6. Testing & Validation Plan

Run on a non-production cluster. Record actual output against each expected outcome.

### Test 1 — VAP generation

**Goal:** confirm eligible policies produce native objects.

Apply a test policy:

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-env-label
spec:
  background: false
  rules:
    - name: check-env-label
      match:
        any:
          - resources:
              kinds:
                - Pod
              operations:
                - CREATE
                - UPDATE
      validate:
        failureAction: Enforce
        cel:
          expressions:
            - expression: "'env' in object.metadata.?labels.orValue({})"
              message: "Pods must have the 'env' label."
```

Check generation status on the policy itself:

```bash
kubectl get clusterpolicy require-env-label -o jsonpath='{.status.validatingadmissionpolicy}' | jq
# expected: { "generated": true, "message": "" }
```

Then the native objects:

```bash
kubectl get validatingadmissionpolicies
kubectl get validatingadmissionpolicybindings
kubectl get validatingadmissionpolicy require-env-label -o yaml
```

**Expected outcome:**
* `require-env-label` and `require-env-label-binding` exist, both labelled `app.kubernetes.io/managed-by: kyverno` with an `ownerReference` back to the ClusterPolicy.
* The generated VAP has `failurePolicy: Fail`.
* The binding has `validationActions: [Deny]` (an `Audit` policy would instead produce `[Audit, Warn]`).
* Because the policy matches Pods only, `matchConstraints.resourceRules` includes both `pods` and `pods/ephemeralcontainers` — expected, not a bug.

**Also verify reconciliation:** delete the generated VAP and confirm Kyverno recreates it.

```bash
kubectl delete validatingadmissionpolicy require-env-label
sleep 10 && kubectl get validatingadmissionpolicy require-env-label
```

> Record which of our production policies **fail** to generate and why. That list is the real output of this PoC.

### Test 2 — Enforcement survives a Kyverno outage

**Goal:** prove the API server enforces independently.

> **Important:** with the default `failurePolicy: Fail`, simply scaling Kyverno to zero makes the API server reject matched requests at the *webhook*, which looks like enforcement but isn't. The webhook configurations must actually be gone before this test means anything. Kyverno removes them on graceful shutdown; confirm rather than assume.

```bash
kubectl scale deployment kyverno-admission-controller -n kyverno --replicas=0
kubectl scale deployment kyverno-background-controller -n kyverno --replicas=0
kubectl scale deployment kyverno-reports-controller -n kyverno --replicas=0

# Gate: this must return nothing before proceeding
kubectl get validatingwebhookconfigurations | grep kyverno
kubectl get mutatingwebhookconfigurations | grep kyverno
```

If the webhook configurations persist, delete them manually for the duration of the test and note that as a finding — it means a hard pod failure leaves the cluster in the `Fail`/`Ignore` trade-off regardless of VAP.

Then attempt a violating Pod:

```bash
kubectl run test-violation --image=nginx
```

**Expected outcome** — denial attributed to the VAP, with no mention of a webhook:

```
The pods "test-violation" is invalid: ValidatingAdmissionPolicy 'require-env-label'
with binding 'require-env-label-binding' denied request: Pods must have the 'env' label.
```

Confirm the compliant case still passes:

```bash
kubectl run test-ok --image=nginx --labels=env=dev
```

Restore:

```bash
kubectl scale deployment kyverno-admission-controller -n kyverno --replicas=3
kubectl scale deployment kyverno-background-controller -n kyverno --replicas=2
kubectl scale deployment kyverno-reports-controller -n kyverno --replicas=1
kubectl get validatingwebhookconfigurations | grep kyverno   # webhooks should return
```

### Test 3 — Exception survival during an outage

**Goal:** confirm pre-existing exceptions continue to exempt workloads while Kyverno is down.

1. With Kyverno running, create a `PolicyException` against `require-env-label` for a named workload.
2. **Verify translation before scaling down** — this is the step that actually proves the mechanism:

   ```bash
   kubectl get validatingadmissionpolicy require-env-label \
     -o jsonpath='{.spec.matchConstraints.excludeResourceRules}' | jq
   ```

   If this is empty, the exception was **not** translated and the rest of the test will fail. Stop and record why.
3. Scale Kyverno down and confirm webhook removal, as in Test 2.
4. Deploy the excepted workload (violating the policy, matching the exception).

**Expected outcome:** admitted successfully, proving the exclusion is enforced natively.

**Run this test twice** — once with a `kyverno.io/v2` `PolicyException` and once with a `policies.kyverno.io` CEL-based `PolicyException` against a `ValidatingPolicy`. The second case is the one we don't have documented confirmation for, and the answer determines whether our self-service exception model is compatible with VAP at all.

### Test 4 — New exception applied during an outage

**Goal:** document the failure mode honestly rather than discovering it in production.

1. Scale Kyverno down (webhooks removed).
2. Apply a new `PolicyException` for a violating workload.
3. Attempt to deploy that workload.

**Expected outcome:** the workload is **blocked**. The exception CRD is accepted into etcd but never translated.
4. Restore Kyverno, wait for reconciliation, retry — should now be admitted.

Record the reconciliation lag; it determines the "time to unblock a team after an incident" figure for the runbook.

### Test 5 — Cleanup and rollback

**Goal:** confirm we can back out.

```bash
kubectl delete clusterpolicy require-env-label
kubectl get validatingadmissionpolicies        # generated objects should be garbage-collected
```

Then disable `generateValidatingAdmissionPolicy` in Helm, upgrade, and confirm previously generated VAPs are removed and webhook-based enforcement resumes cleanly with no enforcement gap during the transition.

---

## 7. Risks & Open Questions

| # | Item | Why it matters |
| --- | --- | --- |
| 1 | Do CEL-based `PolicyException` resources translate into generated VAPs? | If not, the self-service exception model is incompatible with VAP and needs redesign. Blocking question. |
| 2 | Do Kyverno's webhook configurations reliably disappear on ungraceful pod failure? | If not, the resilience benefit is much weaker than advertised. |
| 3 | Loss of exception granularity (`conditions{}`, `podSecurity{}`, CEL matches) | Existing exceptions may silently become broader or narrower once translated. Needs a diff of every live exception. |
| 4 | Wider visibility of exception contents in cluster-scoped VAPs | Check against the platform's information-handling expectations. |
| 5 | Two enforcement paths active simultaneously | Both the webhook and the VAP evaluate the same policy while Kyverno is healthy. Confirm no double-reporting or duplicated denial messages confusing consumer teams. |
| 6 | `ClusterPolicy` deprecation in 1.18 | Any VAP work built on `ClusterPolicy` has a limited shelf life. Sequence against a `ValidatingPolicy` migration. |
| 7 | AKS vs EKS parity | Confirm identical behaviour on both; managed control planes differ in defaults. |
| 8 | Audit-mode gap | Audit results still depend on the reports controller. During an outage, `Deny` policies hold but `Audit` policies produce no record. |

---

## 8. Acceptance Criteria

* **AC1** — Every custom and vendored upstream policy is classified as VAP-eligible or not, with the specific blocking reason recorded for each ineligible policy.
* **AC2** — With generation enabled, Kyverno produces a matching `ValidatingAdmissionPolicy` and `ValidatingAdmissionPolicyBinding` for each eligible policy, with `status.validatingadmissionpolicy.generated: true`, and reconciles them after manual deletion or modification.
* **AC3** — With all Kyverno controllers scaled to zero and webhook configurations confirmed absent, a violating workload is denied by the API server with a message attributing the denial to the named `ValidatingAdmissionPolicy`, and a compliant workload is admitted.
* **AC4** — A `PolicyException` created before an outage is confirmed present in the generated VAP's `excludeResourceRules` and continues to exempt its workload while Kyverno is down.
* **AC5** — The behaviour of exceptions created, modified or deleted *during* an outage is tested and documented, including reconciliation lag on recovery.
* **AC6** — Both generation paths (`ClusterPolicy` + `validate.cel`, and `ValidatingPolicy` + `spec.autogen`) are tested, with a recommendation on which to standardise on.
* **AC7** — Rollback is demonstrated: disabling the feature removes generated objects and restores webhook enforcement with no enforcement gap.
* **AC8** — Results are equivalent on AKS and EKS, or the differences are documented.

---

## 9. References

* Kyverno — Validate Rules, ValidatingAdmissionPolicies section: <https://kyverno.io/docs/policy-types/cluster-policy/validate/>
* Kyverno — ValidatingPolicy: <https://kyverno.io/docs/policy-types/validating-policy/>
* Kyverno — Policy Exceptions: <https://kyverno.io/docs/guides/exceptions/>
* Kyverno — Configuring Kyverno (container flags, RBAC): <https://kyverno.io/docs/installation/customization/>
* Kubernetes — Validating Admission Policy: <https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/>
