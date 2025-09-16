# Device42 to Infrahub Sync

This example demonstrates how to synchronize data from Device42 to Infrahub using the infrahub-sync framework.

## Overview

Device42 is a comprehensive IT asset management and documentation solution. This sync integration allows you to:

- Import organizations (customers) from Device42 as organizations in Infrahub
- Sync locations (buildings and rooms) as location hierarchies
- Transfer device inventory including racks, hardware models, and devices
- Map network interfaces from Device42 IP records

## Configuration

### Device42 Settings

Update the `config.yml` file with your Device42 instance details:

```yaml
source:
  name: device42
  settings:
    url: "https://your-device42-instance.com"
    username: "your-username"
    password: "your-password"
    verify_ssl: true
```

### Environment Variables

You can also configure the connection using environment variables:

```bash
export DEVICE42_URL="https://your-device42-instance.com"
export DEVICE42_USERNAME="your-username"  
export DEVICE42_PASSWORD="your-password"
```

### Infrahub Settings

Configure your Infrahub destination:

```yaml
destination:
  name: infrahub
  settings:
    url: "http://localhost:8000"
```

## Data Mapping

This example maps the following Device42 objects to Infrahub:

| Device42 Object | Infrahub Object | Notes |
|----------------|----------------|-------|
| Customers | OrganizationGeneric | Device42 customer records |
| Buildings | LocationGeneric | Building-type locations |
| Rooms | LocationGeneric | Room-type locations |
| Racks | InfraRack | Physical rack infrastructure |
| Hardware Models | ChoiceDeviceType | Device type definitions |
| Devices | InfraDevice | Network devices and servers |
| IP Records (with labels) | InfraInterfaceL2L3 | Network interfaces |

## API Endpoints Used

The sync utilizes these Device42 API v2.0 endpoints:

- `/api/2.0/customers/` - Customer/organization data
- `/api/2.0/buildings/` - Building location data
- `/api/2.0/rooms/` - Room location data
- `/api/2.0/racks/` - Rack infrastructure
- `/api/2.0/hardwares/` - Hardware model definitions
- `/api/2.0/devices/` - Device inventory
- `/api/2.0/ips/` - IP address and interface data

## Running the Sync

1. Configure your settings in `config.yml` or environment variables
2. Ensure Infrahub is running and accessible
3. Run the synchronization:

```bash
infrahub-sync sync examples/device42_to_infrahub/config.yml
```

## Customization

### Filters

You can add filters to limit which objects are synchronized. For example, to only sync devices from a specific customer:

```yaml
- name: InfraDevice
  mapping: devices
  filters:
    - field: customer
      operation: "equals"
      value: "your-customer-name"
```

### Transforms

Transform data during synchronization. For example, to normalize device names:

```yaml
transforms:
  - field: name
    expression: "{{ name.lower().replace(' ', '-') if name else '' }}"
```

## Authentication

Device42 uses HTTP Basic Authentication. Ensure your user account has the necessary permissions to read the required objects:

- Customer management
- Location management  
- Device management
- Network management

## Troubleshooting

### Connection Issues

1. Verify your Device42 URL is correct and accessible
2. Check that your credentials are valid
3. Ensure SSL certificate verification settings are appropriate

### Data Issues

1. Check the Device42 API documentation for field availability
2. Verify that referenced objects exist (e.g., racks must exist before devices)
3. Review the sync order in `config.yml` - dependencies should be synced first

### API Limits

Device42 may have API rate limits. If you encounter issues:

1. Add delays between requests in the sync configuration
2. Consider using smaller batch sizes
3. Monitor Device42 system resources during large syncs

## Support

For Device42-specific questions, consult the Device42 API documentation at: https://api.device42.com/

For infrahub-sync framework questions, see the main documentation.