from tasks import linter


def test_ty_check_command_excludes_managed_on_python_310() -> None:
    assert linter._ty_check_command(3, 10) == (
        "uv run ty check --exclude infrahub_sync/service --exclude tests/service ."
    )


def test_ty_check_command_checks_managed_on_supported_python() -> None:
    assert linter._ty_check_command(3, 11) == "uv run ty check ."
    assert linter._ty_check_command(3, 13) == "uv run ty check ."


def test_pylint_command_excludes_managed_on_python_310() -> None:
    assert linter._pylint_command(3, 10) == (
        "pylint --output-format=json2 --ignore-paths='^infrahub_sync/service/' infrahub_sync/"
    )


def test_pylint_command_checks_managed_on_supported_python() -> None:
    assert linter._pylint_command(3, 11) == "pylint --output-format=json2 infrahub_sync/"
    assert linter._pylint_command(3, 13) == "pylint --output-format=json2 infrahub_sync/"


def test_pylint_regression_locations_reports_only_regressed_codes() -> None:
    report = {
        "messages": [
            {
                "messageId": "E0401",
                "path": "infrahub_sync/service/deploy.py",
                "line": 8,
                "symbol": "import-error",
            },
            {"messageId": "C0302", "path": "infrahub_sync/cli.py", "line": 1, "symbol": "too-many-lines"},
        ]
    }

    assert linter._pylint_regression_locations(report) == [
        "infrahub_sync/service/deploy.py:8: E0401 (import-error)",
    ]


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
