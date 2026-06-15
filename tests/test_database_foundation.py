from __future__ import annotations

import sqlite3

import pytest

from upstream_edge.obsidian_db import Database, RsvCat


def test_open_close_context_and_path(tmp_path):
    db_path = tmp_path / "foundation.obsdb"
    sqlite3.connect(db_path).close()

    with Database.open(db_path) as db:
        assert db.path == db_path

    with pytest.raises(RuntimeError, match="Database is closed"):
        _ = db.wells()


def test_open_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not create new files"):
        Database.open(tmp_path / "missing.obsdb")


def test_transaction_commits_and_rolls_back(tmp_path):
    db_path = tmp_path / "tx.obsdb"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "create table Main (prop_id text primary key, api_10 text, rsv_cat text not null, "
            'lease text, well_number text, field text, operator text, category text, "group" text, '
            "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
            "first_prod text, state text, county text, surface_latitude real, surface_longitude real, "
            "bh_latitude real, bh_longitude real)"
        )
        conn.execute(
            "create table WellModels (prop_id text, scenario text, exp_model_name text, "
            "capex_model_name text, diff_model_name text, tax_model_name text, "
            "shrink_yield_model_name text, interest_model_name text)"
        )
        conn.execute(
            "create table Interest (prop_id text, model text, start text, wi_pct real, nri_pct real)"
        )
        conn.execute("create table Abandonment (prop_id text, model text, cost_gross real)")
        conn.execute("create table WellAttributes (prop_id text primary key)")
        conn.commit()
    finally:
        conn.close()

    with Database.open(db_path) as db:
        with db.transaction():
            db.add_well("P1", rsv_cat=RsvCat.PDP)

        assert db.well("P1") is not None

        with pytest.raises(ValueError), db.transaction():
            db.add_well("P2", rsv_cat=RsvCat.PDP)
            raise ValueError("boom")

        assert db.well("P2") is None


def test_nested_transaction_raises(tmp_path):
    db_path = tmp_path / "nested.obsdb"
    sqlite3.connect(db_path).close()

    with Database.open(db_path) as db, db.transaction():
        with pytest.raises(RuntimeError, match="transaction already open"):
            with db.transaction():
                pass
