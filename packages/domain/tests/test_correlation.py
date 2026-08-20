from aic_domain.correlation import DEFAULT_SERVICE_DEPENDENCIES, ServiceDependencyGraph
from aic_domain.models import ServiceDependency


def test_connected_services_share_a_group_key() -> None:
    graph = ServiceDependencyGraph(
        [ServiceDependency(service="checkout-service", depends_on="payment-service")]
    )
    assert graph.group_key("checkout-service") == graph.group_key("payment-service")


def test_group_key_is_canonicalized_regardless_of_lookup_member() -> None:
    graph = ServiceDependencyGraph(
        [ServiceDependency(service="checkout-service", depends_on="payment-service")]
    )
    assert graph.group_key("checkout-service") == "checkout-service"
    assert graph.group_key("payment-service") == "checkout-service"


def test_unrelated_service_is_its_own_group() -> None:
    graph = ServiceDependencyGraph(
        [ServiceDependency(service="checkout-service", depends_on="payment-service")]
    )
    assert graph.group_key("unrelated-service") == "unrelated-service"


def test_empty_graph_returns_service_itself() -> None:
    graph = ServiceDependencyGraph([])
    assert graph.group_key("payment-service") == "payment-service"


def test_transitive_chain_groups_together() -> None:
    graph = ServiceDependencyGraph(
        [
            ServiceDependency(service="a", depends_on="b"),
            ServiceDependency(service="b", depends_on="c"),
        ]
    )
    assert graph.group_key("a") == graph.group_key("b") == graph.group_key("c") == "a"


def test_from_pairs_matches_direct_construction() -> None:
    graph = ServiceDependencyGraph.from_pairs(DEFAULT_SERVICE_DEPENDENCIES)
    assert graph.group_key("checkout-service") == graph.group_key("payment-service")
