This is a script that extracts AES keys from the mako VOD flash player.

There are two separate keys:
* Payment service key: the service that generates tokens for Akamai HDS. Its
  responses are encrypted.
* Playlist key: for some reason the playlist XML containing the stream URL is
  also encrypted, and using a different key.

Dependencies (all executables are assumed to be in `PATH`):
* RABCDasm
* swftools
* redtamarin (redshell)
