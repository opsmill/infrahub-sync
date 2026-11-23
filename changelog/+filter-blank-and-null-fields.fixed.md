Schema mapping filters now handle records whose field is empty (`""`) or null, such as a NetBox device with no description, no platform or no primary IP address, instead of dropping them or stopping the load of the model:

- `not contains` and `not in` with a string value now keep these records. Before, `not contains` dropped them, so they passed neither `contains` nor `not contains`.
- `regex`, `is_ip_within`, `>`, `<`, `>=`, `<=` and `in` with a string value now drop these records. Before, they raised `TypeError` or `ValueError`, which stopped the load of the whole model.
