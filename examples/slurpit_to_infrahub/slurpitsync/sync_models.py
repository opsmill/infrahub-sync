from typing import Any, List, Optional

from infrahub_sync.adapters.slurpitsync import SlurpitsyncModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraDevice(SlurpitsyncModel):
    _modelname = "InfraDevice"
    _identifiers = ("hostname",)
    _attributes = ("manufacturer", "location", "device_type", "platform", "fqdn")
    hostname: str
    fqdn: Optional[str] = None
    manufacturer: Optional[str] = None
    location: Optional[str] = None
    device_type: Optional[str] = None
    platform: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraHardwareInfo(SlurpitsyncModel):
    _modelname = "InfraHardwareInfo"
    _identifiers = ("device", "serial")
    _attributes = ("name", "description", "product", "version")
    name: str
    description: Optional[str] = None
    product: Optional[str] = None
    serial: Optional[str] = None
    version: Optional[str] = None
    device: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraIPAddress(SlurpitsyncModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "ip_prefix")
    _attributes = ("interface",)
    address: str
    interface: Optional[str] = None
    ip_prefix: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraInterface(SlurpitsyncModel):
    _modelname = "InfraInterface"
    _identifiers = ("device", "name")
    _attributes = ("description", "mac_address")
    name: str
    description: Optional[str] = None
    mac_address: Optional[str] = None
    device: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraPlatform(SlurpitsyncModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraPrefix(SlurpitsyncModel):
    _modelname = "InfraPrefix"
    _identifiers = ("vrf", "prefix")
    _attributes = ()
    prefix: str
    vrf: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraVLAN(SlurpitsyncModel):
    _modelname = "InfraVLAN"
    _identifiers = ("vlan_id", "name")
    _attributes = ()
    name: Optional[str] = None
    vlan_id: int

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraVRF(SlurpitsyncModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class InfraVersion(SlurpitsyncModel):
    _modelname = "InfraVersion"
    _identifiers = ("version",)
    _attributes = ("devices", "file")
    version: str
    file: Optional[str] = None
    devices: Optional[List[str]] = []

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class LocationGeneric(SlurpitsyncModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("description", "number", "street", "city", "county", "state", "zipcode", "country", "phonenumber")
    name: str
    description: Optional[str] = None
    number: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    county: Optional[str] = None
    state: Optional[str] = None
    zipcode: Optional[str] = None
    country: Optional[str] = None
    phonenumber: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class OrganizationGeneric(SlurpitsyncModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("type",)
    name: str
    type: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None

class TemplateDeviceType(SlurpitsyncModel):
    _modelname = "TemplateDeviceType"
    _identifiers = ("name",)
    _attributes = ("manufacturer",)
    name: str
    manufacturer: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None
