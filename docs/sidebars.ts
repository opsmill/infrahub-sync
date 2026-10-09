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
            id: 'development/knowledge/index',
          },
          items: [
            'development/knowledge/sync-architecture',
            'development/knowledge/repository-tour',
            'development/knowledge/adapter-anatomy',
            'development/knowledge/schema-mapping',
            'development/knowledge/incremental-and-cache',
            'development/knowledge/plan-artifact',
            'development/knowledge/planned-write-and-apply',
            'development/knowledge/apply-guard',
            'development/knowledge/configuration-foundation',
            'development/knowledge/execution-surface',
            'development/knowledge/orchestration-prefect',
            'development/knowledge/quality-gates',
            'development/knowledge/image-publishing',
          ],
        },
        {
          type: 'category',
          label: 'Developer guides',
          link: {
            type: 'doc',
            id: 'development/guides/index',
          },
          items: [
            'development/guides/adding-an-adapter',
            'development/guides/testing-an-adapter',
            'development/guides/publishing-an-image',
            'development/guides/preview-stack',
            'development/guides/netbox-benchmark-tiers',
          ],
        },
        {
          type: 'category',
          label: 'Guidelines',
          link: {
            type: 'doc',
            id: 'development/guidelines/index',
          },
          items: [
            'development/guidelines/writing-an-adapter',
            'development/guidelines/testing-adapters',
            'development/guidelines/testing',
            'development/guidelines/testing-tiers',
            'development/guidelines/secret-redaction',
            'development/guidelines/ci-workflows',
          ],
        },
        'development/constitution',
        'development/adr-index',
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
