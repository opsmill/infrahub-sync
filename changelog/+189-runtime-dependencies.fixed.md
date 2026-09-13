A plain `pip install infrahub-sync` now includes `requests` and `urllib3`, which product code
imports but which previously arrived only through the `dev` extra. `packaging`, `pydantic` and
`typing-extensions` are declared directly instead of relying on other packages to bring them. The
`netutils` lower bound now matches the oldest version that works on Python 3.13. The `pyarrow`
bound is unchanged and stays higher than that: it already refuses every release before the one
carrying its fix. The locked and tested diffsync is now 2.2.3; the previously locked 2.2.2 was
yanked upstream.
