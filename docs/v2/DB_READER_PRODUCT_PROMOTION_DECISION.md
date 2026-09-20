# DB Reader Product Promotion Decision — P1

## Decision

**P1 = MET.** The isolated database provider is approved for promotion to a
**supported, explicitly enabled local Reader role** behind the existing generic
Reader boundary.

The intended product role is:

- when a user has explicitly enabled database mode and its local prerequisites
  are ready, the database Reader is the preferred local read path;
- visual capture remains the fallback path;
- until the remaining promotion gates and product wiring are complete,
  `selected_source_name()` continues to default to `visual` and the database
  provider remains unregistered and unwired.

This records the product role. It does not itself implement that role.

## Evidence supporting the decision

- G1 synthetic implementation gate: MET.
- G2 real multi-part gate: MET at `f92311d`.
- P3 real multi-part verification: MET under D-032.
- The real gate verified seven readable message parts, complete conversation
  listing, real cross-part pagination, stable public message identity and
  sequence, valid empty parts, and the need for WAL-aware acquisition.

## What P1 does not authorize

P1 is deliberately narrower than product integration.

- **P2 remains unmet:** no standing acquisition capability is approved.
- **P4 remains unmet:** complete-container and future-WeChat-version
  compatibility are not yet established.
- **P5 remains unmet as an implementation gate:** no runtime/default selection
  behavior has changed. Visual remains the current product/default source and
  database mode remains off unless a later product integration explicitly
  enables it.
- No key extraction, decryption, process instrumentation, app re-signing,
  filesystem discovery or WAL replay is added to shipped code.
- No production module imports or registers the isolated provider.

## Next bounded implementation

The next implementation is **IdentityCatalog**, still inside the isolated
provider. It will accept only explicitly supplied read-only identity/database
handles and convert source-authored identity evidence into the existing
`IdentityResolver` inputs:

- conversation/session identity mapping;
- contact remark and nickname candidates.

It will not search for databases, obtain keys, decrypt files, automate WeChat,
or change source selection.

Room-member-specific display names are outside this first IdentityCatalog
slice unless independently supported by source-authored evidence.
