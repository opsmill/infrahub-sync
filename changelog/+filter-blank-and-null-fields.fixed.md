Schema mapping filters now handle records whose field is empty (`""`) or null, such as a NetBox device with no description, no platform or no primary IP address. Each filter keeps or drops these records instead of stopping the load of the model:

- `not contains` now keeps them. Before, it dropped them, so they passed neither `contains` nor `not contains`.
- `not in` with a string value now keeps them. Before, it raised `TypeError` on a null field and dropped an empty one.
- `in` with a string value now drops them. Before, it raised `TypeError` on a null field and kept an empty one.
- `regex` now drops them. Before, it raised `TypeError` on a null field, and kept an empty one when the pattern matched an empty string, such as `.*`.
- `is_ip_within`, `>`, `<`, `>=` and `<=` now drop them. Before, they raised `TypeError` or `ValueError`.

Before, a `TypeError` or `ValueError` from a filter stopped the load of the whole model.

`contains` and `not contains` now raise `TypeError` for a number field of `0`, as they already did for other numbers. Before, both dropped a record whose field was `0`.
