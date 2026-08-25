# H5 Privacy Decisions (accepted)

Authoritative record of the two privacy decisions H4.6 raised and left open.
Both are now **accepted by the user**. This file is the reference; the H4.5 and
H4.6 reports remain sealed as evidence of *how* the questions were established
and are deliberately not edited.

Status at time of writing: **no real WeChat data has been accessed.** These
decisions govern the initial real-data phase once the remaining gates clear.

## Decision 1 — Provider transmission: ACCEPTED

**Real WeChat message content may be transmitted to Anthropic / Claude for
inference.**

This is an explicit privacy boundary, not local-only processing. It follows
directly from the architecture: the digest is produced by a hosted model, so
message text must leave the machine for every digest. H4.6 demonstrated this
directly by observing a synthetic canary token in the outgoing request body.

What this acceptance does and does not cover:

- **Covered:** message text, sender names, conversation titles, and visible-time
  strings from captured WeChat messages are sent to Anthropic as part of the
  digest request.
- **Not covered by local controls:** anything Anthropic retains is governed by
  the provider's API terms and retention policy. The local consent gate, the
  read-only MCP boundary, and the runner's cleanup policy have **no effect** on
  provider-side retention.
- **Prompt caching** was observed at a 94% cache-read rate in H4, which implies
  provider-side retention of prompt content for at least the cache TTL.
- **Scope of the acceptance:** Anthropic / Claude only. Switching providers is
  a **new** decision requiring separate acceptance, not something this covers.

This boundary must stay documented wherever the shadow workflow is described.
It is a property of the design, not a defect to be fixed later.

## Decision 2 — Digest retention: review-only, no on-disk retention

For the initial real-data phase, digest output is **review-only**:

- printed to stdout for the operator to read, then discarded;
- **not** written to a normal file by default;
- no logging of digest text, and no copying into notes, reports, or commits.

The reasoning is empirical rather than precautionary. A digest built from real
messages is itself derived real data, and a synthetic H5 run showed the model
reproducing a credential-like string **verbatim** in its digest, while earlier
runs had voluntarily withheld similar strings. Redaction is model discretion and
cannot be assumed, so the output must be treated as sensitive as the input.

The runner already implements this: it prints the digest to stdout and never
writes it to a file.

Any future on-disk retention is a **separate decision** and needs an explicitly
approved policy covering location, lifetime, and deletion — the same standard
applied to the Hermes session store.

## Unchanged limitations

Neither decision alters these:

- **Deletion semantics** — cleanup is an application-level purge verified by
  content search. No forensic or physical erasure is claimed (free-space
  remnants, TRIM, APFS snapshots, Spotlight, swap, and pre-cleanup backups
  remain out of scope).
- **Local persistence** — raw content still lands in the Hermes session store on
  every run, and in a `request_dump_*.json` on provider failure. The runner's
  three-part cleanup remains mandatory on both paths.
- **Electron `userData`** — still an unaudited persistence boundary outside
  `HERMES_HOME`; see the pending Desktop gates.

## Remaining gates, in order

1. **#94339** merged and available in a stock Hermes release.
2. **Stock A + J smoke** on the updated stock runtime.
3. **Desktop synthetic validation** — isolated `HERMES_DESKTOP_USER_DATA_DIR`,
   canary audit of both `HERMES_HOME` and Electron `userData`, and structural
   enforcement or pre-read assertion of the exact eight-tool boundary.
4. **H5 real-data shadow run.**

Provider transmission and digest retention are **no longer gates** — they are
decided, and recorded here as standing boundaries.
