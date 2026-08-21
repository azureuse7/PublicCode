# Removing `kube-system` from Kyverno `resourceFilters` — test plan

**Ticket:** confirm whether there are drawbacks to removing `kube-system` from `resourceFilters`, and whether doing so costs functionality, stability, or introduces risk.

| | |
|---|---|
| **Date** | 2026-08-20 |
| **Install** | Kyverno via `kyverno/kyverno` Helm chart |
| **Platforms** | EKS and AKS only |
| **Policies** | Upstream set: `best-practices-vpol`, `other-vpol`, `other-mpol`, `pod-security-vpol` — CEL-based `ValidatingPolicy`/`MutatingPolicy` (`policies.kyverno.io`), **not** legacy `ClusterPolicy` |

> **Two conventions used throughout.** `T1`–`T14` are the destructive tests in §6. `O1`–`O6` are the outcome options in §10. Section references are `§n`. Everything from §4 onward must be run and recorded **twice — once on EKS, once on AKS**; they fail in different ways.

---

## Start here — the 90-minute path that may close the ticket

Do these five steps before planning anything else. They are read-only, and there is a good chance they answer the ticket outright.

**1. Confirm what is actually blocking kube-system today** → §2.3

```bash
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json | jq -r '.items[] | select(.metadata.name|test("kyverno")) | .metadata.name as $n | .webhooks[] | "\($n)\t\(.name)\tfail=\(.failurePolicy)\tnsSel=\(.namespaceSelector // {} | tostring)"'
```

If you see `kubernetes.io/metadata.name NotIn [kube-system]`, a **second, independent gate** is in force and your earlier experiment changed nothing at admission.

**2. Check whether kube-system is already being reported on** → §2.3

```bash
kubectl get policyreports -n kube-system
```

Results here mean background scanning already covers kube-system, so reporting coverage is not the gap.

**3. Check whether any policy already bypasses both gates** → §2.3

```bash
kubectl get validatingadmissionpolicies
```

Anything listed is enforced by the API server itself, outside Kyverno's filters.

**4. Run the offline simulation** → §3 — zero risk, ~30 minutes, gives you the exact list of what would break.

**5. Read the policy risk table** → §2.6.

### What you will most likely conclude

With the upstream policy set currently deployed, full enforcement on kube-system is **not viable as-is** on either platform — five policies conflict structurally with provider-managed addons that you cannot modify. The realistic outcomes are **O2 (reporting only)**, **O4 (scoped enforcement)**, or **O5 (per-policy VAP opt-in)**. §3 confirms this in about an hour.

If you only have time for one thing: **run §3.**

---

## 1. How kube-system is actually gated

### 1.1 There are four gates, and the ticket touches one

| # | Gate | Where it lives | Default | What it does |
|---|---|---|---|---|
| 1 | Webhook `namespaceSelector` | `config.webhooks` → rendered onto Kyverno's generated webhook configurations | `kubernetes.io/metadata.name NotIn ["kube-system"]` | The **API server never sends** kube-system AdmissionReviews to Kyverno |
| 2 | `resourceFilters` ← *the ticket* | `config.resourceFilters` → `kyverno` ConfigMap | `'[*/*,kube-system,*]'` | Kyverno's **engine drops** the request after it arrives |
| 3 | Background scan | reports controller `--skipResourceFilters` (default `true`) | filters **not** applied to background scans | kube-system is **already scanned and reported on today** |
| 4 | Generated VAPs | `spec.autogen.validatingAdmissionPolicy.enabled` per policy | off unless set | Runs **inside the API server** — gates 1 and 2 do not apply at all |

### 1.2 Two things `resourceFilters` does not cover

**Cluster-scoped resources are already unfiltered.** `[*/*,kube-system,*]` is a `[Kind,Namespace,Name]` match and cluster-scoped objects have no namespace. Policies matching `ClusterRole`, `ClusterRoleBinding`, `Namespace`, `Node` are **enforcing today**, ticket or no ticket. For your RBAC-heavy `other-vpol` set that is roughly half the policies. Only the namespaced half — `Role`, `RoleBinding`, `Pod`, `ServiceAccount`, `Secret`, `ConfigMap` *in* kube-system — is in scope for this change.

**Background scanning already reports on kube-system**, per gate 3. Verify before telling the ticket you have no coverage.

### 1.3 Why the current experiment proved less than it looks

* *"We commented it out and pods appear to work fine"* — expected. Gate 1 is still in force, so admission behaviour did not change. Not evidence of safety.
* *"We successfully applied policy against workloads in a namespace"* — that was a non-system namespace, never filtered in the first place. Not evidence either.
* Removing the filter alone mainly changes **background/report**, **generate**, and **mutate-existing** behaviour — not admission enforcement.

### 1.4 The two settings, and what each combination does

| resourceFilters entry | webhook selector | Result |
|---|---|---|
| removed | unchanged | No admission change. But generate / mutate-existing / cleanup can now reach kube-system. |
| kept | kube-system removed | Kyverno is now in the critical path for kube-system while the engine still short-circuits → **all the risk, none of the benefit. Never ship this.** |
| **removed** | **kube-system removed** | Actual enforcement on kube-system. This is what the ticket really means, and what §4–§6 test. |

---

## 2. Baseline capture

Run from Git Bash / WSL / Cloud Shell. Requires `jq`.

### 2.1 Helm preflight

Every command below assumes release `kyverno` in namespace `kyverno`. Helm prefixes object names with the release name, so if yours differs, `kyverno-admission-controller`, `kyverno-svc-metrics` and `cm kyverno` all shift.

```bash
helm list -A | grep -i kyverno
```

```bash
export KREL=kyverno KNS=kyverno
kubectl -n $KNS get deploy,svc,cm -o name
```

**Check how Helm is driven.** If Argo CD or Flux manages Kyverno, the procedure changes:

```bash
kubectl get application -A 2>/dev/null | grep -i kyverno; kubectl get helmrelease -A 2>/dev/null | grep -i kyverno
```

If either returns anything: the change must land in **git**, self-heal will revert direct `kubectl patch` within one sync interval (so §8.2 is not a reliable rollback), and you must record the sync interval — otherwise you will misread a reverted change as "the setting had no effect".

**One Helm trap:** capture `helm get values <rel> --all` for reference but **never feed it back into `helm upgrade -f`**. It pins every current default as an explicit override, freezing `resourceFilters` at today's list and silently blocking future chart updates. Always upgrade from `helm get values <rel>` (user values only) plus your candidate overlay.

### 2.2 Snapshot everything you might have to restore

```bash
mkdir -p ~/kyverno-baseline && cd ~/kyverno-baseline
kubectl -n kyverno get cm kyverno -o yaml            > cm-kyverno.yaml
helm -n kyverno get values kyverno                   > helm-values-user.yaml
helm -n kyverno get values kyverno --all             > helm-values-all.yaml
helm -n kyverno list -o yaml                         > helm-release.yaml
kubectl get validatingwebhookconfigurations -o yaml  > vwc-all.yaml
kubectl get mutatingwebhookconfigurations  -o yaml   > mwc-all.yaml
kubectl -n kyverno get deploy,pod -o wide            > kyverno-workloads.txt
kubectl version -o yaml                              > k8s-version.yaml
```

`kubectl get cpol` returns **nothing** on this estate — the policies are in `policies.kyverno.io`:

```bash
kubectl get validatingpolicies,mutatingpolicies,imagevalidatingpolicies,generatingpolicies,deletingpolicies -A -o yaml > policies.yaml 2>/dev/null
kubectl get validatingadmissionpolicies,validatingadmissionpolicybindings -o yaml > vap-all.yaml 2>/dev/null
```

Confirm which CRDs and short names exist — these vary by version:

```bash
kubectl api-resources --api-group=policies.kyverno.io
```

Record the chart and app version, and **pin every finding to it** — filter syntax and defaults have changed across minors:

```bash
helm -n kyverno list
kubectl -n kyverno get deploy kyverno-admission-controller -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

### 2.3 ⭐ Verify all four gates

**Gate 1 — webhook selector:**

```bash
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json | jq -r '.items[] | select(.metadata.name|test("kyverno")) | .metadata.name as $n | .webhooks[] | "\($n)\t\(.name)\tfail=\(.failurePolicy)\ttimeout=\(.timeoutSeconds)\tnsSel=\(.namespaceSelector // {} | tostring)"'
```

`NotIn [kube-system]` means gate 1 is live and the resourceFilters change was a no-op for admission.

**Gate 2 — engine filter:**

```bash
kubectl -n kyverno get cm kyverno -o jsonpath='{.data.resourceFilters}' | tr ' ' '\n' | grep -i kube-system
```

**Gate 3 — is kube-system already scanned?**

```bash
kubectl get policyreports -n kube-system -o json | jq -r '.items[].results[] | select(.result=="fail") | "\(.policy)\t\(.resources[0].kind)/\(.resources[0].name)"' | sort | uniq -c | sort -rn
```

```bash
kubectl -n kyverno get deploy kyverno-reports-controller -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep -i skipResourceFilters
```

Reports present here confirm background scanning already ignores the filter. Note it in the ticket — the reporting half of "coverage" already exists.

**Gate 4 — native VAPs.** Kyverno can compile a `ValidatingPolicy` into a native `ValidatingAdmissionPolicy`, which the API server evaluates directly. Kyverno's engine is never invoked, so `resourceFilters` cannot apply and there is no webhook call for the `namespaceSelector` to filter. Such a policy **may already be enforcing on kube-system**.

```bash
kubectl get validatingpolicies -o json | jq -r '.items[] | "\(.metadata.name)\tvap=\(.spec.autogen.validatingAdmissionPolicy.enabled // false)\tactions=\(.spec.validationActions // [] | join(","))\tfailurePolicy=\(.spec.failurePolicy // "-")"'
```

```bash
kubectl get validatingadmissionpolicybindings -o json | jq -r '.items[] | "\(.metadata.name)\tpolicy=\(.spec.policyName)\tactions=\(.spec.validationActions|join(","))\tmatch=\(.spec.matchResources // {} | tostring)"'
```

**Prove it rather than reasoning about it.** With kube-system still filtered:

```bash
kubectl -n kube-system run vap-probe --image=nginx:latest --dry-run=server -o yaml
```

`nginx:latest` should trip `disallow-latest-tag`. **Rejected while kube-system is still in `resourceFilters` = a VAP is enforcing outside both gates.** That is a significant finding — record it per policy and per platform.

> VAP-backed policies have **no availability dependency on Kyverno**, so the §6.5 deadlock does not apply to them. That is the basis of option **O5**.

### 2.4 Inventory kube-system

On both platforms the **control plane is managed and is not in kube-system** — no `kube-apiserver`/`etcd`/`kube-scheduler` pods. The provider-reconciled **addons** are the entire risk surface.

```bash
kubectl -n kube-system get deploy,ds,sts,job,cronjob,svc,cm,sa,role,rolebinding,netpol,pdb
```

```bash
kubectl -n kube-system get deploy,ds -o json | jq -r '.items[] | "\(.kind)/\(.metadata.name)\tsa=\(.spec.template.spec.serviceAccountName)\taddonmgr=\(.metadata.labels["addonmanager.kubernetes.io/mode"] // "-")"'
```

**EKS**

| Workload | Source | Patchable? | If blocked or mutated |
|---|---|---|---|
| `aws-node` (VPC CNI) | EKS managed addon | No — reconciled | **New nodes never go Ready; pods get no IP** |
| `kube-proxy` | EKS managed addon | No — reconciled | Service routing dead on the node |
| `coredns` | EKS managed addon | No — reconciled | **Cluster-wide DNS outage** |
| `aws-ebs-csi-driver` / `aws-efs-csi-driver` | EKS managed addon | No | Volumes fail to mount |
| `eks-pod-identity-agent` | EKS managed addon | No | Workload IAM auth breaks |
| `metrics-server` | addon or self-installed | Sometimes | HPA breaks |
| Karpenter / autoscaler / AWS LB controller | usually self-installed | **Yes** | Yours — safe to enforce on |

```bash
aws eks list-addons --cluster-name <CLUSTER> --output table
```

**AKS**

| Workload | Source | Patchable? | If blocked or mutated |
|---|---|---|---|
| `konnectivity-agent` | AKS managed | No | ⚠️ **Worst case on AKS — see below** |
| `coredns`, `coredns-autoscaler` | AKS addon-manager | No — reconciled | **Cluster-wide DNS outage** |
| `kube-proxy` (absent on Cilium dataplane) | AKS managed | No | Service routing dead |
| `azure-cni-networkmonitor`, `azure-ip-masq-agent` | AKS managed | No | Pod networking / SNAT broken |
| `cloud-node-manager` | AKS managed | No | Node lifecycle breaks |
| `csi-azuredisk-node`, `csi-azurefile-node` | AKS managed | No | Volumes fail to mount |
| `metrics-server` | AKS managed | No | HPA breaks |
| `ama-logs` / `omsagent` | Container Insights | No | Log/metric pipeline stops |
| `azure-policy`, `gatekeeper-*` | Azure Policy addon | No | See coexistence note |

AKS labels managed addons, giving you a precise exclusion selector: `Reconcile` = fully managed, mutations reverted; `EnsureExists` = only recreated if deleted, mutations tend to survive. **EKS has no equivalent label** — you must exclude by ServiceAccount or name.

> ⚠️ **`konnectivity-agent` is the AKS worst case.** The AKS API server sits in a Microsoft-managed control plane and reaches in-cluster webhooks — including Kyverno's — through the konnectivity tunnel. Block those pods from being created and the API server loses its route to the Kyverno webhook; with `failurePolicy: Fail` that blocks every subsequent write, including the fix. **Exclude `konnectivity-agent` explicitly regardless of which option you choose.**

> ⚠️ **Azure Policy / Gatekeeper coexistence.** If the addon is enabled, Gatekeeper is already admission-controlling this cluster. Two controllers evaluating kube-system means additive latency and ambiguous denials. Check and record — it makes AKS materially riskier than EKS:
> ```bash
> az aks show -g <RG> -n <CLUSTER> --query addonProfiles.azurepolicy
> ```

### 2.5 Blast radius from the policies

```bash
kubectl get validatingpolicies -o json | jq -r '.items[] | .metadata.name as $p | (.spec.validationActions // ["-"] | join(",")) as $act | (.spec.failurePolicy // "-") as $fp | .spec.matchConstraints.resourceRules[]? | "\($p)\tactions=\($act)\tfailurePolicy=\($fp)\tres=\(.resources|join(","))\tops=\(.operations|join(","))"'
```

```bash
kubectl get mutatingpolicies -o json | jq -r '.items[] | "\(.metadata.name)\tadmission=\(.spec.evaluation.admission.enabled // "-")\tmutateExisting=\(.spec.evaluation.mutateExisting.enabled // false)"'
```

High-risk patterns:

* `resources: ["*"]` — starts intercepting high-churn kube-system objects (`Lease`, `EndpointSlice`). Leader-election Leases update every ~2s; node Leases every 10s per node. Biggest performance risk.
* `MutatingPolicy` with `mutateExisting: true` — patches **existing** kube-system objects the moment the filter is removed, asynchronously, with no admission request to observe. Highest-risk item in an `mpol`.
* `GeneratingPolicy` matching `Namespace` or wildcards — a generated default-deny NetworkPolicy in kube-system **breaks cluster DNS**.
* `DeletingPolicy` / cleanup policies — can now delete kube-system objects.
* `validationActions: ["Deny"]` — hard blocks. `Audit` and `Warn` do not block.

Record the existing safety valves:

```bash
kubectl -n kyverno get cm kyverno -o jsonpath='{.data.excludeGroups}{"\n"}{.data.excludeUsernames}{"\n"}'
```

`excludeGroups: system:nodes` exempts kubelet-submitted objects — but **not the ones that matter**. DaemonSet pods (`aws-node`, `kube-proxy`, `csi-*-node`, `konnectivity-agent`) are created by the **daemonset controller**, not the kubelet, so they go through Kyverno.

### 2.6 Policy-by-policy risk — the deployed upstream set

Expectations to test against, not a substitute for testing. Confirm each against your §3 results.

**Scope:** **N** = namespaced (in scope for this ticket) · **C** = cluster-scoped (**already enforcing today**, per §1.2)

**best-practices-vpol**

| Policy | Scope | Expected effect on kube-system | Risk |
|---|---|---|---|
| `check-deprecated-apis` | N/C | Flags addon manifests on deprecated APIs. Informational. | Low |
| `disallow-default-namespace` | N | Targets the `default` namespace. **No effect on kube-system.** | None |
| `disallow-latest-tag` | N | Addons use pinned tags/digests. Check floating tags on `ama-logs`. | Low |
| `require-drop-all` | N | ⚠️ `aws-node` needs `NET_ADMIN`; `kube-proxy` privileged; `csi-*-node`, `azure-ip-masq-agent`, `cloud-node-manager` need capabilities. **Cannot drop ALL, cannot be patched.** | **Critical** |
| `require-ro-rootfs` | N | ⚠️ CNI, kube-proxy and CSI node plugins write to their root filesystem. **Permanent violations.** | **Critical** |
| `restrict-image-registries` | N | ⚠️ EKS addons come from `<acct>.dkr.ecr.<region>.amazonaws.com` — **account ID differs per region/partition**. AKS from `mcr.microsoft.com`. If the allowlist is your own ECR/ACR, **every addon image is blocked**. | **Critical** |

**other-vpol** — RBAC-focused, so much of it is cluster-scoped and already live

| Policy | Scope | Expected effect on kube-system | Risk |
|---|---|---|---|
| `deny-secret-service-account-token-type` | N | Legacy SA-token Secrets exist in kube-system on some clusters. | Medium |
| `disallow-secrets-from-env-vars` | N | Logging/monitoring addons commonly use `secretKeyRef`. | Medium |
| `restrict-binding-clusteradmin` | C + N | ClusterRoleBindings already enforced; the **RoleBinding** half newly in scope. | High |
| `restrict-binding-system-groups` | C + N | ⚠️ kube-system holds bootstrap RoleBindings (`system::leader-locking-kube-controller-manager`, `system::leader-locking-kube-scheduler`, `system:controller:*`) that **the API server reconciles by itself**. Denying them creates a permanent failure loop inside a managed control plane you cannot debug. | **Critical** |
| `restrict-clusterrole-nodesproxy` | C | Already enforced. No change. | None (already live) |
| `restrict-escalation-verbs-roles` | N | kube-system bootstrap Roles newly in scope. | High |
| `restrict-sa-automount-sa-token` | N | ⚠️ `coredns`, `aws-node`, `kube-proxy`, CSI drivers **all require** their SA token. | **Critical** |
| `restrict-secret-role-verbs` | N | Bootstrap Roles (`token-cleaner`, `bootstrap-signer`) hold Secret verbs by design. | High |
| `restrict-wildcard-resources` | C + N | Bootstrap and addon Roles use `resources: ["*"]`. | High |
| `restrict-wildcard-verbs` | C + N | Same, for `verbs: ["*"]`. | High |

**other-mpol**

| Policy | Scope | Expected effect on kube-system | Risk |
|---|---|---|---|
| `disable-automountserviceaccounttoken` | N | ⚠️ With `mutateExisting: true`, patches **existing** kube-system ServiceAccounts as soon as the filter is removed — `coredns`, `aws-node`, `kube-proxy` all need their tokens. Provider reconcilers then fight it. | **Critical** |

```bash
kubectl get mutatingpolicies disable-automountserviceaccounttoken -o jsonpath='{.spec.evaluation.mutateExisting.enabled}{"\n"}'
```

**pod-security-vpol**

| Policy | Scope | Expected effect on kube-system | Risk |
|---|---|---|---|
| Pod Security Standards (baseline / restricted) | N | ⚠️ `kube-proxy`, `aws-node`, `azure-cni-*`, `csi-*-node` run privileged, hostNetwork, hostPath. They violate **baseline**, let alone **restricted**. Unfixable on both platforms. | **Critical** |

### 2.7 What this implies before you run a single test

Five policies are expected to produce **permanent, unpatchable violations** on provider-managed addons: `require-drop-all`, `require-ro-rootfs`, `restrict-image-registries`, `restrict-sa-automount-sa-token`, and pod-security. A sixth, `restrict-binding-system-groups`, collides with RBAC the managed control plane reconciles on its own. A seventh, `disable-automountserviceaccounttoken`, acts asynchronously via `mutateExisting`.

**So O6 (full enforcement) is not viable with this policy set as written**, on either platform. §3 should confirm that within an hour. Realistic outcomes are **O2**, **O4** or **O5** (§10).

---

## 3. Offline simulation — zero risk, do this first

`kyverno apply` does **not** honour `resourceFilters`, which makes it an exact preview of "what happens if kube-system is unfiltered", with no cluster changes at all.

```bash
curl -sLO https://github.com/kyverno/kyverno/releases/latest/download/kyverno-cli_linux_x86_64.tar.gz
```

```bash
tar -xzf kyverno-cli_linux_x86_64.tar.gz && sudo mv kyverno /usr/local/bin/ && kyverno version
```

```bash
mkdir -p ~/kyverno-offline && cd ~/kyverno-offline
kubectl -n kube-system get deploy,daemonset,statefulset,job,cronjob,pod,svc,sa,cm,role,rolebinding,netpol,pdb -o yaml > kube-system-dump.yaml
kubectl get validatingpolicies,mutatingpolicies -o yaml > policies-under-test.yaml
```

```bash
kyverno apply policies-under-test.yaml --resource kube-system-dump.yaml --policy-report --detailed-results --table | tee offline-results.txt
```

Same thing live against the cluster, still read-only:

```bash
kyverno apply policies-under-test.yaml --cluster --namespace kube-system --policy-report --detailed-results
```

**Exit criteria.** You now have the exact list of `(policy, resource)` pairs that would fail if kube-system were unfiltered. Use it to fill in the §2.6 table for real. Given the deployed set it will almost certainly be non-empty, and every entry is a provider-managed workload you cannot patch.

> ⚠️ This covers **validate only**. It does not simulate mutate-existing loops, generate side effects, admission latency, or the "Kyverno is down" failure mode. §4–§7 exist for those.

---

## 4. Build the test clusters

**You must test on both platforms.** EKS's worst case is a CNI/node-bootstrap deadlock; AKS's is losing the konnectivity tunnel the API server needs to *reach Kyverno's webhook*. A result from one is not evidence for the other.

### Which cluster for which stage

| Stage | Cluster | Why |
|---|---|---|
| §3 offline | none | No cluster changes at all |
| §5 audit soak | **staging** (preferred) or disposable | Needs realistic workload churn over days |
| §6 enforce + destructive | **disposable only** | T11/T12 are designed to break the cluster |

If you cannot get a disposable cluster approved, run §6's destructive tests (T4–T7, T11, T12) on staging inside a declared maintenance window with §8.3 break-glass pre-staged. **Do not skip T11** — it is the test that answers the ticket.

### 4.1 EKS

```bash
eksctl create cluster --name kyverno-rf --region <REGION> --version <SAME_AS_PROD> --nodegroup-name ng1 --nodes 2 --nodes-min 1 --nodes-max 4 --managed
```

Install the **same managed addons at the same versions as production**, or the test proves nothing:

```bash
aws eks list-addons --cluster-name <PROD_CLUSTER> --output text
```

```bash
aws eks create-addon --cluster-name kyverno-rf --addon-name vpc-cni --addon-version <SAME_AS_PROD>
```

Repeat for `kube-proxy`, `coredns`, `aws-ebs-csi-driver`, `eks-pod-identity-agent` as applicable.

### 4.2 AKS

```bash
az group create -n kyverno-rf-rg -l <REGION>
```

```bash
az aks create -g kyverno-rf-rg -n kyverno-rf --kubernetes-version <SAME_AS_PROD> --node-count 2 --network-plugin <SAME_AS_PROD> --generate-ssh-keys
```

Match the production addon profile — especially the Azure Policy addon, since Gatekeeper coexistence is part of what you are testing:

```bash
az aks show -g <PROD_RG> -n <PROD_CLUSTER> --query addonProfiles
```

### 4.3 Install Kyverno identically on both

```bash
helm repo add kyverno https://kyverno.github.io/kyverno && helm repo update
```

```bash
helm install kyverno kyverno/kyverno -n kyverno --create-namespace --version <SAME_VERSION_AS_PROD> -f ~/kyverno-baseline/helm-values-user.yaml
```

```bash
kubectl apply -f ~/kyverno-baseline/policies.yaml
```

### 4.4 Parity checklist — do not trust a result until every box is ticked

- [ ] Same Kubernetes minor version as production
- [ ] Same Kyverno chart version and same `helm-values-user.yaml`
- [ ] Same managed addon set **and versions**
- [ ] Same CNI / network plugin (on AKS, Cilium dataplane changes whether `kube-proxy` exists at all)
- [ ] Same Kyverno `replicaCount` and PDBs — a 1-replica install has an outage window on every restart that a 3-replica install does not, and understates availability risk
- [ ] Azure Policy addon matches production (AKS)
- [ ] At least 2 nodes, and a node group that can actually scale (T4/T5 need it)

Tear down when finished:

```bash
eksctl delete cluster --name kyverno-rf --region <REGION>
```

```bash
az group delete -n kyverno-rf-rg --yes --no-wait
```

---

## 5. Audit soak — the phase that produces the real evidence

### 5.1 The values change (upgrade-safe form)

Do **not** hand-edit or comment out lines in the default `resourceFilters` list — you will silently lose entries future chart versions add. Use the dedicated key.

```yaml
# values-candidate.yaml
config:
  # Gate 2: removes ONLY the kube-system entry from the chart's default filter list
  resourceFiltersExcludeNamespaces:
    - kube-system

  # Gate 1: drop kube-system from the webhook namespaceSelector.
  # NOTE: `values: []` with operator NotIn is INVALID - keep at least one entry.
  webhooks:
    namespaceSelector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: NotIn
          values:
            - kyverno
```

### 5.2 Diff the render before applying

```bash
helm template kyverno kyverno/kyverno -n kyverno --version <VER> -f ~/kyverno-baseline/helm-values-user.yaml > /tmp/before.yaml
```

```bash
helm template kyverno kyverno/kyverno -n kyverno --version <VER> -f ~/kyverno-baseline/helm-values-user.yaml -f values-candidate.yaml > /tmp/after.yaml
```

```bash
diff -u /tmp/before.yaml /tmp/after.yaml | tee /tmp/values-diff.txt
```

Confirm it touches **only** the ConfigMap `resourceFilters` key and the webhook selector. Attach to the ticket.

### 5.3 Put every policy in Audit first

`ValidatingPolicy` uses `spec.validationActions` (`Deny` / `Audit` / `Warn`). **There is no `validationFailureAction` on these types** — patching it silently does nothing.

```bash
for p in $(kubectl get validatingpolicies -o name); do kubectl patch $p --type=merge -p '{"spec":{"validationActions":["Audit"],"failurePolicy":"Ignore"}}'; done
```

```bash
kubectl get validatingpolicies -o custom-columns=NAME:.metadata.name,ACTIONS:.spec.validationActions,FAILPOL:.spec.failurePolicy,VAP:.spec.autogen.validatingAdmissionPolicy.enabled
```

**Also neutralise the MutatingPolicy.** Mutation has no audit mode — it either patches or it does not, and `mutateExisting` will rewrite kube-system ServiceAccounts as soon as the filter comes off:

```bash
kubectl patch mutatingpolicy disable-automountserviceaccounttoken --type=merge -p '{"spec":{"evaluation":{"mutateExisting":{"enabled":false}}}}'
```

Re-enable it deliberately in §6 (T8), not by accident here.

> If any policy has VAP generation enabled, confirm the action change propagated — the API server enforces that copy, not Kyverno:
> ```bash
> kubectl get validatingadmissionpolicybindings -o custom-columns=NAME:.metadata.name,POLICY:.spec.policyName,ACTIONS:.spec.validationActions
> ```

### 5.4 Apply and confirm both gates actually moved

```bash
helm upgrade kyverno kyverno/kyverno -n kyverno --version <VER> -f ~/kyverno-baseline/helm-values-user.yaml -f values-candidate.yaml --wait
```

```bash
kubectl -n kyverno get cm kyverno -o jsonpath='{.data.resourceFilters}' | tr ' ' '\n' | grep -c kube-system
```

Expect `0`.

```bash
kubectl get validatingwebhookconfigurations -o json | jq -r '.items[]|select(.metadata.name|test("kyverno"))|.webhooks[]|.namespaceSelector|tostring' | sort -u
```

Expect no `kube-system`.

> The ConfigMap is re-read at runtime, but **webhook configurations are regenerated by the admission controller**. Allow 30–60s, or force it with `kubectl -n kyverno rollout restart deploy/kyverno-admission-controller`.

### 5.5 Soak

Run Audit for **at least one full change cycle** — minimum 7 days, ideally spanning one addon update and one node-group recycle. Collect daily:

```bash
kubectl get policyreports -n kube-system -o json | jq -r '.items[].results[] | select(.result=="fail") | "\(.policy)\t\(.resources[0].kind)/\(.resources[0].name)"' | sort | uniq -c | sort -rn | tee -a audit-soak-$(date +%F).txt
```

**Every line is a request that would have been blocked under `Deny`.** This is the single most important artefact for the ticket.

---

## 6. Enforce, then deliberately try to break it

Disposable clusters only. Start only once §5.5 gives a stable, understood failure list. Set `validationActions: ["Deny"]`, keep `failurePolicy: Ignore` for the first pass, then repeat everything with `failurePolicy: Fail`.

```bash
for p in $(kubectl get validatingpolicies -o name); do kubectl patch $p --type=merge -p '{"spec":{"validationActions":["Deny"]}}'; done
```

### 6.1 Test matrix

Run every test **twice — once on EKS, once on AKS** — recording PASS/FAIL plus evidence.

| # | Test | Expected | Why it matters |
|---|---|---|---|
| T1 | CoreDNS pod recreate | Pods back to `Running` < 60s | Blocked = **cluster-wide DNS outage** |
| T2 | kube-proxy rollout | Completes | Blocked = service routing dead on the node |
| T3 | CNI rollout | Completes | Blocked = new pods get no IP |
| T4 | **New node joins** | Node `Ready`, all DaemonSet pods scheduled | **The #1 real-world failure mode** |
| T5 | Node drain + replace | Replacement node fully `Ready` | Autoscaler / spot reclaim path |
| T6 | Managed addon upgrade | Addon reaches healthy state | Blocked = **cannot patch CVEs in addons** |
| T7 | Cluster minor upgrade | Control plane **and** node pool succeed | Blocked = cannot upgrade the cluster |
| T8 | Mutation fight loop | `metadata.generation` stable over 30 min | Provider reconciler vs Kyverno ping-pong |
| T9 | Generate side effects | Nothing unexpectedly generated into kube-system | A generated default-deny NetworkPolicy kills DNS |
| T10 | Cleanup / deleting policies | No kube-system objects in scope | Silent deletion of addons |
| T11 | **Kyverno outage** | `Ignore`: T1–T5 pass. `Fail`: expect failures | The deadlock — see §6.5 |
| T12 | Cold start | Cluster reaches healthy state from cold | kube-system must come up *before* Kyverno can |
| T13 | **AKS only — konnectivity** | `kubectl logs`/`exec` work; agent pods recreate | Losing the tunnel makes the webhook unreachable |
| T14 | **AKS only — Gatekeeper** | Both controllers respond within timeout | Additive latency; ambiguous denials |

### 6.2 EKS commands

```bash
kubectl -n kube-system delete pod -l k8s-app=kube-dns                                                                    # T1
kubectl -n kube-system rollout restart ds/kube-proxy && kubectl -n kube-system rollout status ds/kube-proxy --timeout=5m  # T2
kubectl -n kube-system rollout restart ds/aws-node   && kubectl -n kube-system rollout status ds/aws-node   --timeout=5m  # T3
```

```bash
aws eks update-nodegroup-config --cluster-name <C> --nodegroup-name <NG> --scaling-config minSize=1,maxSize=6,desiredSize=4
```

```bash
aws eks update-addon --cluster-name <C> --addon-name coredns --addon-version <NEWER> --resolve-conflicts PRESERVE
```

```bash
aws eks describe-addon --cluster-name <C> --addon-name coredns --query 'addon.{status:status,health:health}'
```

T6 passes when status reaches `ACTIVE`. A policy denial typically surfaces as `DEGRADED` / `UPDATE_FAILED` with an admission-denied health issue — capture the whole `health` block.

```bash
aws eks update-cluster-version --name <C> --kubernetes-version <NEXT>                    # T7 control plane
aws eks update-nodegroup-version --cluster-name <C> --nodegroup-name <NG>                # T7 nodes / T5
```

> **Why T4 is the highest-value EKS test:** DaemonSet pods on a new node are created by the **daemonset controller**, not the kubelet, so `excludeGroups: system:nodes` does **not** exempt them. They go through Kyverno.

### 6.3 AKS commands

```bash
kubectl -n kube-system delete pod -l k8s-app=kube-dns                                     # T1
kubectl -n kube-system rollout restart deploy/coredns
kubectl -n kube-system rollout restart ds/azure-cni-networkmonitor                        # T3 — adjust to your dataplane
kubectl -n kube-system rollout restart ds/csi-azuredisk-node
```

```bash
az aks nodepool scale -g <RG> --cluster-name <C> -n <POOL> --node-count 4                 # T4
```

```bash
az aks nodepool upgrade -g <RG> --cluster-name <C> -n <POOL> --kubernetes-version <NEXT>  # T5 rolling node replacement
az aks upgrade -g <RG> -n <C> --kubernetes-version <NEXT>                                 # T7 control plane
```

```bash
kubectl -n kube-system delete pod -l app=konnectivity-agent && kubectl -n kube-system get pods -l app=konnectivity-agent -w   # T13
```

```bash
kubectl logs -n kube-system deploy/coredns --tail=5                                       # T13 pass check: tunnel works
```

> **Run T13 first**, with `failurePolicy: Ignore` still set and §8.3 break-glass open in another terminal. If konnectivity cannot recreate, the API server loses its route to the Kyverno webhook and you are in the §6.5 deadlock with no in-cluster path out.

```bash
kubectl get validatingwebhookconfigurations -o json | jq -r '.items[] | "\(.metadata.name)\t\(.webhooks|length) webhooks"'   # T14
```

### 6.4 Both platforms — T8, T9, T10

```bash
kubectl -n kube-system get deploy coredns -o jsonpath='{.metadata.generation}{"\n"}'       # T8: sample every 5 min for 30 min
```

```bash
kubectl get netpol,resourcequota,limitrange,cm -n kube-system                              # T9
```

```bash
kubectl get deletingpolicies,clustercleanuppolicy,cleanuppolicy -A -o yaml 2>/dev/null | grep -B2 -A6 -i 'match'   # T10
```

### 6.5 T11 — the deadlock test, and the one that answers the ticket

With kube-system unfiltered **and** `failurePolicy: Fail`, an unavailable Kyverno means the API server rejects kube-system writes.

**On EKS** the loop closes: Kyverno down → a node scales in (autoscaler, spot reclaim, rolling nodegroup update) → the daemonset controller tries to create `aws-node` and `kube-proxy` on the new node → admission fails closed → no CNI → node stays `NotReady` → nothing schedules there → Kyverno has fewer places to run. **Self-sustaining outage.**

**On AKS** the path is shorter: Kyverno down, or `konnectivity-agent` cannot be recreated → the managed API server has no tunnel to the Kyverno webhook → every matching write fails closed, including the fix.

Run with §8.3 break-glass already open in a second terminal.

```bash
kubectl -n kyverno scale deploy kyverno-admission-controller --replicas=0
```

Cheap check first:

```bash
kubectl -n kube-system delete pod -l k8s-app=kube-dns && kubectl -n kube-system get pods -l k8s-app=kube-dns -w
```

```bash
kubectl get events -A --sort-by=.lastTimestamp | grep -i 'failed calling webhook' | tail -20
```

Now the one that matters — **scale a node in while Kyverno is still at 0 replicas**:

```bash
aws eks update-nodegroup-config --cluster-name <C> --nodegroup-name <NG> --scaling-config minSize=1,maxSize=6,desiredSize=<current+1>
```

```bash
az aks nodepool scale -g <RG> --cluster-name <C> -n <POOL> --node-count <current+1>
```

```bash
kubectl get nodes -w
```

**Pass = the new node reaches `Ready`. Fail = it hangs `NotReady` with no CNI pod.**

Recover:

```bash
kubectl -n kyverno scale deploy kyverno-admission-controller --replicas=<PROD_REPLICAS>
```

If that command is itself blocked, go to §8.3 — and record in the ticket that self-recovery was impossible, because that is precisely the risk being assessed.

**Run T11 four times: `Ignore` and `Fail`, on EKS and on AKS.** The difference between those four runs *is* the risk assessment.

### 6.6 Evidence to capture during every test

```bash
kubectl get events -A --sort-by=.lastTimestamp | grep -i -E 'denied|webhook|kyverno' | tail -50
```

```bash
kubectl -n kyverno logs deploy/kyverno-admission-controller --tail=200 | grep -i -E 'error|denied|timeout'
```

---

## 7. Performance and scale

Removing the filter increases admission traffic **only for the kinds your policies match**. Measure it.

```bash
kubectl -n kyverno port-forward svc/kyverno-svc-metrics 8000:8000
```

```bash
curl -s localhost:8000/metrics | grep -E 'kyverno_admission_requests_total|kyverno_admission_review_duration_seconds|kyverno_policy_execution_duration_seconds' | head -40
```

API-server-side latency — the number that matters to everyone else:

```promql
histogram_quantile(0.99, sum(rate(apiserver_admission_webhook_admission_duration_seconds_bucket{name=~".*kyverno.*"}[5m])) by (le, name))
```

```promql
sum(rate(apiserver_admission_webhook_rejection_count[5m])) by (name, error_type)
```

Record p50/p99 before and after. Budget: p99 must stay well under the webhook `timeoutSeconds` (default 10s), with headroom.

Report volume and memory — the reports controller is what OOMs when report counts jump:

```bash
kubectl get polr -A --no-headers | wc -l && kubectl -n kyverno top pod
```

Churn check — if `Lease` or `EndpointSlice` appear after the change, a wildcard policy is now processing thousands of requests a minute. Scope it down before going further:

```bash
curl -s localhost:8000/metrics | grep kyverno_admission_requests_total | sort -t' ' -k2 -rn | head -20
```

---

## 8. Rollback and break-glass

Rehearse all of this **before** §6, not during it. Paste it into the change ticket.

### 8.1 Normal rollback

```bash
helm -n kyverno rollback kyverno
```

### 8.2 Fast rollback without Helm

The ConfigMap is re-read at runtime, so this takes effect immediately:

```bash
kubectl -n kyverno patch cm kyverno --type=merge -p '{"data":{"resourceFilters":"[*/*,kube-system,*]"}}'
```

Then restore the full original value from `~/kyverno-baseline/cm-kyverno.yaml`.

> ⚠️ **Helm drift.** This is now out of sync with the Helm release — the next `helm upgrade` overwrites it, and under Argo CD / Flux it is reverted within one sync. Treat it as "stop the bleeding", then revert the values file and run §8.1 so desired state matches.

> 💡 **Useful for testing.** The same runtime re-read lets you toggle **gate 2 alone** in seconds via `kubectl -n $KNS edit cm $KREL`. **Gate 1 cannot be tested this way** — it is rendered by the chart into the webhook configurations and needs a real `helm upgrade`. Do not conclude "removing the filter did nothing" from a ConfigMap-only test; that is the §1.3 trap.

### 8.3 Break-glass when the webhook is blocking your own recovery

Deleting Kyverno's webhook configurations removes it from the admission chain entirely:

```bash
kubectl delete validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
```

```bash
kubectl delete mutatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
```

Kyverno regenerates them once the admission controller is healthy (`--autoUpdateWebhooks=true` by default). **Verify the label matches your chart version before you need it:**

```bash
kubectl get validatingwebhookconfigurations --show-labels | grep -i kyverno
```

> ⚠️ **This does not disable a generated VAP.** If you adopt O5, you must also delete the binding: `kubectl delete validatingadmissionpolicybinding <name>`.

**Getting a working `kubectl` in the first place.** On both platforms the API server is managed and stays up even when kube-system is broken, so this is about *reaching* it with admin rights, not reviving a control plane.

*EKS* — the IAM principal that created the cluster holds implicit `cluster-admin` and depends on nothing in-cluster:

```bash
aws eks update-kubeconfig --name <CLUSTER> --region <REGION> --role-arn <CLUSTER_CREATOR_ROLE>
```

If the endpoint is private-only, your bastion/VPN path must already work — you will not be able to create a new bastion while admission is blocking writes.

*AKS* — `az aks command invoke` runs kubectl from **inside** the cluster via the Azure control plane, needing no network line-of-sight:

```bash
az aks command invoke -g <RG> -n <CLUSTER> --command "kubectl delete validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno"
```

> ⚠️ **Test this before relying on it.** It works by creating a short-lived pod (in its own namespace, commonly `aks-command`), and that pod creation goes through admission like anything else — so a broad `Deny` policy can block your own break-glass tool. **Exclude that namespace before §6** and verify the mechanism while the cluster is still healthy.

**Pre-stage checklist**

- [ ] EKS cluster-creator role assumable, kubeconfig tested
- [ ] AKS `command invoke` tested end-to-end **and** its namespace excluded from all policies
- [ ] Private-endpoint network path verified from wherever you will be sitting
- [ ] Webhook label string confirmed against your chart version
- [ ] A second person also holds these credentials

---

## 9. The answer

### 9.1 Drawbacks

| # | Drawback | Severity | Proven by |
|---|---|---|---|
| D1 | **Removing the filter alone changes almost nothing at admission** — gate 1 still excludes kube-system. Appearance of coverage without the coverage. | High (misleading) | §2.3 |
| D2 | **Generated VAPs bypass the setting entirely** and may already be enforcing on kube-system. | High (invisible) | §2.3 gate 4 |
| D3 | **Cluster-scoped policies are already unfiltered** — the ticket only changes the namespaced half. | Medium (scoping) | §1.2, §2.6 |
| D4 | **Enforcing on unpatchable workloads.** `require-drop-all`, `require-ro-rootfs`, `restrict-image-registries`, `restrict-sa-automount-sa-token` and pod-security all collide with provider-managed addons. Violations are permanent. | **Critical** | §3, §2.6 |
| D5 | **`restrict-binding-system-groups` collides with API-server RBAC bootstrap** — a permanent failure loop inside a managed control plane you cannot debug. | **Critical** | §2.6, T7 |
| D6 | **`mutateExisting` patches kube-system ServiceAccounts asynchronously** the moment the filter is removed — including ones that need their tokens. | **Critical** | §2.6, T8 |
| D7 | **Addon and cluster upgrades can be blocked**, including security patches. | High | T6, T7 |
| D8 | **EKS: node-bootstrap deadlock** with `failurePolicy: Fail` — new nodes never get a CNI and the cluster cannot self-heal. | **Critical** | T11, T12 |
| D9 | **AKS: konnectivity deadlock** — the API server loses its route to the webhook, blocking the writes needed to fix it. | **Critical** | T13, T11 |
| D10 | **AKS: Gatekeeper coexistence** — two admission controllers, additive latency, ambiguous denials. | Medium | T14 |
| D11 | **Mutation fight loops** — Kyverno mutates, the provider reconciler reverts, repeat. | Medium | T8 |
| D12 | **Generate side effects** — a default-deny NetworkPolicy in kube-system kills DNS. | Critical if triggered | T9 |
| D13 | **Deleting/cleanup policies gain reach into kube-system.** | High if present | T10 |
| D14 | **Admission latency and volume**, especially with wildcard-resource policies. | Medium | §7 |
| D15 | **Report volume growth** → reports-controller memory and etcd pressure. | Low–Medium | §7 |
| D16 | **Hand-editing the filter list loses future chart additions.** Use `resourceFiltersExcludeNamespaces`. | Medium | §5.1 |
| D17 | **Permanent red in compliance dashboards** — unfixable violations mean reports are never clean and people stop reading them. | Medium (cultural) | §5.5 |

### 9.2 Verdict on the acceptance criteria

**Loss of functionality: no.** Nothing Kyverno does today stops working. The change only adds scope.

**Loss of stability: conditional — and with this policy set the condition is met.** Steady state is unaffected, which is exactly why "pods appear to work fine" was observed. It degrades at the edges: node scale events, addon upgrades, cluster upgrades, Kyverno outages. With `require-drop-all`, `require-ro-rootfs`, `restrict-image-registries`, `restrict-sa-automount-sa-token` and pod-security deployed, those edges are hit routinely rather than rarely.

**Risk: yes — partly tail risk, partly immediate.** The deadlock (D8/D9) is tail risk. But several deployed policies are structurally incompatible with provider-managed addons, and `mutateExisting` acts as soon as the filter is removed with no admission request to observe. **Steady-state observation is not a valid safety signal here** — that is the core finding for the ticket.

### 9.3 Draft ticket wording

> Removing `kube-system` from `resourceFilters` is safe in isolation but also largely ineffective in isolation: the chart's `config.webhooks.namespaceSelector` independently excludes `kube-system` from the admission webhooks. Background scanning already covers kube-system (`--skipResourceFilters=true`), and cluster-scoped policies — roughly half of `other-vpol` — are unaffected by the filter and already enforcing. The coverage gap is therefore narrower than the ticket assumes.
>
> Enforcing on kube-system requires removing **both** gates. With the currently deployed upstream policy set that is not viable as-is: `require-drop-all`, `require-ro-rootfs`, `restrict-image-registries`, `restrict-sa-automount-sa-token` and the pod-security set all conflict with EKS/AKS managed addons (CNI, kube-proxy, CSI node plugins) that we cannot modify; `restrict-binding-system-groups` conflicts with RBAC the managed control plane reconciles itself; and `disable-automountserviceaccounttoken` with `mutateExisting` would patch kube-system ServiceAccounts that require their tokens.
>
> Separately, with `failurePolicy: Fail` a Kyverno outage during a node scale-in prevents CNI/kube-proxy pods from being created on new nodes (EKS), or breaks the konnectivity tunnel the API server needs to reach the webhook (AKS). Neither is recoverable without manual intervention.
>
> Recommendation: [O2 / O4 / O5], validated via §3, the §5.5 audit soak and the §6.5 deadlock test, on both EKS and AKS.

---

## 10. Options — pick one per platform

| Option | What it is | Trade-off |
|---|---|---|
| **O1. Status quo** | Leave both gates in place | No risk. Compensate with RBAC on who can write to kube-system. This is the documented Kyverno recommendation. |
| **O2. Reporting only** | Leave both gates; rely on background scanning, which already covers kube-system | Full visibility, zero admission risk, zero deadlock risk. Check §2.3 gate 3 — you may already have this. |
| **O3. Audit enforcement** | Remove both gates, `validationActions: ["Audit"]` + `failurePolicy: Ignore` | Real admission-path visibility, no blocking. Small latency cost. Required as §5 regardless of the end goal. |
| **O4. Scoped enforcement** | Remove both gates, exclude provider-managed workloads per policy, `failurePolicy: Ignore`, no wildcard resources, `mutateExisting` off for kube-system | Genuine enforcement on *your* kube-system workloads without touching the addons. The realistic end state if you want enforcement. Ongoing maintenance cost on EKS (see §10.2). |
| **O5. Per-policy VAP opt-in** | Leave both global gates alone. For policies that are genuinely safe on kube-system, set `spec.autogen.validatingAdmissionPolicy.enabled: true` and scope via the policy's own `matchConstraints` | Runs in the API server, bypassing both gates, **with no Kyverno availability dependency — the §6.5 deadlock does not apply**. See caveats in §10.1. |
| **O6. Full enforcement** | Remove both gates, `Deny` + `Fail` | **Not viable with the current policy set** (§2.7). Revisit only if the incompatible policies are re-scoped, and only after T11/T12 pass and break-glass is rehearsed. |

### 10.1 O5 caveats — verify before relying on it

VAP generation is not universal. Confirm on **each** cluster:

- [ ] Kubernetes version supports `ValidatingAdmissionPolicy` GA — `kubectl api-resources | grep validatingadmissionpolic`
- [ ] The specific policy can actually be expressed as a VAP (policies using Kyverno-only features will not generate one)
- [ ] A VAP object was actually created — `kubectl get validatingadmissionpolicies`
- [ ] The binding's `matchResources` scopes it as intended, including kube-system
- [ ] The generated VAP's `validationActions` matches the parent policy
- [ ] Everyone understands §8.3 break-glass will **not** disable it — the binding must be deleted separately

### 10.2 Scoping syntax for ValidatingPolicy

These are CEL-based policies; the legacy `exclude:` block does not apply. Use `matchConstraints` (VAP-shaped) and `matchConditions`:

```yaml
spec:
  matchConstraints:
    namespaceSelector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: NotIn
          values: [kube-system]        # per-policy opt-out, independent of the global gate
    resourceRules:
      - apiGroups: [""]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["pods"]
  matchConditions:
    - name: skip-provider-addons
      expression: >-
        !(object.metadata.namespace == 'kube-system' &&
          object.spec.serviceAccountName in ['coredns','aws-node','kube-proxy','konnectivity-agent'])
```

Verify field names against your installed CRD — the schema is the authority:

```bash
kubectl explain validatingpolicy.spec --recursive | head -60
```

**The two platforms need different exclusion strategies.**

* **AKS** stamps `addonmanager.kubernetes.io/mode` on managed addons, so a label-based selector works directly. Always add `konnectivity-agent` and the `az aks command invoke` namespace on top.
* **EKS** has no equivalent label. Exclude by ServiceAccount or name, enumerated from `aws eks list-addons` — and note that a **newly added addon will not be excluded automatically**. That ongoing maintenance burden belongs in the ticket as a cost of O4.

---

## 11. Sign-off checklist

Every box needs a linked artefact. Everything below "per platform" is ticked **twice**.

**Analysis (once)**

- [ ] Kyverno chart + app version recorded, confirmed identical on both platforms
- [ ] §2.1 Helm release name / namespace parameterised; GitOps presence determined
- [ ] §2.3 all four gates verified, including the `vap-probe` result
- [ ] Confirmed whether PolicyReports already exist in kube-system (gate 3)
- [ ] Policy inventory captured with `validatingpolicies,mutatingpolicies` (**not** `cpol`)
- [ ] Identified which policies are cluster-scoped and therefore already enforcing — do not double-count these as new coverage
- [ ] `mutateExisting` on `disable-automountserviceaccounttoken` confirmed disabled before §5
- [ ] §2.6 table filled in from §3 results rather than from the expectations as written
- [ ] `helm template` diff attached, confirmed to touch only the intended keys

**Per platform (EKS, then AKS)**

- [ ] kube-system inventory filled in, "Patchable?" verified not assumed
- [ ] §3 offline results attached
- [ ] Audit soak ≥ 7 days, failure list attached
- [ ] T1–T7 pass with `failurePolicy: Ignore`
- [ ] T8–T10 pass — no fight loop, no unexpected generates, no cleanup reach
- [ ] **T11 run for both `Ignore` and `Fail`, including the node scale-in step**
- [ ] T12 cold-start run
- [ ] Before/after p99 admission latency and report counts recorded
- [ ] *(AKS)* `konnectivity-agent` excluded in all policies; T13 run **first**; T14 latency measured; Azure Policy addon presence recorded

**Safety — before §6, not during**

- [ ] §8.1/§8.2 rollback executed successfully on both platforms
- [ ] §8.3 break-glass pre-stage checklist complete
- [ ] A second person holds the break-glass credentials

**Decision**

- [ ] Option O1–O6 chosen and recorded with rationale, **per platform** — they may legitimately differ

---

## 12. Gotchas that will waste your time

1. **`kubectl get cpol` returns nothing here.** The policies are `ValidatingPolicy`/`MutatingPolicy` in `policies.kyverno.io`. Any command or dashboard filtering on `ClusterPolicy` is silently reporting an empty set.
2. **`validationFailureAction` does not exist on these types.** Use `spec.validationActions: [Deny|Audit|Warn]` — always verify with the `custom-columns` check in §5.3.
3. **Mutation has no audit mode.** `mutateExisting` acts asynchronously with no admission request to observe. Disable it explicitly for §5.
4. **A generated VAP survives break-glass.** Deleting Kyverno's webhooks does not disable it; delete the `ValidatingAdmissionPolicyBinding`.
5. **Webhook configs are not updated by the ConfigMap.** `resourceFilters` is re-read at runtime; the `namespaceSelector` change needs the admission controller to regenerate the webhook objects. Wait 60s or restart it.
6. **`values: []` with `operator: NotIn` is rejected by the API server.** Keep at least one entry.
7. **Filter syntax differs by version** — `[*,kube-system,*]` vs `[*/*,kube-system,*]`. Grep, don't assume. Quote every entry in YAML or it parses as a nested list.
8. **`excludeGroups: system:nodes` does not cover what matters.** DaemonSet pods come from the daemonset controller, not the kubelet.
9. **Never generalise a result between EKS and AKS.** Different reconcilers, different worst cases.
10. **Don't test with a single Kyverno replica** — it understates the outage profile.
11. **The Kyverno namespace must stay excluded.** Removing `kyverno` from the selector is a separate, well-known self-deadlock.
12. **Object names carry the Helm release name** — see §2.1.
13. **Never `helm upgrade -f` from `helm get values --all`** — it freezes `resourceFilters` at today's list.
14. **GitOps changes the rollback path.** Under Argo CD / Flux both `helm rollback` and `kubectl patch` are reverted by the next sync.
15. **`helm upgrade` restarts the admission controller**, regenerating webhooks. Don't start a timed test in the first minute after one.

---

## 13. References

* Kyverno Helm chart values (`config.resourceFilters`, `config.webhooks`, `config.resourceFiltersExcludeNamespaces`, `config.excludeGroups`): https://github.com/kyverno/kyverno/blob/main/charts/kyverno/values.yaml
* Installation → Customization (filter syntax, namespace selectors, container flags incl. `--skipResourceFilters`): https://kyverno.io/docs/installation/customization/
* Troubleshooting (system namespaces, exclusion guidance): https://kyverno.io/docs/troubleshooting/
* ValidatingPolicy (`validationActions`, `matchConstraints`, `autogen`): https://kyverno.io/docs/policy-types/validating-policy/
* Upstream policy library: https://github.com/kyverno/policies
* Kyverno CLI `apply`: https://kyverno.io/docs/kyverno-cli/usage/apply/

**Chart defaults verified at time of writing (`main`):**

```yaml
config:
  excludeGroups: [system:nodes]
  webhooks:
    namespaceSelector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: NotIn
          values: [kube-system]
  resourceFilters:
    - '[Event,*,*]'
    - '[*/*,kube-system,*]'
    - '[*/*,kube-public,*]'
    - '[*/*,kube-node-lease,*]'
    - '[Node,*,*]'
    # ...plus APIService, TokenReview, SubjectAccessReview, Binding, Pod/binding,
    # ReplicaSet, EphemeralReport, ClusterEphemeralReport, and ~70 templated
    # entries covering Kyverno's own objects
  resourceFiltersExcludeNamespaces: []
  resourceFiltersIncludeNamespaces: []
```

Re-check against **your** installed chart version before relying on them.
