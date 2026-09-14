The saved plan format is now version 3: an update operation records the destination object it will
be applied to. Plans written in format 2 can still be read and reviewed, but `apply` refuses them and
asks for a fresh `diff`, because their updates carry no recorded id to key on.
