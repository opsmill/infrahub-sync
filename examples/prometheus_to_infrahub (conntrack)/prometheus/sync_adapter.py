from infrahub_sync.adapters.prometheus import PrometheusAdapter

from .sync_models import (
    MonitoringNetConntrackDialer,
)


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class PrometheusSync(PrometheusAdapter):
    MonitoringNetConntrackDialer = MonitoringNetConntrackDialer
