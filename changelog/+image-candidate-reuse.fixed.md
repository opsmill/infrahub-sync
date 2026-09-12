Fixed the container image build reusing a cached wheel of the previous source after a
Python-only edit, which could ship earlier code under a newer recorded revision. The one
local distribution's wheel is now rebuilt and reinstalled whenever either of its import
trees or its package metadata changes. Everything that runs after a build — the bill of
materials, the vulnerability scan, and the image smoke — now exports each platform from
the retained OCI layout instead of building a second image, so those gates describe the
artifact the recorded digests name.
