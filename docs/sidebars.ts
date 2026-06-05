import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  syncSidebar: [
    'readme',
    {
      type: 'category',
      label: 'Get started',
      items: [
        'installation',
        'creating-a-sync-project',
        'running-a-sync',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'using-netbox-or-nautobot-with-infrahub',
        'migrating-from-netbox-or-nautobot',
        'orchestration',
        'custom-certificates',
      ],
    },
    {
      type: 'category',
      label: 'Adapters',
      items: [
        'adapters/choosing-an-adapter',
        'adapters/aci',
        'adapters/device42',
        'adapters/genericrestapi',
        'adapters/infrahub',
        'adapters/ipfabric',
        'adapters/librenms',
        'adapters/local-adapters',
        'adapters/nautobot',
        'adapters/netbox',
        'adapters/observium',
        'adapters/peering-manager',
        'adapters/peeringdb',
        'adapters/prometheus',
        'adapters/slurpit',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/config',
        'reference/schema-mapping',
        'reference/cli',
        'reference/incremental-extraction',
      ],
    },
    'contributing',
  ]
};

export default sidebars;
