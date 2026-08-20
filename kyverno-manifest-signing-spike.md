# Spike: Kyverno Manifest Signature Verification for PolicyExceptions

**Ticket scope:** Evaluate Kyverno's Sigstore `k8s-manifest-sigstore` manifest-signing capability. Determine whether it can gate *all* policy exemptions, identify other valid use cases, and document how to achieve "only signed exemptions allowed in any cluster."

---

## 1. TL;DR

- The feature works as advertised: sign a manifest, Kyverno verifies the signature and rejects anything that doesn't match the signed original.
- **It only exists on `ClusterPolicy`** (Kyverno's legacy JMESPath engine). Kyverno's own migration guide confirms this directly — its field-by-field mapping table lists `spec.rules.validate.manifests` → **"Not supported"** in the CEL-based types (proof in §3.1). `ClusterPolicy` itself was marked Deprecated in Kyverno 1.17 and is scheduled for removal in v1.20, estimated Oct 2026 (timeline in §3.4).
- The upstream `k8s-manifest-sigstore` tool itself is still flagged "not ready for production use" in its own README (§3, footnote).
- The legacy `kyverno.io` `PolicyException` — the same API version our current guardrails target — is *also* named explicitly in Kyverno's deprecation notice, separate from the ClusterPolicy issue (§3.5).
- **Recommendation:** don't build the PolicyException control on this feature. Use it as a documented, demoed PoC for the spike, but implement "signed-only exemptions" at the GitOps/commit layer instead (§6), which reuses guardrails already in place and doesn't inherit the removal deadline.

---

## 2. Concepts

### 2.1 What it is
Sigstore's [`k8s-manifest-sigstore`](https://github.com/sigstore/k8s-manifest-sigstore) project extends container-image signing (cosign) to arbitrary Kubernetes YAML. You sign a manifest's *content*; later, anyone (or an admission controller) can verify it hasn't been tampered with — and see exactly what changed if it has.
[Source: k8s-manifest-sigstore project README](https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md)

### 2.2 How signing works
```bash
cosign generate-key-pair
kubectl-sigstore sign -f manifest.yaml -k cosign.key --tarball no -o manifest-signed.yaml
```
This computes a signature over the manifest content and writes it back onto the object as two annotations:
- `cosign.sigstore.dev/message` — a compressed copy of the originally-signed content
- `cosign.sigstore.dev/signature` — the signature itself

(There's also a mode that bundles the manifest as an OCI artifact and pushes it to a registry — the `--tarball` flag controls this — but inline annotations are simpler for something like a PolicyException.)
[Source: Kyverno — Validate Rules, "Manifest Validation" section](https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation)

### 2.3 How Kyverno verifies it
A `ClusterPolicy` `validate.manifests` rule matches a kind, lists one or more **attestors**, and at admission time:
1. Verifies the embedded signature against the attestor
2. Re-serializes the *current* object being submitted
3. Diffs it against the originally-signed content
4. Allows if they match and the signature is valid; denies with a field-level diff if not

[Source: Kyverno — Validate Rules, "Manifest Validation" section](https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation)

### 2.4 Keyed vs keyless
- **Keyed** — static keypair (`cosign generate-key-pair`). Simple, but you own key storage/rotation.
- **Keyless** — OIDC identity via Fulcio. The signer authenticates (GitHub/Google/corporate SSO), gets a short-lived cert, and the policy verifies the *subject* + *issuer* instead of a fixed key. No key management; the identity of the human approver is what's checked. Enabled via `COSIGN_EXPERIMENTAL=1` in the signing tool.

[Source: k8s-manifest-sigstore README — keyless signing flag](https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md) · [Source: k8s-manifest-sigstore Go package docs](https://pkg.go.dev/github.com/sigstore/k8s-manifest-sigstore)

### 2.5 Field exclusions, multi-signature, dry-run
- `ignoreFields` — exempt specific paths (e.g. `spec.replicas`) so signatures survive expected mutation by controllers
- `attestors[].count` — require N-of-M signatures (e.g. two approvers)
- `dryRun` — dry-runs admission in a sandbox namespace first, to strip out noise from defaulting/mutating webhooks before diffing

Full field reference: `kubectl explain clusterpolicy.spec.rules.validate.manifests`
[Source: Kyverno — Validate Rules, "Manifest Validation" section](https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation)

---

## 3. Critical finding: this is a deprecated-engine-only feature — direct proof

### 3.1 The direct proof: Kyverno's own migration-mapping table says "Not supported"

Kyverno publishes an official field-by-field mapping from every legacy `ClusterPolicy` capability to its CEL-based (`ValidatingPolicy`) equivalent. In the **Validate Rule** mapping table, the row for manifest signing reads:

| ClusterPolicy field | CEL-based policies |
|---|---|
| `spec.rules.validate.manifests` | **Not supported** |

That's as direct an answer as Kyverno's own documentation gives — this is not an inference, it's stated outright.
[Source: Kyverno — Migrating to CEL Policies, "Validate Rule" section](https://kyverno.io/docs/guides/migration-to-cel/#validate-rule)

Worth flagging for the team: the same page opens with a banner stating *"The CEL-based policy types provide full feature parity as of v1.19."* Don't take that at face value without checking the table underneath it — the table on that same page lists **four** `ClusterPolicy` validate sub-features as unsupported in CEL: `manifests`, `podSecurity`, `allowExistingViolations`, and `failureActionOverrides`. Manifest signing is explicitly one of the named gaps, on the same page as the parity claim.
[Same source: Migration Guide — deprecation notice + Validate Rule table](https://kyverno.io/docs/guides/migration-to-cel/)

### 3.2 Corroborating evidence: the CEL function library has no equivalent capability

Independently, the CEL environment available to `ValidatingPolicy` (the "CEL Libraries" reference) lists every extended function Kyverno provides — Hash (`md5`/`sha1`/`sha256`), X509 (`x509.decode`), JSON/YAML parsing, HTTP calls, GlobalContext, Transform, Random, Math, Time. None of them is a packaged sigstore/cosign manifest-verify-and-diff function. The only Sigstore/Cosign integration in the CEL world is scoped to container images, via `ImageValidatingPolicy`.
[Source: Kyverno — CEL Libraries reference](https://main.kyverno.io/docs/policy-types/cel-libraries/) · [Source: Kyverno — ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/)

### 3.3 The deprecation is tracked as real engineering work, not just messaging

- GitHub issue [kyverno/kyverno#16865](https://github.com/kyverno/kyverno/issues/16865): *"The legacy kyverno.io policy types (ClusterPolicy, Policy, ClusterCleanupPolicy, CleanupPolicy) are deprecated in 1.19 and planned for removal in 1.20."* Filed as part of parent tracking issue #16302, titled *"1.19 — feature parity + hard deprecation of legacy kyverno.io policy types."*
- The official `kyverno-policies` Helm chart already defaults to the new type. Its `values.yaml` sets `policyType: ValidatingPolicy` by default, with an inline source comment: *"Set to `ClusterPolicy` to keep installing the legacy kyverno.io policy types, which are deprecated and will be removed in a future release."*
  [Source: kyverno/kyverno — `charts/kyverno-policies/values.yaml`](https://github.com/kyverno/kyverno/blob/main/charts/kyverno-policies/values.yaml)

### 3.4 Timeline

| Milestone | Date | Status | Source |
|---|---|---|---|
| v1.17 | Jan 2026 | `ClusterPolicy` / `CleanupPolicy` publicly marked **Deprecated**; migration guide and schedule published | [Kyverno 1.17 announcement](https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/) |
| v1.18 | Apr 2026 | Critical fixes only for legacy types | [Kyverno 1.17 announcement](https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/) |
| v1.19 | Jul 2026 | "Hard deprecation" certified; CEL types declared feature-parity-complete, with the exceptions noted in §3.1 | [Migration Guide](https://kyverno.io/docs/guides/migration-to-cel/) · [GitHub issue #16865](https://github.com/kyverno/kyverno/issues/16865) |
| v1.20 | Oct 2026 (estimated) | Planned removal of `ClusterPolicy`, `Policy`, `CleanupPolicy`, and the legacy `kyverno.io` `PolicyException` | [Kyverno 1.17 announcement](https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/) — independently corroborated by [heise online](https://www.heise.de/en/news/CEL-Policies-in-Kyverno-1-17-Production-Ready-Legacy-APIs-Deprecated-11183145.html) and [Nirmata](https://nirmata.com/2026/07/26/kyverno-policy-migration-to-cel-based-policies/) (Kyverno's founding/maintaining company) |

### 3.5 Also worth flagging: our own test fixture's API is on the same removal list

The Migration Guide's deprecation notice names casualties explicitly: *"`ClusterPolicy`, `Policy`, `CleanupPolicy`, and the legacy `kyverno.io` PolicyException are deprecated as of Kyverno v1.19 and will be removed in v1.20."* That means the `kyverno.io/v2 PolicyException` object tested against in §5 — the same API version our current guardrail VAPs and RBAC model target today — is itself scheduled for replacement by the CEL-aligned `policies.kyverno.io` `PolicyException`. Worth its own migration conversation, separate from this spike.
[Source: Migration Guide, "PolicyException" section](https://kyverno.io/docs/guides/migration-to-cel/#policyexception)

### 3.6 What's unaffected

`PolicyException` as a concept reached stable `v1` (the new `policies.kyverno.io` group) in the 1.17 release — it's the legacy `kyverno.io` version and the `ClusterPolicy` engine used to gate it that are going away, not the idea of exceptions itself.
[Source: Kyverno 1.17 announcement, "CEL Policy Types reach v1 (GA)"](https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/)

### 3.7 Footnote: the signing tool's own maturity caveat

Separate from the Kyverno-side deprecation, the signing tool itself carries a standing warning: *"Still under development, not ready for production use yet!"*
[Source: k8s-manifest-sigstore README](https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md)

---

## 4. Answering the acceptance criteria

**Can manifest validation cover all policy exemptions?**
Technically yes for any single kind, including `PolicyException` — but given §3, adopting it here means building on infrastructure that's mid-sunset, on roughly the same timeline as the rollout itself. Not recommended as the PolicyException control plane.

**Other valid use cases?**
Tamper-evidence for a small number of static, rarely-touched bootstrap objects provisioned outside GitOps (core `Namespace` defs, CRDs) where proof of exact-content approval matters. Not a fit for anything that changes routinely or needs to survive past v1.20.

**How to achieve "only signed exemptions allowed in any cluster"?**
Two paths — both documented below. §5 is the native-feature PoC (useful evidence for this spike). §6 is what I'd actually recommend running in production.

---

## 5. Hands-on local test guide

### 5.1 Prerequisites
- Docker (or Podman) running locally
- `kubectl`
- `helm` 3.x
- Optional: Go 1.21+ (only needed if you use the `go install` routes below instead of prebuilt binaries)

### 5.2 Step 1 — Spin up a local cluster
```bash
# macOS
brew install kind
# Linux (or use: go install sigs.k8s.io/kind@v0.32.0)
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64
chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind

kind create cluster --name kyverno-manifest-poc
kubectl cluster-info --context kind-kyverno-manifest-poc
```
Kyverno should already be running on whichever cluster you use here before continuing — skip straight to Step 2 below if you're pointing this at an existing kind/AKS/EKS test cluster that already has it installed.
[Source: kind Quick Start](https://kind.sigs.k8s.io/docs/user/quick-start/) · [kind repo](https://github.com/kubernetes-sigs/kind)

### 5.3 Step 2 — Install cosign
```bash
# macOS
brew install cosign
# Linux
curl -O -L "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64"
sudo mv cosign-linux-amd64 /usr/local/bin/cosign
sudo chmod +x /usr/local/bin/cosign
cosign version
```
[Source: cosign `generate-key-pair` reference](https://github.com/sigstore/cosign/blob/main/doc/cosign_generate-key-pair.md)

### 5.4 Step 3 — Install kubectl-sigstore
```bash
go install github.com/sigstore/k8s-manifest-sigstore/cmd/kubectl-sigstore@latest
# or grab a prebuilt binary from the releases page and put it on your PATH
```
Installed this way it's invocable either as `kubectl-sigstore <cmd>` directly, or as `kubectl sigstore <cmd>` (kubectl auto-discovers PATH binaries named `kubectl-*`). Commands below use the direct form.
[Source: k8s-manifest-sigstore install docs](https://github.com/sigstore/k8s-manifest-sigstore) · [releases](https://github.com/sigstore/k8s-manifest-sigstore/releases)

### 5.5 Step 4 — Generate a signing key pair
```bash
cosign generate-key-pair
# prompts for a password; writes cosign.key (private) and cosign.pub (public)
```
[Source: Sigstore — Signing with Self-Managed Keys](https://docs.sigstore.dev/cosign/key_management/signing_with_self-managed_keys/)

### 5.6 Step 5 — Create a test PolicyException
`pe-test.yaml`:
```yaml
apiVersion: kyverno.io/v2
kind: PolicyException
metadata:
  name: allow-hostpath-important-tool
  namespace: default
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
(Shape taken from Kyverno's own PolicyException example. Note: this is the *legacy* `kyverno.io` PolicyException API — see §3.5 for why that matters beyond this PoC.)
[Source: Kyverno — Validate Rules, ValidatingAdmissionPolicies/PolicyException example](https://kyverno.io/docs/policy-types/cluster-policy/validate/#validatingadmissionpolicies)

Sign it:
```bash
kubectl-sigstore sign -f pe-test.yaml -k cosign.key --tarball no -o pe-test-signed.yaml
```
Open `pe-test-signed.yaml` — you'll see `cosign.sigstore.dev/message` and `cosign.sigstore.dev/signature` annotations added under `metadata.annotations`.

**Optional sanity check**, independent of Kyverno, before wiring anything into the cluster:
```bash
kubectl-sigstore verify-resource -f pe-test-signed.yaml -k cosign.pub
```
[Source: `verify-resource` flags — k8s-manifest-sigstore Go package docs](https://pkg.go.dev/github.com/sigstore/k8s-manifest-sigstore)

### 5.7 Step 6 — Write the enforcing ClusterPolicy
`require-signed-policy-exceptions.yaml`:
```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-signed-policy-exceptions
spec:
  background: true
  rules:
    - name: require-signed-policy-exceptions
      match:
        any:
          - resources:
              kinds:
                - PolicyException
      validate:
        failureAction: Enforce
        message: "PolicyExceptions must be signed by an approved key before they are allowed."
        manifests:
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: |-
                      -----BEGIN PUBLIC KEY-----
                      <paste contents of cosign.pub here>
                      -----END PUBLIC KEY-----
          ignoreFields:
            - objects:
                - kind: PolicyException
              fields:
                - metadata.resourceVersion
                - metadata.uid
                - metadata.creationTimestamp
                - metadata.generation
                - metadata.managedFields
```
```bash
kubectl apply -f require-signed-policy-exceptions.yaml
kubectl get clusterpolicy require-signed-policy-exceptions
```
[Source: Kyverno — Manifest Validation example policy](https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation)

### 5.8 Step 7 — Negative test: unsigned is rejected
```bash
kubectl apply -f pe-test.yaml
```
Expected: denied — no valid signature annotation present. Capture the exact error text for the Confluence page.

### 5.9 Step 8 — Positive test: signed is accepted
```bash
kubectl apply -f pe-test-signed.yaml
kubectl get policyexception allow-hostpath-important-tool -n default
```
Expected: created successfully.

### 5.10 Step 9 — Tamper test (the key evidence for this spike)
Copy `pe-test-signed.yaml` to `pe-test-tampered.yaml` and change something in the *body* without re-signing — e.g. add a second name under `match.any[0].resources.names`:
```yaml
          names:
            - important-tool
            - sneaky-tool
```
```bash
kubectl apply -f pe-test-tampered.yaml
```
Expected: denied, with Kyverno's response showing the exact field-level diff between the signed original and what was submitted. This diff output is the strongest visual for the Confluence write-up — screenshot it.

### 5.11 Step 10 — `ignoreFields` in practice (optional)
PolicyExceptions aren't typically mutated by controllers post-creation, so this matters less here than it would for, say, a Deployment's `spec.replicas`. To see the mechanism anyway: add a label such as `metadata.labels.reviewed-by` to the `ignoreFields` list, then apply a copy of the signed manifest with only that label changed — it should still be accepted, unlike Step 9.

### 5.12 Step 11 — `dryRun` mode (optional, more advanced)
```yaml
manifests:
  dryRun:
    enable: true
    namespace: default
```
Requires Kyverno's ServiceAccount to have create permissions (dry-run) on the target kind in that namespace. Eliminates false diffs caused by other mutating webhooks/defaulting.
[Source: Kyverno — Manifest Validation, `dryRun` section](https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation)

### 5.13 Step 12 — Keyless signing (optional)
```bash
export COSIGN_EXPERIMENTAL=1
kubectl-sigstore sign -f pe-test.yaml --tarball no -o pe-test-signed-keyless.yaml
```
This opens a browser OIDC flow (GitHub/Google/Microsoft) to prove identity, and signs using a short-lived Fulcio cert plus a public Rekor transparency-log entry. The policy attestor then becomes:
```yaml
manifests:
  attestors:
    - count: 1
      entries:
        - keyless:
            subject: "you@yourcompany.com"
            issuer: "https://accounts.google.com"
            rekor:
              url: https://rekor.sigstore.dev
```
[Source: k8s-manifest-sigstore README — keyless flow](https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md)

**Flag for our environment:** this needs egress to the public Fulcio/Rekor endpoints, which is exactly the kind of thing our NetworkPolicy/pre-DNAT rules for Kyverno have needed manual carve-outs for before. Worth testing that egress path explicitly if you take this further.

### 5.14 Cleanup
```bash
kind delete cluster --name kyverno-manifest-poc
```

### 5.15 Evidence to capture for the Confluence page
- [ ] Error output from Step 7 (unsigned rejected)
- [ ] Success output from Step 8 (signed accepted)
- [ ] Diff output from Step 9 (tamper detected) — the headline screenshot
- [ ] `kubectl describe clusterpolicy require-signed-policy-exceptions` showing rule status
- [ ] Note the Kyverno chart/app version used, so the finding is reproducible before v1.20 lands

---

## 6. Recommended production approach (given §3)

Rather than building on a feature mid-deprecation, get the same outcome — "only signed/approved exemptions allowed" — at the delivery-pipeline layer, reusing what's already in place:

- PolicyExceptions can only be written by the GitOps controller SA (already partly true — the guard-policy stack permits `kustomize-controller` updates while everyone else needs break-glass)
- Require PR approval from a CODEOWNERS-protected path for anything under the PolicyException tree
- Require **signed Git commits** on that path — Sigstore's [`gitsign`](https://github.com/sigstore/gitsign) does keyless commit signing on the same Fulcio/Rekor identity model as `k8s-manifest-sigstore`, but is an actively maintained Sigstore subproject rather than an experimental one — enforce via branch protection requiring verified commits
- Existing guardrail VAPs stay as the runtime backstop, denying anything that didn't come through that pipeline

This achieves the acceptance criteria's goal without inheriting the v1.20 removal deadline, and without introducing a `ClusterPolicy` into a VPOL-only estate.

---

## 7. References

### Primary / official sources
1. Kyverno — Validate Rules / Manifest Validation: https://kyverno.io/docs/policy-types/cluster-policy/validate/#manifest-validation
2. Kyverno — Migrating to CEL Policies (the "Not supported" mapping table): https://kyverno.io/docs/guides/migration-to-cel/#validate-rule
3. Kyverno — CEL Libraries reference: https://main.kyverno.io/docs/policy-types/cel-libraries/
4. Kyverno — ImageValidatingPolicy overview: https://kyverno.io/docs/policy-types/image-validating-policy/
5. Kyverno — ValidatingPolicy overview: https://kyverno.io/docs/policy-types/validating-policy/
6. Kyverno official blog — "Announcing Kyverno Release 1.17!" (deprecation schedule): https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/
7. GitHub — kyverno/kyverno issue #16865 (deprecation tracked as engineering work): https://github.com/kyverno/kyverno/issues/16865
8. GitHub — kyverno/kyverno `charts/kyverno-policies/values.yaml` (chart defaults to `ValidatingPolicy`): https://github.com/kyverno/kyverno/blob/main/charts/kyverno-policies/values.yaml
9. Sigstore — k8s-manifest-sigstore project (incl. "not production ready" notice): https://github.com/sigstore/k8s-manifest-sigstore
10. k8s-manifest-sigstore README (install + usage + keyless flow): https://github.com/sigstore/k8s-manifest-sigstore/blob/main/README.md
11. k8s-manifest-sigstore Go package docs (`verify-resource` flags): https://pkg.go.dev/github.com/sigstore/k8s-manifest-sigstore
12. Sigstore — Signing with Self-Managed Keys (cosign): https://docs.sigstore.dev/cosign/key_management/signing_with_self-managed_keys/
13. cosign `generate-key-pair` reference: https://github.com/sigstore/cosign/blob/main/doc/cosign_generate-key-pair.md
14. Sigstore `gitsign` (Git commit signing): https://github.com/sigstore/gitsign
15. kind — Quick Start: https://kind.sigs.k8s.io/docs/user/quick-start/
16. kind — GitHub repo: https://github.com/kubernetes-sigs/kind

### Independent corroboration
17. heise online — "CEL Policies in Kyverno 1.17 Production Ready, Legacy APIs Deprecated": https://www.heise.de/en/news/CEL-Policies-in-Kyverno-1-17-Production-Ready-Legacy-APIs-Deprecated-11183145.html
18. Nirmata (Kyverno's founding/maintaining company) — "Kyverno Policy: Migration to CEL Based Policies": https://nirmata.com/2026/07/26/kyverno-policy-migration-to-cel-based-policies/
