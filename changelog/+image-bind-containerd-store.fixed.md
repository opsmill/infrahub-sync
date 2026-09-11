Fixed the Compose deployment refusing to start on a host whose Docker runs the containerd
image store, which is the default from Docker Engine 29. That store identifies a loaded
image by the digest of a manifest it synthesizes at load time, and the shipped archive
carries none of its own, so neither identity the bundle named could be resolved. The
release now derives that third identity from the exported archive's own bytes and writes
it into `image.bind` as `INFRAHUB_SYNC_IMAGE_MANIFEST`, and the deployment tries the index
reference, the manifest digest, and the configuration digest in that order, persisting
whichever one this host holds. A host that resolves none of them is told all three and
which archive to load.
