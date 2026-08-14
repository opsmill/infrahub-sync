from tasks import linter


def test_ty_check_command_excludes_managed_on_python_310() -> None:
    assert linter._ty_check_command(3, 10) == (
        "uv run ty check --exclude infrahub_sync/managed --exclude tests/managed ."
    )


def test_ty_check_command_checks_managed_on_supported_python() -> None:
    assert linter._ty_check_command(3, 11) == "uv run ty check ."
    assert linter._ty_check_command(3, 13) == "uv run ty check ."
