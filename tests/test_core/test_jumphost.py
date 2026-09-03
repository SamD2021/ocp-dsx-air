
from ocp_dsx_air.core.jumphost import cluster_dns_host_block, merge_hosts_file
from ocp_dsx_air.models.runtime import ClusterNetworkConfig


def test_cluster_dns_block_and_merge_are_idempotent() -> None:
    network = ClusterNetworkConfig(
        cluster_name="ocp",
        base_dns_domain="example.test",
        api_vip="192.0.2.10",
        ingress_vip="192.0.2.11",
    )
    block = cluster_dns_host_block(network)

    merged = merge_hosts_file("127.0.0.1 localhost\n", block)

    assert "192.0.2.10 api.ocp.example.test api-int.ocp.example.test" in merged
    assert "192.0.2.11 console-openshift-console.apps.ocp.example.test" in merged
    assert merge_hosts_file(merged, block) == merged


def test_merge_hosts_file_replaces_only_managed_block() -> None:
    old = """127.0.0.1 localhost
# BEGIN ocp-dsx-air
192.0.2.1 old.example.test
# END ocp-dsx-air
203.0.113.2 preserved.example.test
"""

    result = merge_hosts_file(
        old,
        "# BEGIN ocp-dsx-air\n192.0.2.10 new.example.test\n# END ocp-dsx-air\n",
    )

    assert "old.example.test" not in result
    assert "new.example.test" in result
    assert "preserved.example.test" in result
