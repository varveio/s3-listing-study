# normalize.sh adapter fixtures

Synthetic `s4cmd ls` output modeled on `pretty_print` (s4cmd.py:1592-1622), used
to exercise `normalize.sh`. **Synthetic, not captured from a real s4cmd run** —
no listing mode could be executed (no anonymous access, CREDS=none), so these are
a construction check of the parser, not a `[RUN]` against tool output.

- `fixture-ls-r.txt` → `expected-ls-r.tsv` (recursive): includes a key with an
  interior space, correctly preserved.
- `fixture-ls-shallow.txt` → `expected-ls-shallow.tsv` (shallow): a `DIR` line and
  a root-level object.

Known non-coverage (tool-side, not adapter): s4cmd `rstrip()`s each output line
(s4cmd.py:1622), so a key with **trailing** whitespace is unrecoverable; a key
containing a **newline** is split by the line-oriented format. The fixtures do not
cover these because the tool itself cannot represent them.
