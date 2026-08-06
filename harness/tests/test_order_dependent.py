"""
Ground truth category: ORDER_DEPENDENT

The consumer test reads state that the setup test writes. It only passes if
setup happened to run first. Requires randomised execution order to surface
(the workflow installs pytest-randomly), which is exactly how order-dependent
flakiness behaves in practice: invisible until the order changes.
"""
_shared_state = {}


def test_order_dependent_setup():
    _shared_state["initialized"] = True
    assert True


def test_order_dependent_consumer():
    assert _shared_state.get("initialized") is True
