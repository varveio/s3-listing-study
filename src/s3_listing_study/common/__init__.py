"""Modules used both inside a subject image and on the host.

Kept separate because everything here ships in every derived image: the attempt
engine reaches it, so it is part of what runs next to eleven third-party
binaries, and editing it moves every subject image's digest. Adding a module
here is a deliberate widening of that surface, not a convenience.
"""
