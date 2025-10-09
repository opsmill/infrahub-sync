from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.prometheus import PrometheusModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class VirtualizationVMDisk(PrometheusModel):
    _modelname = "VirtualizationVMDisk"
    _identifiers = ("virtual_machine", "device")
    _attributes = ("rotational",)
    device: str
    rotational: bool | None = None
    virtual_machine: str

    local_id: str | None = None
    local_data: Any | None = None


class VirtualizationVMFilesystem(PrometheusModel):
    _modelname = "VirtualizationVMFilesystem"
    _identifiers = ("virtual_machine", "mountpoint")
    _attributes = ("readonly", "fstype", "size_bytes", "device_error", "device")
    readonly: bool | None = None
    fstype: str | None = None
    size_bytes: int | None = None
    device_error: bool | None = None
    device: str | None = None
    mountpoint: str
    virtual_machine: str

    local_id: str | None = None
    local_data: Any | None = None


class VirtualizationVMNetworkInterface(PrometheusModel):
    _modelname = "VirtualizationVMNetworkInterface"
    _identifiers = ("virtual_machine", "name")
    _attributes = ("mtu", "mac_address", "carrier", "operational_status", "speed_bps", "duplex")
    mtu: int | None = None
    mac_address: str | None = None
    carrier: bool | None = None
    operational_status: str | None = None
    speed_bps: int | None = None
    duplex: str | None = None
    name: str
    virtual_machine: str

    local_id: str | None = None
    local_data: Any | None = None


class VirtualizationVirtualMachine(PrometheusModel):
    _modelname = "VirtualizationVirtualMachine"
    _identifiers = ("name",)
    _attributes = (
        "os_name",
        "ip_forwarding_enabled",
        "os_kernel",
        "status",
        "architecture",
        "conntrack_limit",
        "mem_total_bytes",
    )
    os_name: str | None = None
    ip_forwarding_enabled: bool | None = None
    os_kernel: str | None = None
    status: str | None = None
    architecture: str | None = None
    conntrack_limit: int | None = None
    name: str
    mem_total_bytes: int | None = None

    local_id: str | None = None
    local_data: Any | None = None
