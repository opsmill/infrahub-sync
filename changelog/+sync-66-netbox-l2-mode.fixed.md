Fixed NetBox interface synchronization failing for tagged and tagged-all L2 modes.
NetBox q-in-q mode is explicitly refused because the example destination schema cannot
represent it; malformed non-null modes also fail contextually at the transform boundary.
