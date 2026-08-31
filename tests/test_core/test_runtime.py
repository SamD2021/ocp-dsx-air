from ocp_dsx_air.models.runtime import ClusterNetworkConfig


def test_cluster_network_config_distinguishes_base_and_cluster_domains() -> None:
    network = ClusterNetworkConfig(
        cluster_name="ocp",
        base_dns_domain="dsx.air.local",
        api_vip="192.168.200.10",
        ingress_vip="192.168.200.11",
    )

    assert network.base_dns_domain == "dsx.air.local"
    assert network.cluster_domain == "ocp.dsx.air.local"
    assert network.apps_domain == "apps.ocp.dsx.air.local"
