# Triage Commands Explained — What Each One Actually Tells You

Companion to `kyverno-vap-conversion-triage.md`.

---

## 0. The short answer

**Do these commands narrow down which policies fit VAP's execution model?**

**No — not on their own.** They answer two narrower questions:

1. *Does this policy do something structurally impossible in VAP?* (mutation, generation, network, cluster reads)
2. *Is it written in a language that isn't CEL?* (pattern / anyPattern / deny / podSecurity)

Those are useful and they remove the majority of your repo cheaply. But **execution-model fit is a
semantic property**, and grep is a text matcher. See §4 for exactly what it cannot see.

Treat Steps 1–3 as a **cheap pre-filter that produces a shortlist**, and Step 4 (Kyverno's own
verdict) as the thing that actually decides eligibility.

> **Shell note:** these are Unix-shell commands. On Windows, run them in Git Bash or WSL —
> `grep`, `comm`, and process substitution `<(...)` do not exist in PowerShell.

---

## 1. Command-by-command

### The grep flags used throughout

| Flag | Meaning |
|---|---|
| `-r` | recurse into the directory tree |
| `-l` | print **only the filenames** that contain a match, not the matching lines |
| `-L` | inverse of `-l` — print filenames with **no** match |
| `-E` | extended regex, so `\|` works as alternation (OR) |

`-l` is the important one: **output granularity is one line per file**, not per rule.

---

### Step 1 — structurally impossible

```bash
grep -rlE 'mutate:|generate:|verifyImages:|apiCall:|imageRegistry|externalData' policies/
```

**What it does:** lists every file containing any of six keywords.

**What you get:** the set of policies that can never be a VAP regardless of how they're written,
because VAP has no write path and no network.

| Token | What it catches | Why it disqualifies |
|---|---|---|
| `mutate:` | Kyverno mutation rules | VAP is validate-only |
| `generate:` | resource generation/cloning | no write path |
| `verifyImages:` | signature / attestation checks | requires registry network calls |
| `apiCall:` | `context` entries reading other cluster resources | CEL can only see `object`, `oldObject`, `namespaceObject`, `params` |
| `imageRegistry` | `context` registry lookups | network |
| `externalData` | **nothing — this is a Gatekeeper term** | remove it; it will never match a Kyverno policy |

**Confidence: high.** These are top-level YAML keys and rarely appear as false positives.

**Known flaws:**
- **File-granular.** A ClusterPolicy with one `mutate` rule and three convertible `validate.cel`
  rules is excluded wholesale. This is the single biggest inaccuracy in the grep approach.
- Matches inside comments and `message:` strings.
- `apiCall:` appears under `context:` — but Kyverno also allows `globalReference` and `variable`
  context entries that can smuggle in external state. Not covered by this pattern.

**Improved version — add the missing context types, drop the dead token:**

```bash
grep -rlE 'mutate:|generate:|verifyImages:|apiCall:|imageRegistry:|globalReference:' policies/
```

---

### Step 2 — not actually CEL

```bash
grep -rlE 'pattern:|anyPattern:|podSecurity:|deny:' policies/
```

**What it does:** finds validation rules written in Kyverno's *own* expression languages rather
than CEL.

**What you get:** the rewrite backlog. These are convertible **in principle** but every one needs
its logic re-expressed in CEL first. For most Kyverno estates this is the largest pile and the
dominant cost of the whole initiative.

| Token | Language |
|---|---|
| `pattern:` | Kyverno pattern DSL with anchors `+()`, `=()`, `X()`, `^()` |
| `anyPattern:` | same, OR-combined |
| `deny:` | `validate.deny.conditions` — **JMESPath**, not CEL |
| `podSecurity:` | Kyverno's Pod Security Standards integration |

**Confidence: medium-high**, with real false positives:

- `deny:` also appears in unrelated contexts and in RBAC-flavoured message text.
- `pattern:` is a substring risk — it will not match `anyPattern:` (capital `P`), which is why
  both are listed, but it *will* match any custom key ending in `pattern:`.
- Case sensitivity means a stray `Pattern:` slips through.

---

### Step 3 — the shortlist

```bash
grep -rl 'cel:' policies/
```

**What it does:** finds files containing a `cel:` key.

**The bug:** this is an **independent** grep. It does not subtract Steps 1 and 2, so a file with
both a `mutate` rule and a `validate.cel` rule appears in Step 1 *and* Step 3. The doc implied
set subtraction but never performed it.

**Corrected — actual set subtraction:**

```bash
comm -23 \
  <(grep -rl 'cel:' policies/ | sort -u) \
  <(grep -rlE 'mutate:|generate:|verifyImages:|apiCall:|imageRegistry:|globalReference:' policies/ | sort -u)
```

`comm -23` prints lines unique to the first input — i.e. files that contain CEL **and** none of
the structural disqualifiers.

**Still file-granular.** Which is why the next section exists.

---

## 2. What you actually want: per-rule triage

Eligibility is a property of a **rule**, not a file. This produces one CSV row per rule and
replaces Steps 1–3 entirely. Requires `yq` (v4) and `jq`.

```bash
find policies -name '*.yaml' -exec yq -o=json -I=0 '.' {} \; \
| jq -r '
  select(.kind=="ClusterPolicy" or .kind=="Policy")
  | .metadata.name as $policy
  | (.spec.rules | length) as $rulecount
  | .spec.rules[]
  | [ $policy,
      .name,
      $rulecount,
      (if   .mutate                then "mutate"
       elif .generate              then "generate"
       elif .verifyImages          then "verifyImages"
       elif .validate.cel          then "validate.cel"
       elif .validate.pattern      then "validate.pattern"
       elif .validate.anyPattern   then "validate.anyPattern"
       elif .validate.deny         then "validate.deny"
       elif .validate.podSecurity  then "validate.podSecurity"
       else "other" end),
      ([.context[]? | keys[]] | unique | join("+")),
      (if .preconditions then "yes" else "no" end),
      ([.match.any[]?.resources.kinds[]?, .match.all[]?.resources.kinds[]?] | unique | join("+"))
    ] | @csv'
```

**Columns:** policy · rule · rule-count · rule type · context types used · has preconditions ·
matched kinds.

**Why this beats grep:**

- Per-rule, so mixed-content policies are classified correctly.
- The **rule-count** column flags multi-rule policies that need splitting (one VAP per rule).
- The **context** column tells you *which* external input is needed — `configMap` is reworkable
  into `paramRef`, `apiCall` is a hard stop. Grep collapsed that distinction.
- The **matched kinds** column is your autogen signal: a rule matching only `Pod` is one whose
  controller coverage depends entirely on autogen (§3, Step 5).

Pipe it to a file and it *is* the inventory table from the main doc.

---

## 3. The cluster-side steps

### Step 4 — Kyverno adjudicates

```bash
kubectl get validatingadmissionpolicies
```

**What it does:** lists VAPs that exist in the cluster.

**What it gives you:** on its own, just names — it does **not** tell you which Kyverno policy
produced each one, nor why anything was skipped. Kyverno sets an owner reference on the VAPs it
generates, so map them back:

```bash
kubectl get validatingadmissionpolicies \
  -o custom-columns='VAP:.metadata.name,OWNER:.metadata.ownerReferences[0].name'
```

**The actual diff — policies that produced no VAP:**

```bash
comm -23 \
  <(kubectl get clusterpolicy -o name | sed 's|.*/||' | sort -u) \
  <(kubectl get validatingadmissionpolicies \
      -o jsonpath='{.items[*].metadata.ownerReferences[0].name}' | tr ' ' '\n' | sort -u)
```

**Then get the reason** for each name on that list:

```bash
kubectl describe clusterpolicy <name>
```

The skip reason appears in `.status` conditions and/or emitted events. **This is the authoritative
eligibility verdict** — it reflects your exact Kyverno version's rules, which no checklist can.

*Prerequisites:* VAP generation enabled via the Kyverno Helm feature flag, and Kubernetes 1.30+.
If generation is off, this command returns nothing and tells you nothing.

---

### Step 5 — autogen coverage

Sweep every generated VAP and print the kinds it actually matches:

```bash
kubectl get validatingadmissionpolicies -o json | jq -r '
  .items[]
  | [ .metadata.name,
      ([.spec.matchConstraints.resourceRules[].resources[]] | unique | join("+")) ] | @tsv'
```

**What to look for:** any VAP originating from a Pod rule whose resource list is *only* `pods`.
In Kyverno, that rule silently also covered Deployments, StatefulSets, DaemonSets, Jobs and
CronJobs via autogen. If the generated VAP does not list those kinds, **you have lost controller
coverage** — the policy will pass admission on a Deployment that Kyverno would have blocked.

That is a silent security regression, not a cosmetic gap. Test it explicitly with a deliberately
non-compliant Deployment before retiring any Kyverno policy.

---

### Missing step — PolicyException reliance

Not in the original list and not grep-detectable, because exceptions live in **separate objects**:

```bash
kubectl get polex -A
```

Cross-reference the referenced policy/rule names against your shortlist. Any policy with an active
exception needs an approximation designed before conversion — VAP has no equivalent construct.

---

## 4. Coverage: what the grep pass can and cannot see

| VAP execution-model constraint | Detectable by grep? | Notes |
|---|---|---|
| No mutation / generation | **Yes** | top-level keys, high confidence |
| No network calls | **Yes** | `verifyImages`, `imageRegistry` |
| No cluster reads | **Mostly** | `apiCall` yes; `globalReference` only if you add it |
| Expression language isn't CEL | **Yes, file-granular** | the large rewrite pile |
| Multi-rule policy needs splitting | **No** | `grep -l` cannot count rules → use §2 |
| ConfigMap context → reworkable to params | **Partly** | grep can't distinguish reworkable from fatal context |
| CEL cost-budget overruns | **No** | runtime-only; surfaces as admission failures under load |
| Autogen dependence | **No** | it's an *absence*, not a keyword → §2 + Step 5 |
| PolicyException reliance | **No** | lives in separate CRs entirely |
| Background-scan / reporting dependence | **No** | operational property, not textual |
| Regex needing lookahead/backrefs | **No** | requires reading the expression |

**Reading of this table:** the grep pass reliably removes the *impossible*. It does not identify
the *fit*. Everything in the lower half of the table requires the per-rule extract (§2), the
cluster verdict (Step 4), or human review.

---

## 5. Recommended sequence

1. Run the **per-rule extract** (§2) → this is your inventory skeleton, correct at rule
   granularity. Skip the three greps entirely; they are a strictly weaker version of it.
2. Bucket by rule type: `mutate`/`generate`/`verifyImages` → not-convertible.
   `pattern`/`anyPattern`/`deny`/`podSecurity` → rewrite backlog. `validate.cel` → shortlist.
3. Flag every rule with `context: apiCall` (fatal) vs `context: configMap` (reworkable to params).
4. Flag every multi-rule policy for splitting.
5. Enable VAP generation in **non-prod**, apply everything, run the **Step 4 diff** → capture
   Kyverno's stated reason for each non-generated policy. Reconcile against your predictions;
   where they disagree, Kyverno is right.
6. Run the **Step 5 autogen sweep** and test a non-compliant Deployment per Pod-scoped policy.
7. Check `PolicyException` usage and design replacements.
8. Only then estimate effort.

Steps 5–7 are where the real answer to "does it fit the execution model" comes from. Steps 1–4
just make that list short enough to be worth running.
