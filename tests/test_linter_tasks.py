from tasks import linter


def test_ty_check_command_excludes_managed_on_python_310() -> None:
    assert linter._ty_check_command(3, 10) == (
        "uv run ty check --exclude infrahub_sync/managed --exclude tests/managed ."
    )


def test_ty_check_command_checks_managed_on_supported_python() -> None:
    assert linter._ty_check_command(3, 11) == "uv run ty check ."
    assert linter._ty_check_command(3, 13) == "uv run ty check ."


def test_pylint_baseline_accepts_current_or_lower_counts() -> None:
    messages = [
        {"messageId": message_id}
        for message_id, maximum in linter.PYLINT_BASELINE_MAX_COUNTS.items()
        for _ in range(maximum)
    ]

    assert linter._pylint_regressions({"messages": messages}) == []
    assert linter._pylint_regressions({"messages": messages[:-1]}) == []


def test_pylint_baseline_rejects_new_codes_and_increased_counts() -> None:
    messages = [
        {"messageId": "C0302"},
        {"messageId": "C0302"},
        {"messageId": "E0401"},
    ]

    assert linter._pylint_regressions({"messages": messages}) == [
        "C0302 increased from at most 1 to 2",
        "new diagnostic code E0401 (1)",
    ]


def test_pylint_baseline_rejects_malformed_reports() -> None:
    assert linter._pylint_regressions({}) == ["report does not contain a messages list"]
    assert linter._pylint_regressions({"messages": [{}]}) == ["report contains a diagnostic without a messageId"]
