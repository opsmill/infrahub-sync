"""Public declared-configuration contracts for Infrahub Sync v3."""

from .capabilities import (
    BUILTIN_ADAPTER_CAPABILITIES,
    AdapterConfigurationCapabilities,
    AdapterRole,
    ConfigurationValidator,
    UnknownAdapterCapabilitiesError,
    WriteOperation,
    get_adapter_capabilities,
)
from .credentials import (
    CredentialConfigurationError,
)
from .models import (
    ConfigurationPackage,
    ConfigurationPackageMetadata,
    ConfigurationPackageParseError,
    RemovedCredentialFormatError,
    ValidationFinding,
    parse_configuration_package,
    sort_findings,
)
from .stored_package import StoredConfigurationPackage, parse_stored_configuration_package
from .validation import collect_findings, validate_package_credentials

__all__ = [
    "BUILTIN_ADAPTER_CAPABILITIES",
    "AdapterConfigurationCapabilities",
    "AdapterRole",
    "ConfigurationPackage",
    "ConfigurationPackageMetadata",
    "ConfigurationPackageParseError",
    "ConfigurationValidator",
    "CredentialConfigurationError",
    "RemovedCredentialFormatError",
    "StoredConfigurationPackage",
    "UnknownAdapterCapabilitiesError",
    "ValidationFinding",
    "WriteOperation",
    "collect_findings",
    "get_adapter_capabilities",
    "parse_configuration_package",
    "parse_stored_configuration_package",
    "sort_findings",
    "validate_package_credentials",
]
