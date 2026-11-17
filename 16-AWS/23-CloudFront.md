# Amazon CloudFront: Content Delivery Network (CDN)

> CloudFront is AWS's globally distributed CDN. It caches your content at **edge locations** around the world, serving requests from the nearest edge instead of your origin — reducing latency, absorbing traffic spikes, and lowering origin load.

---

## How CloudFront Works

```
User (London)
    │
    ▼
CloudFront Edge Location (London) ──► Cache HIT → return cached response
    │
    │ Cache MISS
    ▼
Origin (S3 bucket / ALB / EC2 / custom HTTP server)
    │
    ▼
Response cached at edge for next request
```

---

## Key Concepts

| Term | Description |
|------|-------------|
| **Distribution** | The CloudFront resource — maps a domain to an origin |
| **Edge Location** | AWS data centre close to users that caches content |
| **Origin** | The source of truth — S3, ALB, EC2, API Gateway, or any URL |
| **Cache Behaviour** | Rules for how CloudFront handles requests (TTL, headers, cookies) |
| **TTL** | Time-to-Live — how long an object stays cached at the edge |
| **Invalidation** | Force CloudFront to evict cached objects before TTL expires |
| **OAC** | Origin Access Control — ensures S3 only accepts requests from CloudFront |

---

## Common Use Cases

- **Static website hosting** — serve an S3-hosted site globally with HTTPS
- **API acceleration** — cache GET responses from API Gateway or ALB
- **Media streaming** — HLS/DASH video delivery with low latency
- **Security** — integrate with AWS WAF to block malicious traffic at the edge
- **DDoS protection** — CloudFront + AWS Shield absorb volumetric attacks

---

## S3 + CloudFront Setup (Secure)

```bash
# 1. Create a CloudFront distribution pointing to an S3 bucket
aws cloudfront create-distribution \
  --origin-domain-name mybucket.s3.amazonaws.com \
  --default-root-object index.html

# 2. Use Origin Access Control (OAC) so S3 blocks direct public access
#    and only allows CloudFront requests
```

S3 bucket policy allowing only CloudFront:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "cloudfront.amazonaws.com" },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::mybucket/*",
    "Condition": {
      "StringEquals": {
        "AWS:SourceArn": "arn:aws:cloudfront::123456789012:distribution/DIST_ID"
      }
    }
  }]
}
```

---

## Cache Invalidation

```bash
# Invalidate a specific file
aws cloudfront create-invalidation \
  --distribution-id <DIST_ID> \
  --paths "/index.html"

# Invalidate all files
aws cloudfront create-invalidation \
  --distribution-id <DIST_ID> \
  --paths "/*"
```

---

## CloudFront vs Direct S3 Access

| | CloudFront | Direct S3 |
|-|-----------|----------|
| Latency | Low (edge cached) | Higher (single region) |
| HTTPS | Built-in with ACM cert | S3 static site is HTTP only |
| DDoS protection | Yes (Shield Standard) | No |
| Cost | Cheaper at scale for reads | Per-request charges |
| Custom domain | Yes | Limited (complex) |
