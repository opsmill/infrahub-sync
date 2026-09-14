Destination writes are now keyed from the plan's own data rather than from a check on the SDK's
private pre-save render, which had stopped agreeing with the mutation actually sent: an update carries
the destination id recorded for it at plan time, and a create is refused unless planning can prove its
payload carries every human-friendly-ID component the destination matches on. Kinds whose
human-friendly ID crosses a relationship are supported again, kinds that declare none are supported for
updates, and several creates that would converge onto one destination object are refused instead of
silently merged.
