# Selected natural-run infrastructure

The user selected Hetzner for the CPU/desktop candidates and RunPod for the H100
research subjects on 7 October 2026. The [revision record](revisions/2026-10-07-hetzner-runpod.md)
links the five task replacements and the separate PaperBench grading adaptation.
This is an intended allocation. Native qualification, account quotas, compatible
stock and cloud provisioning remain pending.

| Intended placement | Task slots | Qualification still needed |
| --- | ---: | --- |
| Hetzner CPU/desktop pool | 192 | Enforce native resource allocations, service isolation and desktop VM boot. This count includes 32 closed-book controller tasks with no subject tools or delegation. |
| RunPod H100 research subjects | 8 | Four PaperBench and four PostTrainBench tasks. Verify actual GPU memory, CPU/RAM allocation, dependency support, persistent state and recovery. |
| Separate PaperBench reproduction grading | Outside the 200 subject slots | Qualify the approved H100 environment, native reproduction rules, private rubric and seven-day grading maximum. |

The five replacements remove the Windows, T4 and L4 requirements. Molecular
Cartesian geometry and the retained Terminal-Bench CAD task provide 3D work;
the Gaussian-splatting task's visual-media creation coverage is removed. OSWorld
still needs its separate Ubuntu x86 desktop guests. Keep each native CPU and
memory allocation. The ordinary 4-CPU/16-GiB baseline applies only when a native
allocation is absent.

PaperBench fresh reproduction uses one H100 with 80 GB GPU memory, 16 CPU units,
128 GiB RAM and 400 GB storage. It is separate from the uncapped subject attempt.
The original rubric remains unchanged and grader-only. Its seven-day maximum
is an external reproduction limit, never a subject work deadline. This AgentTime
adaptation does not establish original PaperBench comparability or RunPod
reproduction readiness. Preserve task-specific disk units; PostTrainBench's
400 GiB is distinct from PaperBench's 400 GB.

Choose exact regions and compatible machines, map each declared CPU unit to the
provider's allocation, and quote storage retention, archive transfer and grader
costs separately. The revised 168 subject compute allocations require 695 declared CPU units,
3,088 GiB RAM and eight H100s at full overlap. These are intended allocations,
not a claim about provisioned physical cores. Controllers, hypervisors, spares
and separate H100 grading are additional. Bind quotes to the combined profile
evidence in the new preparation pack.
Forecast values must not determine resources or stopping times. Any operational
stop produces a censored observation.

Prefer non-preemptible capacity for the first natural cohort. Before removing
compute, prove the subject has stopped and verify that the required state survives
removal. Qualify native session restoration and independent Mac archives with
disposable workloads before any selected task starts. Check provider capacity
and agent-subscription traffic capacity separately. See the
[natural preparation plan](NATURAL_RUN_PREPARATION.md) for the task holds and build
order. No account, credential, capacity or current price check is claimed here.

## Deployment constraints checked 7 October 2026

OSWorld requires a compatible desktop-VM host. Hetzner's Cloud FAQ explicitly
rules out nested virtualization, including Cloud instances with dedicated CPU
resources. Plan a suitable dedicated physical host and verify native KVM/guest
boot, observation, capture and resource enforcement before admission. This stays
within the selected provider pair; the exact host is not chosen or rented.
[Hetzner Cloud FAQ](https://docs.hetzner.com/cloud/servers/faq/#can-i-run-virtual-machines-on-cloud-servers-or-rather-is-nested-virtualization-possible).

RunPod's GPU Pod overview says an inner Docker daemon and Docker Compose are
unsupported. Qualify a direct provider/container execution path for the pinned
GPU task images and graders; do not infer GPU capability from a CPU product
announcement. This remains a native integration gate.
[RunPod Pod limitations](https://docs.runpod.io/pods/overview#limitations).

Model access is independent of compute placement. The user selected a Claude Code
subscription/API mix while retaining the existing subscription forecasts. Freeze
and record each task's route before launch, qualify both routes, and carry the
API/subscription forecast-pairing difference into analysis. The [launch plan](superpowers/plans/2026-10-07-opus-natural-launch.md)
leaves the exact split and spending choices open.

## Historical comparison checked 6 October 2026

The section below preserves the earlier provider comparison. Its Windows and
mixed-GPU assumptions describe the previous roster. Its recommendation and price
anchors are historical; they do not override the selected allocation above or
provide current prices.

<details>
<summary>Read the original 6 October comparison</summary>

# Choosing natural-run infrastructure

Checked 6 October 2026. This is a public-document comparison, not a deployment
quote or an account-capacity check. No resources have been provisioned.

My provisional recommendation is an AWS or Google Cloud VM fleet for the main
study. Compare RunPod on-demand Pods for the H100 workloads after testing their
exact environment and preservation needs. Start with one main cloud if its
credits and quotas make that affordable; adding a second provider adds recovery,
transfer and operational work. Modal is useful for bounded preparation or
grading, but its current sandbox lifetime is a problem for our uncapped subjects.

This recommendation follows from the suite's mix of Linux containers, nested
services, Windows desktops and long GPU sessions. It is not a measured provider
performance ranking.

| Provider | Best potential role | Main condition to resolve |
| --- | --- | --- |
| AWS EC2 | Main Linux and Windows VM fleet; single-H100 machines are also available as an instance type. | Check regional Standard, G and P quotas, capacity, exact images, instance cost and persistent-disk lifecycle. |
| Google Compute Engine | Main Linux and desktop fleet; explicit support for Windows GPU workstations. | Match benchmark guest/software state. Small A3 H100 allocations have provisioning constraints described below. |
| RunPod Pods | Compare for dedicated H100 subject workers; CPU Pods now support Docker and network volumes. | Verify exact RAM/CPU/GPU allocation, Docker needs of each task and a compatible Harbor provider path. Native Windows support is not established by the sources checked. |
| Modal Sandboxes | Automated disposable preparation and compatible bounded graders. | Maximum sandbox lifetime is 24 hours. GPU sandboxes currently use gVisor; Docker-in-sandbox requires the VM runtime. This is not a ready substitute for our uncapped GPU workers. |

AWS documents Linux and Windows on-demand billing, and `p5.4xlarge` provides one
H100 with 16 vCPUs and 256 GiB RAM. That host would still need an enforced subject
allocation matching our contract. AWS quotas are regional and instance-family
specific; documented defaults are far below this cohort.
[AWS pricing](https://aws.amazon.com/ec2/pricing/on-demand/),
[P5 specifications](https://aws.amazon.com/ec2/instance-types/p5/),
[EC2 quotas](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html).

Google provides a Windows Server GPU-workstation route with L4 and T4 options.
That does not prove compatibility with every benchmark's required Windows image.
For H100s, the documented `a3-highgpu-1g`, `2g` and `4g` route requires Spot or
Flex-start. Flex-start has a seven-day maximum lifetime, which is still an
external limit. Do not silently impose it on a natural run or treat interruption
as completion. Larger or reserved options need a specific quote.
[Windows workstations](https://docs.cloud.google.com/compute/docs/virtual-workstation/windows-gpu),
[A3 provisioning](https://docs.cloud.google.com/ai-hypercomputer/docs/create/create-vm-a3-high-mega),
[Flex-start lifecycle](https://docs.cloud.google.com/compute/docs/instances/about-flex-start-vms).

RunPod's CPU announcement specifically adds Docker and independent network
volumes. It does not establish all nested-Docker or VM requirements for GPU Pods.
A Pod volume disk survives stop/restart but is deleted at termination; use a
qualified independent export/volume arrangement before removing compute. A
stopped GPU Pod can also lose access to its previous GPU slot.
[CPU support](https://www.runpod.io/blog/enhanced-cpu-pods-docker-network),
[storage lifecycle](https://docs.runpod.io/pods/storage/types),
[GPU availability on restart](https://docs.runpod.io/pods/troubleshooting/zero-gpus).

Modal's documented 24-hour limit conflicts with an uncapped study unless a
separate continuation design is qualified. GPU sandboxes use gVisor, while its
VM runtime supports nested Docker. Filesystem snapshots exclude mounted volumes;
experimental memory snapshots have additional limits, including no GPUs and
incomplete restoration of background processes launched through `exec`. Do not
treat a snapshot feature as proof that native sessions and tasks can resume.
[Sandbox lifecycle and runtimes](https://modal.com/docs/guide/sandboxes),
[snapshot limits](https://modal.com/docs/guide/sandbox-snapshots).

## Price anchors, not a fleet estimate

| Example | Published price or quoting basis | Exclusions / comparability |
| --- | --- | --- |
| RunPod H100 PCIe Pod | $2.89/hour on the checked public page | Advertised bundle: 16 vCPUs and 188 GB RAM. Verify actual compatible stock, unit conversion, disk and network charges. |
| RunPod H100 SXM Pod | $3.49/hour on the checked public page | Advertised 125 GB RAM is below the required 128 GiB research profile; do not select it unchanged. |
| Modal H100 SXM5 | About $3.95/hour for the GPU alone | CPU and memory are additional; the sandbox lifetime still applies. |
| Modal sandbox CPU/memory | About $0.142 per physical core-hour plus $0.024 per GiB-hour | One Modal physical core is documented as two vCPUs. Minimum request or actual usage, whichever is higher, determines billing. |
| AWS CPU example | The official us-east-1 example lists `m6i.xlarge` at $0.192/hour | Illustration only; obtain a fresh region/OS/storage-specific quote, particularly for Windows and GPU machines. |
| Google Cloud | Quote exact machine family, region, disks, Windows/vWS license and GPU provisioning model | No comparable all-in quote was established by this public-document check. |

Prices are USD before applicable taxes. These rows are different products, not
equal-performance offers. GPU form factor, usable memory, CPU allocation, disk,
network and runtime limits must be matched before comparing totals.
[RunPod pricing](https://www.runpod.io/pricing),
[Modal pricing](https://modal.com/pricing),
[AWS published example](https://docs.aws.amazon.com/prescriptive-guidance/latest/optimize-costs-microsoft-workloads/right-size-selection.html),
[Google pricing scope](https://cloud.google.com/products/compute/gpus-pricing).

Do not multiply model forecasts into an infrastructure budget and then use that
budget to stop subjects. Quote infrastructure hourly capacity, disk retention,
archive egress and separate grader costs. Choose any operational spending/stop
policy explicitly; an enforced stop produces a censored observation.

## What the final provider choice needs

The [frozen requirements](NATURAL_RUN_PREPARATION.md) call for approximately 3 TiB
of subject RAM at full overlap and a mixed CPU/GPU/desktop fleet. Add controller,
guest-host and grading overhead. Obtain actual account quotas and compatible
stock before claiming 200-way capacity. Use disposable workloads for the ramp.

Prefer non-preemptible capacity for the first natural cohort. This is a
recommendation to avoid adding preventable interruptions, not a promise of
failure-free operation. Every provider still needs our native timing, independent
Mac archive, stop-proof and recovery qualification. The agent subscription's
tool-enabled traffic capacity is separate from cloud compute capacity.

Before selecting AWS versus Google Cloud, check the user's available credits,
existing quotas and required regions. RunPod can be a second GPU pool only if the
concrete cost saving and compatibility justify operating both. No provider or
region has been selected by this comparison.

</details>
