# NAP `Balanced` consolidation test: steps, commands and explanations

## What we are testing

NAP removes under-used nodes to save money (this is called consolidation).

- `WhenEmptyOrUnderutilized` (current policy): saves the most but restarts pods often.
- `Balanced` (new policy): only moves pods when the saving is worth the disruption.

**Question:** does `Balanced` save about as much money with fewer pod restarts?

**Method:** deploy 20 pods, leave one pod on every node, and watch what NAP does. Do this with `Balanced`, then with the current policy, and compare.

Dev cluster only. The pool also runs cluster add-ons, and consolidation can move them.

**Names used here** (find and replace with yours):

| Name | What it is |
|---|---|
| `my-aks-cluster` | The test cluster's kubectl context |
| `nap-test` | Namespace for the test |
| `nap-test-app` | The test deployment |
| `/tmp/nap-test.yaml` | Your existing manifest for the 20 replicas |
| `system` | The NodePool under test (already set to `Balanced`) |

---

## Step 1: Check the setup

**1a. Point kubectl at the test cluster**

```bash
kubectl config use-context my-aks-cluster
```

*What it does:* switches kubectl to the test cluster, so nothing runs against the wrong one.

**1b. Check the pool is on `Balanced`**

```bash
kubectl get nodepools.karpenter.sh system -o jsonpath='{.spec.disruption}{"\n"}'
```

*What it does:* prints the disruption settings of the `system` pool. You expect to see `Balanced`, `30s` and a 10% budget. 📸 Take a screenshot for the ticket. If it is not `Balanced`, fix that first.

**1c. Create the namespace**

```bash
kubectl create ns nap-test
```

*What it does:* makes a namespace for the test, so cleanup is just deleting it. Skip this if it already exists.

**1d. Record the baseline node count**

```bash
kubectl get nodes -l karpenter.sh/nodepool=system --no-headers | wc -l
```

*What it does:* counts the nodes in the pool. The label filter (`-l`) picks the nodes in the `system` pool, `--no-headers` drops the header row, and `wc -l` counts the lines. **Write the number down.** This is your baseline.

---

## Step 2: Deploy the 20 test pods

**2a. Create the deployment**

Use your existing manifest (20 replicas, 500m CPU requests, `sleep infinity`).

```bash
kubectl apply -f /tmp/nap-test.yaml
```

*What it does:* creates the 20-pod deployment. Karpenter sees 20 pods needing space and launches about 7 nodes. The 500m CPU requests matter because Karpenter works from requests, not actual usage. `sleep infinity` keeps the pods alive without doing anything, so any restart you see is caused by consolidation.

**2b. Wait until all pods are running**

```bash
kubectl -n nap-test get pods --no-headers | grep -vc Running
```

*What it does:* counts the pods that are **not** Running. `grep -v` keeps the lines that do not contain "Running", and `-c` counts them. Wait until it prints `0`, then wait 5 more minutes so things are stable.

If pods stay Pending, run `kubectl -n nap-test describe pod <pod-name>` and read the Events at the bottom. The usual causes are quota, a taint or a selector mismatch.

**2c. Check the starting point**

```bash
kubectl -n nap-test get pods -o wide --no-headers | awk '{print $7}' | sort | uniq -c
```

*What it does:* shows how many test pods are on each node.
- `-o wide` adds extra columns. Column 7 is the node name.
- `awk '{print $7}'` prints only that column.
- `sort` groups the same node names together.
- `uniq -c` counts each name.

You should see about 7 nodes with about 3 pods each. You will use this command several times.

---

## Step 3: Create the imbalance

**Goal:** leave exactly one pod on every node. If you just scale down, Kubernetes removes pods at random and the cluster may stay balanced. So first you tag the pods that should go.

**3a. Tag all but one pod on each node**

```bash
kubectl -n nap-test get pods -o wide --no-headers | awk '{print $7}' | sort -u | xargs -I{} sh -c 'kubectl -n nap-test get pods --field-selector spec.nodeName={} -o name | tail -n +2 | xargs -I% kubectl -n nap-test annotate % controller.kubernetes.io/pod-deletion-cost=-1000 --overwrite'
```

*What it does:*
1. `... | sort -u` makes a list of the nodes that have test pods, each once.
2. `xargs -I{} sh -c '...'` runs the quoted command once per node, replacing `{}` with the node name.
3. Inside it, `get pods --field-selector spec.nodeName={} -o name` lists the pods on that node.
4. `tail -n +2` skips the first pod, so the rest are selected.
5. `annotate ... pod-deletion-cost=-1000` tags each selected pod. This is a built-in Kubernetes hint meaning "delete me first when scaling down". The default is `0`, so `-1000` goes first.
6. `--overwrite` lets you re-run the command without an error. The `%` is used instead of `{}` because the second `xargs` is nested inside the first.

Result: every node has one untagged pod, and the others are tagged to go first.

**3b. Record the start time**

```bash
date
```

*What it does:* prints the current time. This is **t=0**, the start of your measurements.

**3c. Scale down to one pod per node**

```bash
kubectl -n nap-test scale deploy nap-test-app --replicas=$(kubectl -n nap-test get pods -o wide --no-headers | awk '{print $7}' | sort -u | wc -l | tr -d ' ')
```

*What it does:* changes the deployment to the number of nodes.
- `$(...)` runs the inner command first and puts its output in that place. The inner command counts the nodes that have test pods.
- `tr -d ' '` removes the spaces macOS adds to that number.
- Since there is one untagged pod per node, scaling to that number removes exactly the tagged pods.

Kubernetes removes these pods, not Karpenter. Karpenter only reacts afterwards, when it sees the nearly empty nodes.

**3d. Confirm the imbalance**

Wait 1 minute, then run:

```bash
kubectl -n nap-test get pods -o wide --no-headers | awk '{print $7}' | sort | uniq -c
```

*What it does:* the same pods-per-node check as 2c. **Every node should now show `1`.** If not, stop and ask for help, because the test is not valid.

---

## Step 4: Watch for 20 minutes

```bash
kubectl get nodeclaims -w
```

*What it does:* streams Karpenter's NodeClaims, one per node it manages (`-w` means watch). A new NodeClaim appearing means a node is being launched (a replacement). A NodeClaim disappearing means a node is being removed.

Do not change anything. Note:
- how long until the first node is removed (compare with the time from 3b)
- whether nodes are **deleted** (pods move to existing nodes) or **replaced** (a new NodeClaim appears first)
- whether it removes one node at a time (the 10% budget allows roughly one at a time)
- when the node count stops changing

20 minutes is needed because Karpenter waits 30 seconds before acting, then disrupts about one node at a time, and each node needs time to drain.

**If nothing happens, that is a valid result.** `Balanced` skips moves where the saving is too small, and skipped moves create no events.

---

## Step 5: Collect the results

**5a. Finish time**

```bash
date
```

*What it does:* records when you finished, so you can work out how long it took.

**5b. Final node count**

```bash
kubectl get nodes -l karpenter.sh/nodepool=system --no-headers | wc -l
```

*What it does:* the same count as 1d. The difference from before the scale-down is how many nodes were removed.

**5c. Test pods and their ages**

```bash
kubectl -n nap-test get pods -o wide
```

*What it does:* shows the test pods with their age and node. A small AGE means the pod was evicted and recreated by consolidation. Count those.

**5d. Consolidation events**

```bash
kubectl get events -A --sort-by=.lastTimestamp | grep -iE "disrupt|consolidat|evict"
```

*What it does:* shows Kubernetes events about consolidation. `-A` means all namespaces, `--sort-by` puts the newest last, and `grep -iE` keeps lines containing any of those words (`-i` ignores case and `|` means "or"). Events only last about an hour, so run this soon after the test.

**5e. Check whether add-on pods were moved**

```bash
kubectl get pods -A --sort-by=.metadata.creationTimestamp | tail -10
```

*What it does:* lists pods in all namespaces from oldest to newest and shows the last 10. A small age on a pod that is not one of your test pods means an add-on was moved.

---

## Step 6: Repeat with the current policy

**6a. Remove the test pods**

```bash
kubectl delete -f /tmp/nap-test.yaml
```

*What it does:* deletes everything described in the file. The nodes become empty and drain away over several minutes.

**6b. Wait until the pool is back to baseline**

```bash
kubectl get nodes -l karpenter.sh/nodepool=system --no-headers | wc -l
```

*What it does:* the same node count as 1d. Run it every few minutes until it matches your baseline, so the second run starts from the same state as the first.

**6c. Switch the pool to the current policy**

```bash
kubectl patch nodepools.karpenter.sh system --type merge -p '{"spec":{"disruption":{"consolidationPolicy":"WhenEmptyOrUnderutilized"}}}'
```

*What it does:* changes one field on the pool. `patch` edits part of an object, `--type merge` means "merge my change into what is there", and `-p` is the change. The policy is the only thing that changes between the two runs, so any difference in behaviour is down to the policy. Existing nodes are not replaced.

This puts the cluster out of step with Terraform. Do not run `terraform apply` until you finish Step 8.

**6d. Check it stayed**

```bash
kubectl get nodepools.karpenter.sh system -o jsonpath='{.spec.disruption.consolidationPolicy}{"\n"}'
```

*What it does:* prints just the policy. Run it, wait 1 minute, and run it again to make sure nothing changed it back. If it was reverted, the second run would just repeat the first.

**6e. Run the test again**

Repeat Steps 2 to 5 exactly the same.

---

## Step 7: Compare and decide

Fill this in from your two runs:

| Measure | `Balanced` | `WhenEmptyOrUnderutilized` |
|---|---|---|
| Nodes before / after | | |
| Deleted or replaced | | |
| Test pods recreated | | |
| Add-on pods moved? | | |
| Time to first node removed | | |
| Time to settle | | |

How to read it:
- Same nodes removed but fewer pods restarted: `Balanced` is better.
- Same behaviour: little difference.
- `Balanced` does nothing while the current policy consolidates: it may be too cautious. Note the cost of the nodes it left running. You can also try a smaller scale-down to see where it starts to act.

**Pros:** fewer evictions when the saving is small, still saves when it is large, one field per pool, no node replacement when switching.

**Cons:** may leave under-used nodes running (slightly higher cost), and decisions are hard to see on NAP because rejected moves create no events.



| Result | Action |
|---|---|
| `Balanced` is better (code change needed) | Create an implementation ticket |
| `Balanced` is better (no code change) | Update the documentation |
| No real benefit | Write up the findings and close |

---

~~~~
~~~~