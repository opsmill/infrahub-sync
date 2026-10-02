The node round-trip integration test now waits until GraphQL exposes the probe kind's create mutation, not only until the REST schema lists the kind, so it no longer fails on a freshly reset stack.
