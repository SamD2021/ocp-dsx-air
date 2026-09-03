"""Pure jump-host DNS helpers retained outside the NVIDIA Air adapter."""

from ocp_dsx_air.models.runtime import ClusterNetworkConfig

_HOSTS_BEGIN = "# BEGIN ocp-dsx-air"
_HOSTS_END = "# END ocp-dsx-air"


def cluster_dns_host_block(network: ClusterNetworkConfig) -> str:
    """Return the managed hosts-file block used by the jump host."""
    api_names = f"api.{network.cluster_domain} api-int.{network.cluster_domain}"
    ingress_names = (
        f"console-openshift-console.{network.apps_domain} "
        f"oauth-openshift.{network.apps_domain} "
        f"downloads-openshift-console.{network.apps_domain} "
        f"canary-openshift-ingress-canary.{network.apps_domain}"
    )
    return (
        f"{_HOSTS_BEGIN}\n"
        f"{network.api_vip} {api_names}\n"
        f"{network.ingress_vip} {ingress_names}\n"
        f"{_HOSTS_END}\n"
    )


def merge_hosts_file(existing: str, block: str) -> str:
    """Replace or append the ocp-dsx-air-owned hosts-file block."""
    start = existing.find(_HOSTS_BEGIN)
    end = existing.find(_HOSTS_END)
    body = block if block.endswith("\n") else block + "\n"
    if start != -1 and end != -1 and end >= start:
        after = existing[end + len(_HOSTS_END) :].lstrip("\n")
        return existing[:start].rstrip("\n") + "\n" + body + after
    text = existing.rstrip("\n")
    if text:
        text += "\n"
    return text + body
