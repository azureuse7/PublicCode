import pathlib, yaml, re, sys

ROOT = pathlib.Path(__file__).parent
docs, policies, bindings = [], {}, []

for f in sorted(ROOT.glob("*.yaml")):
    for d in yaml.safe_load_all(f.read_text(encoding="utf-8")):
        if d:
            docs.append((f.name, d))

errs, warns = [], []
for fn, d in docs:
    kind = d.get("kind")
    name = d.get("metadata", {}).get("name")
    if d.get("apiVersion") != "admissionregistration.k8s.io/v1":
        errs.append(f"{fn}: unexpected apiVersion {d.get('apiVersion')}")
    if kind == "ValidatingAdmissionPolicy":
        policies[name] = d
    elif kind == "ValidatingAdmissionPolicyBinding":
        bindings.append((fn, name, d))
    else:
        errs.append(f"{fn}: unexpected kind {kind}")

VALID_REASONS = {"Unauthorized", "Forbidden", "Invalid", "RequestEntityTooLarge"}
VALID_OPS = {"CREATE", "UPDATE", "DELETE", "CONNECT", "*"}

for name, p in policies.items():
    spec = p["spec"]
    if spec.get("failurePolicy") != "Fail":
        warns.append(f"{name}: failurePolicy is not Fail")
    if not spec.get("matchConstraints", {}).get("resourceRules"):
        errs.append(f"{name}: no resourceRules")
    for rule in spec["matchConstraints"]["resourceRules"]:
        for op in rule.get("operations", []):
            if op not in VALID_OPS:
                errs.append(f"{name}: bad operation {op}")
        for k in ("apiGroups", "apiVersions", "resources"):
            if not rule.get(k):
                errs.append(f"{name}: rule missing {k}")
    if not spec.get("validations"):
        errs.append(f"{name}: no validations")
    for v in spec.get("validations", []):
        if "expression" not in v:
            errs.append(f"{name}: validation without expression")
        if v.get("reason") and v["reason"] not in VALID_REASONS:
            errs.append(f"{name}: invalid reason {v['reason']}")

    # variables are NOT available inside matchConditions - catch that mistake
    for mc in spec.get("matchConditions", []):
        if "variables." in mc.get("expression", ""):
            errs.append(f"{name}/{mc.get('name')}: matchCondition references "
                        f"variables, which Kubernetes evaluates too late")

    # crude CEL sanity: balanced quotes and parens across every expression
    exprs = [v.get("expression", "") for v in spec.get("validations", [])]
    exprs += [v.get("messageExpression", "") for v in spec.get("validations", [])]
    exprs += [mc.get("expression", "") for mc in spec.get("matchConditions", [])]
    for e in filter(None, exprs):
        if e.count("(") != e.count(")"):
            errs.append(f"{name}: unbalanced parens in: {e[:60]}")
        if e.count("'") % 2:
            errs.append(f"{name}: unbalanced quotes in: {e[:60]}")
        if e.count('"') % 2:
            errs.append(f"{name}: unbalanced dquotes in: {e[:60]}")

for fn, bname, b in bindings:
    pn = b["spec"].get("policyName")
    if pn not in policies:
        errs.append(f"{fn}: binding {bname} -> unknown policy '{pn}'")
    va = b["spec"].get("validationActions", [])
    if not va:
        errs.append(f"{fn}: binding {bname} has no validationActions")
    for a in va:
        if a not in {"Deny", "Audit", "Warn"}:
            errs.append(f"{fn}: bad validationAction {a}")

print(f"policies: {len(policies)}  bindings: {len(bindings)}\n")
for n, p in policies.items():
    r = p["spec"]["matchConstraints"]["resourceRules"][0]
    bound = [b for _, _, b in bindings if b["spec"]["policyName"] == n]
    print(f"  {n}")
    print(f"      ops       {','.join(r['operations'])}")
    print(f"      resources {','.join(r['resources'])}")
    print(f"      names     {','.join(r.get('resourceNames', ['<any>']))}")
    print(f"      matchCond {len(p['spec'].get('matchConditions', []))}")
    print(f"      binding   {bound[0]['spec']['validationActions'] if bound else 'NONE'}")
    print()

for w in warns:
    print("WARN ", w)
for e in errs:
    print("ERROR", e)
print(f"\n{len(errs)} errors, {len(warns)} warnings")
sys.exit(1 if errs else 0)
