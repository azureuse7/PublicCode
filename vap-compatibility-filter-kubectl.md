# Filtering Kyverno ValidatingPolicies for VAP Compatibility

A kubectl-only runbook for identifying which `ValidatingPolicy` /
`NamespacedValidatingPolicy` resources fit Kubernetes' `ValidatingAdmissionPolicy`
(VAP) execution model — using Kyverno's own generation status as the source of
truth, rather than static analysis of policy YAML.

This assumes `--generateValidatingAdmissionPolicy=true` is already set on the
admission controller, along with the RBAC to create VAPs and VAP bindings.

## 0. Confirm resource names and status shape on your cluster

Field names vary by version — confirm before scripting against them at scale.

```bash
kubectl api-resources | grep -i valid
```

Then inspect one already-applied policy's raw status:

```bash
kubectl get vpol <existing-policy-name> -o json | jq .status
```

The commands below use `vpol` and `status.generated` / `status.message`, based
on the shorthand used throughout Kyverno's GitHub issues and the one confirmed
real-world status example available. Adjust to match what the two commands
above actually return on your cluster.

> **Run this against a test cluster, not production.** The moment
> `autogen.enabled=true` produces a real VAP object, that binding enforces at
> the API server immediately and independently of Kyverno's webhook —
> instantly, if the policy's `validationActions` is `Deny`.

## 1. Turn on VAP generation for every policy, without touching the chart

```bash
for p in $(kubectl get vpol -o jsonpath='{.items[*].metadata.name}'); do
  kubectl patch vpol "$p" --type=merge \
    -p '{"spec":{"autogen":{"validatingAdmissionPolicy":{"enabled":true}}}}'
done
```

Repeat against `namespacedvalidatingpolicies -A` if that kind is in use too.
This is a live-test shortcut — anything you decide to keep still needs the
same value set in the actual Helm chart values.

## 2. Read Kyverno's verdict

```bash
kubectl get vpol \
  -o custom-columns='NAME:.metadata.name,GENERATED:.status.generated,MESSAGE:.status.message'
```

This is the actual filter:

- `GENERATED=true` → the policy fits VAP's execution model on this Kyverno version
- `GENERATED=false` → `MESSAGE` carries Kyverno's own reason

Because this queries the `validatingpolicies` type specifically,
`ImageValidatingPolicy` and any other kind never appear — no kind-checking
logic required, unlike scanning raw YAML files.

## 3. Don't fully trust `generated: true`

Kyverno has shipped a bug where success events fired for policies whose
generation was actually skipped
([kyverno/kyverno#13722](https://github.com/kyverno/kyverno/issues/13722)).
This is cheap to rule out — it finds anything claiming `true` with no matching
VAP object:

```bash
comm -23 \
  <(kubectl get vpol -o json | jq -r '.items[] | select(.status.generated==true) | .metadata.name' | sort) \
  <(kubectl get validatingadmissionpolicy -l app.kubernetes.io/managed-by=kyverno -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort)
```

Anything printed here is unresolved, not a confirmed candidate.

## 4. Cross-check PolicyExceptions

Older Kyverno docs state a PolicyException blocks VAP generation outright for
the policy it targets. That specific behavior is also the subject of an open
tracking item —
[kyverno/kyverno#10197](https://github.com/kyverno/kyverno/issues/10197),
"Support generating VAPs from PolicyExceptions" — so treat it as
version-dependent rather than settled. This shows what's affected right now
rather than assuming:

```bash
comm -12 \
  <(kubectl get policyexception -A -o jsonpath='{range .items[*]}{range .spec.policyRefs[*]}{.name}{"\n"}{end}{end}' | sort -u) \
  <(kubectl get vpol -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort -u)
```

Cross-reference the output against step 2 — if any of these show
`generated: false` with an otherwise-unexplained message, this is probably why.

## Where this stops being reliable

Step 2's message is confirmed self-explanatory for the pod-controller-autogen
conflict — Kyverno's actual text is:

```
skip generating ValidatingAdmissionPolicy: pod controllers autogen is enabled.
```

There's no confirmation it's equally articulate for a policy that fails
because it calls `resource.Get()` or another Kyverno-only CEL function. That
failure could show a clean skip message, or it might only surface when the
generated (and invalid) VAP is rejected by the API server's own CEL compiler —
which may never appear in the VPOL's status at all.

If a policy shows `generated: false` with no informative message, or `true`
but gets flagged by step 3, check the admission-controller pod logs and
`kubectl describe vpol <name>` events next. Don't assume the message field
always tells the whole story.
