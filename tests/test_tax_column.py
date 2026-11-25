"""TaxModel's ad valorem column under either spelling.

Obsidian has always written `ad_valorum_tax`. A later version will write the
correct `ad_valorem_tax`; the library reads and writes whichever a file has,
and never renames it.
"""

from __future__ import annotations

import sqlite3
import warnings

import pytest
from conftest import create_scenario_schema, exec_sql, seed_models

from upstream_edge.obsidian_db import Database, RsvCat, TaxModelKind, TaxModelSegment

RENAME = "ALTER TABLE TaxModel RENAME COLUMN ad_valorum_tax TO ad_valorem_tax"
BOTH = "ALTER TABLE TaxModel ADD COLUMN ad_valorem_tax REAL"


def make(tmp_path, *changes):
    path = tmp_path / "tax.obsdb"
    create_scenario_schema(path)
    seed_models(path)  # TAX: every rate 0.1
    exec_sql(path, list(changes))
    return path


def columns(path):
    conn = sqlite3.connect(path)
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(TaxModel)")]
    finally:
        conn.close()


def stored(path, column, name="TAX"):
    conn = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in conn.execute(
                f"SELECT {column} FROM TaxModel WHERE tax_model_name = ? ORDER BY model_type",
                (name,),
            )
        ]
    finally:
        conn.close()


def segment(rate):
    return TaxModelSegment(
        kind=TaxModelKind.SIMPLE,
        sev_tax_oil=0.046,
        sev_tax_gas=0.075,
        sev_tax_ngl=0.075,
        ad_valorum_tax=rate,
    )


@pytest.mark.parametrize("changes", [(), (RENAME,)], ids=["as written today", "renamed"])
def test_the_rate_reads_and_writes_under_the_files_own_column(tmp_path, changes):
    path = make(tmp_path, *changes)
    before = columns(path)
    column = "ad_valorem_tax" if changes else "ad_valorum_tax"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path, change_log=False) as db:
            assert db.tax_models("TAX")[0].ad_valorum_tax == 0.1
            db.set_tax_model("TAX", [segment(0.025)])
            db.set_tax_model("NEW", [segment(0.031)])
            assert db.tax_models("TAX")[0].ad_valorum_tax == 0.025
            assert [model.ad_valorum_tax for model in db.tax_models()] == [0.031, 0.025]
    assert columns(path) == before, "the column is never renamed or added"
    assert stored(path, column) == [0.025]
    assert stored(path, column, "NEW") == [0.031]


def test_a_file_with_both_columns_uses_the_old_one_as_obsidian_does(tmp_path):
    path = make(tmp_path, BOTH, "UPDATE TaxModel SET ad_valorem_tax = 0.9")
    with Database.open(path, change_log=False) as db:
        assert db.tax_models("TAX")[0].ad_valorum_tax == 0.1
        db.set_tax_model("TAX", [segment(0.025)])
        assert db.tax_models("TAX")[0].ad_valorum_tax == 0.025
    assert stored(path, "ad_valorum_tax") == [0.025]


def test_a_wells_own_tax_model_is_copied_in_a_renamed_file(tmp_path):
    path = make(tmp_path, RENAME)
    exec_sql(
        path,
        [
            "insert into TaxModel values ('MAIN<>P1', 'SIMPLE', 0.04, 0.07, 0.07, 0.033)",
        ],
    )
    with Database.open(path, change_log=False) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
    exec_sql(path, ["update WellModels set tax_model_name = 'MAIN<>P1' where prop_id = 'P1'"])
    with Database.open(path, change_log=False) as db:
        db.copy_well("P1", "P2")
        (copied,) = db.tax_models("MAIN<>P2")
        assert copied.ad_valorum_tax == 0.033
    assert columns(path)[-1] == "ad_valorem_tax"
