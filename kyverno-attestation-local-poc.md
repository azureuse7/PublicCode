# Proving Attestations on AKS — Runbook

**Companion to:** `kyverno-image-signature-verification-combined.md` §4.5 and §6.9
**Cluster:** AKS · Kubernetes 1.36 "Haru"
**Kyverno:** latest (1.18.x — confirm in §3)
**Registry:** Artifactory — `gautam.jfrog.com`
**Goal:** reproduce, on AKS, the same level of proof you already have for signatures — but for attestations
**Time:** ~45 minutes the first time

---

## 0. Why this needs a real cluster

Your main document establishes this in §6.11, and it is why there is no CLI shortcut:

> The Kyverno CLI `test` command supports validate, mutate and generate rule types. **Image
> verification is not in that list**, and the CLI does not embed a Kubernetes control plane.

So `kyverno test` cannot prove any of this. You need an API server, a running Kyverno, and a
reachable registry — which is exactly what you have.

---

## 1. What you are proving

You already proved, for signatures: *signed image admitted, unsigned image denied.*

For attestations there are **five** things to prove, and the middle two are what make an attestation
more than "a signature with extra steps".

| # | Test | Expected | What it proves |
|---|---|---|---|
| **A1** | Valid attestation, trusted key, `criticals: 0` | **Admitted** | The happy path works |
| **A2** | No attestation at all | **Denied** | The policy is actually doing something |
| **A3** | Attestation signed by the **wrong key** | **Denied** | `attestors` is enforced — we check *who* said it |
| **A4** | Correct key, but the content says `criticals: 3` | **Denied** | `conditions` is enforced — we check *what* was said |
| **A5** | A valid attestation from **image A**, presented for **image B** | **Denied** | The `subject.digest` binding — attestations cannot be stolen |

**A3 and A4 together are the whole point.** A3 without A4 means you trust a document you never read.
A4 without A3 means you trust a document anyone could have written. Your policy must fail both ways.

**A4 is the test that distinguishes this from the work you have already done.** A plain signature
policy would *admit* that image — same trusted key, valid signature, only the content differs.

A5 is the one that lands best in a demo.

---

## 2. ⚠️ Running this safely on a shared AKS cluster

This runbook creates **Enforce**-mode policies. On a shared cluster that is a good way to block
other teams' deployments. Three rules:

1. **Scope every policy to a single test namespace.** Every policy in this document has a
   `namespaces: [attest-test]` match. Do not remove it.
2. **Scope the image glob to a test repository.** `gautam.jfrog.com/attest-poc/*`, not `*`.
3. **Start in Audit.** Flip to Enforce only after you have seen the PolicyReport entries.

If you would rather not touch the shared cluster at all, Appendix A spins up a throwaway AKS cluster
with `az aks create` — about ten minutes and a few pounds.

```bash
# Confirm which cluster you are pointed at BEFORE you apply anything
kubectl config current-context
kubectl cluster-info
```

---

## 3. Prerequisites

```bash
az --version
kubectl version -o json | jq -r '.serverVersion.gitVersion'    # expect v1.36.x
helm version
docker --version
cosign version          # https://docs.sigstore.dev/cosign/system_config/installation/
jq --version
```

Confirm the Kyverno version you are testing against:

```bash
helm list -n kyverno
kubectl -n kyverno get deploy kyverno-admission-controller \
  -o jsonpath='{.spec.template.spec.containers[0].image}'; echo
```

**Expect 1.18.x.** Note it here: `___________`

### Artifactory access

You need push access to a Docker repository on `gautam.jfrog.com`. Create a dedicated one for this —
`attest-poc` — so cleanup is trivial and nothing collides with real images.

```bash
export REGISTRY=gautam.jfrog.com
export REPO=$REGISTRY/attest-poc

docker login $REGISTRY -u "$JFROG_USER" -p "$JFROG_TOKEN"
```

> **Check the registry hostname.** JFrog SaaS instances are usually served at `<name>.jfrog.io`,
> with `.jfrog.com` being the marketing site. If `docker login gautam.jfrog.com` fails, try
> `gautam.jfrog.io`. Whatever works, use that **exact** string everywhere — in the push, in the pod
> spec, and in the policy's `imageReferences`. A mismatch means cosign looks in a different
> repository and every verification fails.

> **Use an identity token, not your password.** Generate one in Artifactory under
> *User Profile → Generate an Identity Token*.

### A note on key handling

The private signing key must never live in the registry it signs into — anyone who can read the repo
could then forge signatures, which removes the guarantee the control exists to provide. If your
current flow stores `cosign.key` in Artifactory, that is worth raising as a separate item. For this
test, a throwaway key on disk is fine.

---

## 4. Prepare the cluster

### 4.1 Test namespace

```bash
kubectl create namespace attest-test
```

### 4.2 Pull secret for the workloads

AKS nodes need credentials to pull from Artifactory.

```bash
kubectl -n attest-test create secret docker-registry jfrog-creds \
  --docker-server=$REGISTRY \
  --docker-username="$JFROG_USER" \
  --docker-password="$JFROG_TOKEN"

# Attach to the default service account so test pods pick it up automatically
kubectl -n attest-test patch serviceaccount default \
  -p '{"imagePullSecrets":[{"name":"jfrog-creds"}]}'
```

### 4.3 Credentials for **Kyverno itself** — do not skip this

Kyverno fetches the `.sig` and `.att` objects from Artifactory **itself**, from its own pod. It does
**not** reuse the workload's `imagePullSecrets`. This is the most common cause of a PoC that fails
with confusing errors.

Create the same secret in the Kyverno namespace:

```bash
kubectl -n kyverno create secret docker-registry jfrog-creds \
  --docker-server=$REGISTRY \
  --docker-username="$JFROG_USER" \
  --docker-password="$JFROG_TOKEN"
```

The policies below reference it explicitly via `imageRegistryCredentials.secrets`, which keeps
everything self-contained and avoids changing the Kyverno Helm release on a shared cluster.

> There is also a cluster-wide option — the admission controller's `--imagePullSecrets` flag, set
> through the chart — but on a shared cluster prefer the per-policy form used here.

---

## 5. Generate two key pairs

The second pair is the "attacker" key needed for test A3.

```bash
export COSIGN_PASSWORD=""            # empty password - throwaway test keys only

cosign generate-key-pair && mv cosign.key trusted.key   && mv cosign.pub trusted.pub
cosign generate-key-pair && mv cosign.key untrusted.key && mv cosign.pub untrusted.pub

cat trusted.pub
```

Keep `trusted.pub` open — you will paste it into the policies.

---

## 6. Push three test images

Three, because A5 needs an image to steal *from* and A4 needs one with failing content.

They must have **different digests**, or cosign will treat them as the same artifact and A5 will
pass for the wrong reason. Build them properly rather than retagging the same base:

```bash
for n in a b c; do
  printf 'FROM alpine:3.20\nRUN echo %s > /marker\n' "$n" > Dockerfile.$n
  docker build -f Dockerfile.$n -t $REPO/demo-$n:v1 .
  docker push $REPO/demo-$n:v1
done
```

Confirm all three digests differ:

```bash
for n in a b c; do echo -n "demo-$n: "; crane digest $REPO/demo-$n:v1; done
```

| Image | Role |
|---|---|
| `demo-a` | Gets a good attestation → **A1** |
| `demo-b` | Gets nothing, then a wrong-key attestation → **A2**, **A3**, **A5** |
| `demo-c` | Gets a correctly-signed attestation with bad content → **A4** |

---

## 7. Baseline — reproduce the signature test you already have

Do this first. It confirms the whole chain works on AKS before attestations are added.

```bash
cosign sign --key trusted.key --tlog-upload=false $REPO/demo-a:v1
cosign verify --key trusted.pub --insecure-ignore-tlog=true $REPO/demo-a:v1
```

`--tlog-upload=false` skips the public Sigstore transparency log, which you almost certainly want
for an internal registry. **Your policy must then also be told to skip it** — see `ignoreTlog` in
§10. This is the single most common reason verification fails in a PoC while the same policy works
elsewhere.

---

## 8. Create the attestation — the new part

```bash
cat > predicate-good.json <<'EOF'
{
  "scan": "clean",
  "criticals": 0,
  "highs": 0,
  "branch": "main",
  "scannedBy": "attest-poc"
}
EOF

cosign attest \
  --key trusted.key \
  --tlog-upload=false \
  --predicate predicate-good.json \
  --type https://gautam.example/ScanResult/v1 \
  $REPO/demo-a:v1
```

Verify it independently of Kyverno:

```bash
cosign verify-attestation \
  --key trusted.pub \
  --insecure-ignore-tlog=true \
  --type https://gautam.example/ScanResult/v1 \
  $REPO/demo-a:v1
```

**If this fails, stop here.** Kyverno cannot succeed where cosign fails. Fix it before writing any
policy.

---

## 9. Look at what you created — this is what makes the concept click

```bash
cosign tree $REPO/demo-a:v1
```

```
📦 Supply Chain Security Related artifacts for an image: gautam.jfrog.com/attest-poc/demo-a:v1
└── 💾 Attestations for an image tag: …:sha256-<digest>.att
└── 🔐 Signatures for an image tag: …:sha256-<digest>.sig
```

**Three separate objects in Artifactory** — the image, its `.sig`, and its `.att` — discovered by a
naming convention derived from the image digest. Nothing is stored inside the image itself. Browse
to the `attest-poc` repo in the Artifactory UI and you will see them sitting there as tags.

Now read the payload:

```bash
cosign download attestation --predicate-type https://gautam.example/ScanResult/v1 \
  $REPO/demo-a:v1 | jq -r '.payload' | base64 -d | jq
```

```json
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "predicateType": "https://gautam.example/ScanResult/v1",
  "subject": [
    { "name": "gautam.jfrog.com/attest-poc/demo-a",
      "digest": { "sha256": "…" } }          ← THE BINDING. This is why A5 fails.
  ],
  "predicate": {
    "scan": "clean", "criticals": 0, "branch": "main"
  }
}
```

Show that `subject.digest` line in your demo. It answers *"why can't someone reuse a good
attestation on a bad image?"* — the digest is inside the signed payload, so editing it breaks the
signature.

---

## 10. The policy

Start in **Audit**, confirm the reports look right, then flip to Enforce.

```yaml
# attestation-policy.yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: attest-poc-require-clean-scan
spec:
  validationFailureAction: Audit        # ← start here. Flip to Enforce in §11.
  background: false
  webhookTimeoutSeconds: 30
  rules:
    - name: check-scan-result
      match:
        any:
          - resources:
              kinds: [Pod]
              namespaces: [attest-test]         # ← KEEP THIS. Shared cluster.
      verifyImages:
        - imageReferences:
            - "gautam.jfrog.com/attest-poc/*"   # ← scoped to the test repo only
          mutateDigest: true
          imageRegistryCredentials:
            secrets:
              - jfrog-creds                     # the secret created in §4.3, in the kyverno ns
          attestations:
            - predicateType: https://gautam.example/ScanResult/v1
              attestors:                        # 1. WHO said it
                - entries:
                    - keys:
                        publicKeys: |-
                          -----BEGIN PUBLIC KEY-----
                          <paste trusted.pub here>
                          -----END PUBLIC KEY-----
                        rekor:
                          ignoreTlog: true      # ← REQUIRED with --tlog-upload=false
              conditions:                       # 2. WHAT they said
                - all:
                    - key: "{{ criticals }}"
                      operator: Equals
                      value: 0
                    - key: "{{ branch }}"
                      operator: Equals
                      value: "main"
```

```bash
kubectl apply -f attestation-policy.yaml
kubectl get cpol attest-poc-require-clean-scan
```

### The newer syntax — worth running too

Per §5 of your main document, `ClusterPolicy` is deprecated and `ImageValidatingPolicy` is the
target. On Kyverno 1.18 it is available and the two halves of verification become explicit CEL,
which is considerably easier to explain to an audience:

```yaml
apiVersion: policies.kyverno.io/v1        # confirm served version: kubectl api-resources --api-group=policies.kyverno.io
kind: ImageValidatingPolicy
metadata:
  name: attest-poc-require-clean-scan-cel
spec:
  validationActions: [Audit]
  matchConstraints:
    resourceRules:
      - apiGroups:   [""]
        apiVersions: ["v1"]
        operations:  ["CREATE", "UPDATE"]
        resources:   ["pods"]
    namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: attest-test
  matchImageReferences:
    - glob: "gautam.jfrog.com/attest-poc/*"
  credentials:
    secrets:
      - jfrog-creds
  attestors:
    - name: platform
      cosign:
        key:
          data: |-
            -----BEGIN PUBLIC KEY-----
            <paste trusted.pub here>
            -----END PUBLIC KEY-----
        ignoreTlog: true
  attestations:
    - name: scanresult
      inToto:
        type: https://gautam.example/ScanResult/v1
  validations:
    - expression: >-
        images.containers.all(image,
          verifyAttestationSignatures(image, "scanresult", ["platform"]) &&
          extractPayload(image, "scanresult").criticals == 0 &&
          extractPayload(image, "scanresult").branch == "main")
      message: "Image must carry a scan attestation from the platform key with 0 criticals, built from main."
```

`verifyAttestationSignatures(...)` is the *who*. `extractPayload(...)` is the *what*. Two halves,
visible on two lines.

---

## 11. Run the five tests

### A1 — valid attestation → **admitted**

```bash
kubectl -n attest-test run good --image=$REPO/demo-a:v1 --restart=Never --command -- sleep 3600
kubectl -n attest-test get pod good -o jsonpath='{.spec.containers[0].image}'; echo
```

**Expected:** created, and the image reference **rewritten to a digest** (`@sha256:…`) by
`mutateDigest: true`. Point that out — it is what stops a tag being repointed at different content
after admission.

### A2 — no attestation → **denied**

```bash
kubectl -n attest-test run no-attestation --image=$REPO/demo-b:v1 --restart=Never --command -- sleep 3600
```

**Expected:** denied, with a message about missing attestations.

### A3 — wrong signing key → **denied**

```bash
cosign attest --key untrusted.key --tlog-upload=false \
  --predicate predicate-good.json \
  --type https://gautam.example/ScanResult/v1 \
  $REPO/demo-b:v1

kubectl -n attest-test run wrong-key --image=$REPO/demo-b:v1 --restart=Never --command -- sleep 3600
```

**Expected:** denied. The content is perfect — `criticals: 0`, `branch: main` — but the signer is not
trusted. **Proves `attestors` is doing work.**

### A4 — correct key, failing content → **denied**

```bash
cat > predicate-bad.json <<'EOF'
{ "scan": "dirty", "criticals": 3, "branch": "main", "scannedBy": "attest-poc" }
EOF

cosign attest --key trusted.key --tlog-upload=false \
  --predicate predicate-bad.json \
  --type https://gautam.example/ScanResult/v1 \
  $REPO/demo-c:v1

kubectl -n attest-test run bad-scan --image=$REPO/demo-c:v1 --restart=Never --command -- sleep 3600
```

**Expected:** denied. Correctly signed by the trusted key, but `criticals: 3` fails the condition.
**Proves `conditions` is doing work — and a signature-only policy would have admitted this image.**

### A5 — stolen attestation → **denied**

```bash
# Image A's genuine attestation
cosign download attestation --predicate-type https://gautam.example/ScanResult/v1 \
  $REPO/demo-a:v1 > stolen.json

# The subject digest belongs to demo-a, and it is inside the signed payload
jq -r '.payload' stolen.json | base64 -d | jq '.subject'

# There is no supported way to re-attach it to demo-b - any edit invalidates the signature.
# Demonstrate the check directly:
cosign verify-attestation --key trusted.pub --insecure-ignore-tlog=true \
  --type https://gautam.example/ScanResult/v1 $REPO/demo-b:v1
```

**Expected:** fails for `demo-b`. An attestation exists in the registry for that image and is validly
signed (from A3) — but it is either signed by the wrong key, or *about a different image*. The
binding is what prevents reuse.

### A6 — flip to Enforce and re-run

```bash
kubectl patch cpol attest-poc-require-clean-scan --type=merge \
  -p '{"spec":{"validationFailureAction":"Enforce"}}'

kubectl -n attest-test delete pod --all
# Re-run A1 (admitted) and A2 (now hard-denied)
```

Then look at the reports:

```bash
kubectl -n attest-test get polr -o wide
kubectl -n attest-test get polr -o yaml | grep -A6 "result:"
```

Watching the same test flip between Audit and Enforce is how you demonstrate the rollout mechanism
from §6.8 / Phase 3.

### Results

| # | Test | Expected | Actual | Pass |
|---|---|---|---|---|
| A1 | Valid attestation | Admitted, digest-pinned | | ☐ |
| A2 | No attestation | Denied | | ☐ |
| A3 | Wrong signing key | Denied | | ☐ |
| A4 | `criticals: 3` | Denied | | ☐ |
| A5 | Attestation for a different image | Verification fails | | ☐ |
| A6 | Audit admits with a `fail` report; Enforce blocks | Both observed | | ☐ |

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no matching signatures` / `no matching attestations` on **everything** | Rekor transparency-log check | Add `rekor.ignoreTlog: true` (or `ignoreTlog: true` in CEL) — §7 |
| `failed to fetch attestations`, 401 / 403 | Kyverno has no Artifactory credentials | §4.3 — Kyverno pulls these itself, separately from the pod |
| Pods stuck `ImagePullBackOff` | Workload pull secret missing | §4.2 |
| Works with `cosign verify-attestation` but not in Kyverno | Predicate type mismatch | `--type` at signing and `predicateType` in the policy must match **exactly**, character for character |
| Condition never matches | Wrong JMESPath root | `conditions` keys resolve **inside** `predicate` — use `{{ criticals }}`, not `{{ predicate.criticals }}` |
| Policy ignores the pod entirely | Image glob does not match | `imageReferences` must match the string **as written in the pod spec**, including the registry host |
| A5 passes when it should fail | The two images share a digest | Rebuild with different content — §6 |
| Everything admitted, no denials | Still in Audit, or namespace not matched | Check `validationFailureAction` and the `match` block |
| `docker login` fails | Wrong hostname | Try `gautam.jfrog.io` — see the note in §3 |
| Intermittent admission timeouts | Registry round-trip on every admission | Raise `webhookTimeoutSeconds`; record it for the performance section of the main doc |

Useful log line:

```bash
kubectl -n kyverno logs deploy/kyverno-admission-controller --tail=200 \
  | grep -iE "attest|verif|unauthor|denied|tlog"
```

---

## 13. Clean up

```bash
kubectl delete cpol attest-poc-require-clean-scan --ignore-not-found
kubectl delete imagevalidatingpolicy attest-poc-require-clean-scan-cel --ignore-not-found
kubectl delete namespace attest-test --ignore-not-found
kubectl -n kyverno delete secret jfrog-creds --ignore-not-found

rm -f trusted.key trusted.pub untrusted.key untrusted.pub predicate-*.json stolen.json Dockerfile.*
```

Then delete the `attest-poc` repository in Artifactory — that removes the images together with their
`.sig` and `.att` objects in one go.

> **Double-check nothing is left behind.** A forgotten Enforce-mode ClusterPolicy on a shared cluster
> is exactly the kind of thing that causes an incident three weeks later:
> ```bash
> kubectl get cpol,imagevalidatingpolicy | grep attest-poc
> ```

---

## Appendix A — Throwaway AKS cluster

If you would rather not touch the shared cluster:

```bash
export RG=rg-attest-poc
export CLUSTER=aks-attest-poc
export LOCATION=uksouth

az group create -n $RG -l $LOCATION

az aks create -g $RG -n $CLUSTER \
  --kubernetes-version 1.36 \
  --node-count 2 --node-vm-size Standard_D2s_v5 \
  --generate-ssh-keys --no-wait

az aks wait -g $RG -n $CLUSTER --created
az aks get-credentials -g $RG -n $CLUSTER --overwrite-existing
kubectl get nodes
```

Install Kyverno:

```bash
helm repo add kyverno https://kyverno.github.io/kyverno/ && helm repo update
helm install kyverno kyverno/kyverno -n kyverno --create-namespace \
  --set admissionController.replicas=1 \
  --wait --timeout 5m
```

Then follow §4 onwards. On a throwaway cluster you can relax the namespace scoping in §2, but there
is little reason to.

Teardown — this removes everything, including the node pool:

```bash
az group delete -n $RG --yes --no-wait
```

> Check `az aks get-versions -l $LOCATION -o table` first to confirm 1.36 is offered in your region;
> AKS lags upstream by a few weeks. If it is not yet available, use the newest offered version — none
> of this runbook depends on 1.36 specifically.

---

## Appendix B — Artifactory / JFrog specifics

| Item | Note |
|---|---|
| **Repository type** | Cosign pushes `.sig` and `.att` as OCI artifacts. The target must be a **Docker** or **OCI** repository with OCI support enabled. A generic repo will not work. |
| **Repository path** | JFrog usually requires the repo key in the path: `gautam.jfrog.com/<repo-key>/<image>`. If pushes 404, that is normally the cause. |
| **Authentication** | Use an identity token, not your account password. |
| **Retention / cleanup policies** | ⚠️ Artifactory cleanup rules that delete by age or by "untagged" status **can remove `.sig` and `.att` objects while the image is still running**, causing admission to start failing later with no code change. This is §11.4 of your main document — worth confirming explicitly on the `attest-poc` repo before you generalise the pattern. |
| **Promotion between repos** | Moving an image between Artifactory repositories with `docker pull` / `push` **leaves the signature and attestation behind**. Use `cosign copy` instead, which moves the image together with its attached artifacts. |
| **JFrog Evidence** | Recent Artifactory versions have their own attestation/evidence feature. It is separate from cosign attestations and Kyverno does not read it. Do not confuse the two. |
| **Storage cost** | One `.sig` plus one `.att` per signed image, per tag. Small individually; a real line item across an estate. |

---

## Appendix C — Links

- [Cosign — signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)
- [Cosign — attestations](https://docs.sigstore.dev/cosign/verifying/attestation/)
- [in-toto attestation specification](https://github.com/in-toto/attestation/blob/main/spec/README.md) — statement, predicate, envelope, bundle
- [Standard predicate types](https://github.com/in-toto/attestation/tree/main/spec/predicates)
- [SLSA provenance v1](https://slsa.dev/provenance/v1)
- [Kyverno — verifyImages overview](https://kyverno.io/docs/policy-types/cluster-policy/verify-images/overview/)
- [Kyverno — ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/)
- [Kyverno — policy reports](https://kyverno.io/docs/guides/reports/)
- [JFrog — Docker registry setup](https://jfrog.com/help/r/jfrog-artifactory-documentation/docker-registry)
- [AKS — supported Kubernetes versions](https://learn.microsoft.com/en-us/azure/aks/supported-kubernetes-versions)
