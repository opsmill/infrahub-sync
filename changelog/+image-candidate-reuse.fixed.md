Fixed the container image build reusing a cached wheel of the previous source after a
Python-only edit, which could ship earlier code under a newer recorded revision. The one
local distribution's wheel is now rebuilt and reinstalled whenever either of its import
trees or its package metadata changes.
