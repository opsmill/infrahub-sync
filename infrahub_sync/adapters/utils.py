from __future__ import annotations

from typing import Any


def get_value(obj: Any, name: str) -> Any | None:
    """Query a value in dot notation recursively"""
    if "." not in name:
        # Check if the object is a dictionary and use appropriate method to access the attribute.
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    first_name, remaining_part = name.split(".", maxsplit=1)

    # Check if the object is a dictionary and use appropriate method to access the attribute.
    sub_obj = obj.get(first_name) if isinstance(obj, dict) else getattr(obj, first_name, None)

    if not sub_obj:
        return None
    return get_value(obj=sub_obj, name=remaining_part)


def derive_identifier_key(obj: dict[str, Any]) -> str | None:
    """Try to get obj.id, and if it doesn't exist, try to get a key ending with _id"""
    obj_id = obj.get("id")
    if obj_id is None:
        for key, value in obj.items():
            if key.endswith("_id") and value:
                obj_id = value
                break

    # If we still didn't find any id, raise ValueError
    if obj_id is None:
        msg = "No suitable identifier key found in object"
        raise ValueError(msg)
    return obj_id
