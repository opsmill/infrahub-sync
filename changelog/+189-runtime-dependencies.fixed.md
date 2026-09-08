A plain `pip install infrahub-sync` now includes `requests` and `urllib3`, which product code
imports but which previously arrived only through the `dev` extra. `packaging`, `pydantic` and
`typing-extensions` are declared directly instead of relying on other packages to bring them. The
`netutils` and `pyarrow` lower bounds now match the oldest versions that work on Python 3.13.
