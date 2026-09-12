Fixed the Sync service's S3 adapter leaving a retrieved object's response body open: a
successful read, a size-bounded read that stops early, an invalid response body, and a failed
read now all close the body when the read ends.
