def test_baml_client_importable_at_documented_path():
    # Note: this import succeeds even before Behavior 1 lands, because
    # `activegraph/baml_client/` (no __init__.py) is an implicit PEP 420
    # namespace package and `baml_sdk/` already has its own __init__.py --
    # Python resolves the submodule import regardless. The plan's Given
    # clause ("__init__.py does not exist") is accurate; its Red-step
    # comment ("currently raises ImportError") is not -- this assertion
    # alone was never capable of proving Red. See the second test below
    # for the assertion that actually distinguishes pre/post-Behavior-1.
    from activegraph.baml_client import baml_sdk

    assert baml_sdk.main() == "hello from baml"


def test_baml_client_declares_explicit_reexport_surface():
    # A namespace package (pre-Behavior-1 state) has no __all__ at all --
    # this fails with AttributeError until a real, committed
    # activegraph/baml_client/__init__.py exists and defines it.
    import activegraph.baml_client as baml_client

    assert baml_client.__all__ == ["baml_sdk"]
