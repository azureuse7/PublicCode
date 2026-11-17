# kube-proxy on Amazon EKS

> kube-proxy is a DaemonSet that runs on every node in your EKS cluster. It translates Kubernetes `Service` objects into host-level network rules (iptables or IPVS), enabling pods and external clients to reach services via their virtual ClusterIP or NodePort.

---

## What kube-proxy Does

```
Kubernetes API Server
        │  (watches Service + Endpoints objects)
        ▼
    kube-proxy (on every node)
        │
        ▼
  iptables / IPVS rules in the Linux kernel
        │
        ▼
Traffic to ClusterIP:port → forwarded to one of the healthy Pod IPs
```

kube-proxy does **not** handle Pod-to-Pod networking — that is the responsibility of the CNI plugin (AWS VPC CNI on EKS). kube-proxy handles **Service-level** routing only.

---

## Proxy Modes on EKS

### iptables Mode (default)

- Programs Linux `iptables` NAT rules for each Service and its endpoints
- DNAT rules rewrite packets destined for a ClusterIP to a backend Pod IP
- Works well for clusters with up to ~10,000 Services
- Mature and widely tested

### IPVS Mode (optional, high scale)

- Uses the Linux kernel's IP Virtual Server (IPVS) module
- Better performance at scale — no linear rule scan per packet
- More load-balancing algorithms: round-robin, least connection, shortest expected delay
- Requires `ipvsadm` kernel modules to be loaded on nodes
- Recommended for clusters with thousands of Services

---

## EKS-Specific Details

| Detail | Description |
|---|---|
| **DaemonSet** | One kube-proxy pod per node in `kube-system` namespace |
| **Not on Fargate** | Fargate nodes do not run kube-proxy; AWS manages their routing |
| **EKS Add-on** | Managed via the EKS add-on framework — AWS provides security-patched builds |
| **Version matching** | kube-proxy version should match your cluster's Kubernetes version |
| **Auto Mode** | AWS fully manages kube-proxy in EKS Auto Mode — no manual intervention needed |

---

## Traffic Flows

### ClusterIP Service

```
Pod A calls http://nginx-svc:80
    │
    ▼
iptables DNAT: ClusterIP:80 → Pod B IP:80
    │
    ▼
Pod B receives request
```

### NodePort Service

```
External client calls NodeIP:30080
    │
    ▼
iptables DNAT (NodePort chain): NodeIP:30080 → Pod IP:80
    │
    ▼
Pod receives request
```

### LoadBalancer Service (AWS NLB/ALB)

```
External client → AWS NLB → Node:NodePort
    │
    ▼
iptables DNAT: NodePort → Pod IP:containerPort
    │
    ▼
Pod receives request
```

---

## Hands-On Example

### Deploy nginx and expose as a Service

```yaml
# nginx.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-deploy
spec:
  replicas: 2
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
      - name: nginx
        image: nginx:stable
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: nginx-svc
spec:
  type: NodePort
  selector:
    app: nginx
  ports:
  - port: 80
    targetPort: 80
    nodePort: 30080
```

```bash
kubectl apply -f nginx.yaml

# Verify the service and endpoints
kubectl get svc nginx-svc
kubectl get endpoints nginx-svc -o wide
```

### Inspect iptables rules (SSH into a node)

```bash
# View Service entries in the NAT table
iptables -t nat -L KUBE-SERVICES -n --line-numbers

# View the NodePort chain
iptables -t nat -L KUBE-NODEPORTS -n --line-numbers

# Find chains for a specific service
iptables -t nat -L -n | grep nginx
```

### Test connectivity

```bash
# From inside the cluster (using a temporary pod)
kubectl run test --image=alpine --rm -it -- wget -qO- http://nginx-svc

# From outside (via NodePort — replace with a real node IP)
curl http://<NODE_IP>:30080
```

---

## Managing kube-proxy on EKS

### Check the current version

```bash
kubectl get daemonset kube-proxy -n kube-system \
  -o jsonpath='{.spec.template.spec.containers[0].image}'

# Or via add-on
aws eks describe-addon \
  --cluster-name my-cluster \
  --addon-name kube-proxy
```

### Update kube-proxy add-on

```bash
aws eks update-addon \
  --cluster-name my-cluster \
  --addon-name kube-proxy \
  --addon-version v1.29.3-eksbuild.2 \
  --resolve-conflicts OVERWRITE
```

### View kube-proxy logs

```bash
kubectl logs -n kube-system -l k8s-app=kube-proxy --tail=50
```

### Switch to IPVS mode (for large clusters)

```bash
# Edit the kube-proxy ConfigMap
kubectl -n kube-system edit configmap kube-proxy
# Change: mode: "" → mode: "ipvs"

# Restart the DaemonSet to apply
kubectl -n kube-system rollout restart daemonset kube-proxy

# Verify with ipvsadm (on any node)
# ipvsadm -L -n
```

---

## Monitoring kube-proxy

kube-proxy exposes Prometheus metrics on port 10249:

| Metric | Description |
|---|---|
| `kubeproxy_iptables_sync_duration_seconds` | Time spent syncing iptables rules |
| `kubeproxy_network_programming_duration_seconds` | Latency of programming network rules |
| `kubeproxy_sync_proxy_rules_duration_seconds` | Total sync duration |

```bash
# View metrics from inside the cluster
kubectl exec -n kube-system <kube-proxy-pod> -- wget -qO- http://localhost:10249/metrics
```

---

## Summary

kube-proxy is a transparent but critical component of Kubernetes networking. It ensures that traffic to any `Service` IP gets correctly routed to a healthy pod, across all nodes in the cluster. On EKS, manage it as a managed add-on to stay in sync with cluster upgrades. For large clusters (thousands of Services), switch to IPVS mode to avoid iptables performance degradation.
