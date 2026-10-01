import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  syncSidebar: [
    'readme',
    {
      type: 'category',
      label: 'Get started',
      items: [
        'installation',
        'development-stack',
        'quickstart-compose',
        'creating-a-sync-project',
        'configuration-package',
        'running-a-sync',
      ],
    },
    {
      type: 'category',
      label: 'Tutorials',
      items: [
        'tutorials/netbox-to-existing-infrahub',
        'tutorials/nautobot-to-existing-infrahub',
        'tutorials/netbox-demo-to-infrahub',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'use-with-an-ai-agent',
        'using-netbox-or-nautobot-with-infrahub',
        'migrating-from-netbox-or-nautobot',
        'v2-to-v3',
        'orchestration',
        'custom-certificates',
      ],
    },
    {
      type: 'category',
      label: 'Operations',
      items: [
        'operations/day-2-operations',
        'operations/compose-troubleshooting',
        'operations/supported-platforms-and-limits',
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
        'reference/python-api',
        'reference/cli',
        'reference/incremental-extraction',
        'reference/prefect-remote-run',
        'reference/durable-product-records',
        'reference/cache-layout',
        'reference/sync-http-api',
      ],
    },
    {
      type: 'category',
      label: 'Develop',
      items: [
        {
          type: 'category',
          label: 'Knowledge',
          link: {
            type: 'doc',
            id: 'develop/knowledge/index',
          },
          items: [
            'develop/knowledge/sync-architecture',
            'develop/knowledge/repository-tour',
            'develop/knowledge/adapter-anatomy',
            'develop/knowledge/schema-mapping',
            'develop/knowledge/incremental-and-cache',
            'develop/knowledge/plan-artifact',
            'develop/knowledge/planned-write-and-apply',
            'develop/knowledge/apply-guard',
            'develop/knowledge/configuration-foundation',
            'develop/knowledge/execution-surface',
            'develop/knowledge/orchestration-prefect',
            'develop/knowledge/quality-gates',
          ],
        },
        {
          type: 'category',
          label: 'Developer guides',
          link: {
            type: 'doc',
            id: 'develop/guides/index',
          },
          items: [
            'develop/guides/adding-an-adapter',
            'develop/guides/testing-an-adapter',
            'develop/guides/publishing-an-image',
          ],
        },
        {
          type: 'category',
          label: 'Guidelines',
          link: {
            type: 'doc',
            id: 'develop/guidelines/index',
          },
          items: [
            'develop/guidelines/writing-an-adapter',
            'develop/guidelines/testing-adapters',
            'develop/guidelines/testing',
            'develop/guidelines/testing-tiers',
            'develop/guidelines/secret-redaction',
          ],
        },
        'develop/constitution',
        'develop/adr-index',
      ],
    },
    {
      type: 'category',
      label: 'Release notes',
      collapsible: true,
      collapsed: true,
      link: {
        type: 'generated-index',
        slug: 'release-notes',
      },
      items: [
        {
          type: 'category',
          label: 'Infrahub Sync',
          link: {
            type: 'generated-index',
            slug: 'release-notes/infrahub-sync',
          },
          items: [
            'release-notes/infrahub-sync/release-2_0_1',
            'release-notes/infrahub-sync/release-2_0_0',
            'release-notes/infrahub-sync/release-1_6_0',
            'release-notes/infrahub-sync/release-1_5_6',
          ],
        },
      ],
    },
    'container-image',
    'compose-deployment',
    'contributing',
  ]
};

export default sidebars;
