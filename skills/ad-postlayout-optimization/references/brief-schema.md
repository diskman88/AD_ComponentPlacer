# AI post-layout brief schema

Use `ad-component-placer-ai-postlayout-brief/v1`, following
`AI_POSTLAYOUT_BRIEF.example.json` in the repository root.

Required decisions:

- `handoff`: the matching `.ai-handoff.json` file;
- `mode`: use `preserve_prelayout` unless the user explicitly authorizes a
  broader re-layout;
- `priority`: hard legality, electrical topology, connection length, density,
  then alignment;
- `hard_constraints`: immutable components plus AD-sourced board outline and
  keepouts;
- `blocks`: one operation and local-adjustment budget per Class;
- `approval_required_for`: all operations that can invalidate the pre-layout.

Use only `translate_rigid` for a block when its member offsets must remain
exact. `translate_then_bounded_adjust` permits small, logged member moves up to
`allow_member_delta_mil`; it does not permit moving a locked member or center.

The brief is a planning contract for AI and `eda-agent`; it is not executed by
ClassPlacer and cannot override AD design rules.
