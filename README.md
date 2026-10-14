# Paradise map audit

A reproducible Mapping Equity case: five healthcare records share one street address across **6.035 km**, and a **0.9902-confidence, open emergency-room candidate** conflicts with a documented closed emergency department.

- [Methodology report](report.md)
- [Main figure](results/paradise-identity.png)
- [Exact source records](results/case-records.json)
- [Input provenance and licenses](evidence/input-manifest.json)
- [Licensed closure evidence](evidence/closure.json)

## Run

Python 3.12; approximately 23 MB of bundled inputs. Generated scratch files go in the chosen work directory.

```sh
python -m pip install -r requirements.txt
python reproduce.py --work-dir ./temp/reproduction
```

The single pass builds the spatial join, address-fracture screen, emergency query, review disposition, figure, and a 9,379-row midpoint submission candidate. Embedded assertions check the key row counts, tract assignments, licensed identity match, and measured distances. DuckDB uses one thread and a 512 MB memory budget.

**Entry state:** private research package; formal Zindi account linkage and upload receipt pending. Publication through the competition discussion channel accompanies public code sharing.

Code: MIT. Report and derived tables: CC BY-SA 4.0. Bundled source terms and attributions remain in `evidence/`.
