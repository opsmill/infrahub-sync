<!-- markdownlint-disable -->
![Infrahub Logo](https://assets-global.website-files.com/657aff4a26dd8afbab24944b/657b0e0678f7fd35ce130776_Logo%20INFRAHUB.svg)
<!-- markdownlint-restore -->

# Infrahub Sync

[Infrahub](https://github.com/opsmill/infrahub) by [OpsMill](https://opsmill.com) acts as a central hub to manage the data, templates and playbooks that powers your infrastructure. At its heart, Infrahub is built on 3 fundamental pillars:

- **A Flexible Schema**: A model of the infrastructure and the relation between the objects in the model, that's easily extensible.
- **Version Control**: Natively integrated into the graph database which opens up some new capabilities like branching, diffing, and merging data directly in the database.
- **Unified Storage**: By combining a graph database and git, Infrahub stores data and code needed to manage the infrastructure.

## Introduction

Infrahub Sync is a versatile Python package that synchronizes data between a source and a destination system. It builds on the robust capabilities of `diffsync` to offer flexible and efficient data synchronization across different platforms, including Netbox, Nautobot, and Infrahub. This package features a Typer-based CLI for ease of use, supporting operations such as listing available sync projects, generating diffs, and executing sync processes.

## Using Infrahub sync

Documentation for using Infrahub Sync is available [here](https://docs.infrahub.app/sync/)
