Refuse Infrahub syncs before destination writes when the schema mapping identity
is finer than every key declared by the destination schema, preventing distinct
source objects from silently converging onto one object.
