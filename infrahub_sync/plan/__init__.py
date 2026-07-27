"""Saved plan artifact: the on-disk plan a sync run writes and an apply run reads back.

Holds the artifact's canonical form, its checksums, the reader and its pre-apply
verification, and the review surface that renders a saved plan without touching a
source or a destination.
"""
