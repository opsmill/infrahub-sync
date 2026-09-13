Fixed generated DiffSync model annotations for schemas read from the Infrahub API.
Optional attributes and many relationships are annotated and defaulted correctly again,
and string attribute defaults are now emitted as Python literals, so defaults containing
quotes, newlines or backslashes stay valid Python and keep their exact schema value.
