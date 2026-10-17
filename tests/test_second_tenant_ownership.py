"""Offline regression for the live test's ownership assertion; no graph client calls."""
import pytest

from test_second_tenant import _assert_owned_graphs_exist


def test_owned_graph_presence_tolerates_concurrent_and_real_tenant_names():
    _assert_owned_graphs_exist(
        ["tenant_b9test_owned_a", "tenant_b9test_owned_b", "tenant_b9test_other_a",
         "tenant_falkordb", "tenant_pallets"],
        ["tenant_b9test_owned_a", "tenant_b9test_owned_b"],
    )


def test_missing_owned_graph_fails_even_with_unrelated_names():
    with pytest.raises(AssertionError):
        _assert_owned_graphs_exist(
            ["tenant_b9test_owned_a", "tenant_b9test_other_b"],
            ["tenant_b9test_owned_a", "tenant_b9test_owned_b"],
        )
