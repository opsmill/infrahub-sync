# ruff: noqa: PLR6301, PLW1641, PYI034
"""Deterministic hostile-input cases for public trust-boundary tests.

The ignored rules assume ordinary methods return normally. These hostile callbacks deliberately
raise through a shared ``Never``-returning tripwire instead.
"""

from __future__ import annotations

from collections.abc import Callable, ItemsView, Iterator, KeysView, Mapping
from dataclasses import dataclass, field
from enum import Enum
from itertools import starmap
from typing import Any, Literal, NoReturn, cast

from pydantic import BaseModel, model_serializer
from pydantic_core import PydanticCustomError

ForgedErrorType = Literal[
    "invalid_json_value",
    "invalid_unicode_surrogate",
    "unsupported_declared_fields",
]


class BoundaryOutcome(Enum):
    """Expected public-boundary result for one case."""

    ACCEPT = "accept"
    REJECT = "reject"


@dataclass
class CallbackTripwire:
    """Record a local callback and fail immediately when hostile code executes."""

    _calls: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def calls(self) -> tuple[str, ...]:
        """Return callbacks observed by this tripwire."""
        return tuple(self._calls)

    def trip(self, callback: str) -> NoReturn:
        """Record one callback and raise a value-free failure."""
        self._calls.append(callback)
        msg = "hostile callback executed"
        raise AssertionError(msg)


@dataclass(frozen=True, repr=False)
class BoundaryCase:
    """One safely identified input and its explicit boundary expectations."""

    id: str
    value: object = field(repr=False)
    outcome: BoundaryOutcome
    tripwire: CallbackTripwire = field(repr=False)
    expected_callbacks: tuple[str, ...] = ()
    probed_callback: str | None = None
    _probe: Callable[[], object] | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        """Render only the deterministic ID, never the hostile value."""
        return f"BoundaryCase(id={self.id!r})"

    def probe_callback(self) -> object:
        """Exercise one representative callback to prove its tripwire is live."""
        if self._probe is None:
            msg = f"case {self.id!r} has no callback probe"
            raise ValueError(msg)
        return self._probe()

    def assert_expected_callbacks(self) -> None:
        """Assert callback behavior without rendering the hostile value."""
        assert self.tripwire.calls == self.expected_callbacks, (
            f"{self.id}: callbacks {self.tripwire.calls!r}, expected {self.expected_callbacks!r}"
        )


@dataclass(frozen=True, repr=False)
class InvalidJsonCase:
    """One invalid JSON graph and its stable diagnostic detail."""

    id: str
    value: object = field(repr=False)
    reason: str
    pointer_suffix: str = ""

    def __repr__(self) -> str:
        return f"InvalidJsonCase(id={self.id!r})"


@dataclass(frozen=True, repr=False)
class ForgedDiagnosticCase:
    """One hostile mapping that can raise a trusted-looking validation error."""

    id: str
    value: object = field(repr=False)
    error_type: ForgedErrorType
    context: dict[str, object] = field(repr=False)
    tripwire: CallbackTripwire = field(repr=False)
    _probe: Callable[[], object] = field(repr=False)
    expected_callbacks: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return f"ForgedDiagnosticCase(id={self.id!r})"

    def probe_forged_error(self) -> object:
        """Execute the hostile callback to prove its forged error is live."""
        return self._probe()

    def assert_expected_callbacks(self) -> None:
        """Assert the boundary did not traverse the forged mapping."""
        assert self.tripwire.calls == self.expected_callbacks, (
            f"{self.id}: callbacks {self.tripwire.calls!r}, expected {self.expected_callbacks!r}"
        )


@dataclass(frozen=True)
class UnicodeCase:
    """One Unicode test value and its safe visible form."""

    id: str
    value: str = field(repr=False)
    visible: str
    group: str


@dataclass(frozen=True)
class UnicodeCollisionCase:
    """Two distinct field components that must remain diagnostically distinct."""

    id: str
    raw: str = field(repr=False)
    literal: str = field(repr=False)
    raw_visible: str
    literal_visible: str


@dataclass(frozen=True)
class EndpointCase:
    """One URL-like value and its expected setting-boundary result."""

    id: str
    value: str = field(repr=False)
    form: str
    setting_name: str
    outcome: BoundaryOutcome
    canary: str | None = None
    expected_error: str | None = None


def _plain_boundary_case(case_id: str, value: object, outcome: BoundaryOutcome) -> BoundaryCase:
    return BoundaryCase(case_id, value, outcome, CallbackTripwire())


def _hostile_dict_case() -> BoundaryCase:
    tripwire = CallbackTripwire()

    class _HostileDict(dict[str, object]):  # noqa: FURB189 - hostile exact-type boundary probe.
        def items(self) -> ItemsView[str, object]:  # ty: ignore[invalid-method-override]  # Deliberate probe.
            return tripwire.trip("dict.items")

        def keys(self) -> KeysView[str]:  # ty: ignore[invalid-method-override]  # Deliberate probe.
            return tripwire.trip("dict.keys")

        def __iter__(self) -> Iterator[str]:
            return tripwire.trip("dict.iter")

        def __repr__(self) -> str:
            return tripwire.trip("dict.repr")

        def __str__(self) -> str:
            return tripwire.trip("dict.str")

        def __format__(self, format_spec: str) -> str:
            del format_spec
            return tripwire.trip("dict.format")

        def __eq__(self, other: object) -> bool:
            del other
            return tripwire.trip("dict.compare")

    value = _HostileDict()
    return BoundaryCase(
        "dict-subclass",
        value,
        BoundaryOutcome.REJECT,
        tripwire,
        probed_callback="dict.items",
        _probe=value.items,
    )


def hostile_builtin_cases() -> tuple[BoundaryCase, ...]:
    """Return fresh hostile subclasses of each subclassable JSON built-in value type."""
    cases = [_hostile_dict_case()]

    list_tripwire = CallbackTripwire()

    class _HostileList(list[object]):  # noqa: FURB189 - hostile exact-type boundary probe.
        def __iter__(self) -> Iterator[object]:
            return list_tripwire.trip("list.iter")

        def __repr__(self) -> str:
            return list_tripwire.trip("list.repr")

        def __str__(self) -> str:
            return list_tripwire.trip("list.str")

        def __format__(self, format_spec: str) -> str:
            del format_spec
            return list_tripwire.trip("list.format")

        def __eq__(self, other: object) -> bool:
            del other
            return list_tripwire.trip("list.compare")

    hostile_list = _HostileList()
    cases.append(
        BoundaryCase(
            "list-subclass",
            hostile_list,
            BoundaryOutcome.REJECT,
            list_tripwire,
            probed_callback="list.iter",
            _probe=lambda: iter(hostile_list),
        )
    )

    str_tripwire = CallbackTripwire()

    class _HostileStr(str):  # noqa: FURB189 - hostile exact-type boundary probe.
        __slots__ = ()

        __hash__ = str.__hash__

        def __iter__(self) -> Iterator[str]:  # ty: ignore[invalid-method-override]  # Deliberate probe.
            return str_tripwire.trip("str.iter")

        def __repr__(self) -> str:
            return str_tripwire.trip("str.repr")

        def __str__(self) -> str:
            return str_tripwire.trip("str.str")

        def __format__(self, format_spec: str) -> str:
            del format_spec
            return str_tripwire.trip("str.format")

        def __eq__(self, other: object) -> bool:
            del other
            return str_tripwire.trip("str.compare")

    hostile_str = _HostileStr("string-value-canary")
    cases.append(
        BoundaryCase(
            "str-subclass",
            hostile_str,
            BoundaryOutcome.REJECT,
            str_tripwire,
            probed_callback="str.str",
            _probe=lambda: str(hostile_str),
        )
    )

    int_tripwire = CallbackTripwire()

    class _HostileInt(int):
        def __int__(self) -> int:
            return int_tripwire.trip("int.convert")

        def __repr__(self) -> str:
            return int_tripwire.trip("int.repr")

        def __str__(self) -> str:
            return int_tripwire.trip("int.str")

        def __format__(self, format_spec: str) -> str:
            del format_spec
            return int_tripwire.trip("int.format")

        def __eq__(self, other: object) -> bool:
            del other
            return int_tripwire.trip("int.compare")

    hostile_int = _HostileInt(7)
    cases.append(
        BoundaryCase(
            "int-subclass",
            hostile_int,
            BoundaryOutcome.REJECT,
            int_tripwire,
            probed_callback="int.convert",
            _probe=lambda: int(hostile_int),
        )
    )

    float_tripwire = CallbackTripwire()

    class _HostileFloat(float):
        def __float__(self) -> float:
            return float_tripwire.trip("float.convert")

        def __repr__(self) -> str:
            return float_tripwire.trip("float.repr")

        def __str__(self) -> str:
            return float_tripwire.trip("float.str")

        def __format__(self, format_spec: str) -> str:
            del format_spec
            return float_tripwire.trip("float.format")

        def __eq__(self, other: object) -> bool:
            del other
            return float_tripwire.trip("float.compare")

    hostile_float = _HostileFloat(1.5)
    cases.append(
        BoundaryCase(
            "float-subclass",
            hostile_float,
            BoundaryOutcome.REJECT,
            float_tripwire,
            probed_callback="float.convert",
            _probe=lambda: float(hostile_float),
        )
    )
    return tuple(cases)


def protocol_object_cases() -> tuple[BoundaryCase, ...]:
    """Return fresh hostile Python protocol objects with local tripwires."""
    cases: list[BoundaryCase] = []
    mapping_tripwire = CallbackTripwire()

    class _HostileMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            del key
            return mapping_tripwire.trip("mapping.getitem")

        def __iter__(self) -> Iterator[str]:
            return mapping_tripwire.trip("mapping.iter")

        def __len__(self) -> int:
            return mapping_tripwire.trip("mapping.len")

        def items(self) -> ItemsView[str, object]:
            return mapping_tripwire.trip("mapping.items")

        def keys(self) -> KeysView[str]:
            return mapping_tripwire.trip("mapping.keys")

        def __repr__(self) -> str:
            return mapping_tripwire.trip("mapping.repr")

        def __str__(self) -> str:
            return mapping_tripwire.trip("mapping.str")

    hostile_mapping = _HostileMapping()
    cases.append(
        BoundaryCase(
            "custom-mapping",
            hostile_mapping,
            BoundaryOutcome.REJECT,
            mapping_tripwire,
            probed_callback="mapping.items",
            _probe=hostile_mapping.items,
        )
    )

    iterator_tripwire = CallbackTripwire()

    class _HostileIterator(Iterator[object]):
        def __iter__(self) -> Iterator[object]:
            return iterator_tripwire.trip("iterator.iter")

        def __next__(self) -> object:
            return iterator_tripwire.trip("iterator.next")

        def __repr__(self) -> str:
            return iterator_tripwire.trip("iterator.repr")

        def __str__(self) -> str:
            return iterator_tripwire.trip("iterator.str")

    hostile_iterator = _HostileIterator()
    cases.append(
        BoundaryCase(
            "custom-iterator",
            hostile_iterator,
            BoundaryOutcome.REJECT,
            iterator_tripwire,
            probed_callback="iterator.next",
            _probe=lambda: next(hostile_iterator),
        )
    )

    generator_tripwire = CallbackTripwire()

    def _hostile_generator() -> Iterator[object]:
        generator_tripwire.trip("generator.next")
        yield None

    hostile_generator = _hostile_generator()
    cases.append(
        BoundaryCase(
            "generator",
            hostile_generator,
            BoundaryOutcome.REJECT,
            generator_tripwire,
            probed_callback="generator.next",
            _probe=lambda: next(hostile_generator),
        )
    )

    display_tripwire = CallbackTripwire()

    class _HostileDisplay:
        def __repr__(self) -> str:
            return display_tripwire.trip("object.repr")

        def __str__(self) -> str:
            return display_tripwire.trip("object.str")

        def __format__(self, format_spec: str) -> str:
            del format_spec
            return display_tripwire.trip("object.format")

        def __eq__(self, other: object) -> bool:
            del other
            return display_tripwire.trip("object.compare")

    hostile_display = _HostileDisplay()
    cases.append(
        BoundaryCase(
            "repr-str-format-trap",
            hostile_display,
            BoundaryOutcome.REJECT,
            display_tripwire,
            probed_callback="object.repr",
            _probe=lambda: repr(hostile_display),
        )
    )

    attribute_tripwire = CallbackTripwire()

    class _HostileAttribute:
        def __getattribute__(self, name: str) -> object:
            del name
            return attribute_tripwire.trip("object.attribute")

    hostile_attribute = _HostileAttribute()
    cases.append(
        BoundaryCase(
            "attribute-property-trap",
            hostile_attribute,
            BoundaryOutcome.REJECT,
            attribute_tripwire,
            probed_callback="object.attribute",
            _probe=lambda: hostile_attribute.payload,
        )
    )

    class_tripwire = CallbackTripwire()

    class _SpoofedClass:
        @property
        def __class__(self) -> type[object]:
            return class_tripwire.trip("object.__class__")

    spoofed_class = _SpoofedClass()
    cases.append(
        BoundaryCase(
            "spoofed-class",
            spoofed_class,
            BoundaryOutcome.REJECT,
            class_tripwire,
            probed_callback="object.__class__",
            _probe=lambda: spoofed_class.__class__,
        )
    )
    return tuple(cases)


def root_value_cases(valid_mapping: Mapping[str, object]) -> tuple[BoundaryCase, ...]:
    """Return exact root-shape controls and hostile dict/class probes."""
    spoofed_class = next(case for case in protocol_object_cases() if case.id == "spoofed-class")
    return (
        _plain_boundary_case("exact-dict", dict(valid_mapping), BoundaryOutcome.ACCEPT),
        _plain_boundary_case("none", None, BoundaryOutcome.REJECT),
        _plain_boundary_case("list", [], BoundaryOutcome.REJECT),
        _plain_boundary_case("string", "root-string-canary", BoundaryOutcome.REJECT),
        _plain_boundary_case("int", 7, BoundaryOutcome.REJECT),
        _plain_boundary_case("float", 1.5, BoundaryOutcome.REJECT),
        _hostile_dict_case(),
        spoofed_class,
    )


def framework_root_cases(model_type: type[BaseModel], valid_model: BaseModel) -> tuple[BoundaryCase, ...]:
    """Return existing, constructed-invalid, and subclassed Pydantic roots."""
    valid_case = _plain_boundary_case("valid-model", valid_model, BoundaryOutcome.REJECT)

    constructed_tripwire = CallbackTripwire()

    class _ConstructedValue:
        def __getattribute__(self, name: str) -> object:
            del name
            return constructed_tripwire.trip("constructed-model.attribute")

        def __repr__(self) -> str:
            return constructed_tripwire.trip("constructed-model.repr")

        def __str__(self) -> str:
            return constructed_tripwire.trip("constructed-model.str")

    constructed_value = _ConstructedValue()
    constructed_fields: dict[str, Any] = dict.fromkeys(model_type.model_fields, constructed_value)
    constructed_model = model_type.model_construct(**constructed_fields)
    constructed_case = BoundaryCase(
        "constructed-invalid-model",
        constructed_model,
        BoundaryOutcome.REJECT,
        constructed_tripwire,
        probed_callback="constructed-model.attribute",
        _probe=lambda: constructed_value.payload,
    )

    subclass_tripwire = CallbackTripwire()

    class _SubclassValue:
        def __getattribute__(self, name: str) -> object:
            del name
            return subclass_tripwire.trip("model-subclass.attribute")

        def __repr__(self) -> str:
            return subclass_tripwire.trip("model-subclass.repr")

        def __str__(self) -> str:
            return subclass_tripwire.trip("model-subclass.str")

    @model_serializer
    def _serialize(self: BaseModel) -> dict[str, object]:
        del self
        return subclass_tripwire.trip("model.model_dump")

    hostile_model_type = cast(
        "type[BaseModel]",
        type("_HostileModel", (model_type,), {"__module__": __name__, "_serialize": _serialize}),
    )
    subclass_value = _SubclassValue()
    subclass_fields: dict[str, Any] = dict.fromkeys(model_type.model_fields, subclass_value)
    subclass_model = hostile_model_type.model_construct(**subclass_fields)
    subclass_case = BoundaryCase(
        "model-subclass",
        subclass_model,
        BoundaryOutcome.REJECT,
        subclass_tripwire,
        probed_callback="model.model_dump",
        _probe=subclass_model.model_dump,
    )
    return (valid_case, constructed_case, subclass_case)


def invalid_json_cases() -> tuple[InvalidJsonCase, ...]:
    """Return fresh invalid JSON graphs with deterministic diagnostic metadata."""
    deep_value: object = "leaf"
    for _ in range(66):
        deep_value = [deep_value]

    recursive_list: list[object] = []
    recursive_list.append(recursive_list)
    recursive_mapping: dict[str, object] = {}
    recursive_mapping["self"] = recursive_mapping

    class _NonJsonObject:
        pass

    return (
        InvalidJsonCase(
            "excessive-depth",
            deep_value,
            "maximum declared-content depth exceeded",
            "/0" * 61,
        ),
        InvalidJsonCase("recursive-list", recursive_list, "recursive list", "/0"),
        InvalidJsonCase("recursive-mapping", recursive_mapping, "recursive mapping", "/self"),
        InvalidJsonCase("non-string-key", {_NonJsonObject(): "rejected-value-canary"}, "non-string mapping key"),
        InvalidJsonCase("non-finite-float", float("nan"), "non-finite float"),
        InvalidJsonCase("non-json-value", _NonJsonObject(), "non-JSON value"),
    )


def _forged_diagnostic_case(
    case_id: str,
    error_type: ForgedErrorType,
    context: dict[str, object],
) -> ForgedDiagnosticCase:
    tripwire = CallbackTripwire()

    class _ForgedMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            del key
            return tripwire.trip("forged-mapping.getitem")

        def __iter__(self) -> Iterator[str]:
            return tripwire.trip("forged-mapping.iter")

        def __len__(self) -> int:
            return tripwire.trip("forged-mapping.len")

        def items(self) -> ItemsView[str, object]:
            try:
                return tripwire.trip("forged-mapping.items")
            except AssertionError:
                raise PydanticCustomError(
                    error_type,
                    "{message}",
                    context,
                ) from None

    value = _ForgedMapping()
    return ForgedDiagnosticCase(case_id, value, error_type, context, tripwire, value.items)


def forged_diagnostic_cases() -> tuple[ForgedDiagnosticCase, ...]:
    """Return mappings that raise trusted-looking public Pydantic error shapes."""
    specifications: tuple[tuple[str, ForgedErrorType, dict[str, object]], ...] = (
        (
            "json-value",
            "invalid_json_value",
            {
                "pointer": "/forged\npointer-value-canary",
                "reason": "non-JSON value",
                "message": "pydantic-message-canary\nsecond-line-canary",
            },
        ),
        (
            "unicode-surrogate",
            "invalid_unicode_surrogate",
            {
                "pointer": "/forged\npointer-value-canary",
                "message": "pydantic-message-canary\nsecond-line-canary",
            },
        ),
        (
            "unsupported-fields",
            "unsupported_declared_fields",
            {
                "pointer": "/forged\npointer-value-canary",
                "field_names": ("forged-field-name-canary",),
                "message": "pydantic-message-canary\nsecond-line-canary",
            },
        ),
    )
    return tuple(starmap(_forged_diagnostic_case, specifications))


def diagnostic_unicode_cases() -> tuple[UnicodeCase, ...]:
    """Return a fast representative corpus of non-printable diagnostic characters."""
    specifications = (
        ("nul", "\x00", r"\u0000", "c0"),
        ("tab", "\t", r"\t", "c0"),
        ("lf", "\n", r"\n", "c0"),
        ("cr", "\r", r"\r", "c0"),
        ("escape", "\x1b", r"\u001b", "c0"),
        ("delete", "\x7f", r"\u007f", "del"),
        ("next-line", "\x85", r"\u0085", "c1"),
        ("application-program-command", "\x9f", r"\u009f", "c1"),
        ("right-to-left-override", "\u202e", r"\u202e", "bidi"),
        ("left-to-right-isolate", "\u2066", r"\u2066", "isolate"),
        ("zero-width-space", "\u200b", r"\u200b", "zero-width"),
        ("zero-width-no-break-space", "\ufeff", r"\ufeff", "zero-width"),
        ("line-separator", "\u2028", r"\u2028", "separator"),
        ("paragraph-separator", "\u2029", r"\u2029", "separator"),
        ("language-tag", "\U000e0001", r"\U000e0001", "astral"),
    )
    return tuple(starmap(UnicodeCase, specifications))


def iter_lone_surrogates() -> Iterator[UnicodeCase]:
    """Yield every lone UTF-16 surrogate without loading them into default tests."""
    for codepoint in range(0xD800, 0xE000):
        yield UnicodeCase(f"U+{codepoint:04X}", chr(codepoint), f"\\u{codepoint:04x}", "surrogate")


def valid_unicode_scalar_cases() -> tuple[UnicodeCase, ...]:
    """Return valid scalars plus representative mixed printable Unicode."""
    return (
        UnicodeCase("before-surrogates", "\ud7ff", r"\ud7ff", "valid"),
        UnicodeCase("after-surrogates", "\ue000", r"\ue000", "valid"),
        UnicodeCase("emoji", "😀", "😀", "valid"),
        UnicodeCase("maximum-scalar", "\U0010ffff", r"\U0010ffff", "valid"),
        UnicodeCase("mixed-printable", "café-東京-😀", "café-東京-😀", "valid"),
    )


def unicode_collision_cases() -> tuple[UnicodeCollisionCase, ...]:
    """Return raw/literal pairs that unsafe escaping can collapse."""
    return (
        UnicodeCollisionCase("raw-lf-vs-literal-escape", "\n", r"\n", r"\n", r"\\n"),
        UnicodeCollisionCase("raw-cr-vs-literal-escape", "\r", r"\r", r"\r", r"\\r"),
        UnicodeCollisionCase("raw-tab-vs-literal-escape", "\t", r"\t", r"\t", r"\\t"),
        UnicodeCollisionCase("raw-esc-vs-literal-escape", "\x1b", r"\u001b", r"\u001b", r"\\u001b"),
        UnicodeCollisionCase(
            "raw-line-separator-vs-literal-escape",
            "\u2028",
            r"\u2028",
            r"\u2028",
            r"\\u2028",
        ),
        UnicodeCollisionCase(
            "raw-paragraph-separator-vs-literal-escape",
            "\u2029",
            r"\u2029",
            r"\u2029",
            r"\\u2029",
        ),
        UnicodeCollisionCase(
            "raw-rtl-override-vs-literal-escape",
            "\u202e",
            r"\u202e",
            r"\u202e",
            r"\\u202e",
        ),
        UnicodeCollisionCase(
            "raw-zero-width-space-vs-literal-escape",
            "\u200b",
            r"\u200b",
            r"\u200b",
            r"\\u200b",
        ),
        UnicodeCollisionCase(
            "raw-left-to-right-isolate-vs-literal-escape",
            "\u2066",
            r"\u2066",
            r"\u2066",
            r"\\u2066",
        ),
        UnicodeCollisionCase(
            "raw-zero-width-no-break-space-vs-literal-escape",
            "\ufeff",
            r"\ufeff",
            r"\ufeff",
            r"\\ufeff",
        ),
        UnicodeCollisionCase("backslash", "\\", r"\\", r"\\", r"\\\\"),
        UnicodeCollisionCase("slash", "/", "~1", "~1", "~01"),
        UnicodeCollisionCase("tilde", "~", "~0", "~0", "~00"),
        UnicodeCollisionCase(
            "raw-astral-vs-literal-escape",
            "\U000e0001",
            r"\U000e0001",
            r"\U000e0001",
            r"\\U000e0001",
        ),
    )


def endpoint_cases() -> tuple[EndpointCase, ...]:
    """Return accepted controls and hostile URL/endpoint forms."""
    return (
        EndpointCase(
            "ordinary-absolute",
            "https://service.example/api",
            "absolute",
            "url",
            BoundaryOutcome.ACCEPT,
        ),
        EndpointCase(
            "ordinary-authority",
            "//service.example/api",
            "authority",
            "api_endpoint",
            BoundaryOutcome.REJECT,
            expected_error="must be a relative request path without a scheme or authority",
        ),
        EndpointCase(
            "ordinary-relative",
            "/api/v1/items",
            "relative",
            "api_endpoint",
            BoundaryOutcome.ACCEPT,
        ),
        EndpointCase(
            "userinfo",
            "https://probe:url-userinfo-canary@service.example/api",
            "userinfo",
            "url",
            BoundaryOutcome.REJECT,
            "url-userinfo-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
        EndpointCase(
            "query",
            "https://service.example/api?probe=url-query-canary",
            "query",
            "url",
            BoundaryOutcome.REJECT,
            "url-query-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
        EndpointCase(
            "fragment",
            "https://service.example/api#url-fragment-canary",
            "fragment",
            "url",
            BoundaryOutcome.REJECT,
            "url-fragment-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
        EndpointCase(
            "malformed-authority",
            "https://[url-authority-canary",
            "malformed-authority",
            "url",
            BoundaryOutcome.REJECT,
            "url-authority-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
    )
