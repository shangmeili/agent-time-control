# Security and operational scope

Run only commands and model/tool calls independently authorized by the host.
Skills and timeboxes do not expand permissions. Review Skill source before local
installation; a checksum detects changed bytes but is not an authenticity signature.

The MCP service uses stdio, has no arbitrary file/command tool and does not persist
measurements. Do not expose it as an unauthenticated network endpoint. Apply input
size/rate limits in any network bridge. Forecasting accepts at most 10000
observations and 1000 serial steps; host transports should also cap message bytes.

TimingRecorder is opt-in and memory-only. Timing can still disclose workload
characteristics. Use opaque operation/reference-class identifiers and approved
storage/retention policies. Never include secrets in identifiers or publish raw
workload traces without review. External observations are untrusted evidence:
validate their provenance before allowing them to influence production decisions.

Python task cancellation is cooperative. Process groups do not contain children
that move to another session; Windows direct-process cleanup is not tree containment.
Use an OS supervisor/cgroup/job object for stronger requirements. See INSTALL.md
for exact timeout and cleanup semantics.

Report suspected vulnerabilities privately to the deploying organization's security
contact or the repository's private advisory channel when enabled. Include the
version, platform, affected API and a minimal reproduction without credentials or
customer payloads. Do not publish sensitive traces in a public issue.
