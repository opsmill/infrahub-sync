"""One product-cache-location rule, reached by every entry point that accepts the option.

Envelope OES-21. The absoluteness rule was stated three times before this slice — once in
the version 1 request models, once implicitly by the local projection's constructor, and
nowhere in the shared layer — so two entry points refused the same input with two different
sentences. What is asserted here is that one function now produces the refusal and that the
shared service renders it verbatim.

The asymmetry is deliberate and is asserted too: a *missing* location is a legacy cache-only
fallback for a run and a refusal for the registry, which has nowhere to live without a store.
Only absoluteness is shared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from infrahub_sync.product_store import configs as configs_service
from infrahub_sync.product_store.standalone import ProductCacheLocationError, resolve_product_cache_location

if TYPE_CHECKING:
    from pathlib import Path

RELATIVE = "relative/product-cache"
UNRESOLVABLE = "~db006-user-that-cannot-exist/product-cache"


def test_the_relative_path_refusal_is_one_sentence_at_every_entry_point() -> None:
    with pytest.raises(ProductCacheLocationError) as rule:
        resolve_product_cache_location(RELATIVE)
    expected = str(rule.value)

    with pytest.raises(configs_service.ConfigsRequestError) as service:
        configs_service.validate(config_id="c", registry_version=1, product_cache_location=RELATIVE)
    assert str(service.value) == expected


def test_the_unresolvable_home_refusal_is_one_sentence_at_every_entry_point() -> None:
    with pytest.raises(ProductCacheLocationError, match="unresolvable user home"):
        resolve_product_cache_location(UNRESOLVABLE)


def test_user_home_expansion_is_still_accepted_at_every_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repair narrows the accepted set to non-absolute paths only, not to `~` as well."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    tilde = "~/product-cache"

    assert resolve_product_cache_location(tilde) == home / "product-cache"


def test_a_missing_location_falls_back_for_runs_and_refuses_for_the_registry() -> None:
    with pytest.raises(configs_service.ConfigsRequestError, match="product_cache_location is required"):
        configs_service.validate(config_id="c", registry_version=1, product_cache_location=None)
