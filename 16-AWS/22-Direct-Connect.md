# AWS Direct Connect: Dedicated Private Network Connection

> AWS Direct Connect establishes a **dedicated, private network connection** between your on-premises data centre and AWS. Unlike a VPN (which goes over the public internet), Direct Connect traffic travels over a private physical link — providing consistent throughput, lower latency, and reduced bandwidth costs for large data transfers.

---

## Why Use Direct Connect?

| Scenario | Benefit |
|----------|---------|
| Large data migrations (TB+) | High throughput, no internet bottleneck |
| Latency-sensitive workloads | Consistent sub-millisecond latency |
| Compliance requirements | Traffic never traverses the public internet |
| Hybrid cloud architectures | Stable, predictable connection to AWS VPCs |

---

## How It Works

```
On-Premises Data Centre
        │
        │ (physical fibre — 1 Gbps or 10 Gbps)
        ▼
AWS Direct Connect Location (colocation facility)
        │
        │ (AWS backbone)
        ▼
AWS Region → VPC (via Virtual Private Gateway or Transit Gateway)
```

1. You (or your provider) install a **cross-connect** at a Direct Connect location
2. You create a **Virtual Interface (VIF)** to route traffic to:
   - A **Private VIF** → connects to a VPC via a Virtual Private Gateway
   - A **Public VIF** → connects to AWS public services (S3, DynamoDB, etc.)
   - A **Transit VIF** → connects to a Transit Gateway for multi-VPC routing

---

## Connection Types

| Type | Speed | Use Case |
|------|-------|---------|
| Dedicated Connection | 1 Gbps, 10 Gbps, 100 Gbps | Large enterprises with their own rack in a DX location |
| Hosted Connection | 50 Mbps – 10 Gbps | Ordered through an AWS Partner — more flexible sizing |

---

## Resilience Best Practices

- Deploy **two Direct Connect connections** at different DX locations (active/active or active/standby)
- Use a **VPN as backup** — if Direct Connect fails, traffic fails over to the VPN automatically
- Use **Link Aggregation Groups (LAG)** to bundle multiple connections for higher bandwidth

---

## Direct Connect vs VPN

| | Direct Connect | VPN |
|-|---------------|-----|
| Throughput | Up to 100 Gbps | Up to ~1.25 Gbps per tunnel |
| Latency | Consistent, low | Variable (public internet) |
| Cost | Higher (physical link) | Lower (software-based) |
| Setup time | Weeks (physical install) | Minutes |
| Encryption | Optional (MACsec) | Always encrypted (IPSec) |

---

## Key CLI Commands

```bash
# List Direct Connect connections
aws directconnect describe-connections

# List virtual interfaces
aws directconnect describe-virtual-interfaces

# List Direct Connect gateways
aws directconnect describe-direct-connect-gateways
```
