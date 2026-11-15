"""The root helper of the Linux backend.

The window runs as a normal user and cannot touch the firewall or start a packet-capturing process. This small
service does, for one job only: switch the bypass on (an nftables table plus the engine ``nfqws2``) and off.

Trust model (the same as for membership of the ``docker`` group): whoever may talk to the helper socket can make
the helper run the engine with the options of a preset, as the engine user. The socket is ``root:zapretgui 0660``;
the package adds the installing user to that group. The checks in ``policy`` (fixed engine path, refused options,
files only inside the program folder and the caller's own data folder) are defence in depth, not a sandbox.

* ``protocol`` — one JSON object per line over a Unix socket;
* ``policy``   — what the helper accepts from a caller;
* ``service``  — the lifecycle: start, stop, status and removal of leftovers; every system call is injected;
* ``server``   — the socket loop (``python -m platforms.linux.helper`` is what the systemd unit runs);
* ``client``   — what the window's runner uses.
"""
