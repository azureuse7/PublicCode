# LocalDNS vs CoreDNS in AKS – Explainer

**Short version:** CoreDNS is the cluster's central DNS server. LocalDNS is a small DNS cache that sits in front of it on every node. LocalDNS doesn't replace CoreDNS. It answers repeat lookups locally, so most queries never have to cross the network to reach CoreDNS.

---

## How it works

**Without LocalDNS**

```
Pod → kube-dns service IP (10.0.0.10) → network → CoreDNS pod (possibly on another node)
      → in-cluster names answered by CoreDNS
      → external names forwarded to VNet DNS
```

**With LocalDNS**

```
Pod → LocalDNS on the same node (169.254.10.11)
      → answer cached?  reply immediately
      → not cached?     in-cluster names → CoreDNS
                        external names   → VNet DNS
                        then cache the answer
```

**Analogy:** CoreDNS is the central library. LocalDNS is a shelf in each office holding the books people ask for most.

---

## Key differences

| | CoreDNS | LocalDNS |
|---|---|---|
| Where it runs | A few pods in `kube-system` | On every node, outside Kubernetes (not a pod) |
| Address | kube-dns service IP (usually `10.0.0.10`) | `169.254.10.10` / `169.254.10.11` (local to the node) |
| Job | Main DNS server for the cluster | Cache and proxy in front of CoreDNS and the VNet DNS |
| Enabled | Always, by default | Per node pool, through `localdnsconfig.json` |
| If it has problems | Affects the whole cluster | Can serve cached answers while upstream DNS is down |

It's the same CoreDNS software underneath (our metrics showed version 1.14.7). The difference is where it runs and what job it does.

**Two listeners:**
- `169.254.10.11` – normal pods (`dnsPolicy: ClusterFirst`), configured by `kubeDNSOverrides`
- `169.254.10.10` – pods with `dnsPolicy: Default`, configured by `vnetDNSOverrides`

---

## Why it helps

- **Faster:** no network hop for cached answers. In our test, average lookups dropped from about 2.2 ms to about 0.8 ms.
- **More reliable:** it avoids the Linux conntrack/UDP race behind the well-known "random 5-second DNS timeouts", and keeps serving cached answers if upstream DNS goes down (serve-stale).
- **Less load on CoreDNS:** in our test, about 98% of external lookups were answered on the node without going upstream.

---

## Questions you might get

**Is it the same as NodeLocal DNSCache?**
It's the same idea, but Microsoft manages it and you configure it per node pool, so there's no DaemonSet for us to maintain. The two can't run together.

**Could it serve stale or wrong answers?**
It respects the record's TTL and only caps it (`cacheDurationInSeconds`), never extends it. It serves old answers only when upstream DNS is unavailable, for up to `serveStaleDurationInSeconds` (1 hour in our config).

**Does it cost anything?**
As far as we know there's no separate charge. It uses a little CPU and memory on each node, which is why it requires VM sizes with at least 4 vCPUs.

**What's the catch?**
- Every enable, change or disable **reimages all nodes in the pool**, so it needs PodDisruptionBudgets and a maintenance window.
- Pods need UDP and TCP port 53 to `169.254.10.10` and `169.254.10.11` allowed by network policies (we use Calico).
- Upstream DNS servers and firewalls must allow TCP 53, because in-cluster lookups use TCP.
- DNS troubleshooting moves partly onto the node, using metrics on port 9253 and `queryLogging: Log`.

**Do we have to do anything?**
Yes. From Kubernetes 1.37, AKS Standard can switch LocalDNS on by default for node pools with no LocalDNS setting. We're on 1.36.5, so each pool needs an explicit setting (`Required` or `Disabled`) before the next upgrade.

**What about NAP (Node Auto Provisioning)?**
LocalDNS is configured in `spec.localDNS` on the `AKSNodeClass`. Changing it replaces NAP nodes through drift rather than reimaging them.

**What about EKS?**
EKS Auto Mode has it built in: each node runs CoreDNS as a system service. Standard EKS would need the upstream NodeLocal DNSCache DaemonSet, which we'd deploy and maintain ourselves.

---

## Also good to know

- **Search path and `ndots:5`:** a name like `microsoft.com` is first tried with each search suffix (`d1.svc.cluster.local`, `svc.cluster.local`, `cluster.local`, `reddog.microsoft.com`) before the real name. That's why the metrics show many "denial" (name-not-found) cache hits. LocalDNS answers these locally. Lowering `ndots` for chatty apps is a separate, known way to cut DNS traffic.
- **Supported OS:** Linux node pools only (Ubuntu 22.04+ or Azure Linux). Windows pools aren't supported.
- **Requirements:** Kubernetes 1.31+, Azure CLI 2.80.0+, and VMs with at least 4 vCPUs.
- **Harmless warning:** `nslookup` prints `;; Got recursion not available from 169.254.10.11`. Lookups still work.

---

**Reference:** [Configure LocalDNS in AKS](https://learn.microsoft.com/azure/aks/localdns-custom) · [DNS resolution in AKS](https://learn.microsoft.com/azure/aks/dns-concepts)
