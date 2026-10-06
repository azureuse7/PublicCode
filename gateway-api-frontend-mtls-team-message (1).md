

---

## Full evidence

Frontend (client cert) mTLS per application is not possible through Gateway API on our platform (Traefik v3.7.13, Gateway API CRDs v1.6.2).

There are two separate reasons.

### 1. Traefik does not implement it

Gateway API has a field for client-certificate validation (`Gateway.spec.tls.frontend`, Standard since v1.5.0), but Traefik v3.7.x never reads it. The API server accepts the setting and the Gateway still reports Programmed, but clients with no certificate are not rejected.

- Traefik's own conformance report lists `GatewayFrontendClientCertificateValidation` as unsupported (Gateway API v1.6.1, Traefik v3.7.10):
  https://github.com/kubernetes-sigs/gateway-api/blob/ff199fbed745eb75c0a1fce4fb5cbe73eaa46805/conformance/reports/v1.6/traefik-traefik/experimental-v3.7.10-default-report.yaml#L57-L62
- In v3.7.13 the listener TLS code reads only `mode` and `certificateRefs`. Listener `tls.options` is never read:
  https://github.com/traefik/traefik/blob/v3.7.13/pkg/provider/kubernetes/gateway/kubernetes.go#L550-L602
- No non-test code in the Gateway provider mentions `Frontend`, `ClientCertificateRef` or `TLS.Options` in v3.7.13 or v3.7.14. You can repeat this (it prints nothing):

  ```bash
  git clone --depth 1 --branch v3.7.13 https://github.com/traefik/traefik && cd traefik
  git grep -nE 'Frontend|ClientCertificateRef|TLS\.Options' -- pkg/provider/kubernetes/gateway ':!*_test.go'
  ```

- The maintainers still have it as a TODO on master (6 Oct 2026): "The Gateway frontend TLS configuration (client certificate validation) has to be part of it once supported."
  https://github.com/traefik/traefik/blob/bb4bdd60cf3131f7066af94e0e3133edfd0f3326/pkg/provider/kubernetes/gateway/kubernetes.go#L823-L824

### 2. Even once Traefik implements it, the spec only allows it per Gateway or per port, never per app

The only scopes are `default` (all HTTPS listeners) and `perPort`. Per-listener config was removed on purpose, because HTTP/2 connection reuse across listeners on the same port could bypass client certificate validation. HTTPRoute has no TLS field at all.

- Field definition (`default` and `perPort` only):
  https://github.com/kubernetes-sigs/gateway-api/blob/v1.6.2/apis/v1/gateway_types.go#L652-L677
- GEP-91, client certificate validation:
  https://gateway-api.sigs.k8s.io/geps/gep-91/
- GEP-3567, why per-listener was dropped (HTTP/2 connection coalescing):
  https://github.com/kubernetes-sigs/gateway-api/blob/v1.6.2/geps/gep-3567/index.md
- Listener `tls.options` is only "Implementation-specific" in the spec, and Traefik ignores it:
  https://github.com/kubernetes-sigs/gateway-api/blob/v1.6.2/apis/v1/gateway_types.go#L615-L628

### What works per application today

IngressRoute + TLSOption (`clientAuthType: RequireAndVerifyClientCert`, KMaaS CA chain). Traefik applies TLS options per hostname and returns 421 when a request's Host maps to different TLS options from the connection's, so the coalescing bypass is blocked:
https://github.com/traefik/traefik/blob/v3.7.13/pkg/middlewares/snicheck/snicheck.go#L26-L44

Watch out: do not serve the same hostname from both an HTTPRoute and an IngressRoute. If their TLS options differ, Traefik logs an error and falls back to the default options, so that host silently loses mTLS:
https://github.com/traefik/traefik/blob/v3.7.13/pkg/server/aggregator.go#L301-L317

### Backend TLS is not affected

Backend TLS (Traefik to pod) is not affected. `BackendTLSPolicy` is supported on v3.7.13 (same conformance report, L37-L38):
https://github.com/kubernetes-sigs/gateway-api/blob/ff199fbed745eb75c0a1fce4fb5cbe73eaa46805/conformance/reports/v1.6/traefik-traefik/experimental-v3.7.10-default-report.yaml#L37-L38



---

## Short version



1. Traefik doesn't implement it. Gateway API's client-cert validation field (`Gateway.spec.tls.frontend`) is accepted by the API, but Traefik never reads it, so the Gateway shows Programmed and clients without a cert still get through. Traefik's own conformance report lists it as unsupported:
   https://github.com/kubernetes-sigs/gateway-api/blob/ff199fbed745eb75c0a1fce4fb5cbe73eaa46805/conformance/reports/v1.6/traefik-traefik/experimental-v3.7.10-default-report.yaml#L57-L62
   The maintainers' own TODO on master confirms it isn't built yet:
   https://github.com/traefik/traefik/blob/bb4bdd60cf3131f7066af94e0e3133edfd0f3326/pkg/provider/kubernetes/gateway/kubernetes.go#L823-L824

2. Even when Traefik adds it, the spec only allows it per Gateway or per port, not per app (per-listener was removed on purpose to avoid an HTTP/2 connection-reuse bypass):
   https://gateway-api.sigs.k8s.io/geps/gep-91/

