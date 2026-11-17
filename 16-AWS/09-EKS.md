# Amazon EKS: Elastic Kubernetes Service

> Amazon EKS is a fully managed Kubernetes control plane. AWS handles the master nodes, etcd, and upgrades — you manage worker nodes (or use Fargate/Auto Mode for serverless nodes) and deploy workloads with standard `kubectl`.

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│           AWS Managed Control Plane          │
│  (API server, etcd, scheduler, controllers)  │
└────────────────────┬────────────────────────┘
                     │ kubectl / AWS CLI
        ┌────────────┴────────────┐
        │                         │
  EC2 Node Group            Fargate Profile
  (managed or self)         (serverless pods)
```

---

## Prerequisites

- AWS CLI configured with appropriate IAM permissions
- `kubectl` installed
- `eksctl` installed (recommended for cluster management)
- Sufficient IAM permissions to create EKS clusters, EC2, IAM roles

---

## Step 1: Configure AWS CLI

```bash
aws configure
# Enter: Access Key ID, Secret Access Key, default region, output format
```

Verify your identity:

```bash
aws sts get-caller-identity
```

---

## Step 2: Create a Cluster

### Using eksctl (recommended)

```bash
eksctl create cluster \
  --name demo-cluster \
  --region us-east-1 \
  --nodegroup-name demo-nodes \
  --node-type t3.medium \
  --nodes 2 \
  --nodes-min 1 \
  --nodes-max 4 \
  --managed
```

### Using AWS CLI

```bash
aws eks create-cluster \
  --name demo-cluster \
  --region us-east-1 \
  --kubernetes-version 1.29 \
  --role-arn arn:aws:iam::123456789012:role/AmazonEKSClusterRole \
  --resources-vpc-config subnetIds=subnet-aaa,subnet-bbb,securityGroupIds=sg-xxx
```

---

## Step 3: Configure kubectl

```bash
aws eks update-kubeconfig \
  --name demo-cluster \
  --region us-east-1
```

Verify:

```bash
kubectl get nodes
kubectl get pods --all-namespaces
```

---

## Step 4: Create a Fargate Profile (optional)

Use Fargate to run pods without managing EC2 nodes:

```bash
eksctl create fargateprofile \
  --cluster demo-cluster \
  --region us-east-1 \
  --name my-fargate-profile \
  --namespace my-app
```

---

## Step 5: Deploy the AWS Load Balancer Controller

The ALB controller watches `Ingress` objects and provisions Application Load Balancers automatically.

### 5a. Associate the OIDC Provider

```bash
cluster_name=demo-cluster
region=us-east-1

eksctl utils associate-iam-oidc-provider \
  --cluster $cluster_name \
  --region $region \
  --approve
```

### 5b. Create the IAM Policy

```bash
curl -O https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.5.4/docs/install/iam_policy.json

aws iam create-policy \
  --policy-name AWSLoadBalancerControllerIAMPolicy \
  --policy-document file://iam_policy.json
```

### 5c. Create the IAM Service Account

```bash
eksctl create iamserviceaccount \
  --cluster=$cluster_name \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --role-name AmazonEKSLoadBalancerControllerRole \
  --attach-policy-arn=arn:aws:iam::<ACCOUNT_ID>:policy/AWSLoadBalancerControllerIAMPolicy \
  --approve
```

### 5d. Install via Helm

```bash
helm repo add eks https://aws.github.io/eks-charts
helm repo update eks

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=$cluster_name \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set region=$region \
  --set vpcId=<YOUR_VPC_ID>
```

Verify:

```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
```

---

## Step 6: Deploy a Sample Application with Ingress

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.5.4/docs/examples/2048/2048_full.yaml
```

After a minute, check the Ingress for the ALB address:

```bash
kubectl get ingress -n game-2048
```

---

## Networking Setup Checklist

| Component | Required | Notes |
|---|---|---|
| VPC | Yes | Dedicated VPC recommended (not default) |
| Public subnets | Yes | Tag: `kubernetes.io/role/elb=1` |
| Private subnets | Yes | Tag: `kubernetes.io/role/internal-elb=1` |
| Internet Gateway | Yes | For public ALB and node internet access |
| NAT Gateway | Yes (private nodes) | Allows private nodes to pull images |
| Security Group | Yes | Allow node-to-node and control plane communication |

---

## IAM Roles Summary

| Role | Purpose | Key Policies |
|---|---|---|
| **Cluster IAM Role** | EKS control plane | `AmazonEKSClusterPolicy` |
| **Node IAM Role** | EC2 worker nodes | `AmazonEKSWorkerNodePolicy`, `AmazonEC2ContainerRegistryReadOnly`, `AmazonEKS_CNI_Policy` |
| **Load Balancer Controller Role** | ALB provisioning | Custom `AWSLoadBalancerControllerIAMPolicy` (IRSA) |

---

## Common kubectl Commands

```bash
# Check cluster info
kubectl cluster-info

# List all nodes
kubectl get nodes -o wide

# List all pods across namespaces
kubectl get pods --all-namespaces

# Describe a failing pod
kubectl describe pod <pod-name> -n <namespace>

# Stream pod logs
kubectl logs -f <pod-name> -n <namespace>

# Execute a shell inside a running pod
kubectl exec -it <pod-name> -n <namespace> -- /bin/sh
```

---

## Summary

EKS removes the operational burden of managing the Kubernetes control plane. The typical setup path is: create a cluster → associate OIDC → configure networking → deploy the ALB controller → deploy workloads. Use Fargate profiles or EKS Auto Mode to eliminate EC2 node management entirely.
