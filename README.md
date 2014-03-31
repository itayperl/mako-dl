mako-dl
=======

Download episodes from the Mako VOD service

Dependencies
------------
* libmms is a dependency for some stream (non-HDS streams).
* Python dependencies can be installed by running `pip install -r requirements.txt`.

Example usage
-------------

```
# List shows
mako -l

# List episodes of a show
mako -l /show/url

# Download season 1 of a show (see `mako --help` for the syntax of the  `-s` option)
mako -o DIRNAME -s 1: /show/url
```

TODO
----
* libmako is not script-friendly at all
* XBMC plugin
* The code requires some cleanup and documentation
