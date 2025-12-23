import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  syncSidebar: [
    'readme',
    {
      type: 'category',
      label: 'Guides',
      items: [
        'guides/installation',
        'guides/creation',
        'guides/run',
      ],
    },
    {
      type: 'category',
      label: 'Adapters',
      items: [
        'adapters/genericrestapi',
        'adapters/ipfabric',
        'adapters/librenms',
        'adapters/local-adapters',
        'adapters/nautobot',
        'adapters/netbox',
        'adapters/observium',
        'adapters/peering-manager',
        'adapters/slurpit',
        'adapters/aci',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/config',
        'reference/cli',
      ],
    },
    'development',
  ]
};

export default sidebars;
