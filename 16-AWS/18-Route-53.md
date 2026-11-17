# Amazon Route 53: DNS Web Service

> Route 53 is AWS's scalable, highly available DNS service. It routes end users to AWS resources (EC2, ELB, S3, CloudFront) or external endpoints, with built-in health checks, failover, latency-based routing, and weighted traffic policies.

---

## Core Concepts

| Concept | Description |
|---|---|
| **Hosted Zone** | A container for DNS records for a domain (e.g., `example.com`) |
| **Record Set** | A DNS record within a hosted zone (A, CNAME, MX, TXT, etc.) |
| **TTL** | Time-to-Live — how long resolvers cache the record |
| **Alias Record** | AWS-specific extension of A/AAAA — maps to AWS resources (ELB, CloudFront, S3) with no TTL charge |
| **Health Check** | Monitors endpoint availability; removes unhealthy targets from DNS |

---

## Record Types

| Type | Points to | Use for |
|---|---|---|
| **A** | IPv4 address | Host to IP mapping |
| **AAAA** | IPv6 address | IPv6 host mapping |
| **CNAME** | Another hostname | Aliases (cannot be used at zone apex) |
| **Alias** | AWS resource (ELB, CloudFront, S3) | Zone apex + AWS resources (preferred over CNAME) |
| **MX** | Mail server hostname + priority | Email routing |
| **TXT** | Free-form text | Domain verification (SPF, DKIM, etc.) |
| **NS** | Name server hostnames | Delegation to a hosted zone |
| **SOA** | Authority record | Automatically created per hosted zone |

---

## Routing Policies

| Policy | How it works | Use for |
|---|---|---|
| **Simple** | Single value returned | Single endpoint |
| **Weighted** | Split traffic by percentage (e.g., 90/10) | A/B testing, gradual rollouts |
| **Latency** | Route to the region with lowest latency for the user | Multi-region performance |
| **Failover** | Primary endpoint + health check; failover to secondary if unhealthy | Disaster recovery |
| **Geolocation** | Route based on user's country or continent | Content localisation, compliance |
| **Geoproximity** | Route based on geographic distance + bias | Fine-grained geographic routing |
| **Multivalue** | Returns up to 8 healthy records randomly | Simple client-side load balancing |
| **IP-based** | Route based on client CIDR range | Control routing for specific networks |

---

## Setting Up Route 53

### Step 1: Register or Transfer a Domain

```bash
aws route53domains register-domain \
  --domain-name example.com \
  --duration-in-years 1 \
  --admin-contact file://contact.json \
  --registrant-contact file://contact.json \
  --tech-contact file://contact.json \
  --privacy-protect-admin-contact \
  --privacy-protect-registrant-contact
```

### Step 2: Create a Hosted Zone

```bash
aws route53 create-hosted-zone \
  --name example.com \
  --caller-reference unique-ref-$(date +%s)
```

Take note of the returned **Hosted Zone ID** and **NS records** — add the NS records to your domain registrar.

### Step 3: Create DNS Records

**A record pointing to an IP:**

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z3M3LMPEXAMPLE \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "www.example.com",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [{"Value": "203.0.113.1"}]
      }
    }]
  }'
```

**Alias record pointing to an ALB:**

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z3M3LMPEXAMPLE \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "app.example.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z35SXDOTRQ7X7K",
          "DNSName": "my-alb-123456.us-east-1.elb.amazonaws.com",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

---

## Health Checks

Health checks monitor your endpoints and remove them from DNS if they fail.

```bash
aws route53 create-health-check \
  --caller-reference unique-ref-$(date +%s) \
  --health-check-config '{
    "IPAddress": "203.0.113.1",
    "Port": 443,
    "Type": "HTTPS",
    "ResourcePath": "/health",
    "FullyQualifiedDomainName": "app.example.com",
    "RequestInterval": 30,
    "FailureThreshold": 3
  }'
```

---

## Failover Routing Example

Configure primary and secondary records with health checks for automatic failover:

```bash
# Primary record (us-east-1)
aws route53 change-resource-record-sets \
  --hosted-zone-id Z3M3LMPEXAMPLE \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "app.example.com",
        "Type": "A",
        "SetIdentifier": "primary",
        "Failover": "PRIMARY",
        "TTL": 60,
        "ResourceRecords": [{"Value": "203.0.113.1"}],
        "HealthCheckId": "abc-health-check-id"
      }
    }]
  }'

# Secondary record (us-west-2)
aws route53 change-resource-record-sets \
  --hosted-zone-id Z3M3LMPEXAMPLE \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "app.example.com",
        "Type": "A",
        "SetIdentifier": "secondary",
        "Failover": "SECONDARY",
        "TTL": 60,
        "ResourceRecords": [{"Value": "198.51.100.1"}]
      }
    }]
  }'
```

---

## Useful CLI Commands

```bash
# List all hosted zones
aws route53 list-hosted-zones

# List records in a hosted zone
aws route53 list-resource-record-sets \
  --hosted-zone-id Z3M3LMPEXAMPLE

# List all health checks
aws route53 list-health-checks

# Check DNS propagation (run from your machine)
nslookup www.example.com 8.8.8.8
dig www.example.com +short
```

---

## Summary

Route 53 is more than a DNS service — it's a global traffic management platform. Use Alias records to point to AWS resources (free, no TTL issues), health checks to automatically remove failing endpoints, and routing policies like Latency or Failover to build multi-region resilient architectures. Pair it with CloudFront for global content delivery and ACM for free TLS certificates.
