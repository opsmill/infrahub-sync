# Contract: Infrahub destination settings for lineage

Applies to the `destination.settings` block of a configuration package whose destination adapter is
`infrahub`. Field rules are in [data-model.md](../data-model.md).

## Accepted

```yaml
destination:
  name: infrahub
  settings:
    url: "https://infrahub.example.com"
    token: "${INFRAHUB_API_TOKEN}"
    owner:
      kind: CoreAccountGroup
      name: network-team
    protect: false
```

- `source` absent: the source is the account behind `token`.
- `owner` absent: the import sets no owner.
- `protect` absent: `false`.

## Refused at registration

Each case gives one validation finding at the setting's pointer.

| Input | Finding names |
|---|---|
| `owner: network-team` | `owner` must be `{kind, name}` |
| `source: {kind: BuiltinTag, name: x}` | `source` kind `BuiltinTag` is not one of the allowed kinds |
| `owner: {kind: CoreAccountGroup}` | `owner` has no `name` |
| `owner: {kind: CoreAccountGroup, name: x, id: y}` | `owner` has an unknown key `id` |
| `protect: "yes"` | `protect` must be `true` or `false` |

## Refused at the start of a plan

| Condition | Cause recorded on the run |
|---|---|
| `owner: {kind: CoreAccountGroup, name: network-team}` and no such group | the setting `owner` names `CoreAccountGroup` `network-team`, which does not exist |
| The import account cannot read the named kind | missing permission, quoting the permission Infrahub names ([run-errors.md](./run-errors.md)) |

The run stops before it reads the source system in both cases.

## Refused at the start of an apply

| Condition | Cause recorded on the run |
|---|---|
| A named source or owner no longer exists | the same cause as in a plan (FR-011) |
| The saved plan has no `lineage.import_account` (made before this feature) | the plan records no import account; make a new plan (FR-012) |
| The import account differs from the plan's `lineage.import_account` | the plan recorded `CoreAccount` `netbox`; the import account is `<other account>` (FR-012) |

Apply refuses before any write in both cases.
