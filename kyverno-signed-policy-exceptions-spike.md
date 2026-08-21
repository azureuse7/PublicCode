# Spike — Signed YAML manifests for Kyverno PolicyExceptions

**Ticket goal:** determine whether Kyverno's manifest-signature validation can be used to ensure **only signed PolicyExceptions are accepted in any cluster**, whether it can cover *all* policy exemptions, and whether there are other valid use cases. Document the implementation in detail.

| | |
|---|---|
| **Date** | 2026-08-20 |
| **Feature** | `validate.manifests` (Kyverno ≥ v1.8), backed by [sigstore/k8s-manifest-sigstore](https://github.com/sigstore/k8s-manifest-sigstore) |
| **Our estate** | Kyverno via Helm on EKS + AKS; policies are CEL-based `ValidatingPolicy`/`MutatingPolicy` (`policies.kyverno.io`) |
| **Status** | Spike — no production change proposed yet |

---

## 1. Executive summary

**The feature works and the use case is legitimate, but three things need to be understood before committing to it.**

| # | Finding | Impact |
|---|---|---|
| **F1** | The signing tool, `sigstore/k8s-manifest-sigstore`, states in its own README: *"Still under development, not ready for production use yet!"* It has ~89 GitHub stars. | **Blocker for "all exemptions".** This tool would sit in every workflow that produces or edits an exception. Kyverno's *verification* side is stable; the *signing* side is not. |
| **F2** | `validate.manifests` is a **legacy `ClusterPolicy` (`kyverno.io/v1`) feature**. Our estate has moved to CEL `ValidatingPolicy`. There is no CEL-type equivalent. | Adopting this means **reintroducing a legacy policy type** alongside the new ones. Verify on our version — see §7.1. |
| **F3** | A PolicyException can exempt the very policy that requires signed exceptions. | **Self-defeating unless explicitly closed.** Mitigation in §5.5 — this must not be left as a follow-up. |

**Recommendation:** do **not** adopt manifest signing as the *primary* control for policy exemptions. The primary control should be **RBAC + a dedicated, locked-down exception namespace + GitOps with signed commits** (§6), which delivers most of the assurance at a fraction of the operational cost and with no dependency on pre-production tooling.

Manifest signing is worth keeping on the roadmap as a **defence-in-depth layer**, and there is a **stronger use case for it elsewhere** — signing the *policies themselves* rather than the exceptions (§4.1).

The full implementation is documented in §5 regardless, as required by the acceptance criteria, and the PoC in §7 is worth running to validate these findings first-hand.

---

## 2. How the feature actually works

### 2.1 Mechanism

1. An engineer (or CI) signs a YAML manifest with `kubectl sigstore sign`, using either a **key pair** (cosign key) or **keyless** signing (OIDC identity → Fulcio short-lived cert → Rekor transparency log).
2. The tool bundles the manifest and records the signature. By default it pushes a bundle as an **OCI image** and writes a reference into `metadata.annotations`; it can also embed signature material in annotations directly.
3. The signed YAML is applied to the cluster.
4. Kyverno's admission webhook intercepts it, extracts the signature material from the annotations, reconstructs the signed payload, and verifies it against the configured **attestors**.
5. If the manifest was altered after signing — *any* field not listed in `ignoreFields` — verification fails and the request is denied.

The docs are explicit about how strict this is: *"even the value of the location label from europe to asia will cause the signed manifest to be invalid"*.

### 2.2 Policy shape

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: validate-secrets
spec:
  background: true
  rules:
    - name: validate-secrets
      match:
        any:
          - resources:
              kinds: [Secret]
      validate:
        failureAction: Enforce
        manifests:
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: |-
                      -----BEGIN PUBLIC KEY-----
                      MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEStoX3dPCFYFD2uPgTjZOf1I5UFTa
                      1tIu7uoGoyTxJqqEq7K2aqU+vy+aK76uQ5mcllc+TymVtcLk10kcKvb3FQ==
                      -----END PUBLIC KEY-----
```

### 2.3 The fields that matter

| Field | Purpose | Notes for our use case |
|---|---|---|
| `attestors` | Who must have signed it. `count` = how many valid signatures required; `entries` carries keys or keyless identities. Same structure as image verification. | Use **keyed**, not keyless — see §3.3 |
| `ignoreFields` | Fields allowed to differ between the signed and submitted manifest, per kind. | **Essential** under GitOps — see §3.4 |
| `dryRun` | Server-side dry-run so controller-applied mutations don't fail verification. Takes `enable` and a target `namespace`. | Requires **extra ServiceAccount RBAC** beyond the standard Kyverno install |
| `repository` | OCI repo holding the signature bundle. | Only if using bundle mode rather than embedded annotations |
| `annotationDomain` | Annotation key prefix carrying signature material. | Default is the cosign/sigstore domain |

Authoritative field reference for the installed version:

```bash
kubectl explain clusterpolicy.spec.rules.validate.manifests --recursive
```

---

## 3. Can we use this for *all* policy exemptions?

**Technically yes. Operationally, not as the primary control.** Below is the reasoning, with each point testable in the §7 PoC.

### 3.1 Technically it fits

A `PolicyException` is just a Custom Resource, and `validate.manifests` matches any kind. There is no structural reason a verification policy cannot match `PolicyException` and require a signature. §5 documents exactly that.

### 3.2 We have two PolicyException API groups, and it matters

PolicyExceptions exist in **two separate API groups** depending on which policy family they exempt:

| Exempting… | PolicyException API |
|---|---|
| Legacy `ClusterPolicy` / `Policy` | `kyverno.io/v2` |
| CEL `ValidatingPolicy`, `ImageValidatingPolicy`, `GeneratingPolicy` | `policies.kyverno.io/v1alpha1` (and `v1beta1`) |

Because our policies are CEL-based, **our exceptions are `policies.kyverno.io`**, not `kyverno.io/v2`. A verification policy that only matches the legacy kind would appear to work and silently cover nothing. Confirm what exists before writing any policy:

```bash
kubectl api-resources | grep -i policyexception
kubectl get policyexceptions -A
```

### 3.3 Keyless signing puts Sigstore in the admission path

Keyless verification requires reaching Fulcio and Rekor at admission time. That adds an **external internet dependency and latency to every exception write**, and a Sigstore outage becomes an admission failure. Given the availability concerns already documented for our admission chain, **use keyed signing with the public key embedded in the policy** — no external calls, no added failure mode.

### 3.4 GitOps will break signatures unless handled

Argo CD and Flux inject annotations and labels into applied resources (tracking IDs, instance labels, Kustomize markers). Any such injection changes the manifest after signing and **fails verification**. Two consequences:

* Every injected field must be enumerated in `ignoreFields`, and that list must be maintained as the GitOps tooling changes.
* You must sign the **rendered output**, not the Kustomize/Helm source, since templating alters the manifest.

This is the single largest source of day-two friction and is why signing is a poor fit for a resource type that changes often.

### 3.5 Exceptions are a high-churn, human workflow

An exception is typically created under time pressure, by whoever is unblocking a deployment. Requiring every one of them — including every edit and every expiry extension — to pass through a signing tool that its own maintainers describe as not production-ready is a meaningful operational risk. If the signing step fails or the key is unavailable, **nobody can grant an exception**, including during an incident.

### 3.6 Verdict

| Question | Answer |
|---|---|
| Can manifest validation technically enforce signed PolicyExceptions? | **Yes** — §5 shows how |
| Should it be the primary control for all exemptions? | **No** — F1 (tool maturity), §3.4 (GitOps friction), §3.5 (incident-time fragility) |
| Is it viable as defence-in-depth on top of RBAC + GitOps? | **Yes**, once the signing tool matures or is replaced |
| Is there a better-fitting use case for the same feature? | **Yes** — see §4 |

---

## 4. Other valid use cases

Ranked by fit. The pattern that suits manifest signing is **low-churn, high-blast-radius, cluster-scoped configuration** — the opposite of exceptions.

### 4.1 Signing the policies themselves ⭐ strongest candidate

If an attacker or a mistake weakens a `ValidatingPolicy` — flipping `validationActions` from `Deny` to `Audit`, or widening `matchConstraints` — every downstream control silently degrades and nothing alerts. Requiring policy resources to be signed closes that, and policies change rarely enough that the signing overhead is negligible.

This is arguably a **better return on the same investment** than signing exceptions, and it protects the exception mechanism indirectly.

### 4.2 Cluster-critical RBAC

`ClusterRoleBinding` and `ClusterRole` objects granting elevated access. Low churn, catastrophic if tampered with, and it complements the `restrict-binding-clusteradmin` / `restrict-binding-system-groups` policies already deployed.

### 4.3 Admission webhook configurations

`ValidatingWebhookConfiguration` / `MutatingWebhookConfiguration` — tampering here disables enforcement wholesale. Note the interaction: our documented break-glass procedure *deletes* Kyverno's webhook configurations, so a signing policy here must not block recovery. Exclude the break-glass path explicitly.

### 4.4 Network policy baselines

Default-deny `NetworkPolicy` objects in regulated namespaces. Low churn, high impact if quietly relaxed.

### 4.5 Kyverno's own configuration

The `kyverno` ConfigMap holds `resourceFilters` and the webhook `namespaceSelector` — the two settings evaluated in the `resourceFilters` ticket. An unsigned edit there silently changes policy scope cluster-wide. Signing it is a natural pairing with that work.

### 4.6 Tenant-supplied resources in shared clusters

Where a platform team accepts manifests from teams it does not fully trust, signature verification proves provenance. Only worth it if a signing workflow already exists for those tenants.

---

## 5. Implementation — only signed exceptions accepted in any cluster

This is the full design, as required by the acceptance criteria. Run it in a disposable cluster first (§7).

### 5.0 ⚠️ Prerequisite that will silently defeat everything

**Kyverno's default `resourceFilters` excludes the `kyverno` namespace.** The common default for `--exceptionNamespace` is also `kyverno`. If PolicyExceptions live in a namespace that `resourceFilters` excludes, **Kyverno's engine never evaluates them and the verification policy never fires** — while appearing to be installed and healthy.

Check before anything else:

```bash
kubectl -n kyverno get cm kyverno -o jsonpath='{.data.resourceFilters}' | tr ' ' '\n' | grep -iE 'kyverno|exception'
```

```bash
kubectl -n kyverno get deploy kyverno-admission-controller -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep -iE 'exceptionNamespace|enablePolicyException'
```

**Design decision:** put PolicyExceptions in a dedicated namespace — `policy-exceptions` — that is **not** in `resourceFilters` and **not** excluded by the webhook `namespaceSelector`. Verify both after deploying.

### 5.1 Enable and constrain PolicyExceptions

Exceptions are **disabled by default**. Enabling them without constraining the namespace is the real risk; the signature is a second layer.

```yaml
# values-exceptions.yaml
features:
  policyExceptions:
    enabled: true
    namespace: policy-exceptions
```

> Flag names and Helm value paths differ across chart versions (`--enablePolicyException`, `--exceptionNamespace`). Confirm against the installed chart:
> ```bash
> helm show values kyverno/kyverno --version <VER> | grep -A5 -i exception
> ```

```bash
kubectl create namespace policy-exceptions
```

### 5.2 Generate and store the key pair

Keyed signing — no Sigstore network dependency at admission (§3.3).

```bash
cosign generate-key-pair
```

* **Private key** → CI secret store / cloud KMS. It must **never** be on a laptop, and ideally never leave the signing job. `cosign` supports KMS-backed keys (`--key awskms://...` / `azurekms://...`), which is the preferred form since the key is then non-exportable.
* **Public key** → embedded in the verification policy below, and committed to git.

**Key rotation must be designed in now, not later.** The `attestors.entries` list accepts multiple keys, so rotation is: add the new public key alongside the old, re-sign everything, then remove the old key. Document the runbook before go-live.

### 5.3 Signing workflow

```bash
kubectl krew install sigstore   # or install the plugin from the project releases
```

```bash
kubectl sigstore sign -f exception.yaml -k cosign.key -o exception-signed.yaml
```

```bash
kubectl sigstore verify -f exception-signed.yaml -k cosign.pub
```

> **Two storage modes.** By default the tool bundles the manifest as an **OCI image** and writes a reference annotation, which means the verification policy needs `repository` set and the cluster needs pull access to that registry. The alternative embeds signature material directly in annotations, which removes the registry dependency. **Confirm which mode your plugin version defaults to and pick deliberately** — it changes the policy and the infrastructure requirements:
> ```bash
> kubectl sigstore sign --help
> ```
> For our use case the **embedded/annotation mode is preferable** — an exception should not depend on registry availability at admission time.

### 5.4 The verification policy

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-signed-policy-exceptions
  annotations:
    policies.kyverno.io/title: Require signed PolicyExceptions
    policies.kyverno.io/subject: PolicyException
spec:
  # Manifest verification needs the admission request payload
  background: false
  # Signature verification is slower than a CEL check - give it headroom
  webhookTimeoutSeconds: 30
  # Start Ignore, move to Fail only after §7 passes
  failurePolicy: Ignore
  rules:
    - name: verify-exception-signature
      match:
        any:
          - resources:
              kinds:
                # BOTH families - see §3.2. Verify the exact kind strings for
                # your version with: kubectl api-resources | grep -i policyexception
                - kyverno.io/v2/PolicyException
                - policies.kyverno.io/v1alpha1/PolicyException
              namespaces:
                - policy-exceptions
      validate:
        # Start Audit. Enforce only after the soak in §7.4
        failureAction: Audit
        message: >-
          PolicyExceptions must be signed by the platform signing key.
          See <link to this page> for the signing workflow.
        manifests:
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: |-
                      -----BEGIN PUBLIC KEY-----
                      <PLATFORM PUBLIC KEY>
                      -----END PUBLIC KEY-----
          ignoreFields:
            - objects:
                - kind: PolicyException
              fields:
                # Server-populated - always differ from the signed source
                - metadata.uid
                - metadata.resourceVersion
                - metadata.generation
                - metadata.creationTimestamp
                - metadata.managedFields
                - status
                # GitOps injection - trim to whatever your tooling actually adds
                - metadata.annotations."argocd.argoproj.io/tracking-id"
                - metadata.annotations."kubectl.kubernetes.io/last-applied-configuration"
                - metadata.labels."app.kubernetes.io/instance"
```

**Note the deliberate choices:**

* `background: false` — manifest verification needs the admission request; it cannot run as a background scan. Exceptions are therefore only checked **at write time**, not retroactively. Anything created before this policy lands is unverified — see §5.8.
* `failurePolicy: Ignore` and `failureAction: Audit` **to begin with**. Tightening both is the last step, not the first.
* `namespaces: [policy-exceptions]` — pairs with §5.0. If exceptions can be created anywhere, this match must widen accordingly.

### 5.5 Close the circularity gap (F3) — not optional

Nothing above stops someone creating a PolicyException that exempts `require-signed-policy-exceptions` itself. Close it with a second policy that denies self-referential exceptions:

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: block-exemptions-of-signing-policies
spec:
  background: false
  failurePolicy: Fail
  rules:
    - name: no-self-exemption
      match:
        any:
          - resources:
              kinds:
                - kyverno.io/v2/PolicyException
                - policies.kyverno.io/v1alpha1/PolicyException
      validate:
        failureAction: Enforce
        message: >-
          PolicyExceptions may not target the exception-signing policies.
        deny:
          conditions:
            any:
              # kyverno.io/v2 shape
              - key: "{{ request.object.spec.exceptions[].policyName || `[]` }}"
                operator: AnyIn
                value:
                  - require-signed-policy-exceptions
                  - block-exemptions-of-signing-policies
              # policies.kyverno.io shape
              - key: "{{ request.object.spec.policyRefs[].name || `[]` }}"
                operator: AnyIn
                value:
                  - require-signed-policy-exceptions
                  - block-exemptions-of-signing-policies
```

> The two API groups use **different field names** for the policy reference (`spec.exceptions[].policyName` vs `spec.policyRefs[].name`). Confirm both against the installed CRDs before relying on this:
> ```bash
> kubectl explain policyexception.spec --recursive
> ```

**This policy must itself be protected by RBAC (§5.6) — it is the keystone.** A wildcard exception (`ruleNames: ["*"]`) against a broadly-matched policy could also sidestep the intent, so §7.3 includes an explicit bypass-attempt test.

### 5.6 RBAC — the control that does the most work

Even without signatures, this alone removes most of the risk:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: policy-exception-author
rules:
  - apiGroups: ["kyverno.io", "policies.kyverno.io"]
    resources: ["policyexceptions"]
    verbs: ["get", "list", "watch"]
---
# Write access granted ONLY to the GitOps controller's ServiceAccount,
# in the policy-exceptions namespace only.
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: policy-exception-writer
  namespace: policy-exceptions
subjects:
  - kind: ServiceAccount
    name: <argocd-or-flux-sa>
    namespace: <gitops-namespace>
roleRef:
  kind: ClusterRole
  name: policy-exception-editor      # create with create/update/patch/delete
  apiGroup: rbac.authorization.k8s.io
```

Audit who can write exceptions today:

```bash
kubectl auth can-i create policyexceptions --all-namespaces --list 2>/dev/null | head
```

```bash
for sa in $(kubectl get sa -A -o jsonpath='{range .items[*]}{.metadata.namespace}:{.metadata.name}{"\n"}{end}'); do ns=${sa%%:*}; n=${sa##*:}; kubectl auth can-i create policyexceptions --as=system:serviceaccount:$ns:$n -n policy-exceptions 2>/dev/null | grep -q yes && echo "$ns/$n"; done
```

### 5.7 CI integration

The signing step belongs in the pipeline, not on a laptop:

```yaml
# Illustrative CI stage
- name: Sign PolicyException
  run: |
    kubectl sigstore sign \
      -f exceptions/${EXCEPTION}.yaml \
      -k awskms://${KMS_KEY_ARN} \
      -o exceptions/${EXCEPTION}.signed.yaml
    kubectl sigstore verify \
      -f exceptions/${EXCEPTION}.signed.yaml \
      -k ${PUBLIC_KEY_PATH}
```

Because signing happens in CI on a reviewed, merged commit, **the pull-request approval becomes the authorisation event** and the signature is its cryptographic receipt. That is the actual security value of this design — and note that most of it comes from the PR gate, not the signature (§6).

**Sign the rendered output**, after any Kustomize/Helm processing, not the source (§3.4).

### 5.8 Rollout sequence

| Step | Action | Exit criterion |
|---|---|---|
| 1 | §5.0 prerequisite check | Exceptions namespace confirmed **not** filtered |
| 2 | Deploy `block-exemptions-of-signing-policies` at `Enforce` | Self-exemption attempts denied (§7.3) |
| 3 | Deploy verification policy at `Audit` + `failurePolicy: Ignore` | Policy loads, no denials |
| 4 | Inventory existing exceptions — all are currently unsigned | Signed replacements merged for each |
| 5 | Soak ≥ 2 weeks | PolicyReport shows only known-unsigned exceptions failing |
| 6 | `failureAction: Enforce` | Unsigned exceptions rejected; signed ones accepted |
| 7 | `failurePolicy: Fail` — **only if** step 6 is clean for a further 2 weeks | See the warning below |

> ⚠️ **`failurePolicy: Fail` means that if Kyverno is unavailable, nobody can create a PolicyException.** During an incident that is exactly when someone may need one. Given the availability analysis already done for our admission chain, **`Ignore` is the defensible default here** — the signature check is a governance control, not a containment boundary, and RBAC (§5.6) still applies when Kyverno is down.

### 5.9 Rollback and break-glass

```bash
kubectl edit clusterpolicy require-signed-policy-exceptions   # set validate.failureAction back to Audit
```

```bash
kubectl delete clusterpolicy require-signed-policy-exceptions
```

> A JSON **merge** patch on `spec.rules` replaces the whole array and would silently drop the rest of the rule — edit the resource or revert in git rather than patching the array.


Under GitOps both are reverted at the next sync — the real rollback is a git revert. Pre-agree who may delete these policies in an emergency, and alert on their deletion, since deleting them is also the obvious attack.

---

## 6. Alternatives — what we would compare against

The spike should present this comparison, not just the signing design.

| Approach | Assurance | Cost | Fails safe? |
|---|---|---|---|
| **RBAC + dedicated namespace** | Only named identities can create exceptions | Very low — hours | Yes; independent of Kyverno availability |
| **GitOps-only + branch protection + signed commits** | Every exception is peer-reviewed, attributable, auditable in git history | Low — mostly process | Yes |
| **Kyverno validate rules on exception *content*** | Force narrowness: mandatory expiry, no wildcard `ruleNames`, required ticket-reference annotation, owner label | Low; CEL policy we can write today | Degrades to `Ignore` |
| **Manifest signing (this spike)** | Cryptographic proof the YAML is byte-identical to what was signed | **High** — pre-production tool, key management, GitOps `ignoreFields` maintenance | Only with `failurePolicy: Fail`, which has its own risk |

**The first three are complementary and cheap; the fourth is additive.** Combining RBAC + GitOps + a content-validation policy delivers most of the intent immediately.

Worth noting: the content-validation option is a genuinely valuable quick win independent of this spike's outcome — mandatory expiry dates and a ban on wildcard `ruleNames` address the most common real-world exception failure, which is exceptions that are too broad and never removed.

Kyverno's own guidance supports the layered view: exception use *"can and should be controlled by a number of different mechanisms… including Kubernetes RBAC, [a] specific namespace for PolicyExceptions, existing GitOps governance processes, [and] Kyverno validate rules."* Signing is not named among them.

---

## 7. How to run the spike

Timebox: **3–5 days** on a disposable cluster. Do not run any of this on staging or production.

### 7.1 Day 1 — feasibility gates (stop early if any fail)

Confirm manifest validation exists in the installed version:

```bash
kubectl explain clusterpolicy.spec.rules.validate.manifests
```

Confirm whether the CEL types have any equivalent — expected to return nothing, which confirms **F2**:

```bash
kubectl explain validatingpolicy.spec --recursive | grep -i manifest
```

Confirm the exception API groups in play (**F1/§3.2**):

```bash
kubectl api-resources | grep -i policyexception; kubectl get policyexceptions -A
```

Run the §5.0 prerequisite check.

**Gate:** if `validate.manifests` is absent, or exceptions live in a filtered namespace, stop and report — the design does not work without remediation.

### 7.2 Day 2 — end-to-end happy path

1. Generate a key pair (§5.2).
2. Write a trivial PolicyException, sign it, apply it → **expect accepted**.
3. Apply the same exception **unsigned** → **expect denied** (with the policy at `Enforce` in the test cluster).
4. Take the signed exception, change one character in a matched field, re-apply → **expect denied**.
5. Change only a field listed in `ignoreFields` → **expect accepted**.

Capture the denial messages verbatim — they are what an engineer will see at 2am, and their clarity is itself a finding.

### 7.3 Day 3 — adversarial tests

These are the tests that determine whether the control is real.

| # | Attempt | Expected |
|---|---|---|
| A1 | Create a PolicyException exempting `require-signed-policy-exceptions` | Denied by §5.5 |
| A2 | Create a PolicyException with `ruleNames: ["*"]` against a broadly-matched policy | Denied or clearly bounded |
| A3 | Create an exception in a namespace **other than** `policy-exceptions` | Not honoured by Kyverno (`exceptionNamespace`) |
| A4 | Delete the verification ClusterPolicy | Should require elevated RBAC; should alert |
| A5 | Apply a signed exception signed with a **different** key | Denied |
| A6 | Scale Kyverno to 0, then create an unsigned exception | `Ignore` → accepted (documents the gap); `Fail` → denied |

A6 is the one to think hardest about: it quantifies exactly what the control is worth during a Kyverno outage.

### 7.4 Day 4 — GitOps reality check

Deploy a signed exception **through Argo CD or Flux** rather than `kubectl apply`. This is where signing most often breaks (§3.4). Record every field the tooling injects and confirm the `ignoreFields` list covers all of them.

### 7.5 Day 5 — write up

Populate §8 and §9 with real results and put the recommendation to the team.

---

## 8. Risks and open questions

| # | Risk / question | Owner | Status |
|---|---|---|---|
| R1 | Signing tool self-declared not production-ready. Is there a supported alternative, or do we accept the risk? | | Open |
| R2 | Reintroducing legacy `ClusterPolicy` alongside CEL types — acceptable, or a blocker for our migration direction? | | Open |
| R3 | Private key custody and rotation runbook — KMS-backed? Who holds break-glass? | | Open |
| R4 | `failurePolicy` decision: `Ignore` (available but bypassable during outage) vs `Fail` (strict but blocks incident-time exceptions) | | Open |
| R5 | `ignoreFields` maintenance burden as GitOps tooling changes | | Open |
| R6 | Existing unsigned exceptions — migrate or grandfather? Background scan cannot verify them (`background: false`) | | Open |
| R7 | Does the emergency path still work when the signing pipeline is down? | | Open |
| R8 | EKS vs AKS differences — none expected here, but confirm the PoC on both | | Open |

---

## 9. Proposed conclusion for the ticket

> Kyverno's `validate.manifests` feature can technically enforce that only signed PolicyExceptions are accepted, and §5 documents a complete working design. However, it is **not recommended as the primary control for all policy exemptions**, for three reasons: the required signing tool (`sigstore/k8s-manifest-sigstore`) states it is not production-ready; the feature exists only in the legacy `ClusterPolicy` type, against our migration to CEL policies; and exception workflows are high-churn and incident-time, where a signing dependency is a liability rather than an asset.
>
> We recommend instead: **RBAC restricting exception creation to the GitOps controller, a dedicated non-filtered `policy-exceptions` namespace, and a Kyverno validate rule enforcing exception narrowness** (mandatory expiry, no wildcard rule names, required ticket reference). This delivers most of the intended assurance at a fraction of the cost and fails safe.
>
> Manifest signing should be revisited when the signing tooling matures, and is better applied first to **the policy resources themselves** (§4.1) — lower churn, higher blast radius, and it protects the exception mechanism indirectly.
>
> Findings validated by the PoC in §7 on a disposable cluster; adversarial results in §7.3.

---

## 10. References

* Validate rules — `validate.manifests`: https://kyverno.io/docs/policy-types/cluster-policy/validate/
* PolicyExceptions (API groups, `enablePolicyException`, `exceptionNamespace`, security guidance): https://kyverno.io/docs/exceptions/
* `sigstore/k8s-manifest-sigstore` — the signing tool: https://github.com/sigstore/k8s-manifest-sigstore
* Sample policy — Verify Manifest Integrity: https://kyverno.io/policies/other/verify-manifest-integrity/verify-manifest-integrity/
* Kyverno 1.8 release (feature introduction): https://kyverno.io/blog/2022/10/24/kyverno-1.8-released/
* Design proposal — YAML signing and verification: https://github.com/kyverno/KDP/blob/main/proposals/yaml_signing_and_verification.md
* Sigstore in Kyverno: https://main.kyverno.io/docs/policy-types/cluster-policy/verify-images/sigstore/

**Related internal work:** the `resourceFilters` / `kube-system` test plan — §5.0 here depends on the same `resourceFilters` mechanism, and §4.5 proposes signing that ConfigMap.
