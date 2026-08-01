# CPU Requests vs CPU Limits in Kubernetes

## CPU Requests

A **CPU request** is the amount of CPU a container is **guaranteed** to receive.

- Used by the Kubernetes **scheduler** to decide which node to place the Pod on
- The scheduler only places a Pod on a node that has **at least this much CPU available**
- The container is **always guaranteed** this amount — even under load from other containers
- Think of it as: *"I need at least this much CPU to function correctly"*

```yaml
resources:
  requests:
    cpu: "250m"   # 250 millicores = 0.25 of one CPU core
```

---

## CPU Limits

A **CPU limit** is the **maximum** amount of CPU a container is allowed to use.

- Enforced by the Linux kernel's **CFS (Completely Fair Scheduler) quota**
- If a container tries to use more CPU than its limit, it gets **throttled** (slowed down)
- It does NOT get killed — unlike memory limits where OOM kill happens
- Think of it as: *"You are not allowed to use more than this, even if the node has free CPU"*

```yaml
resources:
  limits:
    cpu: "500m"   # 500 millicores = 0.5 of one CPU core
```

---

## Side-by-Side Comparison

| Aspect | Request | Limit |
|---|---|---|
| Purpose | Scheduling guarantee | Hard cap on usage |
| Who uses it | Kubernetes scheduler | Linux kernel (cgroup) |
| What happens if exceeded | N/A — it's a floor, not a cap | Container is **throttled** |
| Node selection | Yes — node must have enough | No effect on scheduling |
| Overcommittable | Yes | Yes |

---

## How They Work Together

```yaml
resources:
  requests:
    cpu: "250m"
  limits:
    cpu: "1000m"
```

- The container is **guaranteed 250m** — scheduler reserves this on the node
- The container **can burst up to 1000m** if the node has idle CPU
- If it tries to go beyond 1000m, the kernel throttles it

---

## CPU Units

| Unit | Meaning |
|---|---|
| `1` | 1 full CPU core (or 1 vCPU on cloud) |
| `1000m` | 1 CPU core (m = millicores) |
| `250m` | 0.25 of a core |
| `100m` | 0.1 of a core — minimum meaningful unit |

---

## Key Behaviors to Know

**Overcommitment:**
A node with 4 CPUs can have Pods with total requests of 4 CPUs but total limits of 8 CPUs — because limits are only enforced when actually used.

**No limit set:**
If you omit `limits.cpu`, the container can use all available CPU on the node, potentially starving other Pods.

**Request > Limit:**
Invalid — Kubernetes will reject the Pod spec.

**CPU throttling vs OOM:**
- CPU over-limit = throttled (slows down, process keeps running)
- Memory over-limit = OOM killed (process dies immediately)

This asymmetry is important — CPU issues are often invisible (just slow), while memory issues crash your container.

---

## Full Example

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: my-app
spec:
  containers:
    - name: my-container
      image: my-image:latest
      resources:
        requests:
          cpu: "250m"
          memory: "128Mi"
        limits:
          cpu: "1000m"
          memory: "256Mi"
```

---

## Practical Rule of Thumb

- Set **requests** to what the app needs under normal load
- Set **limits** to 2–4x the request to allow bursting
- Never leave **requests** at 0 — it breaks scheduling fairness
- Be cautious with very tight limits — CPU throttling is a common hidden performance issue in production

---

## QoS Classes (Bonus)

Kubernetes assigns a **Quality of Service** class based on requests/limits:

| QoS Class | Condition | Priority |
|---|---|---|
| `Guaranteed` | requests == limits for all resources | Highest — last to be evicted |
| `Burstable` | requests < limits (or only one set) | Medium |
| `BestEffort` | no requests or limits set at all | Lowest — first to be evicted |

---

## Kyverno — CPU Requests and Limits

### Why Kyverno is Different from a Normal App

Kyverno is a **Kubernetes Admission Webhook**. Every time anything is created, updated, or deleted in your cluster — a Pod, a Deployment, a ConfigMap — Kubernetes calls Kyverno **synchronously** before allowing the operation.

This makes Kyverno **cluster-critical infrastructure**. If Kyverno is slow or unresponsive, Kubernetes either:
- **Blocks all resource creation** (if `failurePolicy: Fail`)
- **Lets everything through unvalidated** (if `failurePolicy: Ignore`)

CPU throttling caused by a tight CPU limit directly causes Kyverno to respond slowly → webhook timeouts → broken cluster operations.

---

### Kyverno's 4 Components (since v1.10)

| Component | Role | Criticality |
|---|---|---|
| `admission-controller` | Validates/mutates every API request | **Highest** — blocks cluster if slow |
| `background-controller` | Processes existing resources, generates policies | Medium |
| `cleanup-controller` | Runs cleanup policies on a schedule | Low |
| `reports-controller` | Generates policy reports | Low |

Each component should be tuned separately based on its criticality.

---

### CPU Requests for Kyverno — Always Set Them

**Always set CPU requests.** Without them:
- Kyverno gets `BestEffort` QoS — first to be evicted under node pressure
- The scheduler may place Kyverno on an already-overloaded node
- No CPU is guaranteed, so admission latency spikes unpredictably

---

### CPU Limits for Kyverno — It Depends on the Component

**The danger with tight limits:**
If the admission controller hits its CPU limit during a burst (e.g., a Helm deploy creating 50 resources at once), the kernel throttles it → responses slow down → Kubernetes webhook timeout fires → pods fail to be admitted.

#### Option 1 — No CPU Limit on admission-controller (Recommended for production)

```yaml
# admission-controller
resources:
  requests:
    cpu: "500m"
    memory: "384Mi"
  # no limits.cpu — allow free bursting
  limits:
    memory: "384Mi"
```

Kyverno can burst freely. Safe because Kyverno is event-driven, not a runaway process.
This is what the **Kyverno team officially recommends** for the admission controller.

#### Option 2 — High Limit with generous headroom (Balanced)

```yaml
# admission-controller
resources:
  requests:
    cpu: "500m"
    memory: "384Mi"
  limits:
    cpu: "2000m"    # 4x the request — room to burst
    memory: "384Mi"
```

Good when your cluster enforces that all pods must have CPU limits (e.g., via LimitRange or a Kyverno policy itself).

#### Option 3 — Tight Limit (Avoid for admission-controller)

```yaml
# DO NOT do this for admission-controller
resources:
  requests:
    cpu: "100m"
  limits:
    cpu: "200m"   # too tight — will throttle under burst load
```

This will cause webhook timeouts during any burst of cluster activity. Only acceptable for `reports-controller` or `cleanup-controller`.

---

### Recommended Values Per Component

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---|---|---|---|
| `admission-controller` | `500m` | none (or `2000m`) | `384Mi` | `384Mi` |
| `background-controller` | `200m` | `1000m` | `128Mi` | `256Mi` |
| `cleanup-controller` | `100m` | `500m` | `64Mi` | `128Mi` |
| `reports-controller` | `100m` | `500m` | `64Mi` | `128Mi` |

> Scale up requests/limits based on cluster size and number of policies. Large clusters (500+ nodes) may need 2–4x these values for the admission-controller.

---

### Full Helm Values Example

```yaml
# values.yaml for kyverno Helm chart
admissionController:
  resources:
    requests:
      cpu: 500m
      memory: 384Mi
    limits:
      memory: 384Mi        # intentionally no cpu limit

backgroundController:
  resources:
    requests:
      cpu: 200m
      memory: 128Mi
    limits:
      cpu: 1000m
      memory: 256Mi

cleanupController:
  resources:
    requests:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 500m
      memory: 128Mi

reportsController:
  resources:
    requests:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 500m
      memory: 128Mi
```

---

### Decision Summary for Kyverno

| Question | Answer |
|---|---|
| Should I set CPU **requests**? | **Yes, always** — for all 4 components |
| Should I set CPU **limits** on admission-controller? | **No** (or set very high, 4x+ the request) |
| Should I set CPU **limits** on other components? | Yes, safe to set — they are not on the critical admission path |
| What QoS class should admission-controller have? | `Burstable` (request set, no limit) or `Guaranteed` (if limit = request, set both high) |
| What happens if I set a tight limit on admission-controller? | CPU throttle → slow webhook → admission timeouts → pods fail to start |
