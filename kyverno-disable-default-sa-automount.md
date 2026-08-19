# Kyverno MutatingPolicy — Disabling `automountServiceAccountToken` on `default` ServiceAccounts

**Question:** Will a `MutatingPolicy` with `mutateExisting.enabled: true` apply to ServiceAccounts that already exist in the cluster?

**Short answer:** Yes — the policy below is configured correctly to mutate existing `default` ServiceAccounts. But there are several things that commonly cause it to silently do nothing.

---

## The policy

```yaml
apiVersion: policies.kyverno.io/v1alpha1
kind: MutatingPolicy
metadata:
  name: disable-automountserviceaccounttoken
  annotations:
    policies.kyverno.io/title: Disable automountServiceAccountToken
    policies.kyverno.io/category: Other, EKS Best Practices
    policies.kyverno.io/severity: medium
    policies.kyverno.io/subject: ServiceAccount
spec:
  evaluation:
    admission:
      enabled: true
    mutateExisting:
      enabled: true
  matchConstraints:
    resourceRules:
    - apiGroups: [""]
      apiVersions: ["v1"]
      operations: ["CREATE", "UPDATE"]
      resources: ["serviceaccounts"]
      resourceNames: ["default"]
  mutations:
  - patchType: ApplyConfiguration
    applyConfiguration:
      expression: |
        Object{
          automountServiceAccountToken: false
        }
```

---

## Why it works on existing ServiceAccounts

* `spec.evaluation.mutateExisting.enabled: true` turns on background processing against resources already in the cluster.
* `spec.targetMatchConstraints` is **not** defined, so Kyverno falls back to using `matchConstraints` as the target set. Targets = every existing ServiceAccount named `default`, in every namespace.
* `spec.evaluation.admission.enabled: true` separately covers *future* ServiceAccounts via the admission webhook — including the `default` SA that the controller-manager auto-creates whenever a new namespace appears.

### Mechanics to keep in mind

| Behaviour | Detail |
|---|---|
| **Asynchronous** | There is a variable delay between the trigger being observed and the existing resource actually being patched. Don't judge it one second after `kubectl apply`. |
| **Runs on policy create/update** | It is a one-shot background scan, not a continuous reconcile loop. Editing the policy (even trivially) re-triggers the scan. |
| **Idempotent** | Re-applying `automountServiceAccountToken: false` to an already-false SA produces no change and no mutation loop. |

---

## The #1 cause of failure: RBAC

This is by far the most common reason `mutateExisting` appears to do nothing.

* **Admission** mutations ride along on the AdmissionReview request — no extra permissions needed.
* **Background** mutations are genuine API writes performed by the Kyverno **background controller's** ServiceAccount, which does **not** have write access to `serviceaccounts` by default.

Kyverno's own docs put it plainly: custom permissions are almost always required, because these mutations occur outside of an AdmissionReview.

### 1. Check current permissions

```bash
kubectl auth can-i patch serviceaccounts --as=system:serviceaccount:kyverno:kyverno-background-controller -A
```

### 2. If it returns `no`, grant them via an aggregated ClusterRole

Do **not** edit Kyverno's built-in ClusterRoles — they are overwritten on upgrade. Use the aggregation label instead:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kyverno:mutate-serviceaccounts
  labels:
    rbac.kyverno.io/aggregate-to-background-controller: "true"
rules:
  - apiGroups: [""]
    resources: ["serviceaccounts"]
    verbs: ["get", "list", "watch", "update", "patch"]
```

Apply it:

```bash
kubectl apply -f kyverno-background-sa-rbac.yaml
```

### 3. Re-trigger the background scan

The scan only runs on policy create/update, so bump the policy after fixing RBAC:

```bash
kubectl annotate mutatingpolicy disable-automountserviceaccounttoken kyverno.io/rescan="$(date +%s)" --overwrite
```

---

## Verification

### Check the resulting state of every `default` SA

```bash
kubectl get sa default -A -o custom-columns=NS:.metadata.namespace,AUTOMOUNT:.automountServiceAccountToken
```

Anything still showing `<none>` has not been mutated.

### Check the background controller logs

```bash
kubectl -n kyverno logs deploy/kyverno-background-controller --tail=200 | grep -i -e mutat -e forbidden
```

`forbidden` in the output confirms the RBAC problem above.

### Check policy status

```bash
kubectl get mutatingpolicy disable-automountserviceaccounttoken -o yaml
```

---

## Important caveats

### 1. Already-running pods are NOT affected

This is the caveat people most often miss. A pod that already has its service account token projected keeps it until the pod is recreated. Patching the ServiceAccount only affects pods **scheduled after** the mutation.

The SAs can all look correct while every running workload is still mounting a token. To fully realise the change you must roll the workloads:

```bash
kubectl rollout restart deployment -n <namespace>
```

### 2. Pod spec overrides the ServiceAccount setting

Any pod with an explicit `spec.automountServiceAccountToken: true` still receives a token, regardless of the ServiceAccount setting. Pod-level always wins. If you need to enforce this end-to-end, pair the policy with a second one targeting Pods.

### 3. This includes `kube-system`

`matchConstraints` has no `namespaceSelector`, so the `default` SA in `kube-system`, `kube-public`, etc. is also mutated. This is normally harmless — system components use their own dedicated ServiceAccounts, not `default`. To exclude system namespaces anyway:

```yaml
  matchConstraints:
    namespaceSelector:
      matchExpressions:
      - key: kubernetes.io/metadata.name
        operator: NotIn
        values: ["kube-system", "kube-public", "kube-node-lease"]
    resourceRules:
    - apiGroups: [""]
      apiVersions: ["v1"]
      operations: ["CREATE", "UPDATE"]
      resources: ["serviceaccounts"]
      resourceNames: ["default"]
```

### 4. The change is durable

Nothing in Kubernetes resets `automountServiceAccountToken` on a ServiceAccount, so once patched it stays patched. If a `default` SA is ever deleted, the controller-manager recreates it and the admission path catches it immediately.

---

## Deterministic fallback for existing SAs

If you would rather not depend on the background scan for the resources that already exist, patch them directly. The policy still covers all future namespaces via admission.

**bash / Linux / macOS / Git Bash:**

```bash
kubectl get sa default -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\n"}{end}' | xargs -I{} kubectl patch serviceaccount default -n {} -p '{"automountServiceAccountToken":false}'
```

**PowerShell (Windows):**

```powershell
(kubectl get sa default -A -o json | ConvertFrom-Json).items | ForEach-Object { kubectl patch serviceaccount default -n $_.metadata.namespace --type=merge -p '{\"automountServiceAccountToken\":false}' }
```

---

## Rollout checklist

- [ ] Apply the `MutatingPolicy`
- [ ] Verify background controller RBAC (`kubectl auth can-i ...`)
- [ ] Apply the aggregated ClusterRole if needed
- [ ] Re-trigger the scan by updating the policy
- [ ] Wait for the async mutation, then verify all `default` SAs read `false`
- [ ] Roll existing workloads so running pods drop their mounted tokens
- [ ] Confirm no application actually depended on the `default` SA token

---

## Sources

* [MutatingPolicy — Kyverno docs](https://kyverno.io/docs/policy-types/mutating-policy/)
* [Customizing Permissions — Kyverno docs](https://kyverno.io/docs/installation/customization/)
* [MutatingPolicy (release 1.15) — Kyverno docs](https://release-1-15-0.kyverno.io/docs/policy-types/mutating-policy/)
