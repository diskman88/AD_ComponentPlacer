# AD Component Placer — AI collaboration guide

## Project role

This project is a Windows/Altium Designer component-class placement plugin.
The Python solver calculates a fully validated plan; the Pascal script reads
the PCB and applies that plan atomically in Altium Designer.

## Working rules

- Keep `main` buildable and do not commit per-run snapshots, plans, reports,
  or logs.
- Preserve the plan/snapshot protocol unless both Python and Pascal readers
  are updated together, with compatibility tests.
- `ClassPlacer.template` is the authoritative Pascal source. `Setup.cmd` runs
  `install.py` to generate `ClassPlacer.pas` for a specific local Python path.
- Never move locked components or selected center components. A locked center
  is a user-established placement anchor; its class follows the anchor without
  a ClassPlacer Room/search-boundary restriction, while clearance checks remain
  mandatory.
- Treat electrical score first, total HPWL second, and visual alignment last.
  Cosmetic alignment must never regress electrical HPWL, total HPWL, clearance,
  or center compactness.
- Treat ClassPlacer as a one-shot local pre-layout tool. After `APPLIED`, board
  fitting, structure-aware moves, alignment, and layer decisions belong to the
  human/AI post-layout stage; rerun only if Class membership, center selection,
  or local electrical topology changes.
- Keep source comments and user-facing documentation in Chinese where it helps
  the target users. Save Python sources as UTF-8.

## Required validation after solver or protocol changes

```powershell
python -m unittest -v test_batch
python -m py_compile solver.py batch.py class_desktop.py install.py
```

Altium-side compilation and real-board writeback need manual verification in
Altium Designer; do not claim that they were executed unless they were.
