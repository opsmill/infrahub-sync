"""Example local custom adapter demonstrating the plugin loader system."""

from typing import TYPE_CHECKING
from diffsync import Adapter, DiffSyncModel

if TYPE_CHECKING:
    from infrahub_sync import SyncAdapter

class NetworkDevice(DiffSyncModel):
    """Network device model."""
    _modelname = "NetworkDevice"
    _identifiers = ("hostname",)
    _attributes = ("ip_address", "device_type", "location", "vendor")
    
    hostname: str
    ip_address: str | None = None
    device_type: str | None = None
    location: str | None = None
    vendor: str | None = None

class MyNetworkAdapter(Adapter):
    """Custom network monitoring adapter."""
    
    NetworkDevice = NetworkDevice
    
    def __init__(self, config=None, target=None, adapter: "SyncAdapter | None" = None, **kwargs):
        """Initialize the custom adapter."""
        super().__init__()
        self.config = config
        self.target = target
        self.adapter_config = adapter
        
        # Extract settings
        self.settings = adapter.settings if adapter else {}
        self.api_endpoint = self.settings.get("api_endpoint", "http://localhost:8080")
        self.timeout = self.settings.get("timeout", 30)
        
    def load(self):
        """Load data from the custom network monitoring system."""
        print(f"Loading network devices from {self.api_endpoint}")
        
        # Simulate loading from a custom API/system
        devices_data = [
            {
                "hostname": "core-rtr-01.dc1",
                "ip_address": "10.1.1.1",
                "device_type": "router",
                "location": "datacenter-1",
                "vendor": "cisco"
            },
            {
                "hostname": "access-sw-01.floor2", 
                "ip_address": "10.2.1.1",
                "device_type": "switch",
                "location": "floor-2",
                "vendor": "arista"
            },
            {
                "hostname": "wan-rtr-01.branch",
                "ip_address": "192.168.1.1", 
                "device_type": "router",
                "location": "branch-office",
                "vendor": "juniper"
            }
        ]
        
        print(f"Found {len(devices_data)} network devices")
        
        # Add devices to the adapter
        for device_data in devices_data:
            device = NetworkDevice(**device_data)
            self.add(device)
            
    def create_network_device(self, hostname: str, **kwargs):
        """Example method showing how to extend adapter functionality."""
        device_data = {"hostname": hostname, **kwargs}
        device = NetworkDevice(**device_data)
        # In a real implementation, you would create the device in the external system
        print(f"Would create device {hostname} in external system")
        return device