"""Check that web/js/merge.js and sync_state.merge_srs_row still agree.

    python -m scripts.check_merge_ports

They are two halves of one rule — the phone merges with the JS, the laptop with
the Python — so a disagreement is a schedule that quietly differs by device,
which nothing in the UI would show. Run this after touching either.

Needs node on PATH for the JS half. The cases are generated, not stored: a
spread over every scheduling field plus rows with fields missing entirely,
sampled deterministically so runs are comparable.

One trap. The reset filter lives *inside* mergeSrs on the JS side and one level
up in _merge_srs on the Python side, so the JS is called with resetAt = -1 to
disable it. Comparing without that gives a few hundred phantom differences, all
of them rows with last_at = 0.
"""

import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from chess_trainer import PROJECT_ROOT, sync_state

VALUES = {
    "ease": [1.3, 2.5, 3.2],
    "interval_days": [0, 1, 7.5],
    "due_at": [0, 1000, 9_000_000],
    "reps": [0, 1, 5],
    "lapses": [0, 2, 9],
    "last_at": [0, 1000, 2000],
    "h": ["aaaa1111", "bbbb2222", None],      # None => the key is absent
}

DRIVER = """
import { readFileSync } from 'node:fs';
import * as merge from %(merge_url)s;

const cases = JSON.parse(readFileSync(%(cases)s, 'utf-8'));
const FIELDS = ['ease', 'interval_days', 'due_at', 'reps', 'lapses', 'last_at', 'h'];
const norm = (row) => (row == null ? null
  : Object.fromEntries(FIELDS.map((f) => [f, row[f] ?? null])));

const diffs = [];
cases.forEach((c, i) => {
  const map = new Map();
  if (c.mine) map.set('P', { pid: 'P', ...c.mine });
  const writes = merge.mergeSrs(map, [{ pid: 'P', ...c.incoming }], -1);
  const got = writes.length ? norm(writes[0]) : null;
  if (JSON.stringify(got) !== JSON.stringify(norm(c.want))) {
    diffs.push({ i, mine: c.mine, incoming: c.incoming, js: got, python: norm(c.want) });
  }
});
console.log(JSON.stringify(diffs));
"""


def build_cases() -> list[dict]:
    keys = list(VALUES)
    rows = [
        {k: v for k, v in zip(keys, combo) if v is not None}
        for combo in itertools.product(*(VALUES[k] for k in keys))
    ]
    # The full cross product is 3^7 squared; sample it on coprime strides.
    cases = []
    for i in range(0, len(rows), 37):
        for j in range(0, len(rows), 53):
            cases.append({"mine": rows[i], "incoming": rows[j]})
        cases.append({"mine": None, "incoming": rows[i]})
    cases += [
        # The winner carries no `h`: the value we hold must survive.
        {"mine": {"ease": 2.5, "reps": 1, "lapses": 4, "last_at": 100, "h": "keepme00"},
         "incoming": {"ease": 2.6, "reps": 2, "lapses": 0, "last_at": 200}},
        # Identical rows: no write at all.
        {"mine": {"reps": 3, "last_at": 500}, "incoming": {"reps": 3, "last_at": 500}},
        # Same instant, different progress: the tie-break.
        {"mine": {"reps": 1, "last_at": 500}, "incoming": {"reps": 9, "last_at": 500}},
    ]
    for case in cases:
        case["want"] = sync_state.merge_srs_row(case["mine"], case["incoming"])
    return cases


def main() -> None:
    cases = build_cases()
    tmp = Path(tempfile.mkdtemp())
    cases_path = tmp / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")

    driver = tmp / "driver.mjs"
    driver.write_text(DRIVER % {
        "merge_url": json.dumps((PROJECT_ROOT / "web/js/merge.js").as_uri()),
        "cases": json.dumps(str(cases_path)),
    }, encoding="utf-8")

    try:
        proc = subprocess.run(["node", str(driver)], capture_output=True,
                              text=True, check=True)
    except FileNotFoundError:
        raise SystemExit("node is not on PATH — needed to run the JS half")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"the JS half failed:\n{exc.stderr}") from exc

    diffs = json.loads(proc.stdout)
    if not diffs:
        print(f"{len(cases)}/{len(cases)} cases agree")
        return
    print(f"{len(diffs)}/{len(cases)} cases DISAGREE\n")
    for d in diffs[:5]:
        print(f"  case {d['i']}")
        print(f"    mine     {json.dumps(d['mine'])}")
        print(f"    incoming {json.dumps(d['incoming'])}")
        print(f"    js       {json.dumps(d['js'])}")
        print(f"    python   {json.dumps(d['python'])}")
    sys.exit(1)


if __name__ == "__main__":
    main()
