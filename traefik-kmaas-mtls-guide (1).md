# mTLS with Traefik and KMAAS Vault: Test Guide

Status: written from the Traefik 3.7 docs and issue tracker (Oct 2026). **Not yet tested on a cluster.** Use the test matrix at the end to record results.

## 1. What this guide covers

Two TLS hops need validating:

```
client ──(1) frontend mTLS──▶ Traefik ──(2) backend TLS──▶ backend service
```

1. **Frontend validation (client → Traefik):** Traefik requires a client certificate and verifies it against the KMAAS CA chain.
2. **Backend validation (Traefik → backend):** Traefik verifies the backend's server certificate against a CA, and can optionally present its own client certificate.

### Important finding: what Traefik supports (v3.7.x)

| Capability | Gateway API (`Gateway` + `HTTPRoute`) | `IngressRoute` (Traefik CRD) |
|---|---|---|
| Per-app frontend mTLS (TLSOption bound to one route) | **Not supported.** Listener `tls.options` is ignored, and listener `tls.frontendValidation` is not implemented. | **Supported** via `tls.options` |
| Global frontend mTLS | Only via a TLSOption named `default` (applies to everything) | Same |
| Backend CA validation | `BackendTLSPolicy` (CA in a **ConfigMap**) | `ServersTransport` (CA in a **Secret**) |

The original draft put `traefik.io/tls-option` on a Gateway listener. Traefik doesn't read that field, so the TLSOption would be silently ignored and clients without certificates would be let through. This guide therefore has two paths:

- **Path A (recommended):** `IngressRoute` + `TLSOption` + `ServersTransport`. Per-app mTLS on both hops.
- **Path B (Gateway API):** `Gateway` + `HTTPRoute` + `BackendTLSPolicy`. Backend validation works; frontend mTLS is only possible globally through the `default` TLSOption.

Pick one path per application. `BackendTLSPolicy` only applies to Gateway API routes and `ServersTransport` is the equivalent for `IngressRoute`, so don't mix them on one route.

## 2. Prerequisites and placeholders

- Traefik v3.7.x with the **Kubernetes CRD provider** enabled (needed for `TLSOption`, `IngressRoute`, `ServersTransport`). Path B also needs the **Kubernetes Gateway provider**.
- Path B only: Gateway API CRDs installed (standard channel is enough; `BackendTLSPolicy` is in the standard channel from Gateway API v1.4) and Traefik's RBAC covering `backendtlspolicies`.
- The KMAAS Vault CA chain (intermediate + root) as PEM, and a client certificate/key issued from that chain for testing.
- The Traefik `websecure` entryPoint. On the default Helm chart the container listens on `8443` and the Service/LB exposes `443`. Clients use the externally exposed port, so adjust the test commands to your setup.

Replace these placeholders:

| Placeholder | Meaning |
|---|---|
| `application-namespace` | Namespace of your application |
| `sample.internal.domain` | Hostname clients use |
| `sample-service` | Backend Service name (port 443, serving TLS) |
| `sample-ingress-tls` | Server certificate Secret Traefik presents to clients |
| `kmaas-pki-ca-chain` | Secret holding the KMAAS CA chain |

---

## 3. Path A: IngressRoute (recommended)

### Step 1: CA chain Secret

**What it does:** holds the CA certificates Traefik uses to verify client certificates. Certificates only, never private keys.

**Notes:**
- The key must be named `ca.crt` (Traefik also accepts `tls.ca`).
- Put the intermediate and the root in the same file.
- The Secret must live in the **same namespace as the TLSOption** that references it.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: kmaas-pki-ca-chain
  namespace: application-namespace
type: Opaque
stringData:
  ca.crt: |
    -----BEGIN CERTIFICATE-----
    <Intermediate CA certificate>
    -----END CERTIFICATE-----
    -----BEGIN CERTIFICATE-----
    <Root CA certificate>
    -----END CERTIFICATE-----
```

Or create it from a PEM file:

```bash
kubectl create secret generic kmaas-pki-ca-chain \
  --from-file=ca.crt=./ca-chain.pem \
  -n application-namespace
```

**Check:** `openssl verify -CAfile ca-chain.pem client.crt` should print `OK` before you go any further.

### Step 2: TLSOption

**What it does:** tells Traefik to demand a client certificate and verify it against the CA chain from Step 1.

**Notes:**
- `RequireAndVerifyClientCert` rejects the TLS handshake if the client sends no certificate or one that doesn't chain to your CA.
- To reuse the platform's shared `traefik/mtls-option` instead, skip this step and reference it in Step 4 with `namespace: traefik`. Cross-namespace references need `allowCrossNamespace` enabled on the CRD provider, so confirm that with the platform team.

```yaml
apiVersion: traefik.io/v1alpha1
kind: TLSOption
metadata:
  name: mtls-option
  namespace: application-namespace
spec:
  # minVersion: VersionTLS12      # optional hardening
  clientAuth:
    secretNames:
      - kmaas-pki-ca-chain
    clientAuthType: RequireAndVerifyClientCert
```

### Step 3: Server certificate Secret

**What it does:** the certificate Traefik presents to clients for `sample.internal.domain`. The original draft referenced `sample-ingress-tls` but never defined it.

**Notes:**
- Type must be `kubernetes.io/tls`.
- It must be in the same namespace as the `IngressRoute`.
- Issue it from KMAAS Vault with `sample.internal.domain` in the SANs.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: sample-ingress-tls
  namespace: application-namespace
type: kubernetes.io/tls
stringData:
  tls.crt: |
    -----BEGIN CERTIFICATE-----
    <Server certificate (+ intermediate if needed)>
    -----END CERTIFICATE-----
  tls.key: |
    -----BEGIN PRIVATE KEY-----
    <Server private key>
    -----END PRIVATE KEY-----
```

Prefer creating this one with `kubectl create secret tls` or your existing Vault sync mechanism rather than committing a private key to YAML.

### Step 4: IngressRoute

**What it does:** replaces `Gateway` + `HTTPRoute`. It exposes the hostname, terminates TLS with the server certificate, and binds the mTLS TLSOption to this route only.

**Notes:**
- `entryPoints: websecure` is the entryPoint that maps to your HTTPS port.
- `scheme: https` tells Traefik the backend speaks TLS.
- `serversTransport` is added in Step 5 for backend validation.

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: sample-app
  namespace: application-namespace
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`sample.internal.domain`)
      kind: Rule
      services:
        - name: sample-service
          port: 443
          scheme: https
          serversTransport: sample-backend-transport   # defined in Step 5
  tls:
    secretName: sample-ingress-tls
    options:
      name: mtls-option
      namespace: application-namespace
```

### Step 5: ServersTransport (backend validation)

**What it does:** controls how Traefik connects to the backend. Traefik verifies the backend certificate against the CA, using the given server name.

**Notes:**
- `serverName` is used for SNI and certificate verification, so it must be in the backend certificate's SANs.
- Reusing `kmaas-pki-ca-chain` assumes the backend certificate is signed by the same KMAAS chain. Otherwise create a separate Secret with the right CA.
- The ServersTransport and its Secrets must be in the same namespace. Keeping it in the app namespace avoids cross-namespace questions. The platform's `traefik/kmaas-ca-chain` can be reused only if cross-namespace references are enabled, so confirm with the platform team.
- For full mutual auth (Traefik presents a client certificate to the backend), uncomment `certificatesSecrets` and point it at a `kubernetes.io/tls` Secret. Skip this unless the backend requires client certificates.

```yaml
apiVersion: traefik.io/v1alpha1
kind: ServersTransport
metadata:
  name: sample-backend-transport
  namespace: application-namespace
spec:
  serverName: sample-service.application-namespace.svc
  rootCAsSecrets:
    - kmaas-pki-ca-chain
  # certificatesSecrets:
  #   - backend-client-cert
```

---

## 4. Path B: Gateway API (backend validation only)

Use this if the platform standard is Gateway API and you don't need per-app frontend mTLS.

### Step B1: Gateway and HTTPRoute

**What it does:** exposes the hostname through Gateway API. Traefik ignores `tls.options` and `frontendValidation` on listeners, so they are removed.

**Notes:**
- The listener `port` must match the Traefik **entryPoint** port (container side, e.g. `8443`). A mismatch logs an error and marks the Gateway as not accepted.
- `certificateRefs` in the same namespace needs no `ReferenceGrant`.

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: sample-gateway
  namespace: application-namespace
spec:
  gatewayClassName: traefik
  listeners:
    - name: https
      protocol: HTTPS
      port: 8443                       # must match the Traefik entryPoint port
      hostname: sample.internal.domain
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: sample-ingress-tls   # defined as in Path A, Step 3
      allowedRoutes:
        namespaces:
          from: Same
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: sample-app
  namespace: application-namespace
spec:
  parentRefs:
    - name: sample-gateway
  hostnames:
    - sample.internal.domain
  rules:
    - backendRefs:
        - name: sample-service
          port: 443
```

### Step B2: Frontend mTLS (global only)

Gateway API routes can only use a TLSOption named exactly `default`, and it applies to **every** Gateway and route on that Traefik. Only one `default` TLSOption can exist across all namespaces, so check that the platform doesn't already define one (e.g. in the `traefik` namespace).

```yaml
apiVersion: traefik.io/v1alpha1
kind: TLSOption
metadata:
  name: default
  namespace: application-namespace   # TLSOption and its secretNames must share a namespace
spec:
  clientAuth:
    secretNames:
      - kmaas-pki-ca-chain
    clientAuthType: RequireAndVerifyClientCert
```

If other apps on this Traefik must not require client certificates, don't do this. Use Path A for the mTLS apps instead.

### Step B3: BackendTLSPolicy with a CA ConfigMap

**What it does:** Traefik verifies the backend server certificate against the CA, using `hostname` for SNI and authentication.

**Notes:**
- `caCertificateRefs` accepts a **ConfigMap** only. Secrets are not supported as a source in Traefik. The ConfigMap must use the key `ca.crt`.
- `hostname` must match the backend certificate (it must be in the SANs).
- Traefik doesn't implement `subjectAltNames` on this policy, so don't use that field.
- If the CA bundle is rotated in Vault, the ConfigMap needs re-syncing.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: backend-ca
  namespace: application-namespace
data:
  ca.crt: |
    -----BEGIN CERTIFICATE-----
    <Intermediate CA certificate>
    -----END CERTIFICATE-----
    -----BEGIN CERTIFICATE-----
    <Root CA certificate>
    -----END CERTIFICATE-----
---
apiVersion: gateway.networking.k8s.io/v1
kind: BackendTLSPolicy
metadata:
  name: sample-backend-tls
  namespace: application-namespace
spec:
  targetRefs:
    - group: ""
      kind: Service
      name: sample-service
  validation:
    hostname: sample-service.application-namespace.svc
    caCertificateRefs:
      - group: ""
        kind: ConfigMap
        name: backend-ca
```

### Unconfirmed alternative: ServersTransport annotation on the Service

The original draft used the annotation `traefik.io/service.serverstransport: traefik-kmaas-ca-chain@kubernetescrd` on the Service. The Traefik Gateway API docs only document `traefik.io/service.nativelb` as a Service annotation, so I couldn't confirm this works for Gateway API routes. **Test it before relying on it**, and fall back to `BackendTLSPolicy` if it has no effect.

---

## 5. Differences from Ingress + TLSOption

| Area | Ingress | IngressRoute (Path A) | Gateway API (Path B) |
|---|---|---|---|
| Route resource | `Ingress` | `IngressRoute` | `Gateway` + `HTTPRoute` |
| Attach mTLS policy | annotation `router.tls.options` | `tls.options` | only the global `default` TLSOption |
| Backend TLS | `ServersTransport` annotation | `ServersTransport` via `serversTransport` | `BackendTLSPolicy` |
| CA source | `ca.crt` in Secret | `ca.crt` in Secret (frontend and backend) | Secret (frontend), ConfigMap (backend) |
| Cross-namespace refs | `ns-name@kubernetescrd` | needs `allowCrossNamespace` | `ReferenceGrant` for Gateway API refs |

---

## 6. Validation

Replace `<TRAEFIK_IP>` and the port with how you reach Traefik externally (often `443` through the LB; the container entryPoint is a different port).

**Check the resources exist and are accepted**

```bash
# Path A
kubectl get secret,tlsoption,ingressroute,serverstransport -n application-namespace

# Path B
kubectl get gateway,httproute,backendtlspolicy -n application-namespace
kubectl describe backendtlspolicy sample-backend-tls -n application-namespace   # Accepted / ResolvedRefs
```

**1. No client certificate: must fail**

```bash
curl -v --resolve sample.internal.domain:443:<TRAEFIK_IP> \
  --cacert server-ca.pem https://sample.internal.domain/
```

Expect a TLS handshake failure (e.g. `certificate required` / `bad certificate`). An HTTP response here means mTLS isn't being enforced.

**2. Valid client certificate: must succeed**

```bash
curl -v --resolve sample.internal.domain:443:<TRAEFIK_IP> \
  --cacert server-ca.pem --cert client.crt --key client.key \
  https://sample.internal.domain/
```

**3. Client certificate from the wrong CA: must fail**

Repeat test 2 with a self-signed or other-CA client certificate.

**4. Client CA list is advertised**

```bash
openssl s_client -connect <TRAEFIK_IP>:443 -servername sample.internal.domain \
  -showcerts </dev/null
```

Look for `Acceptable client certificate CA names`. It should list your Intermediate and Root.

**5. Confirm which TLS option the route got**

Enable Traefik debug logging and look for the line `Adding route for sample.internal.domain with TLS options ...`. It should name `application-namespace-mtls-option@kubernetescrd`, not `default` (Path A). For Path B it should say `default`, since that's the only option Gateway routes can use.

**6. Backend validation**

- Normal request succeeds with valid certs.
- Break it on purpose: set `serverName` / `hostname` to a name not in the backend certificate. Expect `502 Bad Gateway`, and Traefik logs should show an x509 error.
- Path B: confirm `Accepted` and `ResolvedRefs` are `True` on the BackendTLSPolicy status.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Request without a client cert succeeds | TLSOption not bound. Path B: option isn't named `default`. Path A: wrong `tls.options` name/namespace. Check the debug log (test 5). |
| Every request fails with a certificate error | CA Secret missing `ca.crt`, wrong namespace (must match the TLSOption), or the client cert doesn't chain to the Secret's CA. |
| 502 Bad Gateway | Backend certificate not trusted, or `serverName`/`hostname` not in the backend SANs. |
| Gateway not Accepted/Programmed | Listener port doesn't match the Traefik entryPoint port, or `certificateRefs` Secret missing. |
| BackendTLSPolicy has no status | CRDs/RBAC for `backendtlspolicies` missing, or Gateway provider not enabled. |

---

## 7. Test matrix

| # | Test | Expected | Result |
|---|---|---|---|
| 1 | Resources created and accepted | all present, status OK | |
| 2 | No client cert | handshake fails | |
| 3 | Valid client cert | 200 from backend | |
| 4 | Wrong-CA client cert | handshake fails | |
| 5 | `s_client` CA list | Intermediate + Root listed | |
| 6 | Debug log TLS option | named option (Path A) | |
| 7 | Backend trusted | request succeeds | |
| 8 | Backend wrong serverName | 502 + x509 error | |
| 9 | Service annotation (unconfirmed, Path B only) | validates backend, or no effect | |

## 8. Open questions

- Does the platform provide a shared Gateway, a shared CA ConfigMap, or a shared `default` TLSOption? A global `default` option would change the Path B decision.
- Is `allowCrossNamespace` enabled on the CRD provider? This decides whether the platform's `traefik/mtls-option` and `traefik/kmaas-ca-chain` can be reused.
- Does the backend need Traefik to present a client certificate (full mutual auth)?
- Who owns CA rotation? The Secret is read live by Traefik, but the ConfigMap in Path B needs syncing from Vault.
- Listener-level mTLS on Gateway API is not supported in Traefik 3.7.x. Tracking issues: traefik/traefik #13818 (TLS options on listeners) and #11975 (`frontendValidation`). Recheck after upgrades.
