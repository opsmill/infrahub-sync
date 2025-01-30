import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  syncSidebar: [
    'sync/readme',
    {
      type: 'category',
      label: 'Guides',
      items: [
        'sync/guides/installation',
        'sync/guides/creation',
        'sync/guides/run',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'sync/reference/config',
        'sync/reference/cli',
      ],
    },
  ]
};

export default sidebars;
