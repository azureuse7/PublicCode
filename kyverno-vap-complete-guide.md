# Making Kyverno Policies Survive an Outage — Complete Guide and Test Plan

**Platform:** AKS (Azure Kubernetes Service) · [Kubernetes 1.36 "Haru"](https://kubernetes.io/releases/) (released 22 April 2026)
**Kyverno:** latest — [1.18.x](https://github.com/kyverno/kyverno/releases/tag/v1.18.0) (confirm the exact version in Step 0)
**Policy type used:** [`ValidatingPolicy`](https://kyverno.io/docs/policy-types/validating-policy/) (short name: VPOL), API group `policies.kyverno.io`
**Status:** Draft — fill in the results as you run the tests
**Owner:** _TBC_

> This document combines three earlier drafts into one. Everything you need is here — you do not
> need the older files.

### Version facts, checked 1 August 2026

Everything below was verified against upstream sources on this date. Re-check before presenting if
significant time has passed.

| Fact | Status | Source |
|---|---|---|
| Kubernetes 1.36 "Haru", released 22 April 2026 | ✅ Confirmed | [Kubernetes releases](https://kubernetes.io/releases/) |
| `ValidatingAdmissionPolicy` GA since Kubernetes 1.30, on by default | ✅ Confirmed | [VAP docs](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/) |
| `MutatingAdmissionPolicy` **GA and on by default in 1.36** | ✅ Confirmed | [MAP docs](https://kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/) |
| Kyverno 1.18 is the current release | ✅ Confirmed | [CNCF announcement](https://www.cncf.io/blog/2026/05/05/announcing-kyverno-release-1-18/) |
| Kyverno CEL policy types (`ValidatingPolicy` etc.) are the recommended path | ✅ Confirmed | [Kyverno 1.17 release notes](https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/) |
| `ClusterPolicy` is being deprecated | ⚠️ Confirmed, but **no firm removal date published**. 1.17 notes indicated v1.20; the 1.18 announcement says only "later this year" | [1.18 announcement](https://www.cncf.io/blog/2026/05/05/announcing-kyverno-release-1-18/) |
| **`PolicyException` API version** | ⚠️ **Unclear — you must check on the cluster.** See the warning in Section 5.4 | [Exceptions guide](https://kyverno.io/docs/guides/exceptions/) |
| Manifest-based admission control is alpha in 1.36 | ✅ Confirmed | [Kubernetes blog](https://kubernetes.io/blog/2026/05/04/kubernetes-v1-36-manifest-based-admission-control/) |

---

## Contents

1. [What this document is for](#1-what-this-document-is-for)
2. [Words you need to know](#2-words-you-need-to-know)
3. [The problem, in plain English](#3-the-problem-in-plain-english)
4. [The idea we are testing](#4-the-idea-we-are-testing)
5. [What we already know before touching the cluster](#5-what-we-already-know-before-touching-the-cluster)
6. [Before you start](#6-before-you-start)
7. [The tests, step by step](#7-the-tests-step-by-step)
8. [Results tables](#8-results-tables)
9. [Limitations and risks](#9-limitations-and-risks)
10. [What changes for the on-call team](#10-what-changes-for-the-on-call-team)
11. [Acceptance criteria](#11-acceptance-criteria)
12. [Write-up outline](#12-write-up-outline)
13. [References and links](#13-references-and-links)

---

## 1. What this document is for

Today, if Kyverno stops working, **our policies stop being enforced**. Nobody gets an alert saying
"policy enforcement is off". Bad workloads simply get accepted.

This document tests a fix. The fix is to convert some of our policies into a **native Kubernetes
object** that the API server enforces by itself, without needing Kyverno to be running.

We want to answer four questions:

| # | Question | Where it is answered |
|---|---|---|
| 1 | Which of our policies can be converted? | Step 3 |
| 2 | Does the conversion actually work? | Step 4 |
| 3 | Do converted policies still block bad workloads when Kyverno is down? | Step 5 |
| 4 | **Do our existing PolicyExceptions still exempt workloads when Kyverno is down?** | **Step 6** — mechanism in [5.4](#54-how-exceptions-are-carried-across--this-is-the-key-finding), proof in 6a, real exceptions in 6f |

> **Question 4 is the one most people will ask about.** The short answer is *yes, and here is why*:
> Kyverno compiles each exception into the generated VAP as a negated match condition, so the
> exemption physically lives inside the native Kubernetes object. The API server reads it directly
> and does not need Kyverno running. Section 5.4 explains the mechanism; **Step 6a Part 1 is the
> command that proves it on our cluster**, and Step 6 tests it under two different crash modes plus
> a replay of our real production exceptions.
>
> The important caveat: this is true for exceptions that **already existed** before the outage. A
> **new** exception created *during* an outage does not work — see 6e. That inverts today's
> behaviour and needs to go in the runbook.

---

## 2. Words you need to know

Read this section once. Everything after it will make sense.

| Term | What it means, simply |
|---|---|
| **API server** | The "front door" of Kubernetes. Every `kubectl apply` goes through it. It decides what gets created. |
| **Admission control** | The checkpoint inside the API server that inspects a request and says yes or no. [Docs](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/) |
| **Webhook** | A phone call from the API server out to another program (Kyverno) asking "is this allowed?". It travels over the network. [Docs](https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/) |
| **Kyverno** | The policy tool we use today. It runs as pods in the cluster and answers those phone calls. [Site](https://kyverno.io/) |
| **`failurePolicy`** | What the API server does when the phone call fails. `Ignore` = let the request through. `Fail` = block the request. |
| **CEL** | A small expression language, a bit like a spreadsheet formula, used to write rules. Example: `object.spec.replicas > 3`. [Docs](https://kubernetes.io/docs/reference/using-api/cel/) |
| **ValidatingAdmissionPolicy (VAP)** | A rule written in CEL that the API server stores and checks **by itself**. No phone call, no network, no pods. [Docs](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/) |
| **VAPBinding** | The object that says *where* a VAP applies and whether it blocks (`Deny`) or just logs (`Audit`). A VAP does nothing without a binding. |
| **ValidatingPolicy (VPOL)** | Kyverno's newer CEL-based policy type. It can automatically produce a VAP for us. [Docs](https://kyverno.io/docs/policy-types/validating-policy/) |
| **PolicyException** | An object that says "this policy does not apply to that workload" — our exemption mechanism. [Docs](https://kyverno.io/docs/guides/exceptions/) |
| **`matchConstraints`** | The part of a policy that says which resources it applies to (for example: Deployments only). |
| **`matchConditions`** | Extra CEL tests that decide whether the policy runs at all for this request. Used for exemptions. |
| **Autogen** | Kyverno automatically generating something for you. Used two different ways — see the warning in Step 5.1. |

---

## 3. The problem, in plain English

### 3.1 How it works today

Kyverno registers a **webhook**. That tells the API server: "before you create a Pod, call me first."

So every matching request depends on:
- the Kyverno pods being alive,
- the Kyverno Service being reachable over the network,
- the TLS certificate being valid.

If any of those break, the phone call fails. What happens next depends on `failurePolicy`:

| Kyverno state | What the API server does | Result |
|---|---|---|
| Healthy | Calls Kyverno, gets allow or deny | ✅ Policy enforced |
| Pods down, network broken, or certificate expired | Call fails, and with `failurePolicy: Ignore` the check is **skipped** | ❌ **Request accepted. No enforcement.** |

**It gets worse.** When the Kyverno admission controller shuts down cleanly, it **deletes its own
webhook registrations**. So there is not even a failing phone call — those resource types quietly
drop out of the checkpoint entirely.

This is a deliberate trade-off. Fail-open is better than freezing the whole cluster. But it means
**our policy coverage is only as reliable as Kyverno's pod health**, and the failure is silent.

Confirm our current setting in Step 0. We expect `Ignore` on most resources.

### 3.2 Why this matters

An outage does not just mean "policies are off for a few minutes". It means:
- Anything deployed during the outage bypassed every check.
- No alert fires, because nothing errored.
- You only find out later, during an audit.

---

## 4. The idea we are testing

A **ValidatingAdmissionPolicy** is different. It is a normal Kubernetes object, and the API server
evaluates it **inside itself** using its own built-in CEL engine.

No webhook. No Service. No network hop. No TLS certificate. No pods.

> **Our hypothesis:** a `ValidatingPolicy` with
> `spec.autogen.validatingAdmissionPolicy.enabled: true` produces a VAP and a VAPBinding that keep
> working when Kyverno is completely down — **and** our existing PolicyExceptions keep working too.

### 4.1 What this is *not*

This is **not** a replacement for Kyverno.

A VAP can only do simple, self-contained checks against a single resource, using the API server's
standard CEL environment. It **cannot**:
- look up data from outside (other resources, ConfigMaps, HTTP endpoints),
- verify image signatures,
- mutate (change) resources,
- generate new resources,
- run background scans of things already in the cluster.

**The target is a layered setup:**

```
┌──────────────────────────────────────────────────────────┐
│  API server                                              │
│                                                          │
│  1. VAP  ── always on, survives a Kyverno outage         │  ← the safety floor
│  2. Kyverno webhook ── everything VAP cannot do          │  ← still fail-open
└──────────────────────────────────────────────────────────┘
```

VAP becomes the floor that never disappears. Kyverno keeps handling everything else.

---

## 5. What we already know before touching the cluster

The behaviour below was established by reading the Kyverno controller source code and its own
conformance test suite. It removes most of the guesswork.

⚠️ **Still confirm every item on our cluster.** Treat these as the *expected results* your tests are
checking against, not as a substitute for testing. The source read was `main`; our installed version
may differ.

### 5.1 The three rules for generation

Kyverno only creates a VAP when **all three** are true:

| # | Rule | Error message in `.status` if broken |
|---|---|---|
| 1 | Kyverno has RBAC permission on `validatingadmissionpolicies` **and** `validatingadmissionpolicybindings` | `insufficient permissions to generate ValidatingAdmissionPolicies` |
| 2 | The policy sets `spec.autogen.validatingAdmissionPolicy.enabled: true` | `skip generating ValidatingAdmissionPolicy: not enabled.` |
| 3 | Pod-controller autogen produced **no** configs — i.e. `spec.autogen.podControllers.controllers: []` | `skip generating ValidatingAdmissionPolicy: pod controllers autogen is enabled.` |

#### ⚠️ Rule 3 is the trap — read this twice

Kyverno has a convenience feature called **pod-controller autogen**. You write a policy about Pods,
and Kyverno automatically extends it to Deployments, StatefulSets, DaemonSets, Jobs and CronJobs.
Most of our policies probably rely on it.

**Pod-controller autogen and VAP generation cannot both be on.** They are mutually exclusive by
design. Kyverno's own conformance test (`autogen-enabled`) asserts that a policy with both settings
produces **no VAP at all**.

Worse: if someone later adds `podControllers` to a policy that was already converted, Kyverno
**deletes** the existing VAP and its binding. Enforcement silently falls back to the fail-open
webhook. Nobody is told.

**What this means for us:** every converted policy must list its target resources explicitly in
`matchConstraints.resourceRules` — Deployments, StatefulSets, DaemonSets and so on, written out one
by one. That is a real authoring cost and it belongs in the candidate audit (Step 3).

See [Kyverno issue #13722](https://github.com/kyverno/kyverno/issues/13722).

#### ⚠️ "Ready" does not mean "generated"

A policy that **skipped** VAP generation still reports `status.conditionStatus.ready: true` and
`WebhookConfigured: True`. That looks healthy but is not.

**The only field that tells the truth is `status.generated`.** Alert on that one.

### 5.2 What the generated objects are called

| Object | Name pattern |
|---|---|
| ValidatingAdmissionPolicy | `vpol-<policy-name>` |
| ValidatingAdmissionPolicyBinding | `vpol-<policy-name>-binding` |

(The `cpol-` prefix is used for VAPs generated from the older `ClusterPolicy` type. Not relevant to us.)

### 5.3 How fields are copied across

| Your ValidatingPolicy field | Becomes, in the generated VAP | Note |
|---|---|---|
| `spec.matchConstraints` | `spec.matchConstraints` | Copied exactly |
| `spec.matchConditions` | `spec.matchConditions` | Copied, then exception conditions are added |
| `spec.validations` | `spec.validations` | Copied, with `exceptions.*` substitutions applied |
| `spec.variables` | `spec.variables` | Copied, then exception variables are added |
| `spec.auditAnnotations` | `spec.auditAnnotations` | Copied |
| `spec.validationActions` | The **binding's** `spec.validationActions` | **Defaults to `[Deny]` if you leave it out** |
| — | `spec.failurePolicy: Fail` | Not taken from your policy — this is the VAP API default |
| `spec.evaluation.*` | *not copied* | VAP only works at admission time |
| `spec.webhookConfiguration.*` | *not copied* | There is no webhook involved |
| `paramKind` / `paramRef` | **Appears not to be wired up for this path** | Check in Step 3 if any of our policies use parameters |

#### ⚠️ Two defaults that can surprise you

**1. `validationActions` defaults to `Deny`.** If you forget to set it, the generated binding blocks
requests. There is no "safe default to Audit". **Always set it explicitly on every policy.**

**2. The generated VAP is always `failurePolicy: Fail`.** Even if your policy sets `Ignore` for its
webhook behaviour, the VAP is fail-closed. A mistake in your CEL expression will **block**
admission, with no webhook to fall back on. Confirm this on our version — it changes the risk
profile of a bad expression.

### 5.4 How exceptions are carried across — this is the key finding

**Exceptions do survive.** This is the answer to the main question in the ticket.

Kyverno takes each PolicyException and compiles it into the generated VAP as a **negated match
condition**. In plain terms: "run this policy *unless* the exception matches."

> ### ⚠️ Check the PolicyException API version before writing any YAML
>
> The `policies.kyverno.io` group has been moving through `v1alpha1` → `v1beta1` → `v1`, and
> **different sources currently disagree** about where `PolicyException` has landed:
>
> - The [Kyverno exceptions guide](https://kyverno.io/docs/guides/exceptions/) shows
>   `policies.kyverno.io/v1alpha1`.
> - Release notes around 1.17/1.18 indicate `v1beta1`, with promotion to `v1` planned.
>
> **The YAML examples in this document use `v1alpha1`, matching the current published guide.**
> That may not be what your cluster serves. Run this first and use whatever it reports:
>
> ```bash
> kubectl api-resources --api-group=policies.kyverno.io
> kubectl explain policyexception --api-version=policies.kyverno.io/v1alpha1 2>/dev/null | head -5
> ```
>
> Getting this wrong is not subtle — `kubectl apply` fails outright with "no matches for kind". It
> will not fail silently. But it will waste your time mid-demo, so check in Step 0.

| PolicyException field | Becomes, in the generated VAP |
|---|---|
| `spec.matchConditions[].expression` | A `spec.matchConditions` entry with the expression **negated**: `!(<original>)`, keeping the original `name` |
| `spec.images[]` | A `spec.variables` entry called `allowedImages`, written as a CEL list |
| `spec.allowedValues[]` | A `spec.variables` entry called `allowedValues`, written as a CEL list |
| `exceptions.allowedImages` / `exceptions.allowedValues` used in a validation | Rewritten to `variables.allowedImages` / `variables.allowedValues` |
| `spec.reportResult` | *not copied* — a reporting concept only |
| `polex.kyverno.io/priority` label | *not copied* |

**Worked example.** You write this exception:

```yaml
matchConditions:
  - name: check-name
    expression: "object.metadata.name == 'skipped-deployment'"
```

Kyverno puts this into the VAP:

```yaml
spec:
  matchConditions:
    - name: check-name
      expression: '!(object.metadata.name == ''skipped-deployment'')'
```

Because VAP match conditions are **ANDed together**, several exceptions combine correctly:
`!(exception1) && !(exception2)` means "skip if *any* exception matches". The logic is right.

**This is why exemptions survive an outage.** The exclusion is baked into the VAP object itself. The
API server reads it directly. Kyverno does not need to be running.

#### ⚠️ Three ways this can break — test all of them

1. **Duplicate match-condition names.** A VAP requires match-condition names to be unique. If two
   exceptions on the same policy both use `name: check-name`, they collide. Kyverno's own test
   deliberately uses different names (`check-name`, `check-namespace`). **At our exception volume
   this is the most likely thing to break.** We would need a naming convention, enforced by a policy
   on PolicyExceptions.
2. **Duplicate `allowedImages` / `allowedValues` variables.** Two fine-grained exceptions on the
   same policy would each try to add a variable with the same name.
3. **Exception CEL must be API-server CEL.** You can use `object`, `oldObject`, `request`,
   `namespaceObject` and `authorizer`. Kyverno's own extended CEL libraries are **not** available.
   [Kyverno CEL libraries](https://kyverno.io/docs/policy-types/cel-libraries/)

### 5.5 Once a VAP exists, the webhook drops the policy

Kyverno only registers a policy in its webhook when `AdmissionEnabled() && !status.Generated`.

So a policy that successfully generates a VAP is **taken out of the Kyverno webhook completely**.

This is better than expected — no double enforcement, no duplicate error messages, less webhook
load. But it has a sharp edge:

> Enforcement for a converted policy now lives **only** in the API server. If generation later fails
> or is skipped, Kyverno deletes the VAP and re-adds the webhook. There is a gap between those two
> events, and the webhook it falls back to is fail-open.

### 5.6 Lifecycle risks

- Generated VAPs have an `ownerReference` pointing at the source policy. **Delete the policy and the
  VAP is garbage-collected** — enforcement disappears with it.
- Generated VAPs are labelled `app.kubernetes.io/managed-by: kyverno` and are continuously
  reconciled. **Manual edits are reverted.** You cannot hand-edit a VAP as a break-glass measure.
- Exceptions are compiled in **by Kyverno, at generation time**. A new or changed exception created
  while Kyverno is down will **not** reach the VAP.

---

## 6. Before you start

### 6.1 What must be in place

| Requirement | Status on AKS 1.36 |
|---|---|
| `ValidatingAdmissionPolicy` API | GA since Kubernetes 1.30 — on by default, **no feature gate needed** |
| `admissionregistration.k8s.io/v1` | On by default |
| Kyverno admission controller flag | `--generateValidatingAdmissionPolicy=true` |
| Kyverno reports controller flag | `--validatingAdmissionPolicyReports=true` |
| Kyverno RBAC | create/update/delete/list on `validatingadmissionpolicies` and `validatingadmissionpolicybindings` |
| PolicyExceptions enabled | `--enablePolicyException=true` and `--exceptionNamespace=<ns>` |

Older guides mention `--feature-gates='ValidatingAdmissionPolicy=true'`. That is obsolete for us —
there is nothing to turn on at the API server.

**Check which API version is served.** The `policies.kyverno.io` group has moved through
`v1alpha1` → `v1beta1` → `v1`. Confirm before writing any manifests:

```bash
kubectl api-resources --api-group=policies.kyverno.io
kubectl explain validatingpolicy.spec.autogen --recursive | head -30
```

### 6.2 Safety rules for testing on AKS

Several tests deliberately take Kyverno offline. Please:

- **Use a non-production cluster first.** While Kyverno is down, every *unconverted* policy is
  unenforced.
- **Book a window** and silence Kyverno alerting.
- **Pause GitOps first.** If Flux or Argo CD manages Kyverno, it will scale Kyverno back up in the
  middle of your test and ruin the result:
  ```bash
  flux suspend helmrelease kyverno -n kyverno
  # or:  argocd app set kyverno --sync-policy none
  ```
- **Keep the restore command ready** in a second terminal before you break anything.

### 6.3 Set up a working folder

Every step writes evidence here. The collected output becomes the write-up.

```bash
mkdir -p vap-poc/{baseline,policies,tests,results}
cd vap-poc
```

---

## 7. The tests, step by step

Each step says **what it proves**, **why it matters**, then gives the commands.

---

### Step 0 — Capture the starting point

**What it proves:** nothing yet. It records "before", so every later change is measurable.
**Why it matters:** the current `failurePolicy` value *is* the business case. Capture it verbatim.

```bash
# Versions
kubectl version -o yaml | grep -A3 serverVersion
kubectl get deploy -n kyverno \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'
helm list -n kyverno

# AKS details
az aks show -g "<RG>" -n "<CLUSTER>" \
  --query '{name:name, k8s:kubernetesVersion, powerState:powerState.code}' -o table

# Which policy APIs are served, and at which version?
# WRITE THESE DOWN - every YAML example in this document depends on them.
kubectl api-resources --api-group=policies.kyverno.io

# Confirm the VAP/MAP native APIs too
kubectl api-resources --api-group=admissionregistration.k8s.io

# Current webhook posture — THIS IS THE PROBLEM STATEMENT
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o custom-columns='NAME:.metadata.name,WEBHOOK:.webhooks[*].name,FAILUREPOLICY:.webhooks[*].failurePolicy'
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno \
  -o yaml > baseline/webhooks.yaml

# Which flags are actually in effect?
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n'
kubectl get deploy kyverno-reports-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n'

# Policy and exception inventory
kubectl get vpol -o wide > baseline/vpols.txt
kubectl get polex -A -o yaml > baseline/exceptions.yaml

# Anything already generated?
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding
```

**Also check whether Azure Policy (Gatekeeper) is installed.** It will not conflict, but it produces
similar-looking deny messages, which makes later forensics confusing.

```bash
az aks show -g "<RG>" -n "<CLUSTER>" --query 'addonProfiles.azurepolicy' -o json
kubectl get pods -n gatekeeper-system 2>/dev/null
```

**Write down:**

| Item | Value on our cluster |
|---|---|
| Kubernetes version | |
| Kyverno version and chart version | |
| `ValidatingPolicy` API version | |
| **`PolicyException` API version** (Section 5.4) | |
| Policy count, Deny vs Audit split | |
| Exception count | |
| `failurePolicy` per webhook | |
| **Replica counts per Kyverno deployment** (needed to restore later) | |
| Azure Policy / Gatekeeper installed? | |

---

### Step 1 — Prove the gap exists ("before" demo)

**What it proves:** that a Kyverno outage really does turn enforcement off.
**Why it matters:** this terminal output is half the argument to the team. Capture it.

```bash
kubectl create ns vap-poc

cat > tests/violating-deployment.yaml << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gap-test
  namespace: vap-poc
  labels: { env: testing }
spec:
  replicas: 1
  selector: { matchLabels: { app: gap-test } }
  template:
    metadata:
      labels: { app: gap-test }
    spec:
      containers:
        - name: nginx
          image: nginx
EOF

# 1. Confirm an existing Deny-mode policy blocks it
kubectl apply -f tests/violating-deployment.yaml
# EXPECT: denied by validate.kyverno.svc

# 2. Take Kyverno down
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/component=admission-controller \
  -n kyverno --timeout=120s

# 3. Are the webhooks gone?
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno

# 4. Try again
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step1-the-gap.txt
# EXPECT: created — THIS IS THE GAP

# 5. Clean up and restore
kubectl delete deploy gap-test -n vap-poc
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=<baseline>
```

---

### Step 2 — Turn on VAP generation

**What it proves:** Kyverno is allowed and configured to create VAPs.
**Why it matters:** if the flag or RBAC is missing, generation fails **silently** later.

Helm values (top-level `features:` block):

```yaml
features:
  generateValidatingAdmissionPolicy:
    enabled: true
  validatingAdmissionPolicyReports:
    enabled: true
  policyExceptions:
    enabled: true
    namespace: "<our-exception-namespace>"   # or "*"
```

> Recent chart versions already default the first two to `true`. Set them explicitly anyway so the
> intent is recorded in Git, and **check against our pinned chart version**.
> Reference: [Kyverno installation & customization](https://kyverno.io/docs/installation/customization/)

Verify:

```bash
kubectl get deploy kyverno-admission-controller -n kyverno \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep -i admissionpolicy

kubectl auth can-i create validatingadmissionpolicies \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
kubectl auth can-i create validatingadmissionpolicybindings \
  --as=system:serviceaccount:kyverno:kyverno-admission-controller
# BOTH must say "yes" — this is rule 1 of the three in Section 5.1
```

If RBAC is missing, add:

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
  - apiGroups: ["admissionregistration.k8s.io"]
    resources: ["validatingadmissionpolicies", "validatingadmissionpolicybindings"]
    verbs: ["create", "update", "delete", "list"]
```

**Note:** turning the flag on changes nothing by itself. Generation is **opt-in per policy**. A
policy with no `autogen` block reports `generated: false`.

#### Optional but recommended: prove VAP works at all on this cluster

On AKS you cannot inspect the API server's flags. This 60-second test proves the VAP engine is
genuinely active, independently of Kyverno.

```bash
kubectl create namespace vap-smoketest

cat > tests/vap-smoketest.yaml <<'EOF'
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: vap-smoketest
spec:
  failurePolicy: Fail
  matchConstraints:
    resourceRules:
      - apiGroups:   [""]
        apiVersions: ["v1"]
        operations:  ["CREATE"]
        resources:   ["configmaps"]
  validations:
    - expression: "false"
      message: "VAP smoketest - the admission plugin IS active"
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicyBinding
metadata:
  name: vap-smoketest-binding
spec:
  policyName: vap-smoketest
  validationActions: [Deny]
  matchResources:
    namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: vap-smoketest
EOF

kubectl apply -f tests/vap-smoketest.yaml && sleep 10
kubectl -n vap-smoketest create configmap probe --from-literal=k=v
# EXPECT: denied, mentioning ValidatingAdmissionPolicy

kubectl delete -f tests/vap-smoketest.yaml && kubectl delete ns vap-smoketest
```

If that ConfigMap is **created** instead of denied, stop the PoC and raise it with the platform team.

---

### Step 3 — Work out which policies can convert *(Acceptance Criterion 1)*

**What it proves:** how much of our policy set can be made outage-proof.
**Why it matters:** this number is the headline result.

#### Things that block conversion

| Feature in the policy | Why it blocks conversion |
|---|---|
| **Relies on `autogen.podControllers`** | Mutually exclusive with VAP generation (Section 5.1). Must enumerate resources in `matchConstraints` instead |
| Kyverno CEL libraries (`resource.Get/List`, `http.*`, `image*`, `verifyImageSignatures`, global context) | Not available in the API server's CEL environment |
| `evaluation.mode: JSON` | Non-Kubernetes payloads — there is no admission path |
| `evaluation.admission.enabled: false` (background only) | Nothing to enforce at admission time |
| `NamespacedValidatingPolicy` | VAP is cluster-scoped |
| `paramKind` / `paramRef` | Appears not to be wired for this path — verify |
| Exceptions using non-translatable features | See Section 5.4 |

#### Triage commands

```bash
# Which policies already opt in, and did they generate?
kubectl get vpol -o json | jq -r '
  .items[] | [
    .metadata.name,
    (.spec.autogen.validatingAdmissionPolicy.enabled // false | tostring),
    (.spec.autogen.podControllers.controllers // [] | length | tostring),
    (.spec.validationActions // ["<unset - defaults to Deny>"] | join(",")),
    (.status.generated // false | tostring)
  ] | @tsv' | column -t -N NAME,VAP_ENABLED,PODCTRL_COUNT,ACTIONS,GENERATED

# Policies that depend on pod-controller autogen (these need matchConstraints rewritten)
kubectl get vpol -o json | jq -r '
  .items[] | select((.spec.autogen.podControllers.controllers // []) | length > 0)
  | "\(.metadata.name)\t\(.spec.autogen.podControllers.controllers | join(","))"'

# Policies using Kyverno-only CEL libraries (hard blockers)
kubectl get vpol -o json | jq -r '
  .items[] as $p |
  ($p.spec.validations[]?.expression, $p.spec.variables[]?.expression, $p.spec.matchConditions[]?.expression)
  | select(test("resource\\.(Get|List)|http\\.|verifyImage|verifyAttestation|globalContext|images\\."))
  | $p.metadata.name' | sort -u

# Background-only or JSON-mode policies
kubectl get vpol -o json | jq -r '
  .items[] | select(.spec.evaluation.mode == "JSON" or .spec.evaluation.admission.enabled == false)
  | .metadata.name'

# Exception name-collision risk (Section 5.4, failure mode 1)
kubectl get polex -A -o json | jq -r '
  .items[] as $e | $e.spec.policyRefs[]? as $ref |
  ($e.spec.matchConditions[]? | "\($ref.name)\t\(.name)\t\($e.metadata.namespace)/\($e.metadata.name)")' \
  | sort | awk -F'\t' '{k=$1"\t"$2; c[k]++; d[k]=d[k]" "$3} END {for (i in c) if (c[i]>1) print "COLLISION:", i, "->", d[i]}'
```

**Run that last command early.** It finds policies where two exceptions share a match-condition
name, which would produce an invalid VAP.

#### Deliverable — the candidate register

| Policy | Source | Action | Blockers | Rewrite needed | Exceptions attached |
|---|---|---|---|---|---|
| disallow-host-path | upstream | Convert | none | — | 2 |
| require-team-label | custom | Convert | podControllers | Enumerate resources in `matchConstraints` | 0 |
| verify-image-signature | custom | Keep on webhook | image CEL library | — | 1 |
| … | | | | | |

**Headline number:** `X of Y policies convertible (Z%)`.

---

### Step 4 — Convert one policy and prove it generated *(Acceptance Criterion 2)*

**What it proves:** the mechanism works end to end.
**Why it matters:** start small. One low-risk policy, in **Audit** mode.

```yaml
apiVersion: policies.kyverno.io/v1        # ← VERIFY the served version - Step 0 table
kind: ValidatingPolicy
metadata:
  name: check-deployment-labels
spec:
  validationActions: [Audit]              # ALWAYS set this - unset means Deny
  autogen:
    validatingAdmissionPolicy:
      enabled: true
    podControllers:
      controllers: []                     # MUST be empty, or generation is skipped
  matchConstraints:
    resourceRules:
      - apiGroups:   [apps]
        apiVersions: [v1]
        operations:  [CREATE, UPDATE]
        resources:   [deployments]
  variables:
    - name: environment
      expression: >-
        has(object.metadata.labels) && 'env' in object.metadata.labels
        && object.metadata.labels['env'] == 'prod'
  validations:
    - expression: "variables.environment == true"
      message: "Deployment labels must be env=prod"
```

**Helm override shape.** Keep the opt-in in our own values file rather than editing vendored
upstream YAML, so upstream sync tooling stays non-destructive:

```yaml
policies:
  check-deployment-labels:
    enabled: true
    validationActions: [Audit]
    autogen:
      validatingAdmissionPolicy: true     # template renders the autogen block when true
      podControllers: []                  # template renders controllers: [] - required
```

Add a chart-level guard so the two can never be set together:

```
{{- if and .autogen.validatingAdmissionPolicy .autogen.podControllers }}
{{- fail "podControllers and validatingAdmissionPolicy are mutually exclusive" }}
{{- end }}
```

#### Verify

```bash
POL=check-deployment-labels

# The only signal that matters
kubectl get vpol $POL -o jsonpath='{.status.generated}'
# EXPECT: true   (remember: .status.conditionStatus.ready can be true even when this is false)

# If it did not generate, why not?
kubectl get vpol $POL -o jsonpath='{.status.conditionStatus.conditions}' | jq
kubectl describe vpol $POL | grep -i -A3 "message"

# The generated objects - note the vpol- prefix
kubectl get validatingadmissionpolicy vpol-$POL -o yaml
kubectl get validatingadmissionpolicybinding vpol-$POL-binding -o yaml

# Whole inventory
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding \
  -l app.kubernetes.io/managed-by=kyverno
```

| Check | Expected |
|---|---|
| `spec.failurePolicy` | `Fail` |
| `metadata.ownerReferences` | ValidatingPolicy, our policy name |
| `metadata.labels` | `app.kubernetes.io/managed-by: kyverno` |
| `spec.matchConstraints` | Mirrors our policy exactly |
| `spec.variables` | Mirrors our policy |
| Binding `spec.validationActions` | `[Audit]` now, `[Deny]` after we flip |

#### Confirm the webhook dropped this policy (Section 5.5)

```bash
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno -o json \
  | jq '.items[].webhooks[] | {name, rules: .rules}'
```

Compare with `baseline/webhooks.yaml`. **Expect `apps/v1 deployments` to have been removed** from
the Kyverno webhook rules. Then apply a violating resource while Kyverno is **up**: you should see
exactly **one** denial message, and it should come from the API server, not `validate.kyverno.svc`.

#### Negative test — prove the podControllers trap is real

```bash
kubectl patch vpol $POL --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":["deployments"]}}}}'
sleep 15
kubectl get vpol $POL -o jsonpath='{.status.generated}'          # EXPECT: false
kubectl describe vpol $POL | grep -i "pod controllers autogen"   # EXPECT: skip message
kubectl get validatingadmissionpolicy vpol-$POL                  # EXPECT: NotFound - it was deleted

# Revert
kubectl patch vpol $POL --type=merge \
  -p '{"spec":{"autogen":{"podControllers":{"controllers":[]}}}}'
```

This demonstrates the silent-revert risk to the team better than any slide.

---

### Step 5 — The resilience test (the headline result)

**What it proves:** converted policies keep working with Kyverno completely down.
**Why it matters:** this is the whole point of the exercise.

```bash
POL=check-deployment-labels

# 0. Flip to Deny and confirm the binding follows
kubectl patch vpol $POL --type=merge -p '{"spec":{"validationActions":["Deny"]}}'
sleep 10
kubectl get validatingadmissionpolicybinding vpol-$POL-binding \
  -o jsonpath='{.spec.validationActions}'
# EXPECT: ["Deny"]

# 1. It blocks while Kyverno is UP
kubectl apply -f tests/violating-deployment.yaml   # EXPECT: denied

# 2. Take Kyverno FULLY down
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
kubectl get pods -n kyverno

# 3. Webhooks gone, but the VAP survives
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding

# 4. THE TEST
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step5-the-proof.txt
```

**Expected output:**

```
The deployments "gap-test" is invalid: : ValidatingAdmissionPolicy 'vpol-check-deployment-labels'
with binding 'vpol-check-deployment-labels-binding' denied request: Deployment labels must be env=prod
```

The denial comes from the **API server**. That is the proof.

```bash
# 5. Control test - an UNCONVERTED policy, same outage window
kubectl apply -f tests/violating-resource-for-unconverted-policy.yaml
# EXPECT: admitted. This is the delta - it shows the gain came from VAP.

# 6. Restore
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno
```

**Note how long** it takes for the webhooks to re-register. That is the length of your recovery
window.

#### Other failure modes worth testing

Scaling to zero is a *clean* shutdown. A real crash behaves differently, because Kyverno never gets
the chance to remove its webhooks.

| Scenario | How to simulate | What to watch for |
|---|---|---|
| **Hard crash** | `kubectl -n kyverno set image deploy/kyverno-admission-controller kyverno=<bad-image>` → pods CrashLoop | Webhooks **stay registered** and now fail. This is where `failurePolicy` actually matters. Undo with `kubectl rollout undo` |
| Node or AZ loss | Cordon and drain Kyverno's nodes | Same result, more realistic |
| Network partition | Deny-all NetworkPolicy on the Kyverno Service | Webhook *times out* rather than disappearing — does `Ignore` still admit? |
| TLS certificate expiry | Delete the Kyverno TLS secret | Webhook errors; VAP unaffected |
| Cluster bootstrap | Restart the cluster with no Kyverno pods | VAP active from the very first request |
| **Policy deleted** | `kubectl delete vpol <name>` | ⚠️ VAP is garbage-collected via ownerReference — enforcement gone |
| **Transition window** | Add `podControllers`, watch closely | Gap between VAP deletion and webhook re-registration |

#### Check nothing broke for real workloads

Server-side dry-run runs the **full** admission chain, including VAPs, without creating anything. It
is safe to run against production manifests.

```bash
for f in tests/real-workloads/*.yaml; do
  printf '%-60s' "$f"
  if kubectl apply -f "$f" --dry-run=server >/dev/null 2>&1; then echo "ADMITTED"; else echo "DENIED  <-- INVESTIGATE"; fi
done | tee results/step5-blast-radius.txt
```

[Dry-run documentation](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run)

---

### Step 6 — Exception testing *(Acceptance Criterion 3)*

**What it proves:** our existing exemptions still work, including during an outage.
**Why it matters:** if exemptions break, previously-working production workloads start getting
blocked. That is worse than the problem we are fixing.

**This is the step that answers the main question in the ticket.** It has seven parts:

| Sub-step | What it covers |
|---|---|
| **6a** | One exception: does it reach the VAP, and does it hold under **both** crash modes? ← **the core test** |
| 6b | Do multiple exceptions combine correctly? |
| 6c | ⚠️ Duplicate match-condition names — expected to break |
| 6d | Fine-grained exceptions (`images` / `allowedValues`) |
| 6e | ⚠️ An exception created *during* an outage — expected to fail closed |
| **6f** | **Replay our real, existing production exceptions during an outage** |
| 6g | Measure the gap between creating an exception and it reaching the VAP |

#### 6a — One exception, translated correctly

```yaml
apiVersion: policies.kyverno.io/v1alpha1   # ← VERIFY on your cluster - see Section 5.4
kind: PolicyException
metadata:
  name: skip-important-tool
  namespace: <our-exception-namespace>
spec:
  policyRefs:
    - name: check-deployment-labels
      kind: ValidatingPolicy
  matchConditions:
    - name: skip-important-tool          # THIS NAME MUST BE UNIQUE per policy
      expression: "object.metadata.name == 'important-tool'"
```

**Part 1 — does the exception reach the VAP at all?**

```bash
kubectl apply -f tests/exception.yaml
sleep 15

# THE MECHANISM CHECK. If this is empty, nothing else in Step 6 will work.
kubectl get validatingadmissionpolicy vpol-check-deployment-labels \
  -o jsonpath='{.spec.matchConditions}' | jq | tee results/step6a-vap-matchconditions.json
# EXPECT: [{"name":"skip-important-tool","expression":"!(object.metadata.name == 'important-tool')"}]
```

**This output is the single most important artefact in the document.** It shows the exemption
physically living inside the native Kubernetes object. Once it is there, the API server enforces it
on its own — Kyverno is no longer involved at request time. Save it for the write-up.

**Part 2 — behaviour with Kyverno UP (the control)**

```bash
kubectl apply -f tests/important-tool-deployment.yaml   # violating, but named important-tool
# EXPECT: allowed
kubectl delete deploy important-tool -n vap-poc
```

**Part 3 — behaviour with Kyverno FULLY DOWN ← the answer the ticket wants**

> Scale down **all four** controllers, not just the admission controller. A partial shutdown is not
> an outage test — the remaining controllers can still reconcile, and you would be proving nothing.

```bash
# Take everything down
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
kubectl get pods -n kyverno            # EXPECT: no resources found

# Confirm the VAP is still there even though Kyverno is not
kubectl get validatingadmissionpolicy vpol-check-deployment-labels \
  -o jsonpath='{.spec.matchConditions}' | jq
# EXPECT: the negated condition is STILL present

# TEST A - the exempt workload
kubectl apply -f tests/important-tool-deployment.yaml 2>&1 | tee results/step6a-exempt-down.txt
# EXPECT: STILL ALLOWED - the exclusion is compiled into the VAP

# TEST B - a non-exempt workload, same outage window
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step6a-nonexempt-down.txt
# EXPECT: still DENIED - so the policy is genuinely active, and the exemption is genuinely targeted
```

**Test A and Test B together are the result.** Test A alone is not enough — if the policy were
somehow inactive, everything would be admitted and Test A would "pass" for the wrong reason. Test B
rules that out.

**Part 4 — restore before continuing**

```bash
kubectl delete deploy important-tool -n vap-poc --ignore-not-found
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done
kubectl rollout status deploy/kyverno-admission-controller -n kyverno --timeout=300s
```

**Part 5 — repeat under a hard crash**

Scaling to zero is a *clean* shutdown, and Kyverno removes its own webhooks on the way out. A real
crash is different: the pods die without cleaning up, so the webhooks stay registered and start
failing. That is a different code path in the API server, so the exemption must be proven under both.

```bash
# Break the image so pods CrashLoop - webhooks stay registered
kubectl -n kyverno set image deploy/kyverno-admission-controller \
  kyverno=mcr.microsoft.com/oss/kubernetes/pause:doesnotexist
kubectl -n kyverno get pods -w      # wait for 0 ready, then Ctrl-C

# Webhooks should STILL be present here - this is the difference from Part 3
kubectl get validatingwebhookconfigurations -l webhook.kyverno.io/managed-by=kyverno

kubectl apply -f tests/important-tool-deployment.yaml 2>&1 | tee results/step6a-exempt-crash.txt
# EXPECT: still ALLOWED
kubectl apply -f tests/violating-deployment.yaml 2>&1 | tee results/step6a-nonexempt-crash.txt
# EXPECT: still DENIED

# Restore
kubectl -n kyverno rollout undo deploy/kyverno-admission-controller
kubectl -n kyverno rollout status deploy/kyverno-admission-controller --timeout=300s
kubectl delete deploy important-tool -n vap-poc --ignore-not-found
```

Those four results — exempt and non-exempt, under both crash modes — are the answer to the question
the ticket asks.

#### 6b — Multiple exceptions combine correctly

```yaml
---
apiVersion: policies.kyverno.io/v1alpha1   # ← VERIFY on your cluster - see Section 5.4
kind: PolicyException
metadata: { name: skip-by-name, namespace: <ns> }
spec:
  policyRefs: [{ name: check-deployment-labels, kind: ValidatingPolicy }]
  matchConditions:
    - name: skip-by-name
      expression: "object.metadata.name == 'important-tool'"
---
apiVersion: policies.kyverno.io/v1alpha1   # ← VERIFY on your cluster - see Section 5.4
kind: PolicyException
metadata: { name: skip-by-namespace, namespace: <ns> }
spec:
  policyRefs: [{ name: check-deployment-labels, kind: ValidatingPolicy }]
  matchConditions:
    - name: skip-by-namespace
      expression: "namespaceObject.metadata.name == 'testing-ns'"
```

```bash
kubectl get validatingadmissionpolicy vpol-check-deployment-labels \
  -o jsonpath='{.spec.matchConditions}' | jq
# EXPECT: both present, both negated, ANDed together
```

#### 6c — ⚠️ Name-collision test (we expect this to break)

```yaml
# Two exceptions, SAME matchCondition name, same policy
- name: check-name
  expression: "object.metadata.name == 'tool-a'"
# and
- name: check-name
  expression: "object.metadata.name == 'tool-b'"
```

```bash
kubectl get vpol check-deployment-labels -o jsonpath='{.status}' | jq
kubectl get validatingadmissionpolicy vpol-check-deployment-labels -o yaml
kubectl logs -n kyverno -l app.kubernetes.io/component=admission-controller --tail=100 \
  | grep -i "matchcondition\|duplicate\|invalid"
```

**Record exactly what happens.** If the VAP is rejected or silently goes stale, we need a naming
convention — for example, `matchConditions[].name` must equal the exception's own name — enforced by
a policy on PolicyExceptions. **This is the most likely real-world failure at our exception volume.**

#### 6d — Fine-grained exceptions (`images` / `allowedValues`)

```bash
kubectl get validatingadmissionpolicy vpol-<name> -o jsonpath='{.spec.variables}' | jq
# EXPECT: allowedImages / allowedValues added as CEL lists
kubectl get validatingadmissionpolicy vpol-<name> -o jsonpath='{.spec.validations}' | jq
# EXPECT: "exceptions.allowedImages" rewritten to "variables.allowedImages"
```

Then repeat with **two** fine-grained exceptions on the same policy — both would try to add
`allowedImages`. Record whether they merge, collide, or one silently wins.

#### 6e — ⚠️ Stale-exception test (we expect this to fail closed)

```bash
kubectl scale deploy kyverno-admission-controller -n kyverno --replicas=0
kubectl apply -f tests/brand-new-exception.yaml
kubectl apply -f tests/newly-exempted-violating-deployment.yaml
# EXPECT: BLOCKED - the VAP knows nothing about the new exception
```

**What this means in practice:** during a Kyverno outage, exception grants are **frozen**. Teams
cannot self-serve an exemption until Kyverno recovers, and the workload is **blocked** rather than
admitted.

This **inverts** today's failure mode. Today an outage means everything gets through. After
conversion, an outage means converted policies are strictly enforced and you cannot grant an
exemption. That must go in the runbook and the write-up.

And remember Section 5.6: you **cannot** hand-edit the VAP as a workaround — Kyverno reverts it. The
supported paths are: restore Kyverno, delete the source policy, or pre-create a standalone
hand-written VAP that Kyverno does not own.

#### 6f — Replay our **real** exceptions during an outage

Everything above uses a synthetic exception on a synthetic policy. The acceptance criterion is about
**"existing PolicyException resources from previous iterations"**, so this step replays the real
ones. This is where silent breakage would hide.

```bash
# List every real exception and which policy it targets
kubectl get polex -A -o wide | tee results/step6f-exceptions.txt

# For each converted policy, show which exclusions actually made it into the VAP
for VAP in $(kubectl get validatingadmissionpolicy -l app.kubernetes.io/managed-by=kyverno \
             -o name | cut -d/ -f2); do
  echo "=== $VAP ==="
  kubectl get validatingadmissionpolicy "$VAP" -o jsonpath='{.spec.matchConditions}' | jq
done | tee results/step6f-vap-conditions.txt
```

**Reconcile the two lists.** Every exception that targets a converted policy must appear in that
policy's VAP. Anything missing is a workload that will be blocked during an outage.

| Exception | Namespace | Target policy | Policy converted? | Present in the VAP? | Verified during outage | Pass |
|---|---|---|---|---|---|---|
| | | | | | | ☐ |
| | | | | | | ☐ |

Now test the real exempt workloads, twice — once healthy, once during an outage. Server-side dry-run
runs the full admission chain **without creating anything**, so this is safe against production
manifests.

```bash
# Save this as a script - you will run it twice
cat > tests/check-exempt-workloads.sh <<'EOF'
#!/usr/bin/env bash
for f in tests/real-exempt-workloads/*.yaml; do
  printf '%-60s' "$(basename "$f")"
  if kubectl apply -f "$f" --dry-run=server >/dev/null 2>&1; then
    echo "ADMITTED"
  else
    echo "DENIED  <-- REGRESSION"
  fi
done
EOF
chmod +x tests/check-exempt-workloads.sh

# Run 1 - Kyverno healthy
./tests/check-exempt-workloads.sh | tee results/step6f-healthy.txt

# Run 2 - Kyverno down
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=0
done
kubectl wait --for=delete pod -l app.kubernetes.io/part-of=kyverno -n kyverno --timeout=180s
./tests/check-exempt-workloads.sh | tee results/step6f-outage.txt

# Restore
for d in admission-controller background-controller reports-controller cleanup-controller; do
  kubectl scale deploy kyverno-$d -n kyverno --replicas=<baseline>
done

# Compare - the two runs must be identical
diff results/step6f-healthy.txt results/step6f-outage.txt && echo "NO REGRESSION"
```

**Both runs must be 100% `ADMITTED`, and the diff must be empty.** A single `DENIED` in the outage
run is a production-blocking regression: that workload's team would be unable to deploy during a
Kyverno incident, and unable to grant themselves an exemption (see 6e).

#### 6g — Measure the danger window

Exceptions are compiled into the VAP **by Kyverno**, so there is a short period between "exception
created" and "exception actually enforced by the API server". If Kyverno dies inside that window,
the exemption is lost.

```bash
kubectl delete -f tests/exception.yaml --ignore-not-found; sleep 20

T0=$(date +%s)
kubectl apply -f tests/exception.yaml
until kubectl get validatingadmissionpolicy vpol-check-deployment-labels \
      -o jsonpath='{.spec.matchConditions}' 2>/dev/null | grep -q important-tool; do
  sleep 1
done
echo "danger window: $(( $(date +%s) - T0 )) seconds" | tee results/step6g-window.txt
```

**Observed:** ____ seconds

**Why this matters operationally.** If someone creates a break-glass exemption during a degrading
incident and Kyverno falls over a few seconds later, the exemption never reaches the API server —
and, per 6e, it cannot be created afterwards either. Put the measured number in the runbook, with
the instruction: *after granting an exemption, confirm it appears in the VAP before relying on it.*

```bash
# The confirmation command to put in the runbook
kubectl get validatingadmissionpolicy vpol-<policy> -o jsonpath='{.spec.matchConditions}' | jq
```

> If the `until` loop never finishes, that is itself a finding — the exception is **not** being
> compiled into the VAP. Stop, press Ctrl-C, and treat it as a failure of the mechanism check in 6a
> Part 1.

#### Exception feature matrix — fill this in

| Feature | Expected | Result | Notes |
|---|---|---|---|
| `matchConditions` on `object` | Translates (negated) | | |
| `matchConditions` on `namespaceObject` | Translates | | |
| `matchConditions` on `request` / `authorizer` | Should translate | | Confirm the CEL compiles |
| Multiple exceptions, distinct names | Combine via AND | | 6b |
| Multiple exceptions, **same name** | **Likely invalid VAP** | | 6c |
| `images[]` | → `variables.allowedImages` | | 6d |
| `allowedValues[]` | → `variables.allowedValues` | | 6d |
| Two exceptions both with `images[]` | **Likely collision** | | 6d |
| `reportResult` | Not translated | | Reporting only |
| Priority label | Not translated | | |
| Exception created during outage | Not applied → blocked | | 6e |
| **Exception honoured with Kyverno down** | **Honoured** | | 6a |

---

### Step 7 — Reports and monitoring

**What it proves:** we can still see what policies are doing.
**Why it matters:** Section 5.5 means converted policies **leave the Kyverno webhook entirely**. Any
dashboard built on Kyverno metrics may simply stop showing them. This is a **likely** regression,
not a hypothetical one.

```bash
kubectl get polr -A
kubectl get polr -n vap-poc -o yaml | grep -A5 "source:"
# Look for a source indicating ValidatingAdmissionPolicy

# API-server-side metrics for VAP
kubectl get --raw /metrics | grep -i validating_admission_policy
```

On AKS, VAP denials appear in the **`kube-audit` / `kube-audit-admin`** diagnostic log categories,
not in Kyverno metrics. Confirm the Diagnostic Setting is enabled, then query Log Analytics:

```kusto
AKSAuditAdmin
| where TimeGenerated > ago(1h)
| where log_s has "ValidatingAdmissionPolicy"
| project TimeGenerated, log_s
| take 50
```

[AKS monitoring docs](https://learn.microsoft.com/en-us/azure/aks/monitor-aks)

**New alerts we will need:**
- `apiserver_validating_admission_policy_check_total{enforcement_action="deny"}`
- An alert on the **absence** of an expected VAP object — a missing VAP is a silently unenforced policy.
- An alert on any policy where `autogen.validatingAdmissionPolicy.enabled: true` but
  `status.generated: false` (see the guardrail in Section 9).

---

### Step 8 — CI validation

**What it proves:** we catch mistakes before they reach a cluster.

```bash
kyverno version
kyverno apply ./policies/check-deployment-labels.yaml --resource ./test/violating-deployment.yaml
kyverno test ./test/

# Generated VAPs must be valid Kubernetes objects
kubectl apply --dry-run=server -f ./generated-vaps/
```

Add two chart-level guards:
1. `podControllers` and `validatingAdmissionPolicy` must never both be set.
2. `validationActions` must be set explicitly on every policy — never rely on the `Deny` default.

---

### Step 9 — Rollback

**What it proves:** we can undo this safely.
**Why it matters:** rehearse it on one policy **before** rolling out widely.

```bash
# Per policy
kubectl patch vpol <name> --type=merge \
  -p '{"spec":{"autogen":{"validatingAdmissionPolicy":{"enabled":false}}}}'
# Kyverno deletes the VAP and re-registers the webhook

# Cluster-wide
helm upgrade kyverno ... --set features.generateValidatingAdmissionPolicy.enabled=false
kubectl delete validatingadmissionpolicy -l app.kubernetes.io/managed-by=kyverno
kubectl delete validatingadmissionpolicybinding -l app.kubernetes.io/managed-by=kyverno
```

Rollback is clean, because the generated objects are labelled and owned. **Confirm the webhook
re-registers** before you declare rollback complete.

Test cleanup:

```bash
kubectl delete ns vap-poc --ignore-not-found
kubectl delete polex -n <exception-ns> skip-important-tool skip-by-name skip-by-namespace --ignore-not-found
```

Then resume GitOps:

```bash
flux resume helmrelease kyverno -n kyverno      # or re-enable Argo auto-sync
```

---

## 8. Results tables

| # | Test | Expected | Actual | Status |
|---|---|---|---|---|
| 1.1 | Violating resource blocked, Kyverno up | Denied | | ⬜ |
| 1.2 | Violating resource admitted, Kyverno down | Admitted (gap proven) | | ⬜ |
| 2.1 | VAP generation flags applied | Present in args | | ⬜ |
| 2.2 | Kyverno has VAP RBAC | `yes` / `yes` | | ⬜ |
| 2.3 | VAP engine active on this cluster (smoketest) | Denied | | ⬜ |
| 3.1 | Candidate audit complete | X of Y convertible | | ⬜ |
| 3.2 | Exception name collisions found | List | | ⬜ |
| 4.1 | `status.generated: true` | true | | ⬜ |
| 4.2 | `vpol-<name>` and `-binding` exist | Both present | | ⬜ |
| 4.3 | Generated VAP is `failurePolicy: Fail` | Fail | | ⬜ |
| 4.4 | Policy removed from Kyverno webhook | Rules absent vs baseline | | ⬜ |
| 4.5 | One denial message, not two | One, from the API server | | ⬜ |
| 4.6 | podControllers trap: VAP deleted | `generated: false`, VAP gone | | ⬜ |
| 5.1 | **VAP enforces with Kyverno down** | **Denied by the API server** | | ⬜ |
| 5.2 | Unconverted policy, same window | Admitted (control) | | ⬜ |
| 5.3 | Delete policy → VAP garbage-collected | VAP gone | | ⬜ |
| 5.4 | Hard crash (CrashLoop) — webhooks stay registered | Webhooks present, VAP still enforces | | ⬜ |
| 5.5 | Real workloads unaffected (dry-run) | All admitted | | ⬜ |
| 6.1 | Exception → negated matchCondition present in VAP | Present | | ⬜ |
| 6.2 | Exception honoured, Kyverno up | Allowed | | ⬜ |
| **6.3** | **Exception honoured, all 4 controllers scaled to 0** | **Allowed** | | ⬜ |
| **6.4** | **Non-exempt still denied, same outage window** | **Denied** | | ⬜ |
| **6.5** | **Exception honoured, hard crash (CrashLoop, webhooks still registered)** | **Allowed** | | ⬜ |
| **6.6** | **Non-exempt still denied, hard crash** | **Denied** | | ⬜ |
| 6.7 | Multiple exceptions combine | Both negated, ANDed | | ⬜ |
| 6.8 | Duplicate matchCondition names | **Expected to break** | | ⬜ |
| 6.9 | `images` / `allowedValues` → variables | Present | | ⬜ |
| 6.10 | Two fine-grained exceptions | Collision behaviour recorded | | ⬜ |
| 6.11 | Exception created during outage | Blocked (stale VAP) | | ⬜ |
| **6.12** | **All real exceptions present in their VAPs** | **100%** | | ⬜ |
| **6.13** | **All real exempt workloads admit, Kyverno healthy** | **100% ADMITTED** | | ⬜ |
| **6.14** | **All real exempt workloads admit, Kyverno down** | **100% ADMITTED, diff empty** | | ⬜ |
| 6.15 | Danger window measured | ____ seconds | | ⬜ |
| 7.1 | VAP results in policy reports | Visible | | ⬜ |
| 7.2 | VAP denials in our dashboards | **Likely gap** | | ⬜ |
| 9.1 | Rollback restores the webhook | Webhook re-registered | | ⬜ |

---

## 9. Limitations and risks

### Functional

- **Coverage is partial.** Only simple, self-contained CEL checks convert. Anything using Kyverno's
  CEL libraries, external data, image verification, or JSON mode stays on the webhook — and stays
  fail-open.
- **Pod-controller autogen is off the table** for converted policies. Every target resource must be
  listed in `matchConstraints`. This is the biggest authoring cost and the easiest thing to get
  silently wrong.
- **`validationActions` defaults to `Deny`.** Leaving it out means enforcing. Guard this in the chart.
- **Generated VAPs are always fail-closed.** A bad CEL expression in a converted policy blocks
  admission, with no webhook to fall back on.
- **Exceptions freeze during an outage** — and fail *closed*, blocking exempted workloads.
- **Exception naming becomes load-bearing.** Duplicate match-condition names across exceptions on
  one policy risk producing an invalid VAP.

### Operational

- **Converted policies leave the Kyverno webhook.** Good for latency and message clarity. It also
  means enforcement has a single definition point, and any generation failure moves it back to
  fail-open with a transition window in between.
- **VAP lifecycle is tied to the policy** via ownerReference. Deleting the policy removes enforcement.
- **Generated VAPs cannot be hand-edited.** Kyverno reverts manual changes. Break-glass must be
  planned differently.
- **Error messages change format.** Update runbooks and any user-facing documentation.
- **Monitoring will probably regress.** Verify dashboards before rollout.
- **`ready` is a misleading signal.** Alert on `status.generated`, not `ready`.

### Suggested guardrail

Once we have converted policies, add a check that fires when any policy has
`autogen.validatingAdmissionPolicy.enabled: true` but `status.generated: false`. That combination
means enforcement has silently fallen back to the fail-open webhook.

---

## 10. What changes for the on-call team

| Before | After conversion |
|---|---|
| Kyverno down → everything gets through | Converted policies still enforce; unconverted ones still get through |
| Deny message says `admission webhook "validate.kyverno.svc" denied...` | Converted policies say `ValidatingAdmissionPolicy 'vpol-...' denied...` |
| Exemption can be granted any time | **Exemption needs a healthy Kyverno.** During an outage, exemptions are frozen and the workload stays blocked |
| Fixing a policy = edit the policy | Same — but the change only reaches the API server once Kyverno reconciles it |
| Break-glass = create an exception | **Restore Kyverno first.** Do not hand-edit the VAP; it will be reverted |

**How to tell who blocked a request:**

| Source | Message looks like |
|---|---|
| Kyverno webhook | `admission webhook "validate.kyverno.svc-fail" denied the request: ...` |
| **ValidatingAdmissionPolicy** | `ValidatingAdmissionPolicy 'vpol-...' with binding '...' denied request: ...` |
| Azure Policy (Gatekeeper) | `admission webhook "validation.gatekeeper.sh" denied the request: [azurepolicy-...]` |

---

## 11. Acceptance criteria

| Criterion | Covered by | Evidence |
|---|---|---|
| All CEL-capable policies identified and listed as VAP candidates | Step 3 | Candidate register |
| Helm override added per policy; Kyverno generates a VAP and VAPBinding for each | Steps 2 and 4 | `status.generated: true` per policy; `vpol-*` inventory |
| Existing PolicyExceptions tested against generated VAPs and confirmed working | Step 6 | Exception matrix plus the Kyverno-down behavioural tests |

> **Correction for the ticket.** The ticket specifies the opt-in as `validate: cel: generate: true`.
> That is not a real field for this policy type. The correct opt-in is
> **`spec.autogen.validatingAdmissionPolicy.enabled: true`**, **plus**
> `spec.autogen.podControllers.controllers: []`. Worth updating the ticket.

---

## 12. Write-up outline

1. **Summary** — one paragraph, one number: "N of M policies convert; converted policies survive a
   total Kyverno outage, and their exceptions survive with them."
2. **The gap** — Step 1 output, pasted verbatim.
3. **The fix** — Step 5 output, pasted verbatim. Two terminal captures side by side make the whole
   argument.
4. **Candidate register** — the Step 3 table.
5. **Exception compatibility** — the Step 6 matrix. *This is what the ticket specifically asks to be
   documented.* Lead with the good news: exceptions **do** survive an outage. Then the caveats:
   naming collisions, and the freeze during an outage.
6. **The podControllers constraint** — give this its own section. It is the main authoring change and
   the main silent-failure risk.
7. **Limitations** — Section 9.
8. **Recommendation** — phased rollout: Audit-mode conversions first, close the monitoring gap, then
   switch to Deny.
9. **Runbook changes** — Section 10.

---

## 13. References and links

### Kyverno

**Core reading — start here**

- [ValidatingPolicy](https://kyverno.io/docs/policy-types/validating-policy/) — the policy type we use, and the `autogen` field that turns on VAP generation
- [Policy Exceptions](https://kyverno.io/docs/guides/exceptions/) — CEL-based exceptions, `policyRefs` and `matchConditions`
- [Migrating to CEL policies](https://kyverno.io/docs/guides/migration-to-cel/) — field-by-field conversion from the older `ClusterPolicy`, plus CEL troubleshooting
- [Installation and customization](https://kyverno.io/docs/installation/customization/) — container flags and Helm values
- [CEL libraries](https://kyverno.io/docs/policy-types/cel-libraries/) — Kyverno's **extra** CEL functions. Anything on this page will **not** work in a generated VAP (Section 5.4, failure mode 3)

**Reporting and tooling**

- [Policy Reports overview](https://kyverno.io/docs/policy-reports/) — how results are surfaced
- [ValidatingAdmissionPolicy reports](https://kyverno.io/docs/policy-reports/validatingadmissionpolicy-reports/) — reporting specifically for generated VAPs (Step 7)
- [Kyverno CLI](https://kyverno.io/docs/kyverno-cli/) — `kyverno apply` and `kyverno test` for the CI checks in Step 8
- [Kyverno Helm chart on ArtifactHub](https://artifacthub.io/packages/helm/kyverno/kyverno) — the full values reference for Step 2

**Other policy types**

- [MutatingPolicy](https://kyverno.io/docs/policy-types/mutating-policy/) — the mutation equivalent (Appendix B)
- [ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/) — image signature verification; **cannot** convert to a VAP

**Releases and issues**

- [Kyverno releases on GitHub](https://github.com/kyverno/kyverno/releases) — check what we actually run
- [Kyverno 1.18 release](https://github.com/kyverno/kyverno/releases/tag/v1.18.0) · [CNCF announcement](https://www.cncf.io/blog/2026/05/05/announcing-kyverno-release-1-18/)
- [Kyverno 1.17 release notes](https://kyverno.io/blog/2026/02/02/announcing-kyverno-release-1.17/) — CEL policy types reaching GA
- [Issue #13722 — VAP generation skipped when podControllers autogen is enabled](https://github.com/kyverno/kyverno/issues/13722) — the trap in Section 5.1

**Source code, for the behaviour described in Section 5**

- [`pkg/controllers/admissionpolicygenerator/`](https://github.com/kyverno/kyverno/tree/main/pkg/controllers/admissionpolicygenerator) — the generation controller
- [`pkg/admissionpolicy/`](https://github.com/kyverno/kyverno/tree/main/pkg/admissionpolicy) — the VAP builder, including exception translation
- [`test/conformance/chainsaw/generate-validating-admission-policy/`](https://github.com/kyverno/kyverno/tree/main/test/conformance/chainsaw/generate-validating-admission-policy) — the conformance tests that confirm the three generation rules

### Kubernetes

**The mechanism**

- [ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/) — the native object we are generating. GA since 1.30
- [MutatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/) — the mutation equivalent. **GA in 1.36**
- [Admission controllers overview](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/) — what the checkpoint is and the order things run in
- [Admission webhooks](https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/) — how today's Kyverno setup works, including `failurePolicy`

**CEL**

- [CEL in Kubernetes](https://kubernetes.io/docs/reference/using-api/cel/) — the expression language, available variables, and cost limits
- [CEL language definition](https://github.com/google/cel-spec/blob/master/doc/langdef.md) — the full syntax reference
- [Validation expression examples](https://kubernetes.io/docs/reference/using-api/cel/#examples) — useful patterns

**Operational**

- [Server-side dry-run](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run) — safe testing against real manifests (Step 5)
- [Owner references and garbage collection](https://kubernetes.io/docs/concepts/architecture/garbage-collection/) — why deleting a policy deletes its VAP (Section 5.6)
- [RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/) and [checking API access](https://kubernetes.io/docs/reference/access-authn-authz/authorization/#checking-api-access) — the `kubectl auth can-i` checks in Step 2
- [API server metrics](https://kubernetes.io/docs/reference/instrumentation/metrics/) — for the new alerts in Step 7
- [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) — what most of our upstream policies implement

**Coming next**

- [Manifest-based admission control (alpha in 1.36)](https://kubernetes.io/blog/2026/05/04/kubernetes-v1-36-manifest-based-admission-control/) — "Admission Policies That Can't Be Deleted" (Appendix B)
- [Kubernetes releases](https://kubernetes.io/releases/) — version and support timeline

### Azure / AKS

- [Monitor AKS](https://learn.microsoft.com/en-us/azure/aks/monitor-aks) — enabling diagnostics
- [AKS monitoring reference](https://learn.microsoft.com/en-us/azure/aks/monitor-aks-reference) — the `kube-audit` and `kube-audit-admin` log categories used in Step 7
- [Azure Policy for AKS](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/policy-for-kubernetes) — the Gatekeeper add-on, if enabled (Step 0)
- [AKS supported Kubernetes versions](https://learn.microsoft.com/en-us/azure/aks/supported-kubernetes-versions) — when 1.36 features become available to us
- [`az aks` CLI reference](https://learn.microsoft.com/en-us/cli/azure/aks) — the commands used in Step 0

### GitOps

Needed for the safety steps in Section 6.2, if Flux or Argo CD manages Kyverno.

- [Flux — suspend a HelmRelease](https://fluxcd.io/flux/cmd/flux_suspend_helmrelease/)
- [Argo CD — automated sync policy](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/)

---

## Appendix A — Useful CEL snippets

Common checks, ready to paste into a `validations[].expression`.

```cel
// No hostPath volumes
!object.spec.?volumes.orValue([]).exists(v, has(v.hostPath))

// No host namespaces
!object.spec.?hostNetwork.orValue(false) &&
!object.spec.?hostPID.orValue(false) &&
!object.spec.?hostIPC.orValue(false)

// No privileged containers (init and regular)
!object.spec.containers.exists(c, c.?securityContext.?privileged.orValue(false)) &&
!object.spec.?initContainers.orValue([]).exists(c, c.?securityContext.?privileged.orValue(false))

// Must run as non-root
object.spec.?securityContext.?runAsNonRoot.orValue(false) ||
object.spec.containers.all(c, c.?securityContext.?runAsNonRoot.orValue(false))

// A required label must exist and not be empty
has(object.metadata.labels) && 'team' in object.metadata.labels && object.metadata.labels['team'] != ''

// No :latest and no untagged images
!object.spec.containers.exists(c, c.image.endsWith(':latest') || !c.image.contains(':'))

// Every container must have CPU and memory limits
object.spec.containers.all(c, has(c.resources) && has(c.resources.limits) &&
  'memory' in c.resources.limits && 'cpu' in c.resources.limits)
```

> **Always guard optional fields** with `.?field.orValue(default)`. If an expression *errors* because
> a field is missing, `failurePolicy` decides what happens — and generated VAPs are always `Fail`,
> so an unguarded expression can block admission unintentionally.

---

## Appendix B — Worth flagging to the team

Two related items in Kubernetes 1.36. Both were verified on 1 August 2026.

### 1. MutatingAdmissionPolicy is now GA

[`MutatingAdmissionPolicy`](https://kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/)
became **generally available and enabled by default in Kubernetes 1.36**. It is the mutation
equivalent of everything in this document: CEL-based changes applied in-process by the API server,
with no webhook.

The same resilience argument therefore applies to our **mutate** rules, which this PoC does not
cover. Kyverno has a [`MutatingPolicy`](https://kyverno.io/docs/policy-types/mutating-policy/) type
and a `features.generateMutatingAdmissionPolicy` flag (defaults to off).

Worth a follow-up spike. Two things to carry over:
- **The same `podControllers` exclusion applies** (Section 5.1).
- Mutations run **before** validations, so a MAP can legitimately make a resource pass a VAP.

### 2. Manifest-based admission control (alpha)

Kubernetes 1.36 added an alpha feature described in the blog post
["Admission Policies That Can't Be Deleted"](https://kubernetes.io/blog/2026/05/04/kubernetes-v1-36-manifest-based-admission-control/).

Policies are written as files on disk and loaded by the API server **at startup, before it serves
any request**. They are configured through an `AdmissionConfiguration` file:

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: AdmissionConfiguration
plugins:
  - name: ValidatingAdmissionPolicy
    configuration:
      apiVersion: apiserver.config.k8s.io/v1
      kind: ValidatingAdmissionPolicyConfiguration
      staticManifestsDir: "/etc/kubernetes/admission/validating-policies/"
```

All such objects must have names ending in `.static.k8s.io`, so they are easy to spot in audit logs
and metrics.

This solves the two gaps this PoC cannot close:
- **The bootstrap window** — the period during cluster startup when no policy is active yet.
- **The "someone deleted the policy" risk** (test 5.3) — these policies cannot be removed through
  the API at all.

Not usable on AKS today, because it needs API server file-system access. But it shows where this is
heading, and it is worth mentioning to the team as the eventual answer.

---

## Appendix C — Quick command reference

```bash
# Did a policy generate a VAP? (the only signal that matters)
kubectl get vpol <name> -o jsonpath='{.status.generated}'

# All Kyverno-generated VAPs and bindings
kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding \
  -l app.kubernetes.io/managed-by=kyverno

# Policies that opted in but did NOT generate (the silent-failure state)
kubectl get vpol -o json | jq -r '
  .items[] | select(.spec.autogen.validatingAdmissionPolicy.enabled == true
                    and (.status.generated // false) == false)
  | .metadata.name'

# What a binding will actually do
kubectl get validatingadmissionpolicybinding vpol-<name>-binding \
  -o jsonpath='{.spec.validationActions}'

# See the exceptions compiled into a VAP
kubectl get validatingadmissionpolicy vpol-<name> -o jsonpath='{.spec.matchConditions}' | jq

# Test a manifest through the full chain without creating it
kubectl apply -f workload.yaml --dry-run=server

# API-server VAP metrics
kubectl get --raw /metrics | grep -i validating_admission_policy
```
