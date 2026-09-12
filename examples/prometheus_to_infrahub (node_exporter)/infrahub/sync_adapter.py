from infrahub_sync.adapters.infrahub import InfrahubAdapter

from .sync_models import (
    VirtualizationVirtualMachine,
    VirtualizationVMDisk,
    VirtualizationVMFilesystem,
    VirtualizationVMNetworkInterface,
)


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class InfrahubSync(InfrahubAdapter):
    VirtualizationVMDisk = VirtualizationVMDisk
    VirtualizationVMFilesystem = VirtualizationVMFilesystem
    VirtualizationVMNetworkInterface = VirtualizationVMNetworkInterface
    VirtualizationVirtualMachine = VirtualizationVirtualMachine
