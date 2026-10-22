# v1 and v2 example configurations

These files are the input for the tests of the `convert` command. Each `config.yml` is a
byte-for-byte copy of an example configuration from a released tag of this repository:

- The 14 directories other than `v1_netbox_to_infrahub` hold `examples/<directory>/config.yml`
  from tag `2.0.2`. The directory names are the same as on the tag.
- `v1_netbox_to_infrahub/config.yml` holds `examples/netbox_to_infrahub/config.yml` from tag
  `1.6.1`.

Do not edit these files. The tests check how the command converts the configurations that
v1 and v2 users have, so a change to a file would test a configuration that no user has. To
refresh a file, copy it again from its tag, for example:

```bash
git show "2.0.2:examples/netbox_to_infrahub/config.yml" > tests/data/v2_examples/netbox_to_infrahub/config.yml
```

`.yamllint.yml` excludes this directory, because some copied files do not follow the
repository's YAML rules.
