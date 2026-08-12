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
    CredentialProvider,
    EnvironmentCredentialProvider,
    provider_for,
    resolve_reference,
    validate_package_credentials,
)
from .models import (
    ConfigurationPackage,
    ConfigurationPackageMetadata,
    CredentialReference,
    CredentialReferenceNode,
    ValidationFinding,
    sort_findings,
)

__all__ = [
    "BUILTIN_ADAPTER_CAPABILITIES",
    "AdapterConfigurationCapabilities",
    "AdapterRole",
    "ConfigurationPackage",
    "ConfigurationPackageMetadata",
    "ConfigurationValidator",
    "CredentialConfigurationError",
    "CredentialProvider",
    "CredentialReference",
    "CredentialReferenceNode",
    "EnvironmentCredentialProvider",
    "UnknownAdapterCapabilitiesError",
    "ValidationFinding",
    "WriteOperation",
    "get_adapter_capabilities",
    "provider_for",
    "resolve_reference",
    "sort_findings",
    "validate_package_credentials",
]
