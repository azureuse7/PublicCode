# ELB: Elastic Load Balancing

> Amazon ELB automatically distributes incoming traffic across multiple targets (EC2, containers, IPs, Lambda) across Availability Zones — providing high availability, fault tolerance, and horizontal scalability.

---

## Load Balancer Types

| Type | Layer | Protocol | Best For |
|---|---|---|---|
| **ALB** (Application) | 7 (HTTP/HTTPS) | HTTP, HTTPS, WebSocket | Web apps, microservices, path/host routing |
| **NLB** (Network) | 4 (TCP/UDP) | TCP, UDP, TLS | Ultra-low latency, high throughput, static IPs |
| **GWLB** (Gateway) | 3 (Network) | IP | Third-party virtual appliances (firewalls, IDS) |
| **CLB** (Classic) | 4 & 7 | HTTP, HTTPS, TCP | Legacy EC2-Classic apps (avoid for new workloads) |

---

## ALB — Application Load Balancer

### Routing Rules

ALB routes requests based on rules applied in order of priority:

| Rule Condition | Example | Routes to |
|---|---|---|
| **Path-based** | `/api/*` | API service target group |
| **Host-based** | `app.example.com` | App target group |
| **HTTP header** | `X-Version: v2` | Canary target group |
| **Query string** | `?env=staging` | Staging target group |

### Key Features
- Native HTTPS termination (upload ACM certificate)
- HTTP → HTTPS redirect at the load balancer
- Sticky sessions via cookies
- WebSocket and HTTP/2 support
- Lambda as a target (single function per target group)

---

## NLB — Network Load Balancer

- Handles **millions of requests/sec** at ultra-low latency
- Assigns **static Elastic IP addresses** per AZ (predictable for firewall allowlisting)
- Preserves client source IP to targets
- Supports TCP, UDP, and TLS passthrough
- Use for gaming, IoT, financial trading, real-time streaming

---

## Health Checks

ELB continuously checks targets and stops routing to unhealthy ones.

| Setting | Description |
|---|---|
| **Protocol** | HTTP, HTTPS, or TCP |
| **Path** | Health check endpoint (e.g., `/health`) |
| **Healthy threshold** | Consecutive successes required (default: 3) |
| **Unhealthy threshold** | Consecutive failures before marking unhealthy (default: 3) |
| **Interval** | Seconds between checks (default: 30s) |

---

## Setting Up an ALB

### Using the AWS Management Console

1. Go to **EC2** → **Load Balancers** → **Create Load Balancer** → choose **Application Load Balancer**
2. Configure:
   - **Scheme**: Internet-facing or Internal
   - **VPC and subnets**: select at least 2 AZs
   - **Security group**: allow ports 80 and 443
3. Create a **Target Group**: choose EC2 instances, containers, or IPs
4. Add **Listeners**: HTTP:80 and HTTPS:443
5. Register your targets
6. Review and create

### Using the AWS CLI

**Step 1: Create a target group**

```bash
aws elbv2 create-target-group \
  --name web-targets \
  --protocol HTTP \
  --port 80 \
  --vpc-id vpc-12345678 \
  --health-check-path /health \
  --health-check-interval-seconds 30
```

**Step 2: Register EC2 instances**

```bash
aws elbv2 register-targets \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/web-targets/abc123 \
  --targets Id=i-1234567890abcdef0 Id=i-0abcdef1234567890
```

**Step 3: Create the load balancer**

```bash
aws elbv2 create-load-balancer \
  --name my-alb \
  --subnets subnet-12345678 subnet-23456789 \
  --security-groups sg-12345678 \
  --scheme internet-facing \
  --type application
```

**Step 4: Create a listener**

```bash
aws elbv2 create-listener \
  --load-balancer-arn arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-alb/abc123 \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/web-targets/abc123
```

**Step 5: Add an HTTPS redirect rule (optional)**

```bash
aws elbv2 create-listener \
  --load-balancer-arn <ALB_ARN> \
  --protocol HTTP \
  --port 80 \
  --default-actions '[{"Type":"redirect","RedirectConfig":{"Protocol":"HTTPS","Port":"443","StatusCode":"HTTP_301"}}]'
```

---

## ALB with EKS (AWS Load Balancer Controller)

When running on EKS, the AWS Load Balancer Controller provisions ALBs automatically from `Ingress` objects:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-ingress
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
spec:
  rules:
  - http:
      paths:
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: api-service
            port:
              number: 80
      - path: /
        pathType: Prefix
        backend:
          service:
            name: web-service
            port:
              number: 80
```

---

## Common Use Cases

| Use Case | Recommended Type |
|---|---|
| Web app with HTTP/HTTPS | ALB |
| Microservices with path routing | ALB |
| Containerised apps (ECS/EKS) | ALB |
| High-throughput TCP/UDP apps | NLB |
| Static IP requirement | NLB |
| Inline firewalls / IDS/IPS | GWLB |

---

## Summary

ELB is a foundational component for any highly available AWS architecture. Use ALB for HTTP workloads with intelligent routing, NLB for raw TCP/UDP performance and static IPs, and GWLB to integrate network security appliances. Always configure health checks so unhealthy targets are automatically removed from rotation.
