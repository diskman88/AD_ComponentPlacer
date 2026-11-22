---
name: ad-postlayout-optimization
description: Plan and execute constrained post-layout optimization in Altium after AD Component Placer has completed its pre-layout. Use for board-frame, keepout, structural, and electrical refinement without scattering the generated functional blocks.
---

# AD post-layout optimization

Use this skill only after ClassPlacer has completed a batch and the matching
`.adstatus` begins with `APPLIED`. Do not invoke AD tools while ClassPlacer is
exporting, solving, waiting, or applying its plan.

Read the job's `.ai-handoff.json` before inspecting the live PCB. Its groups
are `rigid_first`: preserve member offsets and rotation by default. Treat
locked members, centers, and user-designated structural parts as immutable.

First perform a read-only AD preflight: board outline, keepouts, locked parts,
connectors, component collisions, critical nets, and existing routing. Then
prepare an explicit brief using [the reference schema](references/brief-schema.md).

Apply changes in this order:

1. Translate a complete group as a rigid block.
2. Make bounded local adjustments only where the brief permits them.
3. Ask for approval before rotating or re-expanding a group, moving a locked
   part, or crossing an analog/RF/high-voltage/isolation boundary.

After each write, recheck board/keepout membership and collisions. Complete
with DRC and a concise movement report. If constraints conflict, leave the
pre-layout intact and ask for a design decision rather than silently dispersing
the group.
