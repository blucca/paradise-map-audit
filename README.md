# Paradise map audit

A reproducible Mapping Equity case: five healthcare records share one street address across **6.035 km**, and a **0.9902-confidence, open emergency-room candidate** conflicts with a documented closed emergency department.

- [Methodology report](report.md) and [submission PDF](results/paradise-map-audit.pdf)
- [Main figure](results/paradise-identity.png)
- [Exact source records](results/case-records.json)
- [Input provenance and licenses](evidence/input-manifest.json)
- [Licensed closure evidence](evidence/closure.json)
- [Current-release source check](evidence/current-release.json) and [upstream correction request](https://github.com/OvertureMaps/data/issues/583)

## Run

Python 3.12; approximately 23 MB of bundled inputs. Generated scratch files go in the chosen work directory.

```sh
python -m pip install -r requirements.txt
python reproduce.py --work-dir ./temp/reproduction
```

The single pass builds the spatial join, address-fracture screen, emergency query, review disposition, figure, and the 9,794-row neutral-midpoint leaderboard submission. Embedded assertions check the key row counts, tract assignments, licensed identity match, and measured distances. DuckDB uses one thread and a 512 MB memory budget.

**Entry:** Zindi user **blucca**, submission **NQUquvhM**, accepted with public RMSE **0.449105407** on 15 October 2026. The official platform sample requires 415 Eastern Washington IDs beyond the 9,379 current four-region list; all 9,794 supplied IDs receive the same neutral 0.5. The report targets the **Best Bias Discovery** and **Best Documentation** writeup prizes. Public code is shared through [competition discussion 35345](https://zindi.world/competitions/bias-bounty-mapping-equity-challenge/discussions/35345). Both special-prize forms returned confirmation on 15 October 2026.

Code: MIT. Report and derived tables: CC BY-SA 4.0. Bundled source terms and attributions remain in `evidence/`.
