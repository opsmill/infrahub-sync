from infrahub_sync.adapters.prometheus import PrometheusAdapter

from .sync_models import (
    VirtualizationVirtualMachine,
    VirtualizationVMDisk,
    VirtualizationVMFilesystem,
    VirtualizationVMNetworkInterface,
)


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class PrometheusSync(PrometheusAdapter):
    VirtualizationVMDisk = VirtualizationVMDisk
    VirtualizationVMFilesystem = VirtualizationVMFilesystem
    VirtualizationVMNetworkInterface = VirtualizationVMNetworkInterface
    VirtualizationVirtualMachine = VirtualizationVirtualMachine
