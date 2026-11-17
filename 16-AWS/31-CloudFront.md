# Amazon CloudFront: CDN Deep Dive

> See [23-CloudFront.md](23-CloudFront.md) for a quick reference. This guide covers CloudFront in depth — origins, cache behaviours, security, Lambda@Edge, and a full static site walkthrough.

---

## How CloudFront Works

```
User (any location)
        │
        ▼
CloudFront Edge Location (nearest to user)
        │
        ├── Cache HIT ──► Return cached response immediately
        │
        └── Cache MISS ──► Fetch from Origin → cache → return to user
                │
          Origin Server:
          - S3 bucket
          - ALB / EC2
          - API Gateway
          - Custom HTTP server
```

**Key numbers:**
- 450+ Points of Presence (PoPs) globally
- Cache TTL: configurable from 0 to 31,536,000 seconds (1 year)
- Origin request: only on cache miss

---

## Key Concepts

| Term | Description |
|---|---|
| **Distribution** | The top-level CloudFront resource — maps a domain to one or more origins |
| **Origin** | Where CloudFront fetches content on a cache miss |
| **Cache Behaviour** | Rules per URL pattern (e.g., `/api/*` bypasses cache, `/*.jpg` caches 24h) |
| **OAC** | Origin Access Control — S3 only accepts requests from CloudFront (replaces OAI) |
| **TTL** | Time-to-Live — how long an object stays cached at the edge |
| **Invalidation** | Forces CloudFront to discard cached objects before TTL expires |
| **Price Class** | Limits which edge locations serve your content (controls cost vs. coverage) |
| **Lambda@Edge** | Run Lambda functions at edge locations to modify requests/responses |
| **CloudFront Functions** | Lightweight JS functions for simple URL rewrites/header manipulation |

---

## Origins

| Origin Type | Use For |
|---|---|
| **S3 bucket** | Static websites, file downloads, video streaming |
| **S3 website endpoint** | When using S3 static website hosting (HTTP only) |
| **ALB / NLB** | Dynamic content from EC2 or containers |
| **API Gateway** | Serverless APIs |
| **Custom HTTP** | Any publicly accessible server or service |

### Securing S3 Origins with OAC

Keep your S3 bucket private — only CloudFront can fetch from it:

1. Create an **Origin Access Control** (OAC) in CloudFront
2. Attach it to the S3 origin in your distribution
3. Add this bucket policy (CloudFront will prompt you to add it):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "cloudfront.amazonaws.com" },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::my-bucket/*",
    "Condition": {
      "StringEquals": {
        "AWS:SourceArn": "arn:aws:cloudfront::123456789012:distribution/EDFDVBD6EXAMPLE"
      }
    }
  }]
}
```

---

## Cache Behaviours

Configure different caching rules per URL pattern:

| Path Pattern | Cache | Use Case |
|---|---|---|
| `/api/*` | Cache disabled (TTL=0) | Dynamic API responses |
| `/static/*` | Long TTL (1 year) | Versioned assets (CSS, JS, images) |
| `/*.html` | Short TTL (5 min) | HTML that updates frequently |
| `/*` (default) | Medium TTL (1 day) | General content |

---

## Full Example: Static Website with Custom Domain + HTTPS

### Step 1: Host files in S3

```bash
# Create bucket
aws s3api create-bucket \
  --bucket my-website-bucket \
  --region us-east-1

# Upload site
aws s3 sync ./dist s3://my-website-bucket --delete

# Block all public access (CloudFront will serve it, not direct S3)
aws s3api put-public-access-block \
  --bucket my-website-bucket \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,\
    BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### Step 2: Request a TLS certificate in ACM (us-east-1 required for CloudFront)

```bash
aws acm request-certificate \
  --domain-name www.example.com \
  --subject-alternative-names example.com \
  --validation-method DNS \
  --region us-east-1
```

Add the CNAME validation records to Route 53, then wait for `ISSUED` status.

### Step 3: Create the CloudFront Distribution

```bash
aws cloudfront create-distribution \
  --distribution-config '{
    "CallerReference": "unique-ref-001",
    "Comment": "My website distribution",
    "DefaultRootObject": "index.html",
    "Origins": {
      "Quantity": 1,
      "Items": [{
        "Id": "s3-origin",
        "DomainName": "my-website-bucket.s3.us-east-1.amazonaws.com",
        "S3OriginConfig": { "OriginAccessIdentity": "" },
        "OriginAccessControlId": "<OAC_ID>"
      }]
    },
    "DefaultCacheBehavior": {
      "TargetOriginId": "s3-origin",
      "ViewerProtocolPolicy": "redirect-to-https",
      "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
      "Compress": true
    },
    "Aliases": { "Quantity": 1, "Items": ["www.example.com"] },
    "ViewerCertificate": {
      "ACMCertificateArn": "arn:aws:acm:us-east-1:123456789012:certificate/abc-123",
      "SSLSupportMethod": "sni-only",
      "MinimumProtocolVersion": "TLSv1.2_2021"
    },
    "Enabled": true,
    "PriceClass": "PriceClass_100",
    "HttpVersion": "http2and3",
    "CustomErrorResponses": {
      "Quantity": 1,
      "Items": [{
        "ErrorCode": 403,
        "ResponseCode": "200",
        "ResponsePagePath": "/index.html"
      }]
    }
  }'
```

### Step 4: Update Route 53

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z3M3LMPEXAMPLE \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "www.example.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z2FDTNDATAQYW2",
          "DNSName": "d1234abcdefgh.cloudfront.net",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'
```

---

## Cache Invalidation

Force CloudFront to fetch fresh content from origin:

```bash
# Invalidate a single file
aws cloudfront create-invalidation \
  --distribution-id EDFDVBD6EXAMPLE \
  --paths "/index.html"

# Invalidate everything (use sparingly — costs apply after first 1000/month)
aws cloudfront create-invalidation \
  --distribution-id EDFDVBD6EXAMPLE \
  --paths "/*"
```

> Better practice for static assets: use **content hashing** in filenames (e.g., `app.a3f9b1.js`) so new deploys use new URLs — no invalidation needed.

---

## Lambda@Edge vs CloudFront Functions

| | CloudFront Functions | Lambda@Edge |
|---|---|---|
| Triggered at | Viewer request/response | Viewer + Origin request/response |
| Runtime | JavaScript (ES5) | Node.js, Python |
| Max execution | 1ms | 5s (viewer) / 30s (origin) |
| Memory | 2 MB | 128 MB – 10,240 MB |
| Use for | Header manipulation, URL rewrites | Auth, A/B testing, dynamic routing |
| Cost | Very low | Higher |

**Example CloudFront Function — add security headers:**

```javascript
function handler(event) {
  var response = event.response;
  var headers = response.headers;

  headers['strict-transport-security'] = { value: 'max-age=63072000; includeSubdomains; preload' };
  headers['x-content-type-options'] = { value: 'nosniff' };
  headers['x-frame-options'] = { value: 'DENY' };
  headers['x-xss-protection'] = { value: '1; mode=block' };

  return response;
}
```

---

## Summary

CloudFront is the standard way to deliver any web content on AWS. It reduces latency, absorbs traffic spikes, provides free DDoS protection via Shield Standard, and integrates with ACM for free TLS certificates. Always use OAC for S3 origins, redirect HTTP to HTTPS, and combine with WAF for application-layer protection.
