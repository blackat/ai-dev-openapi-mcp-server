"""Basic unit tests."""
import pytest
from openapi_mcp_server.spec_loader import _slug, _make_op_id


def test_slug_replaces_special_chars():
    assert _slug("get/pets/{id}") == "get_pets___id_"


def test_make_op_id():
    assert _make_op_id("get", "/pets/{id}") == "get_pets"


def test_slug_truncates_long_names():
    long = "a" * 100
    assert len(_slug(long)) <= 64
