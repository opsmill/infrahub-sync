Refuse Infrahub creates before destination writes when the generated model
identity is finer than the human-friendly ID (or fallback default filter) used
to match upserts, preventing distinct source objects from silently converging
onto one object. Serial and parallel sync modes both report the refusal through
the normal CLI error path.
