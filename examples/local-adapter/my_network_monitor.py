"""Example custom adapter for a fictional network monitoring system."""
from typing import TYPE_CHECKING
from diffsync import Adapter, DiffSyncModel

if TYPE_CHECKING:
    from infrahub_sync import SyncAdapter

class Device(DiffSyncModel):
    """Device model representing network devices."""
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("ip_address", "vendor", "model", "location", "status")
    
    name: str
    ip_address: str | None = None
    vendor: str | None = None
    model: str | None = None
    location: str | None = None
    status: str = "active"

class Interface(DiffSyncModel):
    """Interface model representing device interfaces."""
    _modelname = "Interface"
    _identifiers = ("device", "name")
    _attributes = ("description", "ip_address", "status", "speed")
    
    device: str
    name: str
    description: str | None = None
    ip_address: str | None = None
    status: str = "up"
    speed: str | None = None

class MyNetworkMonitorAdapter(Adapter):
    """
    Custom adapter for a fictional network monitoring system.
    
    This demonstrates how to create a local adapter that can sync data
    from any custom source into Infrahub.
    """
    
    # Register models this adapter can handle
    Device = Device
    Interface = Interface
    
    def __init__(self, config=None, target=None, adapter: "SyncAdapter | None" = None, **kwargs):
        """Initialize the custom adapter."""
        super().__init__()
        self.config = config
        self.target = target
        self.adapter_config = adapter
        
        # Extract connection settings
        self.settings = adapter.settings if adapter else {}
        self.api_url = self.settings.get("api_url", "")
        self.api_key = self.settings.get("api_key", "")
        
    def load(self):
        """Load data from the network monitoring system."""
        # In a real adapter, you would make API calls, read files, query databases, etc.
        # For this example, we'll simulate loading data
        
        print(f"Loading data from network monitoring system at {self.api_url}")
        
        # Simulate loading devices
        devices_data = [
            {
                "name": "core-router-01",
                "ip_address": "10.0.1.1", 
                "vendor": "Cisco",
                "model": "ISR4331",
                "location": "Datacenter-A",
                "status": "active"
            },
            {
                "name": "access-switch-01",
                "ip_address": "10.0.2.1",
                "vendor": "Arista", 
                "model": "7050SX3-48YC8",
                "location": "Floor-1",
                "status": "active"
            },
            {
                "name": "backup-router-01",
                "ip_address": "10.0.1.2",
                "vendor": "Juniper",
                "model": "MX240",
                "location": "Datacenter-B", 
                "status": "maintenance"
            }
        ]
        
        # Load devices into DiffSync
        for device_data in devices_data:
            device = Device(**device_data)
            self.add(device)
            
        # Simulate loading interfaces  
        interfaces_data = [
            {
                "device": "core-router-01",
                "name": "GigabitEthernet0/0/0",
                "description": "WAN uplink",
                "ip_address": "203.0.113.1",
                "status": "up",
                "speed": "1000"
            },
            {
                "device": "core-router-01", 
                "name": "GigabitEthernet0/0/1",
                "description": "LAN connection",
                "ip_address": "10.0.1.1",
                "status": "up",
                "speed": "1000"
            },
            {
                "device": "access-switch-01",
                "name": "Ethernet1",
                "description": "Server connection",
                "status": "up",
                "speed": "10000"
            }
        ]
        
        # Load interfaces into DiffSync
        for interface_data in interfaces_data:
            interface = Interface(**interface_data)
            self.add(interface)
            
        print(f"Loaded {len(devices_data)} devices and {len(interfaces_data)} interfaces")

    def _make_api_call(self, endpoint: str) -> dict:
        """Helper method for making API calls (example implementation)."""
        # In a real adapter, you would use requests, httpx, or similar
        # to make actual HTTP calls to your system's API
        import requests
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        url = f"{self.api_url.rstrip('/')}/{endpoint}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()