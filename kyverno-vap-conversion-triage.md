# VAP Conversion Triage — Identifying Candidate Policies (Kyverno)

**Goal:** produce a defensible inventory of all custom and upstream policies, classified as
convertible / reworkable / not-convertible to Kubernetes `ValidatingAdmissionPolicy` (VAP).

---

## 1. Reframe the selection criterion

"Which policies are CEL-capable?" is the wrong filter and will over-count candidates.
Almost any *validation predicate* can be written in CEL — that's why "most of them are CEL-based"
feels true.

The real question is: **which policies fit VAP's execution model?**

VAP runs a pure function inside kube-apiserver over a fixed input set:

| Available in CEL | Notes |
|---|---|
| `object` | the admitted object (null on DELETE) |
| `oldObject` | previous state on UPDATE — use for immutability rules |
| `request` | AdmissionRequest attrs, incl. `request.userInfo` |
| `params` | resolved via `paramKind` + `paramRef` on the binding |
| `namespaceObject` | the namespace of the admitted object |
| `authorizer` | RBAC checks from within CEL |
| `variables` | named, reusable sub-expressions |

**Nothing else.** No cluster reads, no network, no state, no writes.

So triage on **inputs and side effects**, not on expression syntax.

---

## 2. Kyverno-specific reality check (read this first)

### 2.1 "CEL-based" in Kyverno means exactly one thing

Only rules written as `validate.cel` are eligible for VAP generation. These are **not** CEL and
must be rewritten before conversion is even possible:

- `validate.pattern` / `validate.anyPattern` — Kyverno's pattern-matching DSL with anchors
  (`+()`, `=()`, `X()`, `^()`)
- `validate.deny.conditions` — JMESPath, not CEL
- `validate.podSecurity` — Kyverno's PSS integration
- Anything using Kyverno variables (`{{ request.object.… }}`, `{{ @ }}`) or JMESPath
  functions (`to_string`, `split`, `regex_match`, `semver_compare`, …)

> **Expect the eligible set to be much smaller than "most of them."** The dominant work item in
> this initiative is likely *rewriting pattern/deny logic into CEL*, which is a prerequisite to
> VAP conversion, not part of it.

### 2.2 Let Kyverno generate the inventory for you

Kyverno auto-generates a `ValidatingAdmissionPolicy` + `ValidatingAdmissionPolicyBinding` for
eligible policies, and **skips ineligible ones with a stated reason**. That refusal list is your
incompatibility inventory and is authoritative over any hand-written checklist.

- The feature is gated behind a flag on the Kyverno admission controller
  (Helm feature-flag value — confirm the exact key for your chart version).
- Requires Kubernetes **1.30+** for GA VAP (1.28/1.29 = beta, behind a feature gate).
- Read the verdicts from the ClusterPolicy `.status` / conditions and the events Kyverno emits,
  plus `kubectl get validatingadmissionpolicies` to see what actually materialised.

Eligibility rules are **version-specific** — broadly: a single rule, `validate.cel`, a simple
match block, and no unsupported constructs. Take the controller's verdict over any doc summary.

### 2.3 Consider `ValidatingPolicy` as the target instead of raw VAP

If you are on Kyverno **1.14+**, there is a newer CEL-native `ValidatingPolicy` CRD that compiles
down to VAP while retaining Kyverno-side features (exceptions, reporting, background scan).
For many teams this is a better migration target than hand-authored VAPs — you get the
apiserver-side enforcement without losing the operational surface.

**Verify what your Kyverno version offers before locking the design.** This decision changes the
shape of the whole initiative.

### 2.4 Two Kyverno traps that silently reduce coverage

**Autogen.** Kyverno automatically expands a Pod-scoped rule to Deployment / StatefulSet /
DaemonSet / Job / CronJob (`pod-policies.kyverno.io/autogen-controllers`). Raw VAP has no
equivalent — you must match the controller kinds yourself and reach into
`object.spec.template.spec` (and `spec.jobTemplate.spec.template.spec` for CronJob).

*Action:* confirm whether your Kyverno version propagates autogen into the generated VAPs. If it
does not, every converted Pod policy silently loses controller coverage. **Test this explicitly.**

**PolicyException.** There is no VAP equivalent. Any policy currently relying on
`PolicyException` objects needs an approximation designed before conversion (see §4).

---

## 3. Triage rubric

Work down the list; the first hit decides the bucket.

| Signal in the policy | Bucket | Why |
|---|---|---|
| `mutate:` — patches, defaults, `patchStrategicMerge`, `patchesJson6902` | **Not VAP** | VAP is validate-only. MutatingAdmissionPolicy is a separate, much newer feature — check your cluster version before relying on it. |
| `generate:` — creates or clones resources | **Not VAP** | No write path. |
| `verifyImages:` — signatures, attestations, SBOM, registry lookups | **Not VAP** | No network access from CEL. |
| `context:` with `apiCall` — reads another cluster resource | **Not VAP** | Only `namespaceObject` and `paramRef` are reachable. |
| Cross-object logic — uniqueness, counting, quota, "does the referenced SA/ConfigMap exist", "is there already a PDB" | **Not VAP** | Stateless, single-object evaluation. |
| Time-based or stateful logic | **Not VAP** | No clock, no state. |
| Exists mainly for background scan / policy reports on already-admitted resources | **Not VAP (or keep both)** | VAP is admission-time only. Converting and removing the Kyverno policy loses compliance reporting. |
| `validate.pattern` / `anyPattern` / `deny` + JMESPath / `podSecurity` | **Rework → CEL first** | Not CEL. Rewrite before conversion is possible. See §2.1. |
| Regex with lookahead / backreferences | **Rework** | CEL `matches()` is RE2 only. |
| `context:` with `configMap` lookups for per-tenant config | **Rework → params** | Convert to `paramKind` + `paramRef`, one policy + N bindings. ConfigMap values are strings — parsing in CEL is limited. |
| Per-namespace / per-tenant values baked into the rule body | **Rework → params** | Same as above. |
| Multiple rules in one ClusterPolicy | **Rework → split** | One VAP per coherent rule; bindings handle scoping. |
| Relies on `PolicyException` | **Rework** | No native equivalent — see §4. |
| Relies on autogen for controller coverage | **Verify** | See §2.4. |
| Deep nested iteration (containers × env × volumeMounts × …) on large objects | **Verify cost** | Per-expression and per-policy CEL cost budgets will reject some of these *at admission time*. |
| Pure field-shape assertion on the incoming object — labels, resource limits, securityContext, hostPath, image prefix, allowed capabilities, ingress class | **Convert** | VAP's sweet spot. |
| Immutability — "field X may not change after creation" | **Convert** | `oldObject` handles this cleanly. |
| "Only user/group X may do Y" | **Convert** | `request.userInfo` + the `authorizer` library. |

---

## 4. Construct mapping (Kyverno → VAP)

| Kyverno | VAP |
|---|---|
| `match` / `exclude` resources | `matchConstraints` (`resourceRules` / `excludeResourceRules`) |
| `match` namespace selectors | `matchConstraints.namespaceSelector` / `objectSelector` |
| `preconditions` | `matchConditions` (max 64) |
| `validate.cel.expressions` | `validations` |
| `validate.message` (with variable substitution) | `messageExpression` — CEL, returns a plain string; more restricted |
| `validationFailureAction: Enforce` | binding `validationActions: [Deny]` |
| `validationFailureAction: Audit` | binding `validationActions: [Audit]` (± `Warn`) |
| `context.configMap` | `paramKind: ConfigMap` + `paramRef` |
| `context.apiCall` | **no equivalent** |
| `PolicyException` | **no equivalent** — approximate with binding label/namespace selectors, `matchConditions`, an `authorizer` check, or an exclusion list carried in params |
| Autogen for Pod controllers | **no equivalent** — must be expressed explicitly (verify §2.4) |
| Namespaced `Policy` | VAP is cluster-scoped; scope via the binding |
| Policy reports / background scan | **no equivalent** — Kyverno-side only |

---

## 5. Practical first pass

Do **not** read policies one at a time. The disqualifiers are syntactically loud — grep for them.

**Step 1 — carve out the structurally impossible:**

```bash
grep -rlE 'mutate:|generate:|verifyImages:|apiCall:|imageRegistry|externalData' policies/
```

**Step 2 — carve out the not-actually-CEL rules (this is the big pile):**

```bash
grep -rlE 'pattern:|anyPattern:|podSecurity:|deny:' policies/
```

**Step 3 — what remains is the eligible shortlist:**

```bash
grep -rl 'cel:' policies/
```

**Step 4 — let Kyverno adjudicate.** Enable VAP generation in a non-production cluster, apply the
full policy set, and diff intent against reality:

```bash
kubectl get validatingadmissionpolicies
```

Anything in your shortlist that did *not* produce a VAP has a reason attached — capture it.

**Step 5 — check autogen coverage.** For each generated VAP originating from a Pod rule, confirm
its `matchConstraints` include the controller kinds. If not, log it as extra work per policy.

---

## 6. Inventory schema

Make the deliverable a table, not a flat list — "candidate" alone won't survive review.

| Column | Values |
|---|---|
| Policy name | |
| Source | custom / upstream |
| Rule type | `validate.cel` / `pattern` / `deny` / `podSecurity` / `mutate` / `generate` / `verifyImages` |
| Action type | validate / mutate / generate / verify |
| External inputs needed | none / configMap / apiCall / registry |
| **Verdict** | convert / rework / not-convertible |
| Blocking reason | free text — required for rework + not-convertible |
| Needs params? | yes / no |
| Uses autogen? | yes / no — and whether coverage is preserved |
| Uses PolicyException? | yes / no |
| Keep original for background scan? | yes / no |
| Effort estimate | S / M / L |

---

## 7. Scope notes to state up front

These change the size of the initiative and should be agreed before work starts:

1. **Conversion is not a swap.** Expect a bake-in period running both: the generated VAP in
   `Audit` mode alongside existing Kyverno enforcement, comparing decisions before flipping to
   `Deny` and retiring the original.
2. **Reporting is not covered by VAP.** Any policy you rely on for PolicyReports must keep a
   Kyverno-side representation regardless of VAP conversion.
3. **Rewriting `pattern`/`deny` rules into CEL is a prerequisite workstream,** not part of VAP
   conversion. Size it separately — it is likely the majority of the effort.
4. **Cluster version gates everything.** VAP is GA at Kubernetes 1.30+. Confirm every target
   cluster before committing to a timeline.
5. **`ValidatingPolicy` vs raw VAP** (§2.3) is an architectural decision that should be made
   before any conversion work begins.
