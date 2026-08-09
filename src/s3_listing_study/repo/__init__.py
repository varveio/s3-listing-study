"""Checks about this repository's own content, not about any attempt.

Capsule validation, Markdown link checking, and source-anchor verification. They
read the tree a contributor is editing and answer whether it is internally
consistent; none of them touches a subject, a result, or a campaign. Kept apart
from ``manager`` so that name stays true to the orchestrating role the
deployment vocabulary gives it, and out of the shipped layers for the same
reason everything here is: nothing in a subject image needs them.
"""
