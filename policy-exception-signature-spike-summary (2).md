# Spike Summary: Signature Verification for PolicyExceptions

**Summary.** Anyone with RBAC access can create a PolicyException and have it admitted to the cluster, with no check that it's actually a valid, approved exception. This spike looks at whether we can add a check so that a PolicyException is only admitted if it carries a valid signature — if the signature is missing or invalid, the request is rejected.

I tested this locally: writing a ClusterPolicy with a `validate.manifests` rule (per Kyverno's own documentation), and signing test manifests using Sigstore's `k8s-manifest-sigstore` tooling. This worked as expected — though it's worth flagging that `k8s-manifest-sigstore` itself is still marked "not production-ready" in its own README.

> Kyverno — Validate Rules, Manifest Validation: https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation
> Sigstore — `k8s-manifest-sigstore` project README: https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md

## The blocker: no VPOL/CEL equivalent

We do hit a blocker: there is no VPOL/CEL equivalent to this feature. All of our policies are written as CEL-based ValidatingPolicy (VPOL), not the legacy ClusterPolicy. Three separate pieces of evidence support this:

**1. Kyverno's own migration guide states it directly, not just implies it.** Its field-by-field mapping table lists `spec.rules.validate.manifests` → **"Not supported"** for CEL-based policies — a flat statement, not something inferred from an absence.

> Kyverno — Migrating to CEL Policies, "Validate Rule" table: https://kyverno.io/docs/guides/migration-to-cel/#validate-rule

**2. The CEL function library has nothing that does this job either.** The full list of CEL functions available to ValidatingPolicy — Hash, X.509 decode, JSON/YAML parsing, HTTP calls, GlobalContext, Transform, Random, Math, Time — includes nothing that verifies a signature annotation against an object and diffs it against the currently-submitted content. If a CEL equivalent existed anywhere, it would be listed here, and it isn't.

> Kyverno — CEL Libraries reference: https://kyverno.io/docs/policy-types/cel-libraries/

**3. The only Sigstore/Cosign integration in the CEL-based world is scoped to container images, not arbitrary manifests.** `ImageValidatingPolicy` verifies image signatures and attestations pulled from an OCI registry — it has no path to verify a signature on a Kubernetes object like a PolicyException.

> Kyverno — ImageValidatingPolicy: https://kyverno.io/docs/policy-types/image-validating-policy/

This isn't a temporary oversight, either — the gap is tracked as real engineering work, and the deprecation of the legacy types is already reflected in Kyverno's own tooling defaults.

> GitHub — kyverno/kyverno issue #16865 ("legacy kyverno.io policy types... deprecated in 1.19 and planned for removal in 1.20"): https://github.com/kyverno/kyverno/issues/16865
> GitHub — kyverno-policies Helm chart `values.yaml` (defaults to `policyType: ValidatingPolicy`): https://github.com/kyverno/kyverno/blob/main/charts/kyverno-policies/values.yaml

## The workaround, and its drawbacks

Having looked into it, there is a possible workaround: we could add a single ClusterPolicy whose only job is to check that every PolicyException carries a valid signature. This does come with its own drawbacks.

**1. `ClusterPolicy` is on a clock.** Kyverno's own docs state v1.19 (current) is the final release with full support for `ClusterPolicy` — it's officially deprecated, with removal planned for v1.20, expected around October 2026, roughly two months out. There's no published statement on what happens to existing `ClusterPolicy` objects once v1.20 ships — no confirmed "upgrade blocked until you migrate" either way — so the behaviour at that point is genuinely unclear.

> Kyverno — Upgrading Kyverno (deprecation notice, v1.19 final full-support release): https://kyverno.io/docs/installation/upgrading/
> Kyverno — Announcing Kyverno Release 1.17! (deprecation schedule, ~Oct 2026 removal): https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/

**2. It reopens the failure mode we just closed.** We moved to VPOL with VAP autogen specifically so enforcement survives Kyverno going down or crashing — a `ValidatingAdmissionPolicy` is evaluated in-process by the API server itself, with no webhook and no external dependency in the path. Introducing a `ClusterPolicy` for this check puts a Kyverno-webhook-dependent policy back in the critical path, working directly against that resilience goal.

> Kubernetes — Validating Admission Policy (in-process alternative to admission webhooks): https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/
> Kubernetes — Validating Admission Policy GA announcement (availability tied to the API server, not a separate webhook pod): https://kubernetes.io/blog/2024/04/24/validating-admission-policy-ga/

## References

1. Kyverno — Validate Rules / Manifest Validation: https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation
2. Sigstore — `k8s-manifest-sigstore` project README: https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md
3. Kyverno — Migrating to CEL Policies, "Validate Rule" table: https://kyverno.io/docs/guides/migration-to-cel/#validate-rule
4. Kyverno — CEL Libraries reference: https://kyverno.io/docs/policy-types/cel-libraries/
5. Kyverno — ImageValidatingPolicy: https://kyverno.io/docs/policy-types/image-validating-policy/
6. GitHub — kyverno/kyverno issue #16865: https://github.com/kyverno/kyverno/issues/16865
7. GitHub — kyverno-policies Helm chart `values.yaml`: https://github.com/kyverno/kyverno/blob/main/charts/kyverno-policies/values.yaml
8. Kyverno — Upgrading Kyverno: https://kyverno.io/docs/installation/upgrading/
9. Kyverno — Announcing Kyverno Release 1.17!: https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/
10. Kubernetes — Validating Admission Policy: https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/
11. Kubernetes — Validating Admission Policy GA announcement: https://kubernetes.io/blog/2024/04/24/validating-admission-policy-ga/
