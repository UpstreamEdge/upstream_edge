from __future__ import annotations

from datetime import date

import pytest
from conftest import create_attribute_schema, exec_sql

from upstream_edge.obsidian_db import Database, MissingDependencyError, RsvCat


def test_df_reader_returns_frame_when_pandas_available(tmp_path):
    pd = pytest.importorskip("pandas")
    db_path = tmp_path / "df.obsdb"
    create_attribute_schema(db_path)
    exec_sql(db_path, ["alter table WellAttributes add column Basin text"])

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP, lease="MITCHELL", first_prod=date(2024, 1, 1))
        db.set_well_attribute("P1", "Basin", "Midland")

        wells_df = db.wells_df()
        attrs_df = db.all_well_attributes_df()

    assert isinstance(wells_df, pd.DataFrame)
    assert list(wells_df.columns)[0:3] == ["prop_id", "api_10", "rsv_cat"]
    assert wells_df.loc[0, "rsv_cat"] == "PDP"
    assert attrs_df.loc[0, "prop_id"] == "P1"
    assert attrs_df.loc[0, "Basin"] == "Midland"


def test_pandas_adapter_raises_missing_dependency(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError("no pandas")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    db_path = tmp_path / "no_pandas.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Main (prop_id text primary key, api_10 text, rsv_cat text not null, "
                'lease text, well_number text, field text, operator text, category text, "group" text, '
                "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
                "first_prod text, state text, county text, surface_latitude real, "
                "surface_longitude real, bh_latitude real, bh_longitude real)"
            ),
        ],
    )

    with Database.open(db_path) as db:
        with pytest.raises(MissingDependencyError, match="upstream-edge\\[pandas\\]"):
            db.wells_df()
