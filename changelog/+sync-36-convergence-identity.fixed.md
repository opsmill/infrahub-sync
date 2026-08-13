Refuse Infrahub syncs before destination writes when the generated model
identity is finer than the human-friendly ID (or fallback default filter) used
to match upserts, preventing distinct source objects from silently converging
onto one object.
