"""Tests for the MariaDB service helpers."""

import pytest

from app.services import mariadb


class TestAuthClause:
    def test_plain_password_is_quoted(self):
        clause = mariadb._auth_clause("p'w", None)
        assert clause == "IDENTIFIED BY 'p''w'"

    def test_native_password_hash_is_reused_verbatim(self):
        h = "*03D98834235F3AD4CE858733AA33C455088FAEF1"
        clause = mariadb._auth_clause("ignored", h)
        assert clause == f"IDENTIFIED VIA mysql_native_password USING '{h}'"

    def test_a_malformed_hash_is_rejected(self):
        for bad in ("03D98834", "*nothex", "*" + "a" * 39, "'; DROP USER"):
            with pytest.raises(ValueError):
                mariadb._auth_clause("x", bad)
