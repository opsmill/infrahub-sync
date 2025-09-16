from infrahub_sync.adapters.infrahub import InfrahubAdapter

from .sync_models import (
    VirtualizationVirtualMachine,
    VirtualizationVMDisk,
    VirtualizationVMFilesystem,
    VirtualizationVMNetworkInterface,
)


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfrahubSync(InfrahubAdapter):
    VirtualizationVMDisk = VirtualizationVMDisk
    VirtualizationVMFilesystem = VirtualizationVMFilesystem
    VirtualizationVMNetworkInterface = VirtualizationVMNetworkInterface
    VirtualizationVirtualMachine = VirtualizationVirtualMachine
