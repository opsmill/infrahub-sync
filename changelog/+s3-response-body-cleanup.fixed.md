Fixed the Sync service's S3 adapter leaving a retrieved object's response body open, so a
successful read, a size-bounded read that stops early, an invalid response, and a failed read
all release the connection immediately instead of holding it until garbage collection.
