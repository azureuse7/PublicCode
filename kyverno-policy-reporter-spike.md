# Kyverno Policy Reporter — implementation & cross-cluster spike

*Timeboxed spike: verify Policy Reporter's capability to monitor Kyverno policies, scope, status, and violations, and assess suitability as a compliance/audit reporting pipeline.*

## 1. Install Policy Reporter

If your egress policy blocks direct pulls from the public internet, proxy this chart through your internal Artifactory instance rather than pulling `kyverno.github.io` directly.

Policy Reporter v3 is one chart — core, UI, and Kyverno plugin all behind feature flags.

```yaml
# policy-reporter-values.yaml
ui:
  enabled: true

metrics:
  enabled: true          # Prometheus endpoint — see §5, Dynatrace note

plugin:
  kyverno:
    enabled: true
    blockReports:
      enabled: true       # required — see note below
      eventNamespace: ""  # empty = watch every namespace, not just `default`
```

```bash
helm repo add policy-reporter https://kyverno.github.io/policy-reporter
helm repo update

helm install policy-reporter policy-reporter/policy-reporter \
  --create-namespace -n policy-reporter \
  -f policy-reporter-values.yaml

# pin a version rather than floating latest:
# helm search repo policy-reporter/policy-reporter --versions
```

**Why `blockReports` matters here:** Kyverno's reports controller only writes PolicyReport entries for audit-mode results and background scans by default. The plugin backfills entries for enforce-mode blocks too, built from the Kubernetes Events Kyverno fires when it denies something. Given the fail-closed/enforce work on the Gatekeeper migration, a real chunk of actual violations are requests blocked at admission that never produced a report — skip `blockReports` and the dashboard under-counts them. `eventNamespace` defaults to watching only `default`; set it to `""` since policies aren't scoped that narrowly.

## 2. Pulling images through your internal JFrog registry

Before overriding anything: check whether your AKS node pool already has a containerd registry mirror configured for `ghcr.io`, or a Kyverno mutate policy that rewrites image registries at admission. If either exists cluster-wide, pods pull from Artifactory transparently and none of the below is needed.

If not, there's no single global "use my registry" switch in this chart — each component's image block needs pointing at Artifactory separately. Confirmed defaults, straight from the chart's `values.yaml`:

| Component | Default registry | Default repository |
|---|---|---|
| Core | `ghcr.io` | `kyverno/policy-reporter` |
| UI | `ghcr.io` | `kyverno/policy-reporter-ui` |
| Kyverno plugin | `ghcr.io` | `kyverno/policy-reporter-kyverno-plugin` *(inferred from the project's naming convention — confirm exact string per the tip below)* |

```yaml
image:
  registry: <your-jfrog-host>
  repository: <your-docker-remote-repo-key>/kyverno/policy-reporter
  # tag: ~   # leave as-is to track the chart's appVersion, or pin explicitly

imagePullSecrets:
  - name: jfrog-pull-secret

ui:
  enabled: true
  image:
    registry: <your-jfrog-host>
    repository: <your-docker-remote-repo-key>/kyverno/policy-reporter-ui

plugin:
  kyverno:
    enabled: true
    image:
      registry: <your-jfrog-host>
      repository: <your-docker-remote-repo-key>/kyverno/policy-reporter-kyverno-plugin
```

Swap `<your-docker-remote-repo-key>` for whatever repo key your Artifactory Docker remote/virtual repo uses — same pattern you're already pulling other chart images through.

**If that Artifactory repo needs credentials to pull:**

```bash
kubectl create secret docker-registry jfrog-pull-secret \
  --namespace policy-reporter \
  --docker-server=<your-jfrog-host> \
  --docker-username=<service-account> \
  --docker-password=<api-key-or-identity-token>
```

The top-level `imagePullSecrets` is confirmed against the chart source. Whether the UI and plugin pods inherit that automatically or need their own copy under `ui.imagePullSecrets` / `plugin.kyverno.imagePullSecrets` isn't confirmed from what's available online — check with the tip below.

**Before trusting any of the repository strings above** — including the plugin one, which is the least certain — dump the chart's actual current defaults yourself:

```bash
helm show values policy-reporter/policy-reporter > /tmp/pr-defaults.yaml
grep -B2 -A4 "^image:\|  image:" /tmp/pr-defaults.yaml
```

That gives the exact registry/repository/tag for every component on the chart version actually in use, and settles the imagePullSecrets nesting question above.

## 3. Confirm it's seeing your policies

```bash
kubectl get pods -n policy-reporter
kubectl get clusterpolicyreport
kubectl get policyreport -A
kubectl port-forward -n policy-reporter svc/policy-reporter-ui 8082:8080
# → http://localhost:8082
```

Reports populate on the next background-scan interval, not instantly. The Kyverno plugin tab adds each policy's description, rules, and full YAML alongside the live results.

## 4. Acceptance criteria mapping

| Criterion | Verdict | Notes |
|---|---|---|
| Surface policies (controls) | Yes | Kyverno plugin lists every policy with rules and YAML |
| Resource scope | Yes | Match/exclude block per policy, plus which resources actually appear in results |
| Enforce / audit / exempt status | Partial | Audit + background scan native; enforce needs `blockReports`; exemptions typically surface as `skip`-status results — confirm explicitly against a known PolicyException |
| Violations, counts, drill-down | Yes, same caveat | Per-resource drill-down works well once `blockReports` is on |
| Dashboards | Yes | Built-in UI, optional Grafana subchart, Prometheus metrics |

### Key finding: VPOL / VAP-autogen reporting gap

There's an open upstream gap specific to the VPOL/VAP-autogen rollout. For policies where autogen has produced a native Kubernetes ValidatingAdmissionPolicy, admission control itself works fine — the native VAP blocks or allows correctly — but nothing writes a corresponding PolicyReport, ClusterPolicyReport, or EphemeralReport entry for that decision, even with the reports controller's dedicated flag for this turned on ([kyverno/kyverno#16153](https://github.com/kyverno/kyverno/issues/16153), reported against Kyverno 1.18 on AKS/EKS).

Policy Reporter isn't entirely blind to the newer engine — its CustomBoard config recognises `KyvernoValidatingPolicy` as a distinct source alongside `kyverno` — so this is specifically the autogenerated-native-VAP path, not VPOL support in general.

**Suggested test:** given the `kubectl get vpol` READY true/false split, point Policy Reporter at a couple of READY=true policies (autogen likely succeeded — native VAP doing the evaluating) versus READY=false ones, and check whether reports show up for both.

## 5. The cross-cluster question

Short answer: partially.

| | Policy Reporter UI (Multi Tenant) | Shared target store |
|---|---|---|
| What it is | One central UI, dropdown to switch between clusters | Every cluster pushes results to Loki / Elasticsearch / S3 / etc; you build the view on top |
| Gives you | Convenience — one URL, one login | A true cross-cluster rollup (e.g. total violations across the estate) |
| Built in? | Yes, ships with the chart | No — you assemble it |
| Cost | Each remote cluster's REST API must be reachable from the central UI (VPN/private networking only, Basic Auth) | New pipeline to build and maintain |

**Multi Tenant config** (on the central Policy Reporter UI):

```yaml
ui:
  clusters:
    - name: AKS Prod
      host: http://policy-reporter.policy-reporter.svc:8080
      plugins:
        - name: kyverno
          host: http://policy-reporter-kyverno-plugin.policy-reporter.svc:8080
    - name: EKS Prod
      host: https://policy-reporter.eks-cluster.internal
      basicAuth:
        username: ...
        password: ...
      plugins:
        - name: kyverno
          host: https://kyverno-plugin.eks-cluster.internal
```

Each remote cluster needs `rest.enabled: true`. The docs are explicit that these REST APIs must never be reachable from outside — VPN, private networking, or an internal load balancer only. Same class of problem as the pre-DNAT/konnectivity routing already solved for NetworkPolicy across AKS and EKS — solvable, but cross-team networking work, not a Helm flag.

**Important for the writeup:** Multi Tenant switches context, it doesn't roll up. There's no single "total violations across the estate" figure — you click into each cluster separately. If "suitability for an audit reporting pipeline" (CECPT-1971) needs a genuine rollup, the realistic path is the shared-target route.

Given the existing Dynatrace DQL dashboard work for Kyverno metrics, the most direct shared-target route is probably the Prometheus endpoint rather than a new target — it exposes `policy_report_result` and `cluster_policy_report_result` as gauges labelled by namespace, policy, category, severity, source and status, which is exactly the shape Dynatrace's Prometheus scraping wants. One query layer, no new target to build.

## Shape for the timebox

- **Day 1** — install per above, confirm the four description bullets against the live cluster, screenshot the UI
- **Day 2** — turn on `blockReports`, run the VAP-autogen READY-true-vs-false test, document the gap
- **Day 3** — stand up Multi Tenant against a second cluster (or document the pattern + the networking ask if a second cluster isn't practical in the timebox), sketch what a shared-target rollup would take
- **Writeup** — the scoring table above, screenshots, a clear yes/no/with-caveats on CECPT-1971 suitability

## References

- [Policy Reporter — Installation](https://kyverno.github.io/policy-reporter-docs/getting-started/installation.html)
- [Policy Reporter — Helm Chart](https://kyverno.github.io/policy-reporter-docs/getting-started/helm.html)
- [Policy Reporter — Kyverno Plugin](https://kyverno.github.io/policy-reporter-docs/plugin-system/kyverno-plugin.html)
- [Policy Reporter — Multi Tenant](https://kyverno.github.io/policy-reporter-docs/policy-reporter-ui/multi-tenant.html)
- [Policy Reporter — Metrics](https://kyverno.github.io/policy-reporter-docs/policy-reporter/metrics.html)
- [Policy Reporter chart values.yaml](https://github.com/kyverno/policy-reporter/blob/main/charts/policy-reporter/values.yaml)
- [kyverno/kyverno#16153 — PolicyReports not generated for generated ValidatingAdmissionPolicy](https://github.com/kyverno/kyverno/issues/16153)
