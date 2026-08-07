# VAPs protecting Kyverno enforcement

| File | Protects | Break-glass |
|---|---|---|
| `01-protect-kyverno-webhooks.yaml` | Kyverno's `Validating`/`MutatingWebhookConfiguration` | exempt service accounts |
| `02-protect-policy-exceptions.yaml` | `PolicyException` resources | delete the binding |
| `03-protect-the-vaps-themselves.yaml` | *optional* — the two above | `cluster-break-glass` group |

These run in-process in kube-apiserver, so they hold when Kyverno is down, broken or
uninstalled. A Kyverno policy protecting the same resources cannot: it is enforced
*through* the webhook being deleted.

## Preflight — do these four before applying anything

**1. Server must be 1.30+ for `admissionregistration.k8s.io/v1`.**
Your local kubectl is 1.26, which predates the GA API entirely.

```bash
kubectl version -o yaml | grep -A3 serverVersion
```

On 1.28–1.29 change every `apiVersion` in these files to
`admissionregistration.k8s.io/v1beta1`. Below 1.28, VAP is alpha and this approach
is not viable — protect the webhooks with RBAC instead.

**2. Confirm the real Kyverno service account names**, or VAP 01 breaks Kyverno on
its next restart. Kyverno rewrites its own webhook configurations at runtime.

```bash
kubectl -n kyverno get sa
```

**3. Confirm the webhook name prefix** that `only-kyverno-webhooks` matches. A
custom Helm release name or `fullnameOverride` changes it, and the policy would
then silently match nothing.

```bash
kubectl get validatingwebhookconfiguration,mutatingwebhookconfiguration \
  -o custom-columns=NAME:.metadata.name
```

**4. Confirm which API group serves PolicyException** and trim VAP 02 to match.

```bash
kubectl api-resources | grep -i policyexception
```

## Rollout — audit first, always

Never go straight to `Deny`. Flip each binding to audit mode, leave it a few days,
and confirm nothing legitimate is being caught:

```yaml
validationActions: ["Audit", "Warn"]
```

`Warn` surfaces in kubectl output immediately; `Audit` writes
`validation.policy.admission.k8s.io/validation_failure` into the API server audit
log (CloudWatch, on EKS). If your GitOps controller or a Kyverno upgrade appears
there, add it to the exemptions *before* switching to `["Deny"]`.

Apply in order — 01 and 02 first, and only add 03 once both are proven:

```bash
kubectl apply -f 01-protect-kyverno-webhooks.yaml
kubectl apply -f 02-protect-policy-exceptions.yaml
```

## Verify they work

```bash
# should be DENIED
kubectl delete validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg --dry-run=server
kubectl apply --dry-run=server -f some-policyexception.yaml

# should still SUCCEED - proves Kyverno can still manage its own webhooks
kubectl -n kyverno rollout restart deploy/kyverno-admission-controller
kubectl -n kyverno get deploy   # admission controller stays Ready
```

That second check is the one people skip, and it is the one that breaks clusters.

## Break-glass: legitimately adding an exception

```bash
kubectl delete validatingadmissionpolicybinding protect-kyverno-policy-exceptions-binding
kubectl apply -f my-exception.yaml
kubectl apply -f 02-protect-policy-exceptions.yaml
```

Deleting the *binding* rather than the policy keeps the definition in place, so
step 3 is a one-line revert. Under GitOps each step is a reviewed PR — which is the
whole point: granting an exemption becomes conspicuous instead of quiet.

## Gotchas found while writing these

- **`variables` are not available inside `matchConditions`.** Kubernetes evaluates
  matchConditions before the rest of the policy, so those expressions must stand
  alone. `_validate.py` checks for this.
- **`validations[].expression` returns `true` to ALLOW.** Opposite of Kyverno's
  deny semantics — hence the bare `false` in each policy, meaning "anything that
  survived matchConditions is denied".
- **`object` is null on DELETE** (and `oldObject` is null on CREATE). These
  policies use `request.name`, which is populated for all three operations, rather
  than reaching into either object.
- **`request.name` can be empty on CREATE with `generateName`.** Irrelevant here —
  the threat is editing or deleting *existing* webhook configs, and nobody creates
  a webhook config with generateName. Worth knowing if you extend the match.
- **Exemptions live in `matchConditions`, so exempt principals produce no audit
  record at all** — the policy is skipped, not passed. Move the identity check
  into `validations` if you want exempt access logged.

## Honest limits

- Structurally validated only (`python _validate.py`): schema fields, CEL bracket
  and quote balance, binding→policy references. **The CEL has not been evaluated
  against a real API server** — kubectl here is 1.26 and could not authenticate to
  the cluster. Run the audit-mode rollout above before trusting it.
- A VAP raises the cost of disabling Kyverno and guarantees a trail. It is not an
  absolute barrier: whoever can delete the binding can still get through. File 03
  narrows that to a named group; RBAC on `validatingadmissionpolicybindings` is
  the other half and is not covered here.
- File 01 protects the webhook *configurations*. Scaling the Kyverno deployment to
  zero is a different bypass — that one fails closed only if your webhooks are set
  to `failurePolicy: Fail`. Worth confirming separately.
