"""Modules both the worker and the manager reach.

Kept separate because everything here ships in every final per-tool image
alongside ``worker``: it is part of what runs next to eleven third-party
binaries, and editing it moves every final image's digest. Adding a module here
is a deliberate widening of that surface, not a convenience.
"""
