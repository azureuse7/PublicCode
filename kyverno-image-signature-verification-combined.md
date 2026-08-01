# Kyverno Image Signature Verification on AKS



---
## 1. Purpose and audience
This document explains three things:
1. What container image signing is and why it matters.
2. How Kyverno checks signatures when a pod is created.
3. How to prove it works 
---

## 2. Executive summary

A container image name like `myapp:v1` is just a label. It points at something, but it can be re-pointed at any time.  This is why signing tools tell you to sign the digest, not the tag ([Secure Pipelines guide](https://secure-pipelines.com/ci-cd-security/signing-verifying-container-images-sigstore-cosign/)).

Image signing fixes this. The build pipeline puts a cryptographic seal on the exact bytes of the image. Kyverno then acts as a gate at the cluster door. Kyverno intercepts the request to create a pod and checks the image signature first. If the signature is missing or wrong, the pod is refused ([Kyverno verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)).



[Our assessment] — where the real cost is. It is not the Kyverno configuration. The policy is about twenty lines. The cost is that **every image running in our AKS clusters must be signed before we can turn enforcement on.** That includes third-party and vendor images that nobody here built. On AKS it is harder still, because Microsoft runs some workloads in our clusters that we cannot sign and do not control (see [8.7](#87-aks-lifecycle-events-and-admission-storms)). Managing that transition is the bulk of the work, not the policy.
---
## 3. The problem in detail
### 3.1 Why tags cannot be trusted

```
artifactory.example.com/platform/myapp:v1.2.3
```

That name is resolved when the image is pulled. The registry hands back whatever the tag points at *right now*. Tags can be moved — a tag can be pointed at a different image after it was signed — while digests are calculated from the content itself and cannot be moved ([Secure Pipelines guide](https://secure-pipelines.com/ci-cd-security/signing-verifying-container-images-sigstore-cosign/)).

### 3.2 What digests fix, and what they do not

```
artifactory.example.com/platform/myapp@sha256:b31bfb4d0213f254d361e0079deaaebefa4f82ba7aa76ef82e90b4935ad5b105
```

There is no single file on disk that *is* the image. What gets signed is the OCI image manifest, written out in a fixed canonical form and hashed with SHA-256. That hash is the digest used to refer to images in registries ([Sigstore blog](https://blog.sigstore.dev/cosign-image-signatures-77bab238a93/), [OCI image spec](https://github.com/opencontainers/image-spec/blob/main/manifest.md)).

A digest gives you **integrity**: if the bytes change, the digest changes.

A digest does **not** give you **provenance**. It says nothing about who made those bytes or whether they were allowed to. An attacker who pushes a malicious image and hands you its digest has given you a perfectly valid, perfectly fixed, perfectly malicious reference.

### 3.3 What signing adds

Signing ties a **trusted identity** to a **specific digest**. That is the missing half. Together, digest plus signature answer both questions: *are these the right bytes*, and *did someone we trust put them there*.

---

## 4. Core concepts

### 4.1 What signing actually produces

Kyverno's `verifyImages` rule uses Cosign to check image signatures and in-toto attestations stored in an OCI registry ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)).

Cosign follows the Red Hat "simple signing" format. The private key is unlocked, used to sign a small payload, and the signature is base64-encoded for storage ([Sigstore blog](https://blog.sigstore.dev/cosign-image-signatures-77bab238a93/)).

When you run:

```bash
cosign sign --key cosign.key artifactory.example.com/platform/myapp:v1.2.3
```

Cosign does four things:

1. Looks up the digest the tag currently points at, e.g. `sha256:b31bfb4d…`
2. Builds a small JSON payload whose subject is that digest, plus any annotations you added with `-a key=value`
3. Signs that payload with the private key
4. Pushes the result back to the registry

**The important part: the signature is not inside the image.** It is a separate object stored next to it.

### 4.2 Where the signature is stored

Cosign stores signatures in the OCI registry and uses a naming convention — a tag derived from the SHA-256 of the thing being signed — to find them again ([cosign README](https://github.com/sigstore/cosign)).

Signature objects go in a defined location so that any tool can find them the same way. Implementations must support at least the tag-based scheme, where the location is calculated from the digest of the signed object ([cosign SIGNATURE_SPEC](https://github.com/sigstore/cosign/blob/main/specs/SIGNATURE_SPEC.md)).

A worked example: signing `…/test/artifact@sha256:551e6cce…` pushes the signature to `…/test/artifact:sha256-551e6cce….sig` ([Sigstore — signing other types](https://docs.sigstore.dev/cosign/signing/other_types/)).

| Object | Tag pattern |
|---|---|
| Signature | `sha256-<digest-hex>.sig` |
| Attestation | `sha256-<digest-hex>.att` |

Newer Cosign versions use the **OCI 1.1 referrers** mechanism instead, which records the relationship natively rather than through tag naming ([Sigstore — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)). More than one discovery method may be in use at once ([cosign SIGNATURE_SPEC](https://github.com/sigstore/cosign/blob/main/specs/SIGNATURE_SPEC.md)). The command `cosign triangulate <image>` prints where Cosign expects the signature to be ([Sigstore blog](https://blog.sigstore.dev/cosign-image-signatures-77bab238a93/)).

**Artifactory specifics.** Cosign is tested against JFrog Artifactory's container registry ([Sigstore registry support](https://docs.sigstore.dev/cosign/system_config/registry_support/)). For OCI 1.1 referrers support in Artifactory, JFrog states you need Cosign **2.0.0 or later** ([JFrog OCI repositories](https://docs.jfrog.com/artifactory/docs/oci-repositories)).

**[Our assessment] — two consequences we have to plan for:**

1. Verifying a signature needs **read access to the repository**, not just permission to pull the image. If Artifactory permissions are scoped tightly by path, Kyverno must also be able to `GET` the `.sig` tag.
2. **We must test our Artifactory version's referrers behaviour explicitly.** If the registry accepts the signature push but does not expose it in a way Cosign can find, you get a confusing "signature not found" on an image that definitely was signed. Registry quirks are documented elsewhere in the ecosystem — for example, giving only a repo path does not work in Google Artifact Registry ([cosign README](https://github.com/sigstore/cosign)), and some registries do not support deletion ([Sigstore — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)). So this is a checkpoint in the proof of concept, not an assumption.
3. **The signature is discoverable only on the repository path it was pushed to.** Because the `.sig` object is a tag in the same repository as the image, signing through one Artifactory path and pulling through another raises the question of whether the signature can still be found. With local, remote and virtual repositories in play this is not a detail — it determines what goes in `imageReferences` and how much of the estate can be signed at all. This is set out in full, with the test that resolves it, in [10.1.1](#1011-artifactory-repository-topology--the-risk-that-sizes-the-programme).

### 4.3 Which signature formats Kyverno supports

The `type` field on the rule selects the signature type. Kyverno supports **Sigstore Cosign** and **Notary** ([Kyverno verifyImages rules](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/)).

**[Our assessment]** Cosign is the right pick. It is far better documented in Kyverno, and Notary would add a second toolchain without meeting a need we actually have. 

### 4.4 Trust models — how we define "signed by someone we trust"

A `verifyImages` rule holds a list of **attestors**. An attestor is a definition of a trusted signer.  ([Kyverno verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)).

#### 4.4.1 Keyed — a key held in a vault (recommended start)

A normal key pair. The private key signs; the public key goes in the policy.

Cosign supports AWS KMS, GCP KMS, Azure Key Vault, HashiCorp Vault, OpenBao and Kubernetes Secrets, referred to by URIs such as `azurekms://` ([Sigstore key management](https://docs.sigstore.dev/cosign/key_management/overview/)).

The Azure Key Vault URI format is `azurekms://[VAULT_NAME][VAULT_URI]/[KEY]`, and you can optionally add a specific key version ([Sigstore key management](https://docs.sigstore.dev/cosign/key_management/overview/), [sigstore Azure KMS package](https://pkg.go.dev/github.com/sigstore/sigstore/pkg/signature/kms/azure)).

A key may also be referenced as `k8s://<namespace>/<secret-name>` — a Kubernetes Secret holding a `cosign.pub` file.

**HashiCorp Vault — `hashivault://`, and an important distinction.** Cosign's Vault integration uses the **Transit** secrets engine, not the PKI engine. Transit provides encryption and signature as a service: Vault holds the private key and performs the signing operation, so the application never sees the key ([Vault Transit secrets engine](https://developer.hashicorp.com/vault/docs/secrets/transit)). Keys are referenced as `hashivault://<keyname>`, the Transit engine must be enabled, and the standard `VAULT_ADDR` / `VAULT_TOKEN` environment variables must be set ([Sigstore key management](https://docs.sigstore.dev/cosign/key_management/overview/)).

```bash
cosign sign --key hashivault://image-signing-key --tlog-upload=false ${IMAGE}
```

The policy side is *syntactically* symmetrical — `publicKeys: "hashivault://image-signing-key"` in place of the `azurekms://` URI — but **do not use that form.** Kyverno can only authenticate to Vault with a static, expiring `VAULT_TOKEN` environment variable, which is not safe to depend on from the admission path. Sign with `hashivault://` and verify with the **exported public key** instead. The reasoning and the full setup are in [8.4](#84-signing-key-custody--vault-transit-and-azure-key-vault), and [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy) is the part that matters.

**[Our assessment] — this is our recommended starting point, and it changes the critical path.** We already run Vault. Using the Transit engine for signing means:

- **No Azure Key Vault dependency and no Entra Workload ID work.** The Azure path in [8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id) comes off the critical path for the first pass, along with the federated-identity plumbing in pre-flight [Check 4](#73-pre-flight-checks--run-these-before-applying-any-policy).
- **Fewer teams to coordinate with.** The Vault team already owns key custody and audit for us.
- **A well-trodden path.** HashiCorp publish a walkthrough of this exact stack ([Signed container images in Kubernetes with Sigstore and Vault](https://medium.com/hashicorp-engineering/signed-container-images-in-kubernetes-with-sigstore-and-hashicorp-vault-e4c6995af262)), and there is a community write-up of Cosign plus Kyverno plus Vault plus GitLab CI ([Angapov](https://angapov.medium.com/kubernetes-container-images-signing-using-cosign-kyverno-hashicorp-vault-and-gitlab-ci-c4e2041d1310)) — both third-party, so treat as orientation rather than specification.

**What it does not remove.** Kyverno still needs egress to Vault to fetch the public key, so [8.1](#81-the-three-layer-egress-problem) applies to the Vault FQDN exactly as it would to Key Vault, and [8.5](#85-private-endpoints-and-dns) applies if Vault is reached privately. It also needs a Vault authentication path of its own — confirm during the proof of concept whether the admission controller authenticates via Kubernetes auth, and what policy grants it read on the Transit key. **Run the proof of concept twice** as [7.4](#74-store-the-public-key) already advises: once with `k8s://` to prove the Kyverno plumbing, once with `hashivault://` to prove the Vault plumbing.

If both Azure Key Vault and Vault are viable, prefer whichever the platform team already operates with a private endpoint and an on-call rotation. The policy shape is identical and switching later is a one-line change.

**A vault-held key is strongly preferred over a file on disk.** The private key never leaves the vault, signing is an API call, and the vault keeps the audit trail instead of us.

**[Our assessment] — one key pair, and who is allowed to use it.** A single organisation-wide key pair works, and is the simplest thing that can work: one private key signs, one public key goes in one policy. But *who holds the private key* decides what the signature is worth, and there are two very different models that look the same on paper.

| Model | What a passing signature proves |
| --- | --- |
| **Central signing** — one platform-owned pipeline stage signs every image; teams cannot invoke it directly | "Our build process produced or approved these bytes." This is the model we want. |
| **Distributed key** — the private key is issued to each team so they sign their own images | Only "somebody who holds the key signed this." Any holder can sign *any* image, including a malicious one. |

Three consequences of the distributed variant, which is why we should rule it out explicitly rather than by omission:

- **No attribution.** With one shared key the policy cannot express "team A may only sign team A's images". Per-team identity requires certificate-based ([4.4.2](#442-certificate-based--our-own-pki)) or keyless ([4.4.3](#443-keyless--fulcio-and-rekor)) — see the comparison in [4.4.4](#444-comparison-and-recommendation).
- **No selective revocation.** Removing one team's ability to sign means rotating the key for everyone, with the full re-signing exercise in [9.5](#95-key-rotation).
- **A weaker claim than intended.** Per the governance point in [4.4.4](#444-comparison-and-recommendation), if a team can reach the signing step, the signature proves only that a team member wanted this deployed.

The single key pair is therefore the right starting choice **provided signing stays inside a pipeline stage teams cannot edit.** If the operating model requires teams to sign for themselves, that is not a keyed-signing configuration change — it is a decision to move to [4.4.2](#442-certificate-based--our-own-pki).

*Downside:* rotating the key is a coordinated event that touches every policy and every image. See [9.5](#95-key-rotation).

Full setup for both vaults, and the verification-side decision that matters most, is in [8.4](#84-signing-key-custody--vault-transit-and-azure-key-vault).

#### 4.4.2 Certificate-based — our own PKI

Sign with an X.509 leaf certificate issued by the corporate CA. The policy trusts the root certificate:

```yaml
attestors:
  - entries:
      - certificates:
          certChain: |-
            -----BEGIN CERTIFICATE-----
            …internal root CA…
            -----END CERTIFICATE-----
```

Certificates are supplied to the attestor as `cert`, the leaf or signing certificate, and `certChain`, the intermediates plus root. To verify against the root alone, `cert` may be omitted ([Kyverno verifyImages rules](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/)).

**[Our assessment] — the original attraction, and why it is weaker than it looks.** The appeal was that each team gets its own leaf certificate, one global policy checks against the root, and delegation and revocation come from PKI infrastructure we already run — with no policy edit per team onboarded. The delegation half holds. **The revocation half does not, and the per-team half does not either.** Three constraints have to be designed around before this model can be recommended.

**Constraint 1 — Kyverno cannot pin the certificate subject, so this is not per-team enforcement.** The policy validates that the signature chains to the supplied root and nothing beyond that. A feature request to match certificate SANs — asked for precisely so that signatures could be restricted to a specific signing identity rather than any certificate issued by the root CA — was **closed as not planned** ([Kyverno issue #4689](https://github.com/kyverno/kyverno/issues/4689)).

The consequence is blunt: with the corporate root in `certChain`, **any certificate that our PKI issues anywhere can sign any image and pass.** If the same CA also issues TLS server certificates, service mesh identities or database certificates, all of those become valid image-signing credentials as far as this policy is concerned.

| Option | Effect |
| --- | --- |
| Corporate root in `certChain` | Unacceptable. Far too wide — see above. |
| **A dedicated image-signing intermediate CA in `certChain`** | The workable design. Only certificates issued under that intermediate pass. No per-team distinction, but a bounded and auditable blast radius. |
| One rule per team, each with the team's leaf in `cert` | True per-team enforcement, but reintroduces a policy edit per onboarding — the exact cost this model was chosen to avoid. |

**Constraint 2 — revocation is not checked at admission.** Cosign implements neither CRL nor OCSP checking. Adding a `--crl` option is an open feature request ([cosign issue #2568](https://github.com/sigstore/cosign/issues/2568)), and Sigstore's deliberate design answer to revocation is very short certificate lifetimes rather than revocation infrastructure ([Sigstore deep dive](https://dev.to/kanywst/sigstore-deep-dive-unmasking-the-magic-behind-keyless-verification-lmh) — third-party explainer). Revoking a leaf in our PKI therefore does **not** stop Kyverno accepting signatures already made with it. Revocation still means re-signing and rotating, exactly as with a plain key pair ([9.5](#95-key-rotation)).

**Constraint 3 — without a trusted timestamp, signatures expire when the certificate does.** Cosign verifies the chain using the leaf's `notBefore`, then checks leaf expiry using a signed timestamp from the Rekor transparency log **or** an RFC 3161 timestamp authority — **or the current time if neither is available** ([Sigstore — timestamps](https://docs.sigstore.dev/cosign/verifying/timestamps/)).

Every policy in this document disables the transparency log (`rekor.ignoreTlog`, and `--tlog-upload=false` when signing — see [11.6](#116-why-ignoretlog-and-ignoresct-appear-in-every-policy-example)). So with PKI-issued leaves and no TSA, verification falls back to current time, and **when a signing certificate expires, every signature made with it stops verifying.** The failure is delayed and therefore dangerous: running pods are unaffected, so nothing breaks on the expiry date. It breaks at the next node image upgrade, autoscaler event or restart ([8.7](#87-aks-lifecycle-events-and-admission-storms)), by which time the cause is weeks in the past. Short PKI role TTLs — normally a virtue — make this arrive sooner.

The fix is to run an RFC 3161 timestamp authority and supply its chain to the policy via `ctlog.tsaCertChain` ([Kyverno Sigstore guide](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/sigstore/)). Sigstore publishes an implementation ([sigstore/timestamp-authority](https://github.com/sigstore/timestamp-authority)). Check the behaviour against our deployed version — there is an open defect in this area ([Kyverno issue #15304](https://github.com/kyverno/kyverno/issues/15304)).

**Operational cost.** BYO-PKI signing is not a one-line command. A published walkthrough uses `cosign generate` to produce the payload, `openssl dgst -sha256 -sign` to sign it, then `cosign attach signature --cert --cert-chain` to upload the result ([Red Hat Developer — verify Cosign BYO PKI signatures](https://developers.redhat.com/articles/2025/09/08/verify-cosign-bring-your-own-pki-signature-openshift)). That is several more moving parts in the golden pipeline than `cosign sign --key hashivault://…`, and each is a place for the pipeline to break.

**[Our assessment] — conclusion.** Certificate-based signing is viable but is **not** the low-friction delegation model the earlier draft of this document assumed. It requires a dedicated signing intermediate, a timestamp authority, and acceptance that revocation is not enforced at admission. See the revised recommendation in [4.4.4](#444-comparison-and-recommendation).

#### 4.4.3 Keyless — Fulcio and Rekor

There is no long-lived key at all. The flow is:

1. Cosign creates a **throwaway** key pair in memory.
2. It gets an **OIDC identity token** — in CI, from the pipeline's own workload identity.
3. It sends a certificate request plus that token to **Fulcio**, a certificate authority. Fulcio checks the token and issues a certificate valid for about ten minutes, tied to the pipeline's identity.
4. The certificate goes into a **certificate transparency log**.
5. Cosign signs, then records the signature and certificate in **Rekor**, an append-only public log, which returns a counter-signature proving the signature was made while the certificate was still valid.
6. The results are pushed to the registry and the throwaway private key is discarded.

That counter-signature is what makes ten-minute certificates workable: by verification time the certificate has long expired, but the log entry proves it was valid when used ([GitHub Blog on artifact attestations](https://github.blog/news-insights/product-news/introducing-artifact-attestations-now-in-public-beta/)).

Fulcio records values from the OIDC token in certificate extensions under OID prefix `1.3.6.1.4.1.57264.1`, and the signed certificate timestamp under `1.3.6.1.4.1.11129.2.4.2` ([Fulcio certificate specification](https://github.com/sigstore/fulcio/blob/main/docs/certificate-specification.md)); the full mapping is in the [Fulcio OID reference](https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md). Certificate chains are checked against either a private Fulcio or the public Sigstore one, with root certificates distributed using TUF ([Ian Lewis — understanding artifact attestations](https://www.ianlewis.org/en/understanding-github-artifact-attestations)).

The policy then asserts on *identity* rather than on key material:

```yaml
attestors:
  - entries:
      - keyless:
          subject: "https://github.com/our-org/app-repo/.github/workflows/build.yaml@refs/tags/*"
          issuer: "https://token.actions.githubusercontent.com"
          additionalExtensions:
            githubWorkflowTrigger: push
            githubWorkflowRepository: our-org/app-repo
          rekor:
            url: https://rekor.sigstore.dev
```

Kyverno's keyless fields are documented in the [Kyverno Sigstore guide](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/sigstore/). `subjectRegExp` and `issuerRegExp` exist where you need patterns.

**[Our assessment]** `additionalExtensions` matters. With reusable workflows, the workflow that *calls* the build and the workflow that *signs* are different. If you only pin the subject, anyone who can invoke a reusable workflow can produce a signature that passes.

#### 4.4.4 Comparison and recommendation

**[Our assessment]** — the whole table below is our evaluation, not a vendor statement.

| | Keyed (Vault Transit) | Keyed (Azure Key Vault) | Certificate (our PKI) | Keyless (public Sigstore) |
|---|---|---|---|---|
| Where the private key lives | Vault, via Transit | Key Vault HSM | HSM / PKI issues the cert; the signing key is handled in the pipeline | Nowhere — thrown away |
| Policy reference | `hashivault://<key>` | `azurekms://<vault>/<key>` | `certChain` (+ optional `cert`) | `keyless` subject and issuer |
| Per-team identity | No | No | **No** — Kyverno cannot pin the certificate subject ([#4689](https://github.com/kyverno/kyverno/issues/4689)) | Yes |
| How you revoke | Edit policy, re-sign | Edit policy, re-sign | **Edit policy, re-sign** — CRL/OCSP is not checked ([#2568](https://github.com/sigstore/cosign/issues/2568)) | Edit the identity in policy |
| Rotation effort | High | High | Medium — rotate the signing intermediate, not every leaf | Not applicable |
| Timestamp authority required | No | No | **Yes**, or signatures expire with the certificate | No — Rekor supplies the timestamp |
| Signing command complexity | One command | One command | Multi-step (`generate` → `openssl` → `attach`) | One command |
| External dependency | Vault — already operated in-house | Key Vault endpoint (can be a private endpoint) | Internal PKI **plus** a TSA we must run | Public internet (Fulcio, Rekor, TUF) |
| Public disclosure | None | None | None | Every signature is logged publicly |
| Works behind Azure Firewall egress | Yes | Yes | Yes | Only with explicit FQDN allow-listing |
| Air-gap viable | Yes | Yes | Yes | Only if fully self-hosted |
| New infrastructure to stand up | None | Managed identity, federated credential | Signing intermediate CA, TSA | Fulcio, Rekor, CT log, TUF root |

**Recommendation:**

- **Rule out public Sigstore keyless.** It needs the admission controller in every cluster to reach public Sigstore infrastructure through our Azure Firewall, and it publishes a record of everything we build to a public log. Kyverno does support a private Sigstore setup — the `--enableTuf` flag exists for exactly this ([Kyverno configuration](https://kyverno.io/docs/installation/customization/)) — but standing up Fulcio, Rekor, a CT log and a TUF root is a platform product in its own right, not a workstream inside this one.
- **Start with HashiCorp Vault Transit for signing, and an exported public key for verification** ([4.4.1](#441-keyed--a-key-held-in-a-vault-recommended-start), [8.4](#84-signing-key-custody--vault-transit-and-azure-key-vault)). We already run Vault, so signing needs no new infrastructure and no new team dependency, and the Azure Key Vault plus Entra Workload ID work in [8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id) comes off the critical path. Critically, **the policy should reference no vault at all** — verification needs only the public key, which removes every vault from the admission hot path ([8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy)). Azure Key Vault remains the right choice in the one case where a live KMS reference at verification time is mandated, because its workload identity support has no expiring-token problem.
- **Certificate-based via corporate PKI is conditional, not the assumed end state.** This is a change from the earlier draft of this document, which treated it as the target. Having examined the behaviour ([4.4.2](#442-certificate-based--our-own-pki)), it should be adopted only if all three of the following are accepted:

  1. A **dedicated image-signing intermediate CA** is issued, and the corporate root is never used in `certChain`. Without this, every certificate the PKI issues anywhere becomes a valid image-signing credential.
  2. An **RFC 3161 timestamp authority** is available and wired into the policy, otherwise signatures expire when their certificate does — a delayed failure that surfaces during an unattended node upgrade.
  3. **Revocation is not enforced at admission.** Revoking a leaf does not stop Kyverno accepting its signatures; recovery is still re-sign and rotate.

  The decisive point is that **Kyverno cannot pin the certificate subject** ([#4689](https://github.com/kyverno/kyverno/issues/4689), closed as not planned), so certificate-based signing does **not** deliver the per-team attribution that was the main reason to prefer it. It buys delegation of *issuance*, not enforcement of *identity*.

- **If per-team identity is genuinely required, keyless is the only model that expresses it** — and it is ruled out above on public-infrastructure grounds, with a private Sigstore deployment judged a platform product in its own right. That is a coherent position, but it should be stated explicitly rather than left as an implication: **we are accepting a single organisational signing identity for the foreseeable future.** If Security will not accept that, the trade-off returns to the table and a private Sigstore deployment has to be costed properly. Settle this in Phase 0.

**Governance point [Our assessment]:** signing must happen in a pipeline stage that teams cannot edit. If a team can change the signing step, the signature only proves that a team member wanted this deployed — a much weaker claim. The shared golden build pipeline is the natural home for it.

### 4.5 Signatures versus attestations

A **signature** proves that someone we trust vouched for these exact bytes. It carries no other meaning.

Signatures do not carry the extra information that frameworks like [SLSA](https://slsa.dev/) need. An **attestation** is signed metadata *about* the image, and that is what provides the verifiable detail ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)). An in-toto attestation is authenticated metadata about one or more artifacts, with four layers: predicate, statement, envelope and bundle ([in-toto specification](https://github.com/in-toto/attestation/blob/main/spec/README.md)).

```json
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "predicateType": "https://slsa.dev/provenance/v1",
  "subject": [
    {
      "name": "artifactory.example.com/platform/myapp",
      "digest": { "sha256": "b31bfb4d…" }
    }
  ],
  "predicate": {
    "buildDefinition": { "buildType": "…" }
  }
}
```

The `predicate` is free-form JSON — build provenance, an SBOM, a scan result, a code-review record. In Kyverno, nested `attestations.attestors` check the attestation's own signature, and `attestations.conditions` check the data inside it ([Kyverno verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)). If you supply attestations, at least one attestor is required ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)).

```yaml
attestations:
  - predicateType: https://example.com/ScanResult/v1
    attestors:
      - entries:
          - keys:
              publicKeys: |- …
    conditions:
      - all:
          - key: "{{ criticals }}"
            operator: Equals
            value: 0
          - key: "{{ branch }}"
            operator: Equals
            value: "main"
```

This is where the real supply-chain value sits: enforcing *"this image was built from `main`, by our pipeline, and scanned clean"* rather than just *"somebody signed it"*.

> **Note:** one `verifyImages` rule handles signatures **or** attestations, not both. Use separate rules for separate concerns.

**[Our assessment]** This is a Phase 5 concern. Do not attempt it on day one.

---

## 5. How Kyverno enforces this

### 5.1 The admission flow

This is the part most people get wrong. Image verification is **not** just a validating webhook check. Most of the work happens in the **mutating** webhook.

The rule adds the image digest to matching images when `mutateDigest` is true, which is the default, provided a digest is not already given ([Kyverno verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)).

```
  Pod create request  (from the AKS managed API server)
          |
          v
  Kyverno MUTATING webhook
    - pulls out the image references
    - matches them against imageReferences globs
    - resolves tag -> digest by calling the registry
    - fetches the signature object
    - checks the signature / certificate / transparency log
          |
          +--> Artifactory  [+ Azure Key Vault for the public key]
          |
          v
  Mutation applied
    - image reference rewritten to a digest   (mutateDigest)
    - a verification annotation is stamped on
          |
          v
  Kyverno VALIDATING webhook
    - confirms verification actually happened  (required)
    - confirms a digest is present             (verifyDigest)
          |
          v
  Pod admitted
```

Two things follow from this:

1. **Rejections appear to come from the mutating webhook.** The error text names `mutate.kyverno.svc`, not `validate.kyverno.svc`. That is a useful diagnostic signal — capture the exact text as evidence ([7.6](#76-evidence-to-capture)).
2. **Verification runs after all other mutate rules.** This is deliberate. It lets registry-rewrite or mirror-redirect policies run first, so verification applies to the *final* image reference. If we rewrite `docker.io/*` to an Artifactory remote, that rewrite happens before verification.

**AKS-specific note.** Kyverno receives validating and mutating webhook callbacks from the Kubernetes API server ([AKS triage — admission controllers](https://learn.microsoft.com/en-us/azure/architecture/operator-guides/aks/aks-triage-controllers)). On AKS the API server is managed by Microsoft and reaches in-cluster webhooks through a managed tunnel. **[Our assessment]:** webhook latency and timeout behaviour therefore differ from a self-managed control plane, which matches the routing problems we hit when building the Kyverno NetworkPolicies. Microsoft's own triage guide points at Kyverno's troubleshooting docs for API-server webhook call failures, which tells us this is a recognised failure class on the platform.

Background reading on the mechanism itself: [Kubernetes admission controllers](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/).

### 5.2 Where Kyverno finds the images

By default Kyverno reads image references from `initContainers`, `containers` and `ephemeralContainers`. Images for pods and pod templates are extracted automatically; for custom resources or raw JSON payloads, an `images` field can declare expressions that pull images out of the payload ([Kyverno ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/)). Custom resources such as Tekton Tasks, KubeVirt DataVolumes and Argo Workflow steps can also reference images ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)). In `ClusterPolicy` this is done with an `imageExtractors` block, which declares a JSON path and optionally a JMESPath transform to strip prefixes such as `docker://`.

### 5.3 What the rule fields mean

Each `verifyImages` rule contains: `type` (Cosign or Notary), `imageReferences`, `skipImageReferences`, `required` (all matching images must be verified), `mutateDigest` (rewrite tags to digests), `verifyDigest` (a digest must be present), `repository` (look for signatures somewhere else) and `imageRegistryCredentials` ([Kyverno verifyImages rules](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/)).

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-platform-images
spec:
  background: false
  webhookConfiguration:
    failurePolicy: Fail
    timeoutSeconds: 30
  rules:
    - name: verify-signature
      match:
        any:
          - resources:
              kinds: [Pod]
      verifyImages:
        - imageReferences:
            - "artifactory.example.com/platform/*"
          skipImageReferences:
            - "artifactory.example.com/platform/sandbox/*"
          failureAction: Enforce
          mutateDigest: true
          verifyDigest: true
          required: true
          imageRegistryCredentials:
            secrets: ["artifactory-pull"]
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: "azurekms://platform-kv.vault.azure.net/image-signing-key"
                    rekor:
                      ignoreTlog: true
                    ctlog:
                      ignoreSCT: true
```

| Field | What it does |
|---|---|
| `imageReferences` | Glob patterns picking which images this rule applies to. **Static strings only — no variables.** |
| `skipImageReferences` | Exceptions to the above |
| `failureAction` | `Audit` (report only) or `Enforce` (block) |
| `mutateDigest` | Rewrite the tag to a digest on success. Default `true` |
| `verifyDigest` | Require that a digest is present. Default `true` |
| `required` | Require the verification annotation at validation time. Default `true` |
| `repository` | Look for signatures in a different repository |
| `imageRegistryCredentials` | Secrets used to log in to the registry |
| `attestors` | List of trusted-signer **groups** |

`match.resources.kinds`, `exclude.resources.kinds`, `imageReferences` and preconditions all need static values. Variables are not supported in those fields.

**Signatures stored elsewhere.** To keep signatures in a separate registry, set `COSIGN_REPOSITORY` when signing, then set `repository` in the policy rule ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/), [Secure Pipelines guide](https://secure-pipelines.com/ci-cd-security/signing-verifying-container-images-sigstore-cosign/)).

**Registry credentials.** Multiple image pull secrets are passed as comma-separated values to the `--imagePullSecrets` container flag ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)); the flag is listed in the controller flag reference ([issue #11067](https://github.com/kyverno/kyverno/issues/11067), [Kyverno configuration](https://kyverno.io/docs/installation/customization/)).

### 5.4 Attestor groups and the `count` field

This trips everyone up and is worth stating plainly.

- `attestors` is a list of **groups**.
- Each group has `entries`.
- Inside a group, `count` says how many entries must pass. **The default is all of them.**
- Across groups, **all** groups must pass.

**[Our assessment]** — this is our reading of `count`. Validate it by experiment before any rotation runbook depends on it.

| What you want | How to write it |
|---|---|
| Signed by key A **OR** key B | One group, both entries, `count: 1` |
| Signed by key A **AND** key B | One group, both entries, no `count` |
| Platform key **AND** any one of three team keys | Two groups; the second has `count: 1` |

Supporting context: an image can carry several signatures, for example one at organisation level and one at project level ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)); multiple signatures can refer to a single image ([Sigstore — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)).

> **[Our assessment] — the most likely cause of a self-inflicted outage.** Adding a new key as a second entry *without* `count: 1` creates an AND. Every image must then carry **both** signatures, and everything stops. Test this deliberately in the lab, where it is free. See [9.5](#95-key-rotation).

### 5.5 Background scans and auto-generation

Auto-generation creates equivalent rules for pod controllers such as Deployments. When it is turned off for a controller, Kyverno still applies the policy to the pods those controllers create ([Kyverno auto-gen rules](https://kyverno.io/docs/policy-types/cluster-policy/autogen/)).

**[Our assessment]** Set `background: false` on verification policies. Registry calls and mutations do not make sense during a background report scan, and enabling it multiplies load on Artifactory for no benefit.

### 5.6 Policy types in Kyverno 1.18

There are two ways to verify image signatures in Kyverno, and choosing between them is one of the first decisions to make. They are not two syntaxes for the same thing — they are different API objects with different maturity, different matching semantics and, importantly, different attestor logic.

**Status, corrected.** An earlier draft of this document recorded `ImageValidatingPolicy` as alpha, introduced in v1.14. That is out of date:

| | `ClusterPolicy` with a `verifyImages` rule | `ImageValidatingPolicy` |
| --- | --- | --- |
| API | `kyverno.io/v1` | `policies.kyverno.io/v1` |
| Status in 1.18 | **Deprecated** since 1.17 | **Stable / GA** ([ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/)) |

The Kyverno docs mark `ImageValidatingPolicy` as "Stable • Kyverno v1.18", and 1.18 shipped reliability work on it specifically: better handling of signed timestamps and TSA certificate chains, Notary resolver fixes, correct `matchImageReferences` filtering, and improved autogen for namespaced policies ([Kyverno 1.18 announcement](https://kyverno.io/blog/2026/04/24/announcing-kyverno-release-1.18/)).

The 1.18 announcement states that `ClusterPolicy` deprecation is on track and that users should start migrating, but does **not** name a removal version or date. Third-party coverage puts removal at v1.20 around October 2026 ([byteiota](https://byteiota.com/kyverno-118-cncf-graduated/), [Bits Lovers migration guide](https://www.bitslovers.com/kyverno-1-18-policy-migration/) — both third-party, unconfirmed upstream). **[Our assessment]** Treat the timeline as unverified but plan as though it is roughly right, and confirm it with the Kyverno release notes for our upgrade path. Even the conservative reading is that `ClusterPolicy` has quarters, not years.

#### Field-by-field comparison

| Concern | `verifyImages` (ClusterPolicy) | `ImageValidatingPolicy` |
| --- | --- | --- |
| Kind | A **rule type** inside a general-purpose policy | A **dedicated policy kind** |
| Resource matching | Kyverno `match` / `exclude` | Kubernetes-native `matchConstraints` / `resourceRules` |
| Image matching | `imageReferences`, `skipImageReferences` — static globs only | `matchImageReferences` — globs **or CEL expressions** |
| Expression language | JMESPath | CEL |
| Trusted signers | `attestors` → anonymous groups → `entries` → `count` | `attestors` with a **`name`**, referenced explicitly |
| AND / OR logic | Implicit, via `count` — see [5.4](#54-attestor-groups-and-the-count-field) | Explicit, written in CEL |
| Enforcement | `failureAction: Enforce` or `Audit` | `validationActions: [Deny]`, `[Audit]`, `[Warn]` |
| Digest handling | `mutateDigest`, `verifyDigest`, `required` at rule level | The same three, grouped under `validationConfigurations` |
| Registry credentials | `imageRegistryCredentials` | `credentials` |
| Images in custom resources | `imageExtractors` (JSON path + JMESPath) | `images` (CEL expressions) |
| Attestation content checks | `attestations.conditions` (JMESPath) | `extractPayload()` in CEL |
| Namespace delegation | None — cluster-scoped only | `NamespacedImageValidatingPolicy` |
| Background evaluation | `spec.background` | `evaluation.background.enabled` |

The CEL functions available are `images.containers` to enumerate container images, `verifyImageSignatures(image, [attestors.name])`, `verifyAttestationSignatures(image, attestations.name, [attestors.name])`, and `extractPayload(image, attestations.name)` ([ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/)).

#### The difference that matters most: named attestors instead of `count`

[5.4](#54-attestor-groups-and-the-count-field) describes the `count` field as "the most likely cause of a self-inflicted outage": add a second key as an extra entry without `count: 1` and you have silently created an AND, so every image must now carry both signatures and everything stops. That risk is a property of the `verifyImages` schema, where the boolean logic is implied by group structure and an optional integer.

`ImageValidatingPolicy` removes it. Attestors are named, and the logic is written out:

```yaml
attestors:
  - name: platform
    cosign:
      key:
        kms: "azurekms://${keyvault_name}.vault.azure.net/${signing_key_name}"
  - name: platform-next          # the incoming key during rotation
    cosign:
      key:
        kms: "azurekms://${keyvault_name}.vault.azure.net/${next_key_name}"

validations:
  - expression: >-
      images.containers.all(image,
        verifyImageSignatures(image, [attestors.platform]) > 0 ||
        verifyImageSignatures(image, [attestors.platform-next]) > 0)
    message: "Image must be signed by a platform key."
```

The `||` is visible in review. Nobody has to remember what an absent `count` field defaults to. **[Our assessment]** For a fail-closed control undergoing key rotation ([9.5](#95-key-rotation)), that is a material safety improvement, not a stylistic preference — it converts a documented outage risk into something a reviewer can read.

Kyverno 1.18 also hardened HTTP use inside policies: CEL HTTP in namespaced policies is off by default, and cluster-scoped policies should be treated as a privileged capability with restricted creation rights ([Kyverno configuration](https://kyverno.io/docs/installation/customization/)).

#### [Our assessment] — recommendation, reversed from the earlier draft

The earlier draft concluded that `ClusterPolicy` was the safer choice because "an alpha API is not a sound foundation for a policy that blocks pods." **That reasoning was correct but the premise was wrong, and correcting the premise reverses the conclusion.** `ImageValidatingPolicy` is GA at `policies.kyverno.io/v1`; `ClusterPolicy` is the deprecated one.

**Build on `ImageValidatingPolicy`.** Four reasons, in order of weight:

1. **`ClusterPolicy` is deprecated and scheduled for removal.** Building a new fail-closed production control on it means committing to a migration we can see coming, on someone else's timetable.
2. **Image verification is a new capability, not a migration.** There are no existing `verifyImages` policies to port. This is the one moment where choosing the new type costs nothing.
3. **Named attestors remove the `count` outage risk** described above.
4. **Namespace-scoped delegation comes free** via `NamespacedImageValidatingPolicy`, which maps onto the self-service exception model we already built.

**A known 1.18.0 defect in exactly the path we would use — read this before committing.**

`ImageValidatingPolicy` in Kyverno **1.18.0** carries a regression affecting **key-based and certificate-based** cosign attestors when transparency-log verification is **enabled**. Two failures were reported together ([Kyverno issue #16435](https://github.com/kyverno/kyverno/issues/16435)):

- A **SIGSEGV nil-pointer crash of the admission controller** when verifying new-bundle-format images with a key or certificate attestor and tlog enabled. `opts.TrustedMaterial` was initialised only in the keyless branch.
- A `not enough verified log entries from transparency log: 0 < 1` failure on the legacy tlog path, because Rekor initialisation was moved to run only for keyless attestors.

Both are **fail-closed**: they deny legitimate, correctly signed images. The issue is closed and assigned to the **1.19.0** milestone, so on 1.18.x it should be assumed present unless we confirm a backport into our patch version.

**[Our assessment] — three things follow, and they are all actionable rather than blocking:**

1. **Our intended configuration avoids it.** We sign with `--tlog-upload=false` and disable tlog verification in policy ([11.6](#116-why-ignoretlog-and-ignoresct-appear-in-every-policy-example)), and both failures occur only when tlog verification is enabled. Under `insecureIgnoreTlog: true` we are outside the affected path. But note the shift in status: `insecureIgnoreTlog` stops being a configuration preference and becomes a **required workaround** on 1.18 — so it must be documented as such, not left looking optional.
2. **This intersects directly with [11.1](#111-cosign-version-compatibility-and-the-v3-bundle-format).** The crash is specific to *new-bundle-format* images, which is what Cosign v3 produces by default. Cosign v3 defaults plus an IVPol key attestor plus tlog enabled is the exact combination that panics. That is a concrete illustration of why the cosign version must be pinned and the pair tested.
3. **Treat it as a maturity signal, not a disqualifier.** GA labelling notwithstanding, this is evidence that the key and certificate paths in `ImageValidatingPolicy` are less exercised than the keyless path. It does not outweigh the deprecation argument above, but it does mean **validate on our exact patch version — 1.18.2 — rather than on the documentation.** Add "admission controller does not crash" to the Stage 1 evidence, which is not a check anyone would otherwise think to write down.

**What else to verify during the proof of concept before committing:**

- That the `validationConfigurations` fields (`mutateDigest`, `verifyDigest`, `required`) behave as the rule-level equivalents do — in particular that `mutateDigest` still rewrites the pod spec, since the GitOps consequence in [9.1](#91-gitops-drift) depends on it.
- That `credentials` reaches Artifactory the same way `imageRegistryCredentials` does, including the `namespace/name` form ([8.8](#88-registry-credentials-and-rbac)).
- That autogen behaves for pod controllers ([5.5](#55-background-scans-and-auto-generation)).
- That the Kyverno CLI limitation in [6.11](#611-the-kyverno-cli-cannot-test-this) is unchanged for this policy type — do not assume the CLI gained image-verification test support along with the new API.

**Fallback position.** If any of the above fails, `ClusterPolicy` remains functional in 1.18 and every example in this document works. Treat it as a documented fallback with a known expiry, not as the default. See [Appendix C](#appendix-c--imagevalidatingpolicy-variant) for the policy shape and open decision 3 in [section 12](#12-open-decisions).

---

## 6. Test Stage 1 — a throwaway AKS cluster and ACR

### 6.0 Why we test on AKS and not on kind



| | Stage 1 (this section) | Stage 2 (section 7) |
|---|---|---|
| Cluster | Throwaway AKS, default networking | Real non-production AKS |
| Registry | Azure Container Registry | Artifactory |
| Key | Local file (`cosign.key`) | Kubernetes Secret, then Azure Key Vault |
| Network | Default egress | Azure Firewall, UDR, NetworkPolicy |
| Purpose | Learn the mechanics | Prove the plumbing |


**Lab-only shortcut.** The Kyverno pods also need to *read* the signature object, which is a separate registry call from the node's pull. For this lab only, turn on anonymous pull so we do not have to wire credentials yet:

```bash
az acr update -n $ACR --anonymous-pull-enabled
```

Anonymous pull is available on Standard and Premium tiers and makes the whole registry publicly readable ([ACR anonymous pull](https://learn.microsoft.com/en-us/azure/container-registry/anonymous-pull-access)).

> **Do not do this anywhere real.** It is the Stage 1 equivalent of the plain-HTTP kind registry: it removes authentication as a variable so that a failure means a *policy* problem, not a *credentials* problem. Stage 2 puts credentials back in deliberately.



### 6.3 Generate a key pair

Local keys are generated with `cosign generate-key-pair` ([Sigstore — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)).

```bash
cosign generate-key-pair
# produces cosign.key (private, password protected) and cosign.pub (public)
```

### 6.4 Build, push and sign

```bash
mkdir -p demo && cd demo
cat > Dockerfile <<'EOF'
FROM alpine:3.20
CMD ["sleep", "3600"]
EOF

# Build inside ACR — no local Docker needed
az acr build -r $ACR -t demo:v1 .

# Log in so cosign can push the signature (token lasts about 3 hours)
az acr login -n $ACR

export IMAGE=$ACR.azurecr.io/demo:v1

# --tlog-upload=false keeps everything private (no public Rekor log)
cosign sign --key ../cosign.key --tlog-upload=false $IMAGE

# Always confirm outside Kyverno first
cosign verify --key ../cosign.pub --insecure-ignore-tlog $IMAGE
cosign triangulate $IMAGE
```

The general signing form is `cosign sign [--key <key path>|<kms uri>] [-r] <image uri>` ([Sigstore — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)). `--tlog-upload=false` skips uploading to the transparency log ([Ratify on Azure](https://ratify.dev/docs/quickstarts/ratify-on-azure/)).

If the base image pull from Docker Hub is rate-limited, swap `alpine:3.20` for an image from Microsoft Artifact Registry, e.g. `mcr.microsoft.com/azurelinux/base/core:3.0`.

### 6.5 Apply the policy

This generates the policy with the real registry name and public key filled in, so there is no copy-paste error:

```bash
cat <<EOF | kubectl apply -f -
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: check-image-signature
spec:
  background: false
  webhookConfiguration:
    failurePolicy: Fail
    timeoutSeconds: 30
  rules:
    - name: check-signature
      match:
        any:
          - resources:
              kinds: [Pod]
      verifyImages:
        - imageReferences:
            - "${ACR}.azurecr.io/demo*"
          failureAction: Enforce
          mutateDigest: true
          verifyDigest: true
          required: true
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: |-
$(sed 's/^/                      /' ../cosign.pub)
                    rekor:
                      ignoreTlog: true
                    ctlog:
                      ignoreSCT: true
EOF
```

### 6.6 The four experiments

The rule fails if the signature is not found in the registry, or if the image was not signed with the given key ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)) — which is why experiments 2 and 3 fail differently.

| # | Action | Expected result |
|---|---|---|
| 1 | Run the signed image | Admitted; image rewritten to a digest |
| 2 | Run an unsigned image | Rejected — signature not found |
| 3 | Run an image signed with a different key | Rejected — invalid signature |
| 4 | Two keys in one group, with and without `count: 1` | Confirms the AND / OR behaviour |

```bash
# 1. Signed
kubectl run signed --image=$ACR.azurecr.io/demo:v1
kubectl get pod signed -o jsonpath='{.spec.containers[0].image}'; echo
# Look for: demo@sha256:...  — NOT demo:v1

# 2. Unsigned
az acr build -r $ACR -t demo:v2 .
kubectl run unsigned --image=$ACR.azurecr.io/demo:v2

# 3. Signed with the wrong key
cosign generate-key-pair --output-key-prefix wrong
cosign sign --key wrong.key --tlog-upload=false $ACR.azurecr.io/demo:v2
kubectl run wrongkey --image=$ACR.azurecr.io/demo:v2
```

**Experiment 4 — do this here, where it costs nothing.** Add both `cosign.pub` and `wrong.pub` as two entries in the *same* group. First without `count`, then with `count: 1`. Confirm the behaviour claimed in [5.4](#54-attestor-groups-and-the-count-field). This is the check that protects the key-rotation runbook.

Expected rejection text:

```
Error from server: admission webhook "mutate.kyverno.svc" denied the request:
resource Pod/default/unsigned was blocked due to the following policies

check-image-signature:
  check-signature: 'image verification failed for <acr>.azurecr.io/demo:v2: signature not found'
```

### 6.7 Inspect what happened

```bash
# The verification annotation Kyverno stamps on the pod
kubectl get pod signed -o jsonpath='{.metadata.annotations}' | jq

# Policy reports — the same data a dashboard would consume
kubectl get policyreport -A
kubectl get clusterpolicyreport

# Where the signature lives, and what is attached
cosign triangulate $ACR.azurecr.io/demo:v1
cosign tree $ACR.azurecr.io/demo:v1
```

`cosign tree` lists attached signatures ([Sigstore — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)). Policy reports are Kubernetes custom resources generated by Kyverno holding the results of applying policies ([Kyverno policy reports](https://kyverno.io/docs/guides/reports/)).

### 6.8 Audit mode

Change `failureAction: Enforce` to `Audit`, reapply, and run the unsigned pod again. It will now be **created**, with a `fail` result recorded in the PolicyReport ([Kyverno policy exceptions](https://kyverno.io/docs/guides/exceptions/)).

This is the mechanism the entire rollout depends on ([Phase 3](#102-phases)). It is worth watching directly rather than taking on trust.

### 6.9 Optional — try an attestation

```bash
echo '{"scan":"clean","criticals":0}' > predicate.json
cosign attest --key ../cosign.key --tlog-upload=false \
  --predicate predicate.json \
  --type https://example.com/ScanResult/v1 \
  $ACR.azurecr.io/demo:v1
```

Then add a rule with an `attestations` block asserting `criticals == 0`, per [4.5](#45-signatures-versus-attestations).

### 6.10 Clean up

```bash
az group delete -n $RG --yes --no-wait
```

That removes the cluster, the registry and everything in them.

### 6.11 The Kyverno CLI cannot test this

**This is a hard constraint, not a preference.** The Kyverno CLI `test` command supports the validate, mutate and generate rule types ([Kyverno CLI](https://kyverno.io/docs/subprojects/kyverno-cli/), [test command reference](https://main.kyverno.io/docs/kyverno-cli/usage/test/)). Image verification is **not** in that list, and the CLI does not embed a Kubernetes control plane ([test command reference](https://main.kyverno.io/docs/kyverno-cli/usage/test/)).



---

## 7. Test Stage 2 — realistic AKS with Artifactory and Key Vault

**Objective [Our assessment]:** prove the four things Stage 1 could not — the Azure egress path, Entra Workload ID to Key Vault, real registry authentication, and TLS trust.

### 7.1 Prerequisites

- [ ] Non-production AKS cluster with Kyverno already deployed
- [ ] **Only if verifying with `azurekms://`:** cluster OIDC issuer and workload identity enabled (see [8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id)). Not required under the recommended design, where the policy references an exported public key ([8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy))
- [ ] Vault Transit signing key created, and the CI signing identity able to authenticate to Vault (see [8.4.1](#841-hashicorp-vault-transit--the-signing-side))
- [ ] Artifactory repository for test images, e.g. `artifactory.example.com/sandbox/`
- [ ] Push credentials to that repository
- [ ] Azure Key Vault with a signing key, or a local key pair for the first pass
- [ ] `cosign`, `kubectl`, `az` CLI locally
- [ ] Agreement that a dedicated test namespace may be created
- [ ] Read access to Azure Firewall logs for the cluster's egress path

### 7.2 Limit the damage if it goes wrong

**[Our assessment]** Do not touch the cluster-wide policy set. Scope the test policy to one dedicated namespace using the label-selector pattern we already established for the Gatekeeper migration.

```bash
kubectl create namespace kyverno-image-test
kubectl label namespace kyverno-image-test kyverno-image-test=enabled
```

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: check-image-signature-sandbox
spec:
  background: false
  webhookConfiguration:
    failurePolicy: Ignore     # see note below
    timeoutSeconds: 15
  rules:
    - name: check-signature
      match:
        any:
          - resources:
              kinds: [Pod]
              namespaceSelector:
                matchLabels:
                  kyverno-image-test: enabled
      verifyImages:
        - imageReferences:
            - "artifactory.example.com/sandbox/*"
          failureAction: Enforce
          imageRegistryCredentials:
            secrets: ["artifactory-pull"]
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: "k8s://kyverno/cosign-pub"
```

> **[Our assessment] — `failurePolicy: Ignore` is deliberate at this stage.** If egress to Artifactory is not yet wired up, a webhook timeout should fail *open* on this test policy while we debug the plumbing, rather than risk affecting unrelated workloads. Switch to `Fail` only once the path is proven end to end.

**Check for an existing exclusion first.** Setting namespace exclusions means later policies cannot act on resources in those namespaces at all, because the API server has been told not to send admission requests for them ([Kyverno installation](https://kyverno.io/docs/installation/)). **[Our assessment]** Confirm the test namespace is not caught by an existing exclusion, or the test will "pass" for entirely the wrong reason.

### 7.3 Pre-flight checks — run these before applying any policy

**[Our assessment]** These separate infrastructure problems from policy problems. On AKS there are three independent layers that can each block the outbound call — see [8.1](#81-the-three-layer-egress-problem).

**Check 1 — the Kubernetes NetworkPolicy layer.**

```bash
kubectl -n kyverno exec deploy/kyverno-admission-controller -- \
  wget -qO- --timeout=5 https://artifactory.example.com
```

Image *pulls* happen via containerd on the node. Signature *verification* is an outbound call from the Kyverno controller pod itself. If the existing NetworkPolicies only opened egress from nodes and kubelet, the Kyverno pods have no route out. A hang here is a NetworkPolicy gap.

**Check 2 — the Azure Firewall / UDR layer.** If Check 1 times out, look at the firewall logs for a denied FQDN before assuming it is NetworkPolicy:

```bash
az network firewall show -g <rg> -n <fw-name> --query "ipConfigurations"
# then query AZFWApplicationRule logs in Log Analytics,
# filtered on the AKS node subnet as source and Fqdn == "artifactory.example.com"
```

**Check 3 — TLS trust versus routing.** A certificate error, rather than a timeout, points at the CA trust problem in [8.3](#83-ca-trust-store). Nodes pull happily because they trust the internal CA; the Kyverno pods do not inherit that trust.

**Check 4 — workload identity to Key Vault** (only if using an `azurekms://` attestor):

```bash
kubectl -n kyverno get sa kyverno-admission-controller -o yaml | grep azure.workload.identity
kubectl -n kyverno exec deploy/kyverno-admission-controller -- env | grep AZURE_
```

`DefaultAzureCredential` uses environment variables injected by the workload identity webhook to authenticate to Key Vault ([Microsoft Learn — Workload ID](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)). If `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_FEDERATED_TOKEN_FILE` are missing, the pod was never labelled for injection.

**Check 5 — does signing work outside Kubernetes at all?**

```bash
docker login artifactory.example.com
docker build -t artifactory.example.com/sandbox/demo:v1 .
docker push artifactory.example.com/sandbox/demo:v1

cosign sign --key cosign.key artifactory.example.com/sandbox/demo:v1
cosign verify --key cosign.pub artifactory.example.com/sandbox/demo:v1
cosign triangulate artifactory.example.com/sandbox/demo:v1
```

If this fails, the problem is Artifactory permissions or repository type — **not** Kyverno. Debugging it here is far cheaper. This is also where an Artifactory referrers incompatibility ([4.2](#42-where-the-signature-is-stored)) will show up. Remember JFrog requires Cosign 2.0.0 or later for OCI 1.1 referrers ([JFrog OCI repositories](https://docs.jfrog.com/artifactory/docs/oci-repositories)).

### 7.4 Store the public key

For the first pass, a Kubernetes Secret avoids the workload identity dependency:

```bash
kubectl create secret generic cosign-pub -n kyverno --from-file=cosign.pub=cosign.pub
```

Referenced from policy as `k8s://kyverno/cosign-pub`. Using a Secret rather than inlining the key also exercises the RBAC path we will need in production ([8.8](#88-registry-credentials-and-rbac)).

**[Our assessment] — under the recommended design, this `k8s://` pass is the target configuration, not a stepping stone.** Per [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy), signing happens in CI against Vault Transit while the cluster verifies with the exported public key, so no vault is referenced from the policy at all. Prove this configuration first and completely.

Run a **second** pass with a live KMS URI only if open decision 1c goes the other way — that is, if a live key reference at verification time is mandated. In that case use `azurekms://` and Entra Workload ID ([8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id)), not `hashivault://`. Either way, do not attempt both in one pass: failures become ambiguous and it wastes days.

### 7.5 Run the experiments

```bash
# Signed — expect admission and a digest
kubectl -n kyverno-image-test run signed --image=artifactory.example.com/sandbox/demo:v1
kubectl -n kyverno-image-test get pod signed \
  -o jsonpath='{.spec.containers[0].image}'; echo

# Unsigned — expect rejection
docker build -t artifactory.example.com/sandbox/demo:v2 .
docker push artifactory.example.com/sandbox/demo:v2
kubectl -n kyverno-image-test run unsigned --image=artifactory.example.com/sandbox/demo:v2

# Control: a pod in a NON-labelled namespace must be unaffected
kubectl -n default run control --image=artifactory.example.com/sandbox/demo:v2
```

That last check matters. It confirms the namespace scoping works and the policy really is isolated.

### 7.6 Evidence to capture

| Evidence | How |
|---|---|
| Signature exists in Artifactory | `cosign triangulate` / `cosign tree` |
| Verification passes outside the cluster | `cosign verify --key cosign.pub …` |
| Signed pod admitted, digest pinned | `kubectl get pod signed -o jsonpath=…` |
| Unsigned pod rejected | The rejection text — **note which webhook is named** |
| Non-labelled namespace unaffected | The control pod runs |
| `count` AND/OR behaviour | Two-key test per [5.4](#54-attestor-groups-and-the-count-field) |
| Azure Key Vault path works | Second pass with an `azurekms://` attestor |
| Azure Firewall allows the FQDN | An allow entry in the AZFWApplicationRule log |
| Admission latency change | Kyverno admission duration metrics, before and after |
| Behaviour under load with a cold cache | Scale a deployment to 50 replicas and watch for timeouts |
| Policy report entries | `kubectl get policyreport -n kyverno-image-test -o yaml` |



---

## 8. AKS platform integration

This section is the real difference between running this on AKS and running it anywhere else.

### 8.1 The three-layer egress problem

**[Our assessment]** On AKS, a Kyverno pod reaching `artifactory.example.com` passes through three separately configured layers. All three must allow it, and each one fails differently. This is the single most likely reason a proof of concept stalls.

| Layer | What it controls | How failure looks |
|---|---|---|
| Kubernetes NetworkPolicy | Pod-to-external egress | Connection refused, or an immediate failure |
| NSG on the node subnet | IP and port | Timeout |
| Azure Firewall via UDR | FQDN / application rules | Timeout, plus a deny entry in the firewall log |

Relevant platform facts:

AKS outbound dependencies are almost entirely defined by FQDNs, which do not have static addresses behind them. Because of that you cannot use NSGs to lock down outbound traffic from an AKS cluster. Microsoft does not recommend deny-all NSG rules for outbound internet traffic, and points instead at a firewall that can control traffic by domain name — Azure Firewall can restrict outbound HTTP and HTTPS by destination FQDN ([AKS outbound rules](https://learn.microsoft.com/en-us/azure/aks/outbound-rules-control-egress)).

With outbound type `userDefinedRouting`, AKS does not create a public load balancer for egress and does not add its own SNAT public IPs. All outbound traffic from the node pool subnets follows the UDR to the Azure Firewall private IP, so the firewall sees every outbound flow ([AKS with Azure Firewall](https://learn.microsoft.com/en-us/azure/architecture/guide/aks/aks-firewall)). Requests from AKS agent nodes follow a UDR on the subnet the cluster was deployed into ([Limit egress traffic](https://docs.azure.cn/en-us/aks/limit-egress-traffic)).

Blocking traffic *inside* the cluster with NSGs and firewalls is not supported; use network policies for that ([AKS outbound rules](https://learn.microsoft.com/en-us/azure/aks/outbound-rules-control-egress)).

**[Our assessment] — actions:**

1. Add an **Azure Firewall application rule** for `artifactory.example.com` on port 443, sourced from the AKS node subnets. This is separate from, and additional to, the NetworkPolicy work.
2. If using an `azurekms://` attestor, add the Key Vault FQDN too — or better, put Key Vault behind a **private endpoint** so the call never leaves the VNet ([8.5](#85-private-endpoints-and-dns)).
3. Do **not** try to solve this with NSG rules alone. Per the guidance above, FQDN destinations are not something NSGs can address.
4. The AKS platform allow-list is maintained for us: rather than tracking those FQDNs by hand, create an Azure Firewall application rule using the `AzureKubernetesService` FQDN tag, which Azure keeps current ([AKS with Azure Firewall](https://learn.microsoft.com/en-us/azure/architecture/guide/aks/aks-firewall)). Our Artifactory and Key Vault rules sit *alongside* that tag, not inside it.
5. Microsoft recommends at least 20 frontend IPs on Azure Firewall to avoid SNAT port exhaustion ([Limit egress traffic](https://docs.azure.cn/en-us/aks/limit-egress-traffic)). **[Our assessment]** Image verification adds an outbound connection per admission. During a mass rollout that is a real new source of SNAT pressure, and should be checked against the current frontend IP allocation.

### 8.2 Which network policy engine the cluster uses

Azure NPM uses iptables on Linux and translates policies into sets of allowed and disallowed IP pairs. Azure NPM for Linux does not scale beyond 250 nodes and 20,000 pods, and past those limits you may hit out-of-memory errors. Microsoft recommends Cilium, which offers Layer 7 policy, FQDN filtering, and an eBPF dataplane. Azure NPM for Linux nodes retires on 30 September 2028 ([AKS network policies](https://learn.microsoft.com/en-us/azure/aks/use-network-policies)). Some features, such as DNS-based rules, need Advanced Container Networking, which costs extra ([Kubernetes on Azure workshop](https://microsoft.github.io/k8s-on-azure-workshop/module-4/3_security/4_network_egress/index.html)).

**[Our assessment] — what this means here:**

- If a cluster runs **Azure NPM**, we cannot write an FQDN-based NetworkPolicy for `artifactory.example.com`. The allowance must be IP-range based at the NetworkPolicy layer and FQDN-based at the Azure Firewall layer. Artifactory's resolved IPs can change, so use a broad-but-bounded CIDR at the NetworkPolicy layer and rely on the firewall for precision.
- If a cluster runs **Cilium**, a `CiliumNetworkPolicy` with `toFQDNs` is available and is much cleaner — but note the Advanced Container Networking cost above.
- Confirm which engine each cluster uses before writing anything. A mixed estate needs both forms.

```bash
az aks show -g <rg> -n <cluster> --query "networkProfile.networkPolicy" -otsv
```

### 8.3 CA trust store

Kyverno's trust store is **not** the node's trust store. This is the most common cause of x509 failures against internal registries.

The Kyverno Helm chart supports `global.caCertificates.data`, described as global CA certificates for Kyverno deployments, supplied as one large string, with per-controller values overriding the global one ([values.yaml](https://github.com/kyverno/kyverno/blob/main/charts/kyverno/values.yaml), [Artifact Hub](https://artifacthub.io/packages/helm/kyverno/kyverno?modal=values)). The capability arrived in chart version 1.12 and is mounted into each deployment as a ConfigMap at `/etc/ssl/certs/ca-certificates.crt`; a `caCertificates.volume` option also exists ([issue #10141](https://github.com/kyverno/kyverno/issues/10141)). `caCertificates` is one of the settings you can define globally and override per component ([Helm chart structure](https://deepwiki.com/kyverno/kyverno/8.1-helm-chart-structure)).

| Option | Behaviour |
|---|---|
| `global.caCertificates.data` | Replaces the whole bundle. Fine for purely internal registries; breaks access to public registries |
| `global.caCertificates.volume` | Mount a volume so the pods inherit the node's trust. Preferred where internal and public registries are mixed |

**[Our assessment]** Prefer the `volume` option. Replacing the bundle outright breaks public registry access, which matters the moment any policy references a public image.

### 8.4 Signing key custody — Vault Transit and Azure Key Vault

The signing key is touched at two different moments, by two different principals, over two different authentication paths:

| Moment | Who | Needs | Where it runs |
| --- | --- | --- | --- |
| **Signing** | The CI pipeline identity | The **private** key operation | Build infrastructure |
| **Verification** | The Kyverno admission controller | The **public** key only | Every AKS cluster, on the admission hot path |

Almost all published guidance conflates these. Keeping them separate is what makes this section tractable, because the two have very different constraints — and the verification side turns out to be where the real design decision sits. **If you read only one part of this section, read [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy).**

#### 8.4.1 HashiCorp Vault Transit — the signing side

Cosign's Vault integration uses the **Transit** secrets engine, which provides signature-as-a-service: Vault holds the private key and performs the signing operation, so nothing outside Vault ever sees it ([Vault Transit secrets engine](https://developer.hashicorp.com/vault/docs/secrets/transit)). The provider requires the Transit engine to be enabled, and reads `VAULT_ADDR` and `VAULT_TOKEN` from the environment; if Transit is mounted at a non-default path, `TRANSIT_SECRET_ENGINE_PATH` overrides it ([Sigstore key management](https://docs.sigstore.dev/cosign/key_management/overview/)).

**Step 1 — enable Transit and create the signing key.**

Cosign uses ECDSA-P256 as its signature algorithm ([Sigstore blog](https://blog.sigstore.dev/cosign-image-signatures-77bab238a93/)), and Vault Transit supports `ecdsa-p256` for signing and verification ([Transit key types](https://developer.hashicorp.com/vault/docs/secrets/transit)). The key type must therefore be set explicitly — the Transit default is not an ECDSA signing key.

```bash
vault secrets enable transit

vault write -f transit/keys/image-signing-key \
  type=ecdsa-p256 \
  exportable=false \
  allow_plaintext_backup=false
```

`exportable=false` is deliberate: the private key must never be retrievable, even by an operator. Cosign can also create the key for us with `cosign generate-key-pair --kms hashivault://image-signing-key` ([Sigstore key management](https://docs.sigstore.dev/cosign/key_management/overview/)), but creating it in Vault directly keeps key provisioning inside the Vault team's normal Terraform.

**Step 2 — a least-privilege Vault policy for the signing identity.**

```hcl
# vault policy write cosign-image-signer signer.hcl
path "transit/keys/image-signing-key" {
  capabilities = ["read"]
}

path "transit/sign/image-signing-key" {
  capabilities = ["update"]
}
```

Read on `transit/keys/…` returns the **public** key; update on `transit/sign/…` performs the signing operation. Note what is absent: no `delete`, no `transit/keys/*` wildcard, and no ability to export. **[Our assessment]** Confirm the exact set of paths Cosign touches during the proof of concept by watching the Vault audit log during a signing run, and trim the policy to match — some versions also exercise `transit/hmac/…` or `transit/verify/…`. Do not grant broader capabilities in advance to avoid a debugging session.

**Step 3 — how the pipeline authenticates.** This is the part to design properly, because `VAULT_TOKEN` must come from somewhere and a long-lived static token in CI is the thing we are trying to avoid.

| CI platform situation | Recommended Vault auth method |
| --- | --- |
| Runners execute as pods in Kubernetes | [Kubernetes auth](https://developer.hashicorp.com/vault/docs/auth/kubernetes) — the runner's service account token is exchanged for a short-lived Vault token |
| GitLab CI, GitHub Actions or similar with OIDC | [JWT/OIDC auth](https://developer.hashicorp.com/vault/docs/auth/jwt) — bind the Vault role to the pipeline's OIDC claims, so only the golden pipeline can obtain the signing role |

The JWT/OIDC route is the closer analogue to what keyless signing achieves ([4.4.3](#443-keyless--fulcio-and-rekor)): the right to sign is bound to a pipeline identity rather than to a stored credential. **[Our assessment]** Bind the Vault role to the specific project and protected-branch or tag claims, not merely to the CI platform. Otherwise any job on the platform can obtain a signing token — the same reusable-workflow weakness described in [4.4.3](#443-keyless--fulcio-and-rekor), in a different guise. This is the Vault-side expression of the governance point in [4.4.4](#444-comparison-and-recommendation): signing must live in a stage teams cannot edit.

**Step 4 — sign.**

```bash
export VAULT_ADDR=https://vault.example.com
# VAULT_TOKEN obtained via Kubernetes or JWT auth in the step above

cosign sign --key hashivault://image-signing-key \
  --tlog-upload=false \
  ${IMAGE}
```

For multi-architecture images add `-r` ([11.2](#112-multi-architecture-images-and-image-indexes)). `--tlog-upload=false` keeps everything off the public transparency log, which is why the policy needs `ignoreTlog` and `ignoreSCT` ([11.6](#116-why-ignoretlog-and-ignoresct-appear-in-every-policy-example)).

**Step 5 — export the public key once.** This is what the clusters will use, and it is the input to [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy):

```bash
cosign public-key --key hashivault://image-signing-key > cosign.pub
```

Treat `cosign.pub` as a normal build artefact under version control in the platform controls repository. It is a public key — there is nothing to protect, and having it in Git gives us a reviewable audit trail of exactly which key each cluster trusts.

#### 8.4.2 Azure Key Vault and Microsoft Entra Workload ID

This remains a valid alternative for key custody, and on the verification side it is in fact the **better-supported** option for a live KMS reference — see [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy). Choose it if the platform team would rather operate Key Vault than take a dependency on the Vault team, or if a live KMS reference in the policy is a firm requirement.

The Cosign Azure KMS module uses `DefaultAzureCredential`, which supports environment variables, workload identity, managed identity, Azure CLI and Azure Developer CLI ([sigstore Azure KMS package](https://pkg.go.dev/github.com/sigstore/sigstore/pkg/signature/kms/azure)). That is what lets the AKS integration work with **no stored secret at all** — and it is precisely the capability the Vault provider lacks.

Microsoft Entra Workload ID uses service account token volume projection so pods can use a Kubernetes identity. A Kubernetes token is issued, and OIDC federation lets applications reach Azure resources using annotated service accounts ([Microsoft Learn — Workload ID](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)). On AKS Automatic, workload identity and the OIDC issuer are preconfigured; on AKS Standard you enable them yourself ([same page](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)).

**Cluster setup** ([Microsoft Learn](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview), [Workload Identity quick start](https://azure.github.io/azure-workload-identity/docs/quick-start.html)):

```bash
az aks update -g <rg> -n <cluster> --enable-oidc-issuer --enable-workload-identity

export AKS_OIDC_ISSUER=$(az aks show -g <rg> -n <cluster> \
  --query "oidcIssuerProfile.issuerURL" -otsv)
```

Then create a user-assigned managed identity, create a federated identity credential binding it to the Kyverno service account, and annotate the workload:

```yaml
# Kyverno Helm values
admissionController:
  serviceAccount:
    annotations:
      azure.workload.identity/client-id: "<managed-identity-client-id>"
  podLabels:
    azure.workload.identity/use: "true"
backgroundController:
  serviceAccount:
    annotations:
      azure.workload.identity/client-id: "<managed-identity-client-id>"
  podLabels:
    azure.workload.identity/use: "true"
```

**Key Vault permissions.** Signing with a Key Vault key does not necessarily need the Key Vault Crypto Officer role — another identity can be given Key Vault Crypto User for the signing action only ([Ratify on Azure](https://ratify.dev/docs/quickstarts/ratify-on-azure/)). Role definitions are in the [Key Vault RBAC guide](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide); key types are covered in [About Key Vault keys](https://learn.microsoft.com/en-us/azure/key-vault/keys/about-keys).

**[Our assessment] — proposed role split:**

| Principal | Role | Purpose |
|---|---|---|
| CI pipeline identity | Key Vault Crypto User | Sign |
| Kyverno controller identity | Key Vault Crypto User, or narrower read-only key access | Fetch the public key to verify |

Verification only needs the public key, so the Kyverno identity should be scoped as narrowly as the provider allows. Work out the minimum viable role during the proof of concept instead of granting Crypto Officer by default.

**Signing command** ([Ratify on Azure](https://ratify.dev/docs/quickstarts/ratify-on-azure/)):

```bash
cosign sign --key azurekms://$AKV_NAME.vault.azure.net/${KEY_NAME}/${KEY_VER} \
  --tlog-upload=false ${IMAGE}
```

**[Our assessment]** Pin the key version (`/${KEY_VER}`) in the *signing* command, but consider leaving it out of the *policy*, so that rotating the key in Key Vault does not instantly invalidate every existing signature. Test this explicitly — do not assume the version-matching behaviour on the verification side. See open decision 7 in [section 12](#12-open-decisions).

#### 8.4.3 The verification side — do not reference Vault from the policy

**[Our assessment] — this is the most consequential finding in section 8, and it corrects the simpler view given in [4.4.1](#441-keyed--a-key-held-in-a-vault-recommended-start).**

Kyverno does support KMS URIs in `publicKeys`, including `hashivault://[KEY]` alongside `azurekms://`, `awskms://` and `gcpkms://` ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)). So `publicKeys: "hashivault://image-signing-key"` is valid syntax. **It is still the wrong choice for us,** for a reason specific to the Vault provider.

**The problem: Vault authentication from a long-running controller.** The Cosign Vault provider authenticates only with a `VAULT_TOKEN` environment variable ([Sigstore key management](https://docs.sigstore.dev/cosign/key_management/overview/)). Kyverno does not support Vault's Kubernetes auth method, and the upstream feature request states the difficulty plainly: a `VAULT_TOKEN` supplied as an immutable environment variable is a problem for a long-running process, because Vault tokens have a TTL ([Kyverno issue #6313](https://github.com/kyverno/kyverno/issues/6313)). Storing a Vault token in an environment variable has also been raised as a concern in Cosign itself, since environment values surface in logs, traces and container inspection ([cosign issue #2861](https://github.com/sigstore/cosign/issues/2861)).

Put together, `hashivault://` in a production policy means: a static Vault token, held in an environment variable, in the pod on the admission hot path, that **expires**. When it expires, image verification fails cluster-wide — and with `failurePolicy: Fail` that means no pods start. This is a scheduled outage with the date hidden in a token TTL. The workaround is a Vault Agent sidecar renewing the token into the pod, which adds a component to the admission path and does not remove the underlying fragility.

**The design that avoids all of it.** Verification needs only the **public** key, and a public key is not a secret. So publish the key into the cluster instead of fetching it live:

```yaml
attestors:
  - count: 1
    entries:
      - keys:
          publicKeys: "k8s://kyverno/cosign-pub"   # a Secret holding cosign.pub
          rekor:
            ignoreTlog: true
          ctlog:
            ignoreSCT: true
```

Created from the artefact exported in [8.4.1](#841-hashicorp-vault-transit--the-signing-side):

```bash
kubectl create secret generic cosign-pub -n kyverno --from-file=cosign.pub=cosign.pub
```

The key may equally be inlined in the policy ([5.3](#53-what-the-rule-fields-mean)); a Secret is preferable because it exercises the RBAC path in [8.8](#88-registry-credentials-and-rbac) and keeps one copy per cluster rather than one per policy.

**What this buys us:**

| Benefit | Why it matters here |
| --- | --- |
| **Vault is off the admission hot path entirely** | No Vault egress rule ([8.1](#81-the-three-layer-egress-problem)), no private DNS zone for Vault ([8.5](#85-private-endpoints-and-dns)), no Vault TLS trust in the Kyverno bundle ([8.3](#83-ca-trust-store)) |
| **No Vault credential in any cluster** | Nothing to rotate, leak, or expire. The [8.4.1](#841-hashicorp-vault-transit--the-signing-side) Vault policy is needed by CI only |
| **Vault availability does not gate pod scheduling** | Removes an entire failure domain from [9.4](#94-disaster-recovery-and-break-glass). A Vault outage during a regional failover no longer stops workloads starting |
| **Fewer moving parts under load** | One less network round trip per cache miss during an admission storm ([8.7](#87-aks-lifecycle-events-and-admission-storms)) |
| **Reviewable trust** | The exact key each cluster trusts is a file in Git, not a runtime lookup |

**The cost, stated honestly:** the public key is now in two places, so rotation must update the clusters as well as Vault. That is not really a new cost — [9.5](#95-key-rotation) already requires a coordinated dual-signing rotation across every policy, and the public key travels with the policy through the same GitOps flow. It is one more field in a change we were already making.

**If a live KMS reference in the policy is a firm requirement** — for example if Security objects to a public key being held in a cluster Secret, or wants key custody to be the single source of truth at verification time — then **use Azure Key Vault rather than Vault Transit for that reference.** `DefaultAzureCredential` supports workload identity natively ([8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id)), so the `azurekms://` path has no static-token problem and no expiring credential. That is a genuine, if narrow, advantage of the Azure option, and it is the one case where Azure Key Vault should be preferred over Vault Transit.

**[Our assessment] — recommended combination:**

| Stage | Choice |
| --- | --- |
| Signing, in CI | **Vault Transit** via `hashivault://`, with Kubernetes or JWT auth ([8.4.1](#841-hashicorp-vault-transit--the-signing-side)) |
| Verification, in cluster | **Exported public key** via `k8s://kyverno/cosign-pub` |
| Fallback if a live KMS reference is mandated | **Azure Key Vault** via `azurekms://` with Entra Workload ID ([8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id)) |

This is the shape to prove in Test Stage 2. Note that [7.4](#74-store-the-public-key) already recommends running the proof of concept twice, starting with `k8s://` — under this design the `k8s://` pass is not a stepping stone, it **is** the target configuration. See open decision 1c in [section 12](#12-open-decisions).

### 8.5 Private endpoints and DNS

**[Our assessment]** — no external source for this; it is a standard Azure pattern, flagged because it is a common failure mode.

If Artifactory or Key Vault is reached through a **private endpoint**, the Kyverno pods must be able to resolve the private DNS zone (`privatelink.vaultcore.azure.net`, or the equivalent for Artifactory's ingress). Check with:

```bash
kubectl -n kyverno exec deploy/kyverno-admission-controller -- \
  nslookup artifactory.example.com
```

If this returns a public IP while the firewall expects private routing, the call fails in a way that looks exactly like a firewall deny. Confirm CoreDNS forwards to the VNet-linked private DNS resolver. Reference: [Key Vault private link](https://learn.microsoft.com/en-us/azure/key-vault/general/private-link-service).

### 8.6 Living alongside the Azure Policy add-on

**This matters given the Gatekeeper-to-Kyverno migration.**

The Azure Policy add-on for AKS extends Gatekeeper, an admission controller webhook for OPA, and applies enforcement across clusters centrally ([Governance options](https://learn.microsoft.com/en-us/azure/architecture/aws-professional/eks-to-aks/governance)). Microsoft's own AKS triage guidance lists both the Azure Policy add-on and Kyverno as admission controllers that can affect cluster operation ([AKS triage](https://learn.microsoft.com/en-us/azure/architecture/operator-guides/aks/aks-triage-controllers)).

**[Our assessment] — three questions to settle before enforcement:**

1. **Is the Azure Policy add-on enabled on our clusters?** If so, Gatekeeper is running alongside Kyverno regardless of the migration, and both sit in the admission path. Two webhooks means two timeout budgets and two failure modes on every pod create.

   ```bash
   az aks show -g <rg> -n <cluster> --query "addonProfiles.azurepolicy.enabled" -otsv
   ```

2. **Does Azure Policy already implement an overlapping image control?** Azure Policy ships built-in constraints for allowed container registries. If one is assigned at subscription or management-group scope, we may already have a partial — and possibly conflicting — control. List the assignments before adding a Kyverno equivalent, or we end up with two engines rejecting for different reasons with different error messages.

   ```bash
   az policy assignment list --scope /subscriptions/<sub> -o table
   ```

3. **Do we need mutual exclusions?** A third-party write-up documents excluding Azure Policy's service accounts from Kyverno evaluation via the Kyverno ConfigMap (`excludeUsernames`, `excludeGroups`, `resourceFilters` for `gatekeeper-system`) to avoid conflicts ([Azure Policy vs Kyverno](https://blog.joshdow.ca/aks-02/) — third-party, verify against upstream docs before applying). At minimum, `gatekeeper-system` must be excluded from our image verification policy, because we cannot sign Microsoft-managed add-on images.

**[Our assessment] — the alternative we considered.** Microsoft's own supply-chain verification engine for AKS is **Ratify**, which integrates with Gatekeeper and pulls artifacts from a registry using workload federated identity ([Ratify on Azure](https://ratify.dev/docs/quickstarts/ratify-on-azure/)). It supports both Notation and Cosign signatures. We are not recommending it — it would mean reintroducing Gatekeeper right after migrating away from it, and it is more tightly coupled to ACR than to Artifactory — but a reviewer will reasonably ask why not, and this is the answer.

### 8.7 AKS lifecycle events and admission storms

**[Our assessment]** — analysis, not sourced. This is the AKS-specific operational risk that generic Kyverno guidance does not cover, and in our view the most serious one.

Image verification runs on **every pod admission**. On AKS, several platform-driven events create large bursts of pod admissions with an empty verification cache:

| Event | Effect |
|---|---|
| Node image auto-upgrade ([docs](https://learn.microsoft.com/en-us/azure/aks/auto-upgrade-node-os-image)) | Node pool cordon, drain, replace; every pod on the node is re-admitted |
| Kubernetes version auto-upgrade ([docs](https://learn.microsoft.com/en-us/azure/aks/auto-upgrade-cluster)) | The same, cluster-wide, possibly overnight |
| Cluster autoscaler scale-up | New nodes, new pods, cold cache |
| Spot node eviction | Sudden mass rescheduling |
| Maintenance window patching | Rolling node replacement |

Linux node images are updated weekly and Windows monthly ([auto-upgrade node OS image](https://learn.microsoft.com/en-us/azure/aks/auto-upgrade-node-os-image)), so this is routine, not exceptional.

Each of these can happen **unattended and out of hours**. Combine that with fail-closed enforcement, and an Artifactory outage or a firewall rule change that coincides with an auto-upgrade window produces a cluster that cannot reschedule its own workloads, with nobody watching.

**Recommended controls:**

1. Test enforcement against a **full node pool drain**, not a single pod create, before production.
2. Align maintenance windows with periods when Artifactory availability is monitored.
3. Consider whether `failurePolicy: Ignore` is the right posture during the auto-upgrade window specifically. A deliberate, documented, time-boxed relaxation beats an unplanned outage.
4. Size `imageVerifyCacheMaxSize` against the total distinct image count across the cluster, not average pod churn ([9.2](#92-verification-cache)).
5. Make sure `kube-system` and other AKS-managed namespaces are excluded. Microsoft-managed add-on images (CNI, CSI, metrics-server, Azure Policy, monitoring agents) cannot be signed by us and must never be in scope.

### 8.8 Registry credentials and RBAC

The `--imagePullSecrets` flag sets the secret names used for registry credentials ([issue #11067](https://github.com/kyverno/kyverno/issues/11067), [Kyverno configuration](https://kyverno.io/docs/installation/customization/)); multiple secrets are comma-separated ([Kyverno verify images](https://release-1-9-0.kyverno.io/docs/writing-policies/verify-images/)). Per-policy overrides use `imageRegistryCredentials` ([verifyImages rules](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/)).

In 1.18, `imageRegistryCredentials.secrets` accepts `namespace/name` notation, and Kyverno also reads the pod's own `spec.imagePullSecrets`.

**[Our assessment]** Reading secrets in target namespaces needs a `Role` plus `RoleBinding` granting `get`, `list` and `watch` on secrets to both `kyverno-admission-controller` and `kyverno-background-controller`. This is a per-namespace binding, consistent with the ClusterRole plus namespace-scoped RoleBinding pattern we already documented for PolicyExceptions. Verify the exact 1.18 behaviour during the proof of concept rather than inheriting it from older documentation.

### 8.9 Chart and pipeline integration

**[Our assessment]** — entirely internal, no external source.

Image verification policies will **not** arrive in usable form from the upstream [Kyverno policy catalogue](https://kyverno.io/policies/) — the published examples carry placeholder keys. The upstream sync flow therefore needs a companion path:

- A local `policies/overlay/` directory in the platform controls repository
- The staging script merges the overlay alongside the vendored upstream set, with the overlay winning on conflict
- Clear separation, so an upstream sync can never silently overwrite an org-specific attestor

**Templating.** The vault name, key name and any certificate chain should come through Terraform variables in the same way the registry hostname does, because they differ per environment. Never hardcode key material or vault references into the chart.

**Testing.** Add `kyverno test` fixtures to the chart pipeline covering policy syntax, match/exclude logic and namespace scoping — but see [6.11](#611-the-kyverno-cli-cannot-test-this) for what this cannot cover.

---

## 9. Operational considerations

### 9.1 GitOps drift

`mutateDigest` converts tags to digests on matching images, and it is on by default ([Kyverno verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)). So `image: app:v1.2.3` becomes `image: app@sha256:…`.

**[Our assessment]** ArgoCD or Flux will read that rewrite as drift from the desired state. Depending on the sync policy it will either flag it permanently or fight the mutation on every reconcile. **Decide up front.** Either:

- Pin digests in Git (preferred — verification then *confirms* rather than *changes* the image), or
- Configure the GitOps tool to ignore the image field

This surprises teams badly in the first week of enforcement. Resolve it before Phase 4.

### 9.2 Verification cache

The cache is configured with `imageVerifyCacheEnabled` (default `true`), `imageVerifyCacheMaxSize` (maximum number of keys, where a key combines policy elements with the image reference; default `1000`, and `0` means use the default) and `imageVerifyCacheTTLDuration` (default `60m`) ([verifyImages rules](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/), [verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)).

**[Our assessment] — two implications:**

1. Size the cache against the real number of distinct images in the cluster, or it will thrash and put admission latency straight back. [8.7](#87-aks-lifecycle-events-and-admission-storms) explains why AKS makes this sharper.
2. **A signature revoked in the registry stays "verified" until the TTL expires.** If revocation speed matters to the threat model, shorten the TTL and accept the extra registry load.

### 9.3 Admission latency and capacity

**[Our assessment]** This is the most expensive rule type Kyverno runs — several network round trips on the admission hot path, now going through the Azure Firewall as well. It will change the numbers in the existing CPU and memory limits work, and it adds firewall SNAT pressure ([8.1](#81-the-three-layer-egress-problem)).

**Test against a mass-restart scenario** — a node pool upgrade or a cluster-wide rollout — not a single pod create. That is when a cold cache and webhook timeouts happen at the same time.

### 9.4 Disaster recovery and break-glass

**[Our assessment]** Fail-closed image verification means that if Artifactory or the egress path is unavailable during a region failover, **nothing starts.** On AKS there are three compounding factors:

- A regional failover means a **cold cluster**. Every pod admission is a cache miss.
- If the DR region's Azure Firewall does not carry the Artifactory application rule, verification fails cluster-wide at exactly the worst moment.
- If Artifactory itself is single-region, the dependency is circular.

**Actions:**

1. Replicate the firewall rule set to the DR region's policy in the same Terraform.
2. Confirm Artifactory availability in the failover region.
3. Write, pre-authorise and **rehearse** a break-glass step — flip `failurePolicy` to `Ignore`, or disable the policy — **before** enforcement, not after.

### 9.5 Key rotation

**[Our assessment]** Build the runbook before it is needed. The pattern is dual-signing, and it depends on the `count` behaviour confirmed in [5.4](#54-attestor-groups-and-the-count-field):

1. Add the new key as a **second entry in the same group** with `count: 1`, so either signature passes
2. Re-sign or rebuild everything
3. Remove the old entry

Without `count: 1` this becomes an AND and everything blocks.

On Azure specifically, also establish whether rotating a Key Vault key *version* invalidates existing signatures, and whether the policy should use a versioned or unversioned key URI ([8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id)).

Under the recommended design the rotation change set is slightly different, and simpler to reason about: create the new key in Vault Transit, add its exported public key as a second attestor entry with `count: 1`, re-sign, then remove the old entry. Because the public key travels with the policy through GitOps ([8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy)), the rotation is visible in a pull request rather than happening invisibly inside a vault — which is an argument for this design in its own right.

### 9.6 Exceptions

PolicyExceptions allow fine-grained, declarative exemptions without changing the core policy. When a resource matches a PolicyException, **none** of the image validation rules run for that resource ([Kyverno policy exceptions](https://kyverno.io/docs/guides/exceptions/)). PolicyExceptions can themselves be governed by Kyverno validate policies acting as guardrails, and upstream considers it best practice to allow only very narrow exceptions to a much broader rule, enforced both in-cluster and in CI/CD ([same page](https://kyverno.io/docs/guides/exceptions/)).

**[Our assessment]** Note the point above: an exception turns image validation **completely off** for that resource. It is a hole in a supply-chain control, not a relaxed resource limit. Therefore:

- Keep these on the **central approval** path, not the self-service path used for other policies
- Add a guardrail validate policy, per the upstream best practice
- Make them time-bound with a mandatory expiry review
- Report open exceptions on the monitoring dashboard

A permanent exception here quietly defeats the whole programme.

---

## 10. Rollout approach

### 10.1 The estate problem

**[Our assessment]** — our analysis throughout this section.

Enforcement cannot be turned on until everything running is signed. Our clusters run at least five categories of image:

| Category | Can we sign it? |
|---|---|
| Applications built by our pipelines | Yes — straightforward |
| Base images and language runtimes | Only if we rebuild them |
| **AKS platform-managed images** (CNI, CSI, metrics-server, monitoring agent, Azure Policy add-on) | **No — these must be excluded, not signed** |
| Third-party platform components (monitoring agents, Falco, ingress, cert-manager) | Some are signed upstream; most are not |
| Operator-managed and short-lived images (Helm hook jobs, migration containers, ArgoCD PreSync jobs) | Often invisible until they fail |

The AKS-managed row is the important addition compared with a generic estate. Those images come from Microsoft Artifact Registry, are updated by the platform on its own schedule, and are entirely outside our control. They must be excluded by namespace, not brought into scope.

**The standard resolution for everything else is to make Artifactory the single way in, and re-sign at the boundary:**

```
  Internal builds          Public / vendor images
  (signed at build)        (mirrored, then re-signed on ingest)
         |                            |
         +------------+---------------+
                      |
                      v
                 Artifactory
        (every in-scope image carries a signature)
                      |
                      v
          Kyverno at admission (AKS)
                      |
                      v
                Workload runs

  AKS platform images (MAR) ----> excluded by namespace
```

The re-signing job pulls an approved third-party image into an Artifactory local repository, optionally scans it, and signs it with the platform key. This does not prove provenance — we did not build it — but it proves **our ingress process approved it**, which is what the policy actually checks. It also collapses the problem to one policy, one attestor, one registry prefix.

If Artifactory remote repositories currently proxy Docker Hub, GHCR and Quay transparently, **this is the largest single piece of work in the programme**, because it changes how teams *consume* images, not merely how they deploy them.

#### 10.1.1 Artifactory repository topology — the risk that sizes the programme

**[Our assessment] — this is the highest-impact risk in the document, and it is independent of every other design choice we make.**

"All our images come from Artifactory" sounds like the problem is already solved. It is not, because *coming from* Artifactory and *being signable in* Artifactory are different things. Artifactory has three repository types, and only one of them can hold a signature.

| Type | What it is | Can an image here be signed in place? |
| --- | --- | --- |
| **Local** | Stores artifacts our own pipelines push ([JFrog local repositories](https://docs.jfrog.com/artifactory/docs/local-repositories)) | **Yes.** This is the straightforward majority — everything we build. |
| **Remote** | A read-through cache of an upstream registry such as Docker Hub, GHCR or Quay ([JFrog remote repositories](https://docs.jfrog.com/artifactory/docs/remote-repositories)) | **No.** Signing means *pushing* a `.sig` object, and a remote repository is a cache, not a push target. |
| **Virtual** | A single endpoint aggregating local and remote repositories. Pulls resolve local first, then remote caches, then the remote itself; pushes go to the configured **default deployment repository**, which is a local repo ([JFrog virtual repositories](https://docs.jfrog.com/artifactory/docs/virtual-repositories), [virtual Docker repositories](https://jfrog.com/help/r/jfrog-artifactory-documentation/virtual-docker-repositories)) | **Indirectly** — the signature lands in the underlying local repo. |

**Consequence 1 — third-party images cannot be signed where they currently sit.** Every image reaching a cluster through a remote repository must first be **copied into a local repository, signed there, and then consumed from that path**. That is the re-sign-on-ingest flow above, and this table is why it is unavoidable rather than merely advisable. Note what it changes: teams stop pulling `artifactory.example.com/docker-virtual/nginx:1.27` and start pulling an approved, signed local copy. That is a change to every consuming team's manifests, Helm values and base image references — which is the real reason this is the largest piece of work, and why it belongs in Phase 1 rather than Phase 4.

**Consequence 2 — the signature discovery path must be proven across repository types.** The signature is stored as a tag in the same repository path as the image ([4.2](#42-where-the-signature-is-stored)). If the pipeline signs `…/docker-local/myapp:v1`, the signature object is created at `…/docker-local/myapp:sha256-….sig`. If workloads pull `…/docker-virtual/myapp:v1`, then Kyverno looks for `…/docker-virtual/myapp:sha256-….sig`. The digest is identical through either path, so the *signature itself* is valid either way — the open question is purely whether the `.sig` **tag is discoverable through the virtual path**. Virtual resolution order suggests it should be, since local repositories are searched first, but this must be demonstrated.

**The test — run this before any other proof-of-concept work.** It costs one image and answers the question permanently:

```bash
# Sign via the local repository path
cosign sign --key cosign.key --tlog-upload=false \
  artifactory.example.com/docker-local/demo:v1
cosign triangulate artifactory.example.com/docker-local/demo:v1

# Now verify through the VIRTUAL path that workloads actually pull from
cosign verify --key cosign.pub --insecure-ignore-tlog \
  artifactory.example.com/docker-virtual/demo:v1
cosign triangulate artifactory.example.com/docker-virtual/demo:v1
```

| Outcome | What it means for the policy |
| --- | --- |
| Both virtual-path commands succeed | Put the virtual path in `imageReferences`. Teams change nothing about how they pull. Best case. |
| Virtual-path verification fails | The policy must match the **local** repository path, and in-scope workloads must be migrated to pull from it. Materially more work, and it needs to be known in Phase 0, not discovered in Phase 3. |

**Consequence 3 — the `imageReferences` glob must match the path in the pod spec, not the path used at signing time.** `imageReferences` accepts static strings only ([5.3](#53-what-the-rule-fields-mean)), so a mismatch between the signing path and the pull path is a silent failure: images that do not match the glob are **not verified and not blocked**. They simply pass. This is the most likely way to build a policy that appears to work while enforcing nothing — and it is a strong argument for pairing this with the registry allow-list policy in [Appendix B](#companion-policy--registry-allow-list), which fails closed on anything unexpected.

**Related risks already documented, which this topology makes sharper:**

- Promoting an image between a dev and a prod local repository leaves the signature behind unless the artifact graph is promoted ([11.3](#113-image-promotion--signatures-do-not-travel-with-the-image)).
- Cleanup rules on a local repository can delete the `.sig` object while the image survives ([11.4](#114-registry-retention-and-garbage-collection-can-delete-signatures)).
- Kyverno needs **read access to the repository**, not just pull rights, on whichever path the policy matches ([4.2](#42-where-the-signature-is-stored), [8.8](#88-registry-credentials-and-rbac)).
- AKS-managed images do not come from Artifactory at all — they come from Microsoft Artifact Registry and must be excluded ([8.7](#87-aks-lifecycle-events-and-admission-storms)).

**Action:** establish the local / remote / virtual breakdown of the repositories currently in use, and the proportion of running images sourced from each, as part of the Phase 3 inventory in [10.3](#103-suggested-first-action). That single number — what fraction of running images sit behind a remote repository — is the best available estimate of how large this programme actually is. See open decision 5 in [section 12](#12-open-decisions).

### 10.2 Phases

**[Our assessment]** — proposed, for review.

| Phase | Content | Exit criteria |
|---|---|---|
| **0** | Decide the trust model ([4.4.4](#444-comparison-and-recommendation)) and policy type ([5.6](#56-policy-types-in-kyverno-118)). Establish whether corporate PKI can issue to pipeline identities. List Azure Policy assignments ([8.6](#86-living-alongside-the-azure-policy-add-on)) | Documented decisions |
| **1** | Signing capability only — no policy. Provision the Key Vault key and Entra Workload ID. Add a signing stage to the golden pipeline. Add a re-sign-on-ingest job. Add a `cosign verify` smoke test to CI | Percentage of pushed images signed, trending up |
| **2** | Azure plumbing: firewall application rule, NetworkPolicy, CA trust, workload identity. Then Test Stage 1 ([section 6](#6-test-stage-1--a-throwaway-aks-cluster-and-acr)) and Test Stage 2 ([section 7](#7-test-stage-2--realistic-aks-with-artifactory-and-key-vault)) | All five pre-flight checks pass; `count` behaviour confirmed |
| **3** | Audit everywhere. `failureAction: Audit`, `required: false`, matching `artifactory.example.com/*`. Dashboard violations by namespace, repository and owning team | One full release cycle observed, including a node image auto-upgrade, a DR test and a Kubernetes version upgrade |
| **4** | Enforce by ring. Split into separate policies per image class. Dev → non-prod → prod, with soak time between. Break-glass rehearsed | Violation worklist cleared; drain test passed |
| **5** | Attestations — SLSA provenance, SBOM, scan results | The control asserts a build standard, not just a signature |

**Phase 3 produces a worklist, not a control.** Its purpose is to enumerate what cannot currently be signed. Expect a long tail of operator-installed and Helm-hook images nobody knew were in the tree.

### 10.3 Suggested first action

**[Our assessment]** Before committing to the programme, run Phase 3 in miniature: an `Audit`-only policy with `imageReferences: ["*"]` and a placeholder attestor, on one non-production cluster, for a fortnight. The only goal is to enumerate what is actually running — including everything AKS itself schedules.

That inventory tells us how large this programme really is, and costs almost nothing to produce.

### 10.4 Critical path

**[Our assessment]** The critical path is **not** the Kyverno configuration. It is:

1. The Azure Firewall rule and the network policy engine question in Phase 2
2. The registry ingress change in Phase 1
3. The violation cleanup between Phases 3 and 4

All three depend on other teams, and all three should be started early.



---

## 11. Topics this document did not previously cover

Everything above is about getting verification to *work*. This section covers the things that make a working proof of concept fail later: version drift between the signer and the verifier, the lifecycle of the signature object after it is created, and the ways the control can be bypassed or silently stop applying.

### 11.1 Cosign version compatibility and the v3 bundle format

**This is the newest and least obvious risk in the whole document.**

Cosign v3 turned on by default what were opt-in experiments in the 2.x line: the standardised Sigstore **bundle format** (`--new-bundle-format`), a single `--trusted-root` / `--signing-config` file for verification material, and storing container signatures as an **OCI 1.1 referring artifact** rather than a `.sig` tag ([cosign v3.0.1 release notes](https://github.com/sigstore/cosign/releases/tag/v3.0.1)). In the v3.1.x line both formats are supported, signing defaults to the bundle format, `--new-bundle-format=false` restores the legacy format, and verification auto-detects which it is looking at. The published deprecation plan removes the old format entirely in v4 ([cosign issue #4696](https://github.com/sigstore/cosign/issues/4696)).

**Why this bites Kyverno specifically.** Kyverno does not shell out to the `cosign` binary. It links the Sigstore Go libraries into the admission controller, so the *verifier* version is whatever the Kyverno release vendored — not whatever `cosign` version our pipeline runs. The two versions drift independently: a pipeline upgrade of `cosign` can break verification without anyone touching Kyverno, and a Kyverno upgrade can break verification without anyone touching the pipeline.

The failure mode is deliberately confusing. `cosign verify` on an engineer's laptop succeeds, because their local binary understands the format it just wrote. Kyverno reports **"signature not found"** — the same message an unsigned image produces. There is precedent for exactly this class of mismatch upstream ([Kyverno issue #11518 — verifyImages fails for an image signed with cosign v2](https://github.com/kyverno/kyverno/issues/11518)).

**[Our assessment] — actions:**

1. **Pin an explicit `cosign` version** in the golden pipeline. Do not use `latest` in a signing stage. A silent minor-version bump in a shared build image is enough to break admission across the estate.
2. **Record the tested pair** — "Kyverno 1.18.2 verified images signed by cosign 2.x.y" — in the platform controls repository next to the policy.
3. Treat a Kyverno upgrade and a `cosign` upgrade as changes that each require the [section 6](#6-test-stage-1--a-throwaway-aks-cluster-and-acr) experiments to be re-run. Add the version pair to the evidence table in [7.6](#76-evidence-to-capture).
4. During the proof of concept, deliberately sign one image with `--new-bundle-format=true` and one with `=false`, and confirm which our Kyverno version accepts. That single experiment answers the question permanently for this version.

**A concrete instance of this risk, already reported upstream.** On Kyverno 1.18.0, verifying a **new-bundle-format** image with an `ImageValidatingPolicy` key or certificate attestor **while tlog verification is enabled** crashes the admission controller with a nil-pointer dereference ([Kyverno issue #16435](https://github.com/kyverno/kyverno/issues/16435), fixed in the 1.19.0 milestone). Cosign v3 produces that bundle format by default. So "pipeline upgraded cosign" and "policy left tlog enabled" combine into a crash loop on the admission path — a good illustration of why the version pair is a tested configuration rather than an assumption. Full detail in [5.6](#56-policy-types-in-kyverno-118).

### 11.2 Multi-architecture images and image indexes

A multi-arch tag does not point at an image. It points at an **image index** (a manifest list) whose entries are the per-platform manifests, each with its own digest ([OCI image index](https://github.com/opencontainers/image-spec/blob/main/image-index.md)).

By default `cosign sign` signs the digest the reference resolves to — the **index** digest. It does not sign the children. The `-r` / `--recursive` flag signs each discrete platform manifest as well as the index ([cosign sign reference](https://github.com/sigstore/cosign/blob/main/doc/cosign_sign.md)). Community guidance is explicit that recursive signing is what you want for a multi-platform index, because if only some child manifests are signed, verification can fail depending on what the verifier resolves ([signing multi-architecture containers](https://some-natalie.dev/blog/sigstore-multiarch/)). The same class of problem has been reported in other tools that verify the child rather than the index ([podman issue #21209](https://github.com/containers/podman/issues/21209)).

**Why this matters on AKS.** Two situations turn this from theory into an outage:

- **Arm node pools.** If any cluster runs Ampere Arm64 nodes alongside x86, workloads deploy the same multi-arch tag and land on different architectures. Whether verification passes may then depend on which node the pod is scheduled to.
- **`mutateDigest`.** Kyverno resolves the tag and rewrites the pod's image to a digest ([5.1](#51-the-admission-flow)). If it pins the *index* digest, the kubelet still selects the right child at pull time and behaviour is consistent. If anything in the chain pins a *child* digest instead, only that platform works.

**[Our assessment] — actions:** sign recursively (`cosign sign -r`) as standard in the golden pipeline, and add a multi-arch image to the Stage 1 experiment matrix in [6.6](#66-the-four-experiments). Verify a pod on each architecture we actually run. This costs one extra experiment and removes an entire class of intermittent, node-dependent failure.

### 11.3 Image promotion — signatures do not travel with the image

This is the most common operational surprise after go-live, and it is a direct consequence of [4.2](#42-where-the-signature-is-stored): the signature is a **separate object** with only a weak reference back to the image.

A plain `docker pull` followed by `docker push` to another repository moves the image and leaves the signature behind. The same is true of Artifactory's own promotion path: after a Promote Docker Image REST API call you must issue a separate Copy Item request for the signature object ([Cosign signatures lifecycle in Artifactory](https://blog.fajfer.org/en/blog/cosign-artifactory/) — third-party, verify against our Artifactory version). The correct mental model is that you promote the **artifact graph**, not the image manifest alone.

Cosign provides `cosign copy` for this, which moves the image together with its attached signatures and attestations ([cosign README](https://github.com/sigstore/cosign)).

**Where this will hit us:**

| Scenario | Consequence if the signature is left behind |
| --- | --- |
| Dev → staging → prod repository promotion in Artifactory | The image runs in dev and is rejected in prod, with "signature not found" |
| Artifactory replication to a DR site | Verification fails cluster-wide in the failover region — compounding [9.4](#94-disaster-recovery-and-break-glass) |
| Re-tagging on release (`v1.2.3` → `stable`) | Harmless for signature lookup, because lookup is by digest, not by tag — worth knowing so nobody "fixes" a non-problem |

That third row is worth internalising: because discovery is keyed on the **digest**, re-tagging the same image never breaks verification. Only moving bytes between repositories does.

**[Our assessment]** Add signature promotion to the release pipeline in Phase 1, not Phase 4, and make one of the Stage 2 experiments a promotion between two Artifactory repositories. If our release process uses Artifactory promotion APIs rather than `cosign copy`, confirm the signature object is included — this is a checkpoint, not an assumption.

### 11.4 Registry retention and garbage collection can delete signatures

A signature object is, from the registry's point of view, an odd little manifest that is not referenced by any normal tag graph. Retention and cleanup automation is prone to treating it as garbage.

JFrog cleanup policies delete artifacts on time-based conditions ([JFrog cleanup policies](https://docs.jfrog.com/administration/docs/cleanup-policies)). Registries elsewhere in the ecosystem show the same hazard from both directions: Azure's own guidance warns against untagged-manifest retention policies when anything pulls by digest ([ACR retention policy](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-retention-policy)), `acr purge --untagged` has been reported deleting manifests belonging to signed and multi-arch images ([acr-cli issue #131](https://github.com/Azure/acr-cli/issues/131)), and Harbor has a reported defect deleting untagged images with a Cosign signature attached ([Harbor issue #18014](https://github.com/goharbor/harbor/issues/18014)).

**Why this is more dangerous than it looks.** Deleting a signature does not break the running workload — the image is already pulled and the pod is running. It breaks the *next* admission. So the damage is invisible until a node upgrade, an autoscaler event or a restart re-admits the pod, and then it fails for a reason that appears to have nothing to do with a cleanup job that ran weeks earlier. Combine this with [8.7](#87-aks-lifecycle-events-and-admission-storms) and a weekly node image upgrade is the trigger.

**[Our assessment] — actions:** audit existing Artifactory cleanup and retention rules for the repositories that will be in scope, before Phase 3. Exclude `*.sig` and `*.att` tag patterns, or confirm the cleanup implementation is referrers-aware. Add "signature still present" to whatever monitors image availability, because the registry will not tell us.

### 11.5 Storing signatures in a separate repository

[5.3](#53-what-the-rule-fields-mean) mentions `COSIGN_REPOSITORY` and the policy `repository` field in one line. It deserves more, because Kyverno itself is the reference example: Kyverno's own container images and manifests are signed with Cosign keyless, and **the signatures live in a separate repository** at `ghcr.io/kyverno/signatures` rather than beside the images ([Kyverno security guide](https://kyverno.io/docs/guides/security/)).

Reasons to consider the same split for us:

- **Permissions.** Signature objects can live in a repository with a different permission model, so the identity that pushes signatures does not need write access to the image repository.
- **Cleanup safety.** A signature repository with no retention policy is immune to [11.4](#114-registry-retention-and-garbage-collection-can-delete-signatures).
- **Registry quirks.** It sidesteps referrers-support questions in the image repository ([4.2](#42-where-the-signature-is-stored)).

The cost is that the setting must match on both sides — `COSIGN_REPOSITORY` when signing, `repository` in the policy rule — and a mismatch produces "signature not found" with everything else looking correct.

**[Our assessment]** Not for the first pass. Keep signatures beside the images so there is one fewer variable, but know this exists — it is the natural answer if the Artifactory permission model in [4.2](#42-where-the-signature-is-stored) turns out to be the blocker.

**Useful side effect:** verifying Kyverno's own images is a free, realistic keyless exercise. The published identity is the release workflow at `https://github.com/kyverno/kyverno/.github/workflows/release.yaml@refs/tags/*` with issuer `https://token.actions.githubusercontent.com` ([Kyverno security guide](https://kyverno.io/docs/guides/security/)). Running that verification by hand teaches the keyless model in [4.4.3](#443-keyless--fulcio-and-rekor) without standing anything up.

### 11.6 Why `ignoreTlog` and `ignoreSCT` appear in every policy example

Every policy in this document sets `rekor.ignoreTlog: true` and `ctlog.ignoreSCT: true` without explaining why, which makes them look like copy-paste noise. They are not.

From Kyverno 1.11 onwards, image verification **checks the transparency log and the signed certificate timestamp by default**. The release notes are explicit that anyone upgrading from 1.10 who did not use Rekor when signing must disable both checks in policy using `rekor.ignoreTlog` and `ctlog.ignoreSCT` ([Kyverno 1.11 release notes](https://kyverno.io/blog/2023/11/16/kyverno-1.11-released/)).

Our Stage 1 signing command uses `--tlog-upload=false` ([6.4](#64-build-push-and-sign)) precisely so nothing is published to the public Rekor instance. That choice **requires** both fields. Omit them and Kyverno tries to reach public Sigstore infrastructure from inside the cluster, which our egress posture ([8.1](#81-the-three-layer-egress-problem)) will block — producing a timeout, or a TUF/Rekor key-fetch error, on an image that is correctly signed.

Related fields worth knowing:

| Field | Purpose |
| --- | --- |
| `rekor.pubkey`, `ctlog.pubkey` | Verify tlog entries and SCTs against a supplied key, without TUF or a Rekor URL ([Kyverno 1.11](https://kyverno.io/blog/2023/11/16/kyverno-1.11-released/)) |
| `ctlog.tsaCertChain` | Certificate chain for a custom RFC 3161 Time Stamp Authority ([Kyverno Sigstore guide](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/sigstore/)) |
| `tsaCertChain`, `insecureIgnoreTlog`, `insecureIgnoreSCT` | The `ImageValidatingPolicy` equivalents ([ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/)) |

**[Our assessment] — the TSA question matters for [4.4.2](#442-certificate-based--our-own-pki).** With keyed signing, the key does not expire, so a timestamp is optional. With certificate-based signing from corporate PKI, the leaf certificate *does* expire — and without a trusted timestamp there is nothing to prove the signature was made while the certificate was valid. That is the same problem keyless solves with Rekor's counter-signature. So if we pursue the PKI end state, **ask the PKI team about an RFC 3161 timestamp service at the same time as asking about code-signing certificates.** Discovering later that we need a TSA is a schedule risk, and there is a known Kyverno defect in this area worth checking against our version ([Kyverno issue #15304](https://github.com/kyverno/kyverno/issues/15304)).

### 11.7 Ephemeral containers, subresources and update paths

[5.2](#52-where-kyverno-finds-the-images) notes that Kyverno reads `ephemeralContainers`. There is a subtlety underneath that: `kubectl debug` does not create a pod, it calls the **`pods/ephemeralcontainers` subresource** on an existing one, which is a different API path from a pod CREATE. Policies that only intercept pod creation historically did not apply to it ([Kubernetes issue #92557](https://github.com/kubernetes/kubernetes/issues/92557)); current engines can handle it, but the webhook must actually match that subresource.

The general principle applies more widely than debug containers. Image verification is an **admission-time** control, so it only ever sees the API calls the webhook is registered for:

- `kubectl debug` adding a debug container to a running pod
- `kubectl set image`, which is a pod-template UPDATE on the controller
- Anything creating pods through a CRD, where images must be surfaced with `imageExtractors` ([5.2](#52-where-kyverno-finds-the-images))

**[Our assessment] — action:** add one experiment to Stage 2. Run `kubectl debug` against an admitted pod using an **unsigned** image, and record what happens. If the debug container starts, we have a documented bypass — an engineer with pod-exec-level access can run arbitrary unsigned images inside a verified pod's namespace, sharing its network and possibly its volumes. That is worth knowing before we tell an auditor the control is complete, whichever way the experiment comes out.

### 11.8 Webhook ordering and mutation after verification

[5.1](#51-the-admission-flow) explains that verification runs after Kyverno's *own* mutate rules, so registry-rewrite policies apply first. What it does not cover is other webhooks in the cluster.

Kubernetes runs mutating webhooks **in series** before validating webhooks, and the order between different webhook configurations is by configuration name — effectively alphabetical — unless changed. A mutating webhook that cares about running last can set `reinvocationPolicy: IfNeeded` to be called again if another webhook modified the object afterwards, though re-invocation is not guaranteed to happen exactly once ([Kubernetes admission controllers](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/), [admission webhook good practices](https://kubernetes.io/docs/concepts/cluster-administration/admission-webhooks-good-practices/)).

**The consequence:** any *other* mutating webhook that rewrites image references and sorts after Kyverno breaks the guarantee entirely. Kyverno verifies image A, a later webhook rewrites it to image B, and the pod runs unverified bytes with a Kyverno annotation on it claiming verification passed. Candidates in a real estate include registry-mirroring or image-swap webhooks, service mesh sidecar injectors, and vendor agent injectors.

Note that `mutateDigest` is what closes the *narrow* version of this race — the gap between Kyverno resolving a tag and the kubelet pulling it, during which the tag could be moved. Pinning the digest means the kubelet pulls exactly the bytes that were verified. It does nothing about a later webhook replacing the reference wholesale.

**[Our assessment] — action:** enumerate the cluster's mutating webhooks and their names before enforcement, and confirm none of them touch image fields after Kyverno:

```bash
kubectl get mutatingwebhookconfigurations \
  -o custom-columns='NAME:.metadata.name,WEBHOOKS:.webhooks[*].name' | sort
```

If one does, the fix is ordering or scope, not policy tuning. Add the output to the Phase 0 documented decisions.

### 11.9 Enforcement never applies to workloads that are already running

Admission control is evaluated on API requests. It is not a runtime scanner. Nothing re-checks a pod that is already running, and turning on `Enforce` does not evict anything.

Three consequences that are easy to miss:

1. **Switching to Enforce looks like a no-op at first.** Nothing breaks on the day of the change. Breakage arrives later, unevenly, as pods happen to be rescheduled — which is exactly the [8.7](#87-aks-lifecycle-events-and-admission-storms) trigger list. A quiet week after enabling enforcement is not evidence of success.
2. **Proving enforcement works requires deliberate churn.** A rolling restart of in-scope namespaces is the only way to know the estate actually complies: `kubectl rollout restart deployment -n <ns>`, ring by ring, during working hours.
3. **Background reports are the inventory tool, not the enforcement tool.** Background scans populate PolicyReports ([Kyverno policy reports](https://kyverno.io/docs/guides/reports/)) but cannot block anything, and [5.5](#55-background-scans-and-auto-generation) recommends `background: false` on verification policies for load reasons. That is the right call — but it means the audit inventory in [10.3](#103-suggested-first-action) comes from admission events over time, not from a single scan of what is running. Give it a full release cycle, as Phase 3 already says.

### 11.10 The bootstrap deadlock

Fail-closed admission control has a circular dependency that only shows up in a genuine incident.

If `failurePolicy: Fail` and the Kyverno admission controller is unavailable, the API server has nowhere to send admission requests, so **pod creation fails cluster-wide — including the pods that would restore Kyverno.** Add a cold cluster after a regional failover ([9.4](#94-disaster-recovery-and-break-glass)) and the recovery path requires deleting a webhook configuration by hand before anything can start.

Two specific traps:

- **Kyverno's own namespace must be out of scope.** Kyverno's images are keyless-signed with signatures in a separate repository ([11.5](#115-storing-signatures-in-a-separate-repository)), so a keyed `azurekms://` policy cannot verify them. If the policy matches the `kyverno` namespace, Kyverno cannot restart itself.
- **Ordering in the break-glass runbook.** Removing the *policy* is not sufficient if the controller is the thing that is down; the `MutatingWebhookConfiguration` is what blocks the API server. The runbook must name the exact object to delete and who is authorised to do it.

**[Our assessment]** [9.4](#94-disaster-recovery-and-break-glass) already calls for a rehearsed break-glass step. Add two things to it: the namespace exclusion list must include `kyverno` itself, and the rehearsal must include the case where **Kyverno is the component that is broken**, not just the case where Artifactory is unreachable. Those have different recovery steps.

### 11.11 What image signing does not protect against

A reviewer will ask this, and the honest answer strengthens the proposal rather than weakening it. **[Our assessment]** — this section is our analysis.

| Claim people assume | What is actually true |
| --- | --- |
| "Signed images are safe" | A signature proves origin, not quality. A signed image with a critical CVE is still a signed image. Signing is not scanning. |
| "Only approved code can run" | A compromised build pipeline signs malicious artefacts with a perfectly valid key. Signing moves the trust boundary to the pipeline — it does not remove it. That is why [4.4.4](#444-comparison-and-recommendation) insists signing happen in a stage teams cannot edit. |
| "We know what is in the image" | Only attestations ([4.5](#45-signatures-versus-attestations)) carry that. A bare signature carries no metadata at all. |
| "The workload cannot change" | A verified image can still pull code at runtime — `pip install` in an entrypoint, a sidecar fetching config, an interpreted app cloning a repository. Admission-time verification says nothing about runtime behaviour. |
| "An insider cannot deploy anything" | Anyone who can obtain a signature — or who holds a PolicyException ([9.6](#96-exceptions)) — can. This is why exceptions belong on the central approval path. |
| "Verification is instant" | A revoked or deleted signature stays "verified" until the cache TTL expires ([9.2](#92-verification-cache)). |

**What this means for how we present the programme:** image signing answers *"did our pipeline produce these exact bytes."* It is a strong answer to a narrow question, and it composes with other controls rather than replacing them — the registry allow-list in [Appendix B](#companion-policy--registry-allow-list), vulnerability scanning, and eventually attestations in Phase 5. The registry allow-list point in Appendix B is worth repeating here: it delivers a large share of the practical benefit for none of the signing infrastructure, and it is a reasonable thing to ship first.

### 11.12 Monitoring and alerting

[7.6](#76-evidence-to-capture) asks for admission latency "before and after" but does not say what to watch in production. Kyverno exposes Prometheus metrics ([metrics reference](https://kyverno.io/docs/reference/metrics/), [monitoring guide](https://kyverno.io/docs/guides/monitoring/)); the two that matter most here are `kyverno_admission_review_duration`, the end-to-end latency of an admission review, and `kyverno_policy_results`, the count of rule results which can be aggregated to policy level.

**[Our assessment] — proposed alerts.** Confirm the exact metric names and label sets against the deployed version before building dashboards, since names and suffixes have changed across releases.

| Signal | Why it matters |
| --- | --- |
| p99 of `kyverno_admission_review_duration` for the verification policy | The leading indicator of registry or egress trouble. It degrades before anything fails. |
| Rate of verification failures, by namespace and image repository | During Phase 3 this *is* the violation worklist ([10.2](#102-phases)). During Phase 4 a spike means a team is blocked. |
| Webhook timeout / failure count | Distinguishes "signature is wrong" from "we could not reach the registry" — two incidents with completely different responses. |
| Verification cache hit ratio | Directly predicts the [8.7](#87-aks-lifecycle-events-and-admission-storms) blast radius. A low ratio means every admission is a live registry call. |
| Open PolicyExceptions | [9.6](#96-exceptions) requires this to be visible, or exceptions become permanent. |

For the reporting side, PolicyReports are Kubernetes resources ([policy reports](https://kyverno.io/docs/guides/reports/)) and the Policy Reporter UI consumes them directly, which is cheaper than building dashboards from scratch.

### 11.13 Kyverno version support window

The document notes that `ClusterPolicy` is deprecated in 1.18 and that `ImageValidatingPolicy` is the successor ([5.6](#56-policy-types-in-kyverno-118)). The support window is the missing half of that decision.

Kyverno historically followed the Kubernetes N-2 policy — current release plus the previous two minor versions — and has moved to an **N-1** model, maintaining fewer releases in order to keep pace with the project's release cadence ([Kyverno releases](https://kyverno.io/docs/installation/releases/), [Kyverno 1.18 announcement](https://www.cncf.io/blog/2026/05/05/announcing-kyverno-release-1-18/)). A compatibility matrix against Kubernetes versions is published with the installation docs ([Kyverno installation](https://kyverno.io/docs/installation/)).

**[Our assessment]** N-1 makes the [5.6](#56-policy-types-in-kyverno-118) decision more urgent, not less. A narrower support window means more frequent Kyverno upgrades, and every upgrade is a change to the verifier ([11.1](#111-cosign-version-compatibility-and-the-v3-bundle-format)) underneath a fail-closed production control. Two things follow: budget a recurring upgrade-and-retest cycle rather than treating this as build-once, and confirm the AKS Kubernetes version and the Kyverno version stay inside the supported matrix together — AKS auto-upgrade ([8.7](#87-aks-lifecycle-events-and-admission-storms)) moves the Kubernetes version on its own schedule.

### 11.14 Testing techniques not mentioned in sections 6 and 7

[6.11](#611-the-kyverno-cli-cannot-test-this) correctly rules out `kyverno test` for image verification. Three techniques fill part of that gap.

**Server-side dry run.** A dry-run request goes through the full admission chain, including webhooks, without persisting anything:

```bash
kubectl run probe --image=artifactory.example.com/sandbox/demo:v2 \
  --dry-run=server -o yaml
```

This is the cheapest way to test a policy change: it returns the real rejection message, or the mutated spec with the digest pinned, and leaves no pod behind. It is also safe to run against a production cluster, which makes it the right tool for verifying an exclusion list before enforcement.

**Deliberate failure injection.** Everything in [8.1](#81-the-three-layer-egress-problem) and [9.4](#94-disaster-recovery-and-break-glass) assumes we know how the system behaves when the registry is unreachable. Prove it rather than assume it: apply a NetworkPolicy that blocks the Kyverno controller's egress, then create a pod with a cold cache. Measure how long the request takes to fail and confirm the error clearly identifies a connectivity problem, not a signature problem. Do this before enforcement, not during an incident.

**Scale and drain testing.** Already recommended in [9.3](#93-admission-latency-and-capacity) and [8.7](#87-aks-lifecycle-events-and-admission-storms); listed here so it appears in the test plan alongside the others. A single `kubectl run` proves the mechanism works and proves nothing about capacity.

**[Our assessment]** Add all three to the Stage 2 evidence table in [7.6](#76-evidence-to-capture). The dry-run technique in particular should go into the runbook as the standard way to validate any future policy change.

### 11.15 Cost and quota

**[Our assessment]** — no external source; flagged because it is invisible until a bill or a throttle arrives.

| Item | Consideration |
| --- | --- |
| Artifactory storage | One signature object per signed image, plus one per attestation. Small individually; multiplied by every tag of every image across the estate, it is a real line item — and it interacts directly with the retention rules in [11.4](#114-registry-retention-and-garbage-collection-can-delete-signatures). |
| Artifactory request load | Every cache miss is a registry call from the admission path. Check whether Artifactory applies rate limits or returns 429 under burst, because an admission storm ([8.7](#87-aks-lifecycle-events-and-admission-storms)) will find that limit. |
| Key Vault operations | Verification fetches the public key; signing is a crypto operation per image. Both are metered. Verification load is bounded by the cache TTL ([9.2](#92-verification-cache)), which makes cache sizing a cost decision as well as a latency one. |
| Azure Firewall | Data processing charges on a new per-admission outbound flow, plus the SNAT frontend IP question already raised in [8.1](#81-the-three-layer-egress-problem). |
| Advanced Container Networking | Only if we choose FQDN-based network policy on Cilium ([8.2](#82-which-network-policy-engine-the-cluster-uses)). |

None of these is likely to change the decision. They are listed so the Phase 1 business case is not revised later.

### 11.16 Helm charts and other OCI artifacts

Cosign signs more than container images — OCI artifacts, blobs, SBOMs and Helm charts stored in an OCI registry are all signable ([Sigstore — signing other types](https://docs.sigstore.dev/cosign/signing/other_types/)).

**[Our assessment]** This is worth knowing mainly to set the boundary of what Kyverno can enforce. Kyverno's `verifyImages` inspects images in Kubernetes resources at admission. A Helm chart is consumed by the deployment tooling *before* any Kubernetes object exists, so chart signature verification belongs in CI or in the GitOps controller, not in an admission policy. If chart provenance is in scope for the wider programme, it is a separate control with a separate owner — do not let it be assumed into this workstream.

### 11.17 Troubleshooting quick reference

The failure modes worth recognising immediately, drawn from upstream issue reports and the layers described in [8.1](#81-the-three-layer-egress-problem):

| Symptom | Most likely cause |
| --- | --- |
| `signature not found`, but `cosign verify` succeeds locally | Format or version mismatch ([11.1](#111-cosign-version-compatibility-and-the-v3-bundle-format)); signature not promoted ([11.3](#113-image-promotion--signatures-do-not-travel-with-the-image)); wrong `repository` ([11.5](#115-storing-signatures-in-a-separate-repository)) |
| `UNAUTHORIZED` on the manifest | Registry credentials — the verification call needs repository read, not just pull ([8.8](#88-registry-credentials-and-rbac)). Reported upstream as [issue #15120](https://github.com/kyverno/kyverno/issues/15120) |
| x509 / certificate error | Kyverno's trust store, not the node's ([8.3](#83-ca-trust-store)) |
| Timeout with no firewall log entry | NetworkPolicy layer ([8.2](#82-which-network-policy-engine-the-cluster-uses)) |
| Timeout with a firewall deny entry | Azure Firewall application rule missing ([8.1](#81-the-three-layer-egress-problem)) |
| Rekor or TUF key-fetch error | `ignoreTlog` / `ignoreSCT` not set ([11.6](#116-why-ignoretlog-and-ignoresct-appear-in-every-policy-example)) |
| Resolves to a public IP behind a private endpoint | DNS ([8.5](#85-private-endpoints-and-dns)) |
| Passes when it should fail | Namespace exclusion, `imageReferences` glob not matching, or a PolicyException ([9.6](#96-exceptions)) |
| Intermittent, node-dependent failures | Multi-arch index signed non-recursively ([11.2](#112-multi-architecture-images-and-image-indexes)) |

First commands to run:

```bash
# Kyverno's own view of what happened
kubectl -n kyverno logs deploy/kyverno-admission-controller --tail=100
kubectl -n kyverno logs deploy/kyverno-admission-controller | grep -i "verif\|cosign"

# Policy results across the cluster
kubectl get policyreport -A
kubectl get clusterpolicyreport

# Where the signature should be, and what is actually attached
cosign triangulate <image>
cosign tree <image>

# Reproduce the admission decision without creating anything
kubectl run probe --image=<image> --dry-run=server -o yaml
```

For deeper diagnosis, raise the controller log level with `--v=4` in `extraArgs`, and confirm registry reachability from inside the pod as in [7.3](#73-pre-flight-checks--run-these-before-applying-any-policy).

---

## 12. Open decisions

**[Our assessment]** — these are the decisions that must be closed before Phase 4, with the numbering used by cross-references elsewhere in this document.

| # | Decision | Owner | Depends on |
| --- | --- | --- | --- |
| 1 | Trust model. Recommended: keyed via Vault Transit. Alternatives: keyed via Azure Key Vault, or certificate-based subject to the three preconditions | Platform + Security | [4.4.4](#444-comparison-and-recommendation) |
| 1a | Which vault holds the signing key for **signing in CI** — Vault Transit (recommended, already operated in-house) or Azure Key Vault | Platform | [4.4.1](#441-keyed--a-key-held-in-a-vault-recommended-start), [8.4.1](#841-hashicorp-vault-transit--the-signing-side) |
| 1c | **How the cluster obtains the public key for verification.** Recommended: exported public key via `k8s://`, keeping every vault off the admission path. Only if a live KMS reference is mandated, use `azurekms://` — never `hashivault://`, which needs a static expiring token in the admission controller | Platform + Security | [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy), [Kyverno #6313](https://github.com/kyverno/kyverno/issues/6313) |
| 1d | Vault auth method for the CI signing identity, and which claims the Vault role is bound to — project and protected branch/tag, not just the CI platform | Platform + Vault team | [8.4.1](#841-hashicorp-vault-transit--the-signing-side) |
| 1b | **Do we accept a single organisational signing identity?** No available model gives per-team attribution except keyless, which is ruled out on cost. This must be stated and agreed, not left implicit | Security | [4.4.4](#444-comparison-and-recommendation) |
| 2 | Only if pursuing certificate-based: can the PKI team issue a **dedicated image-signing intermediate CA**, and is an **RFC 3161 timestamp service** available? Both are hard preconditions, not nice-to-haves | PKI team | [4.4.2](#442-certificate-based--our-own-pki), [11.6](#116-why-ignoretlog-and-ignoresct-appear-in-every-policy-example) |
| 3 | Policy type. **Recommended: `ImageValidatingPolicy`** — it is GA at `policies.kyverno.io/v1` while `ClusterPolicy` is deprecated and slated for removal. Conditional on validating the 1.18.0 key-attestor defect and four field behaviours on our exact patch version | Platform | [5.6](#56-policy-types-in-kyverno-118), [Appendix C](#appendix-c--imagevalidatingpolicy-variant), [#16435](https://github.com/kyverno/kyverno/issues/16435) |
| 3a | Confirm the `ClusterPolicy` removal version and date from upstream release notes, and whether it lands inside our AKS upgrade window | Platform | [5.6](#56-policy-types-in-kyverno-118), [11.13](#1113-kyverno-version-support-window) |
| 4 | Namespace exclusion list for AKS-managed and third-party components, including `kyverno` itself | Platform | [8.7](#87-aks-lifecycle-events-and-admission-storms), [11.10](#1110-the-bootstrap-deadlock) |
| 5 | Registry ingress model: does Artifactory become the only way in, and does re-sign-on-ingest apply to all remote repositories. Includes the local / remote / virtual breakdown and the signature-discovery test | Platform + application teams | [10.1.1](#1011-artifactory-repository-topology--the-risk-that-sizes-the-programme) — the largest single piece of work, and the test that sizes it |
| 5a | Does the policy match the virtual repository path or the local repository path — decided by the test in 10.1.1, not by preference | Platform | [10.1.1](#1011-artifactory-repository-topology--the-risk-that-sizes-the-programme), [5.3](#53-what-the-rule-fields-mean) |
| 5b | Is signing centralised in a pipeline stage teams cannot edit, or is the private key distributed to teams | Platform + Security | [4.4.1](#441-keyed--a-key-held-in-a-vault-recommended-start), [4.4.4](#444-comparison-and-recommendation) |
| 6 | GitOps posture: pin digests in Git, or configure the tooling to ignore the image field | Platform + application teams | [9.1](#91-gitops-drift) |
| 7 | **Only if verifying via `azurekms://`:** versioned or unversioned key URI in the policy, and whether rotating a key version invalidates existing signatures. Must be tested, not assumed. Moot under the recommended design, since the policy holds an exported public key | Platform | [8.4.2](#842-azure-key-vault-and-microsoft-entra-workload-id), [8.4.3](#843-the-verification-side--do-not-reference-vault-from-the-policy), [9.5](#95-key-rotation) |
| 8 | Cosign version pinned in the golden pipeline, and the Kyverno version it is certified against | Platform | [11.1](#111-cosign-version-compatibility-and-the-v3-bundle-format) |
| 9 | `failurePolicy` posture in production, and whether it is deliberately relaxed during AKS auto-upgrade windows | Platform + Security | [8.7](#87-aks-lifecycle-events-and-admission-storms), [9.4](#94-disaster-recovery-and-break-glass) |
| 10 | Approval path and expiry policy for PolicyExceptions on image verification | Security | [9.6](#96-exceptions) |
| 11 | Does the registry allow-list policy ship first, as a cheaper independent control | Platform + Security | [Appendix B](#companion-policy--registry-allow-list), [11.11](#1111-what-image-signing-does-not-protect-against) |
| 12 | Is the Azure Policy add-on enabled, and does an existing assignment overlap | Platform | [8.6](#86-living-alongside-the-azure-policy-add-on) |

---



## Appendix B — Production-shaped policy

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-platform-images
  annotations:
    policies.kyverno.io/title: Verify Platform Image Signatures
    policies.kyverno.io/category: Supply Chain Security
    policies.kyverno.io/severity: high
spec:
  background: false
  webhookConfiguration:
    failurePolicy: Fail
    timeoutSeconds: 30
  rules:
    - name: verify-platform-signature
      match:
        any:
          - resources:
              kinds: [Pod]
              namespaceSelector:
                matchLabels:
                  image-verification: enforce
      exclude:
        any:
          - resources:
              namespaces:
                - kube-system
                - gatekeeper-system
                - azure-arc
                - kube-node-lease
      verifyImages:
        - imageReferences:
            - "artifactory.example.com/*"
          failureAction: Enforce
          mutateDigest: true
          verifyDigest: true
          required: true
          imageRegistryCredentials:
            secrets: ["artifactory-pull"]
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: "azurekms://${keyvault_name}.vault.azure.net/${signing_key_name}"
                    rekor:
                      ignoreTlog: true
                    ctlog:
                      ignoreSCT: true
```

**[Our assessment]** The `exclude` block is **not optional on AKS** — see [8.7](#87-aks-lifecycle-events-and-admission-storms). Adjust the namespace list to match the add-ons actually enabled on our clusters. The exclusion list must also include the `kyverno` namespace itself ([11.10](#1110-the-bootstrap-deadlock)).

**Vault Transit variant.** Per the recommendation in [4.4.4](#444-comparison-and-recommendation), the keyed attestor above becomes a one-line change if we sign with Vault Transit rather than Azure Key Vault:

```yaml
          attestors:
            - count: 1
              entries:
                - keys:
                    publicKeys: "hashivault://${signing_key_name}"
                    rekor:
                      ignoreTlog: true
                    ctlog:
                      ignoreSCT: true
```

Everything else in the policy is unchanged, which is the point made in [4.4.1](#441-keyed--a-key-held-in-a-vault-recommended-start): the choice of vault is reversible.

### Companion policy — registry allow-list

**[Our assessment]** This is arguably the higher-value control and it is far cheaper, because it needs no signing infrastructure at all. Consider shipping it first.

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: restrict-image-registries
spec:
  rules:
    - name: only-artifactory
      match:
        any:
          - resources:
              kinds: [Pod]
      exclude:
        any:
          - resources:
              namespaces: [kube-system, gatekeeper-system, azure-arc]
      validate:
        failureAction: Enforce
        message: "Images may only be pulled from Artifactory."
        pattern:
          spec:
            =(ephemeralContainers):
              - image: "artifactory.example.com/*"
            =(initContainers):
              - image: "artifactory.example.com/*"
            containers:
              - image: "artifactory.example.com/*"
```

For rules that match on Pods as well as other kinds, auto-generation is not activated — a validation policy checking that all images come from an internal trusted registry applies to all resources able to create pods ([Kyverno auto-gen rules](https://kyverno.io/docs/policy-types/cluster-policy/autogen/)).

---

## Appendix C — `ImageValidatingPolicy` variant

Per the [ImageValidatingPolicy documentation](https://kyverno.io/docs/policy-types/image-validating-policy/). Note the API version: this is `policies.kyverno.io/v1`, **stable in 1.18** — an earlier draft of this appendix showed `v1alpha1`, which is out of date. See [5.6](#56-policy-types-in-kyverno-118) for the full comparison and the recommendation.

```yaml
apiVersion: policies.kyverno.io/v1
kind: ImageValidatingPolicy
metadata:
  name: verify-platform-images-ivpol
  annotations:
    policies.kyverno.io/title: Verify Platform Image Signatures
    policies.kyverno.io/category: Supply Chain Security
spec:
  webhookConfiguration:
    timeoutSeconds: 30
  evaluation:
    background:
      enabled: false
  validationActions: [Deny]          # [Audit] for the Phase 3 rollout

  matchConstraints:
    resourceRules:
      - apiGroups: [""]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["pods"]
    # AKS-managed namespaces must be excluded — see 8.7. Confirm the exact
    # selector form against the deployed version before relying on it.
    namespaceSelector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: NotIn
          values:
            - kube-system
            - gatekeeper-system
            - azure-arc
            - kube-node-lease
            - kyverno              # see 11.10 — the bootstrap deadlock

  matchImageReferences:
    - glob: "artifactory.example.com/*"

  credentials:
    secrets: ["artifactory-pull"]

  validationConfigurations:
    mutateDigest: true
    verifyDigest: true
    required: true

  attestors:
    - name: platform
      cosign:
        key:
          # Recommended per 8.4.3 — the exported public key, so no vault sits
          # on the admission path. `data` (inline PEM) and `kms` are the
          # alternatives; confirm the secretRef field shape on our version.
          secretRef:
            name: cosign-pub
            namespace: kyverno
        # Required workaround on 1.18 as well as a design choice — see 5.6
        insecureIgnoreTlog: true
        insecureIgnoreSCT: true

  validations:
    - expression: >-
        images.containers.all(image,
          verifyImageSignatures(image, [attestors.platform]) > 0)
      message: "Image must be signed by the platform signing key."
```

**[Our assessment] — notes on this example:**

- **`insecureIgnoreTlog` / `insecureIgnoreSCT` are not optional here.** They are needed because we sign with `--tlog-upload=false` ([11.6](#116-why-ignoretlog-and-ignoresct-appear-in-every-policy-example)), *and* because leaving tlog verification enabled with a key attestor on 1.18.0 triggers a fail-closed defect including an admission-controller crash ([issue #16435](https://github.com/kyverno/kyverno/issues/16435), fixed in the 1.19.0 milestone). See the caveat in [5.6](#56-policy-types-in-kyverno-118).
- **The `validations` expression is where the AND/OR logic lives.** During key rotation ([9.5](#95-key-rotation)) add a second named attestor and change the expression to `... || verifyImageSignatures(image, [attestors.platform-next]) > 0`. This is the safety improvement over the `count` field in [5.4](#54-attestor-groups-and-the-count-field).
- **Three field shapes to confirm on 1.18.2 rather than assume:** the `namespaceSelector` form for exclusions, the `secretRef` shape under `cosign.key`, and whether `credentials.secrets` accepts the `namespace/name` notation that `imageRegistryCredentials` does ([8.8](#88-registry-credentials-and-rbac)).
- The `ClusterPolicy` equivalent in [Appendix B](#appendix-b--production-shaped-policy) remains functional in 1.18 and is the documented fallback. See open decision 3 in [section 12](#12-open-decisions).

---







