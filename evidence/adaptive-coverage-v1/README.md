# Adaptive cross-release coverage evidence bundle

This compact bundle retains one canonical native simulator-success row for each
of the 95 solved tasks. It is sufficient to replay task-level coverage against
the fixed 120-task catalog. It does not contain failed or infrastructure rows
and therefore must not be used to estimate total campaign token or wall-clock
spend.

The rows comprise 67 canonical successes from the initial strict evaluation and
28 additional unique successes from later validated adaptive releases. The two
task sets are disjoint.

Run-local identifiers, journal names, media filenames, and machine paths are
not part of this compact public bundle. Release identities use stable public
aliases; task, seed, final verdict, Token, VLM-call, and elapsed-time fields are
retained for deterministic replay.
