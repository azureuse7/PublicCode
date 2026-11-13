# NAP node class: GPU, encryption at host and swap

## GPU mode (`gpu_mode`, which becomes `spec.gpu.mode`)

* `Driver` is the default. AKS installs the NVIDIA GPU drivers for you, and NAP only picks GPU VM sizes that support managed drivers.
* `None` skips driver installation. It's for teams that install drivers themselves, usually with the NVIDIA GPU Operator, and NAP can pick any GPU VM size.
* On normal VMs with no GPU, like D2ds, the setting is ignored.
* The NVIDIA management mode setting isn't included in this PR.

## Encryption at host (`encryption_at_host`, which becomes `spec.security.encryptionAtHost`)

* What it does: encrypts the VM's temp disk and the OS and data disk caches on the Azure host itself. It's off by default.
* Two prerequisites: the `EncryptionAtHost` feature has to be registered on the subscription, and the VM size has to support it.
* Why the pool is restricted: many B-series sizes don't support it, so the test pool only allows D2ds and D4ds.

## Swap (`swap_file_size`, which becomes `spec.linuxOSConfig.swapFileSize`)

* What it does: creates a swap file on each node, for example `"2Gi"`, so memory can spill to disk.
* The catch: by default the kubelet refuses to start when swap exists. That's `failSwapOn: true`. It only works when `failSwapOn` is set to `false`, under `spec.kubelet.failSwapOn`.
* Why it's not in the PR: `failSwapOn` belongs to the kubelet config, not the Linux OS config, so it's out of scope here and needs a follow-up ticket.

## What happens to existing nodes

NAP nodes only pick up any of these settings when they're first built. If you change an existing node class, Karpenter replaces its nodes, so roll changes out carefully.
