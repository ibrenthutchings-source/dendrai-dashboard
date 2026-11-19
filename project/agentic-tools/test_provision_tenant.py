"""
test_provision_tenant.py — the pure-Python helpers in provision_tenant.py
(slug validation, db-name derivation, DSN rewriting). These don't touch a
real Postgres admin connection, but getting the DSN-swap wrong is exactly
the kind of bug that would provision one tenant against a DB name derived
from a different tenant's slug — worth covering directly.
"""
import pytest

import provision_tenant as pt


def test_validate_slug_accepts_normal_slug():
    assert pt._validate_slug("companyx") == "companyx"
    assert pt._validate_slug("Company-X") == "company-x"  # case-folded


@pytest.mark.parametrize("bad", ["", "a", "-abc", "abc-", "ABC!", "co mpany", "a" * 64])
def test_validate_slug_rejects_invalid(bad):
    with pytest.raises(ValueError):
        pt._validate_slug(bad)


def test_db_name_for_slug_folds_hyphens_and_prefixes():
    assert pt._db_name_for_slug("company-x") == "tenant_company_x"
    assert pt._db_name_for_slug("companyx") == "tenant_companyx"


def test_dsn_with_dbname_swaps_only_the_database():
    admin_dsn = "postgresql://user:pass@cluster-host:5432/postgres?sslmode=require"
    result = pt._dsn_with_dbname(admin_dsn, "tenant_companyx")
    assert result == "postgresql://user:pass@cluster-host:5432/tenant_companyx?sslmode=require"


def test_dsn_with_dbname_different_slugs_never_collide():
    admin_dsn = "postgresql://user:pass@cluster-host:5432/postgres"
    dsn_a = pt._dsn_with_dbname(admin_dsn, pt._db_name_for_slug("companya"))
    dsn_b = pt._dsn_with_dbname(admin_dsn, pt._db_name_for_slug("companyb"))
    assert dsn_a != dsn_b
    assert "tenant_companya" in dsn_a
    assert "tenant_companyb" in dsn_b
