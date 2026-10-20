"""PHP settings sized from the machine: the arithmetic, and what it refuses."""

from pathlib import Path

import pytest

from app.services import php_tune

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


def facts_for(total_mb, cpu=2, pools=1):
    reserve = php_tune.reserved_memory_mb(total_mb)
    budget = max(php_tune.WORKER_MB, total_mb)
    return {
        "cpu_count": cpu,
        "total_memory_mb": total_mb,
        "available_memory_mb": total_mb // 2,
        "reserved_memory_mb": reserve,
        "php_budget_mb": budget,
        "pool_count": pools,
        "worker_mb": php_tune.WORKER_MB,
        "concurrent_requests": max(1, budget // php_tune.WORKER_MB),
    }


def value_of(rows, key):
    return next(row["value"] for row in rows if row["key"] == key)


@pytest.mark.parametrize(
    "total_mb,memory_limit,opcache_mb",
    [(1024, "1024M", "96"), (2048, "1024M", "128"), (4096, "1024M", "192"), (8192, "1536M", "256"), (16384, "2048M", "384")],
)
def test_the_recommendation_follows_the_machine(total_mb, memory_limit, opcache_mb):
    rows = php_tune.recommendations(facts_for(total_mb))

    assert value_of(rows, "memory_limit") == memory_limit
    assert value_of(rows, "opcache.memory_consumption") == opcache_mb


def test_memory_limit_never_drops_below_what_a_hosting_customer_needs():
    # A ceiling per request, not memory taken up front: pm.max_children is what
    # bounds concurrency. Sizing it down to fit a small server is what kills a
    # WooCommerce import halfway through, so 1024M is a floor on every machine.
    for total_mb in (512, 1024, 2048, 4096, 8192, 16384):
        limit_mb = int(value_of(php_tune.recommendations(facts_for(total_mb)), "memory_limit").rstrip("M"))

        assert limit_mb >= php_tune.MIN_MEMORY_LIMIT_MB, total_mb


def test_a_bigger_machine_may_allow_more_but_never_less():
    limits = [
        int(value_of(php_tune.recommendations(facts_for(total)), "memory_limit").rstrip("M"))
        for total in (1024, 2048, 4096, 8192, 16384)
    ]

    assert limits == sorted(limits), "the recommendation must not shrink as RAM grows"


def test_the_reserve_never_swallows_the_whole_machine():
    for total_mb in (512, 1024, 2048, 16384, 65536):
        reserve = php_tune.reserved_memory_mb(total_mb)

        assert 128 <= reserve <= total_mb - 128


def test_workers_are_sized_off_total_ram_not_ram_minus_the_reserve(monkeypatch):
    """A 6 GB box was sized at 34 workers when its own caps allowed 48.

    Three separate things already hold this number down: WORKER_MB bills a
    worker at 128 MB when a measured WordPress worker is nearer 24 MB RSS,
    pm=ondemand means a worker exists only while it is serving, and the
    helper's cpu_cap/profile_cap bound a small machine. Taking a further
    20-45% off the top was a fourth cut that left every server short.
    """
    monkeypatch.setattr(php_tune, "total_memory_mb", lambda: 5925)
    monkeypatch.setattr(php_tune, "available_memory_mb", lambda: 4415)
    monkeypatch.setattr(php_tune, "cpu_count", lambda: 4)
    monkeypatch.setattr(php_tune, "pool_count", lambda: 3)

    facts = php_tune.server_facts()

    assert facts["php_budget_mb"] == 5925
    assert facts["concurrent_requests"] == 46
    # Still reported - an admin wants to see it - just no longer deducted.
    assert facts["reserved_memory_mb"] == 1481


def test_the_helper_sizes_pools_the_same_way():
    """bash and python must not drift: both size off total RAM."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("calculate_php_fpm_pool_tuning() {", 1)[1].split("\n}", 1)[0]

    assert 'php_budget_mb="$total_mb"' in body
    assert "total_mb - reserve_mb" not in body
    # The dead reserve helper went with it rather than rotting in place.
    assert "php_fpm_reserved_memory_mb" not in helper
    # The caps that actually protect a small box are still there.
    assert "cpu_cap=$((cpu_count * 4))" in body
    assert "pool_children <= cpu_cap" in body
    assert "pool_children <= profile_cap" in body


# What Debian's PHP package already ships, measured on a stock Ubuntu 24.04.
DISTRO_OPCACHE_MB = 128
DISTRO_MAX_FILES = 10000


def test_tuning_never_hands_back_less_than_the_package_already_gave():
    # opcache is one shared segment for the whole pool, not per worker, so
    # shrinking it saves almost nothing and costs cache hits. Recommending less
    # than the machine already had would be a downgrade wearing a tuning label.
    for total_mb in (1024, 2048, 4096, 8192, 16384):
        rows = php_tune.recommendations(facts_for(total_mb))

        assert int(value_of(rows, "opcache.memory_consumption")) >= DISTRO_OPCACHE_MB or total_mb <= 1024
        assert int(value_of(rows, "opcache.max_accelerated_files")) >= DISTRO_MAX_FILES


def test_a_setting_php_defaults_sensibly_is_not_reported_as_a_change(monkeypatch):
    # realpath_cache_size is already 4096K by default and appears in no file.
    # Comparing against the files alone claimed a change that did not exist.
    monkeypatch.setattr(php_tune, "_effective_values", lambda version: {"realpath_cache_size": "4096K"})
    monkeypatch.setattr(php_tune, "_read_ini_values", lambda paths, keys: {})
    monkeypatch.setattr(php_tune, "server_facts", lambda: facts_for(2048))

    row = next(item for item in php_tune.plan("8.4")["settings"] if item["key"] == "realpath_cache_size")

    assert row["current"] == "4096K"
    assert row["changes"] is False


def test_a_setting_nothing_reports_at_all_still_counts_as_a_change(monkeypatch):
    monkeypatch.setattr(php_tune, "_effective_values", lambda version: {})
    monkeypatch.setattr(php_tune, "_read_ini_values", lambda paths, keys: {})
    monkeypatch.setattr(php_tune, "server_facts", lambda: facts_for(2048))

    row = next(item for item in php_tune.plan("8.4")["settings"] if item["key"] == "opcache.enable_cli")

    assert row["current"] == ""
    assert row["changes"] is True, "unset is not the same as off"


def test_opcache_stays_honest_about_changed_files():
    # Turning validate_timestamps off is faster and means a customer editing a
    # file sees nothing happen. Never recommended.
    rows = php_tune.recommendations(facts_for(4096))

    assert value_of(rows, "opcache.validate_timestamps") == "1"
    assert value_of(rows, "opcache.save_comments") == "1"
    assert value_of(rows, "opcache.enable") == "1"


def test_every_recommendation_is_a_key_the_helper_will_accept():
    for row in php_tune.recommendations(facts_for(2048)):
        assert row["key"] in php_tune.TUNABLE_KEYS
        assert row["reason"], f"{row['key']} needs a reason an administrator can read"


def test_the_generated_file_says_where_it_came_from(monkeypatch):
    monkeypatch.setattr(php_tune, "server_facts", lambda: facts_for(2048, cpu=4, pools=3))
    text = php_tune.render_ini("8.4")

    assert "2048 MB RAM, 4 CPU, 3 PHP pool(s)" in text
    assert "memory_limit = 1024M" in text
    # The page where an administrator sets values by hand must keep winning.
    assert "99-spanel.ini" in text


@pytest.mark.parametrize(
    "current,recommended,changes",
    [
        ("128M", "128M", False),
        ("On", "1", False),          # PHP spells booleans both ways
        ("Off", "0", False),
        ("256M", "128M", True),
        ("", "128M", True),          # never set at all
        ("", "0", True),             # unset is not the same as a value of zero
        ("no value", "0", True),     # which is how php -i spells unset
        ("1M", "1024k", False),      # same size, different unit
        ("60", "60", False),
    ],
)
def test_a_setting_only_counts_as_changed_when_it_really_differs(current, recommended, changes):
    assert (php_tune._normalise(current) != php_tune._normalise(recommended)) is changes   # noqa: SLF001


def test_an_unknown_php_version_is_refused():
    with pytest.raises(ValueError):
        php_tune.current_values("9.9")
    with pytest.raises(ValueError):
        php_tune.apply("9.9")


def test_a_value_pinned_on_the_php_config_page_is_shown_as_such(monkeypatch):
    # That page writes every field on each save and PHP reads it last, so its
    # memory_limit wins over anything the tuner writes. Offering it as a change
    # to make would be offering something that cannot happen.
    monkeypatch.setattr(php_tune, "server_facts", lambda: facts_for(2048))
    monkeypatch.setattr(php_tune, "current_values", lambda version: {"memory_limit": "512M"})
    monkeypatch.setattr(php_tune, "pinned_by_php_config", lambda version: {"memory_limit": "512M"})

    result = php_tune.plan("8.4")
    row = next(item for item in result["settings"] if item["key"] == "memory_limit")

    assert row["overridden_value"] == "512M"
    assert result["overridden"] == 1
    # The other rows are still genuine changes; this one is not offered as one,
    # because applying it would not change what PHP runs with.
    offered = [item["key"] for item in result["settings"] if item["changes"] and not item["overridden_value"]]
    assert "memory_limit" not in offered
    assert result["changes"] == len(offered)


def test_jit_is_offered_on_php_8_and_not_before_it():
    # JIT arrived in 8.0; writing the keys on 7.4 would mean nothing.
    keys_84 = [row["key"] for row in php_tune.recommendations(facts_for(2048), "8.4", jit={"supported": True, "usable": True, "blocked_by": ""})]
    keys_74 = [row["key"] for row in php_tune.recommendations(facts_for(2048), "7.4", jit={"supported": False, "usable": False, "blocked_by": ""})]

    assert "opcache.jit" in keys_84 and "opcache.jit_buffer_size" in keys_84
    assert not any(key.startswith("opcache.jit") for key in keys_74)
    assert php_tune.supports_jit("8.0") and not php_tune.supports_jit("7.4")


def test_jit_is_left_out_when_something_on_the_server_blocks_it():
    # ionCube ships with spanel and owns the opcode handlers, so PHP refuses to
    # run JIT and warns on every worker start. Writing the keys would produce
    # that warning forever and change nothing.
    rows = php_tune.recommendations(facts_for(2048), "8.4", {"supported": True, "usable": False, "blocked_by": "ionCube Loader"})

    assert value_of(rows, "opcache.jit") == "disable"


def test_the_plan_reports_why_jit_is_unavailable(monkeypatch):
    monkeypatch.setattr(php_tune, "server_facts", lambda: facts_for(2048))
    monkeypatch.setattr(php_tune, "current_values", lambda version: {})
    monkeypatch.setattr(php_tune, "pinned_by_php_config", lambda version: {})
    monkeypatch.setattr(php_tune, "jit_status",
                        lambda version: {"supported": True, "usable": False, "blocked_by": "ionCube Loader"})

    result = php_tune.plan("8.4")

    assert result["jit_supported"] is True
    assert result["jit_usable"] is False
    assert result["jit_blocked_by"] == "ionCube Loader"
    # Not silence: the rows are there, telling PHP to stop reserving memory for
    # a JIT that cannot start.
    jit_rows = {row["key"]: row["value"] for row in result["settings"] if row["key"].startswith("opcache.jit")}
    assert jit_rows == {"opcache.jit": "disable", "opcache.jit_buffer_size": "0"}


@pytest.mark.parametrize("total_mb,buffer_size", [(1024, "32M"), (2048, "32M"), (4096, "64M"), (16384, "128M")])
def test_the_jit_buffer_follows_the_machine(total_mb, buffer_size):
    rows = php_tune.recommendations(facts_for(total_mb), "8.4", jit={"supported": True, "usable": True, "blocked_by": ""})

    assert value_of(rows, "opcache.jit") == "tracing"
    assert value_of(rows, "opcache.jit_buffer_size") == buffer_size


def test_turning_opcache_off_survives_the_next_auto_tune(monkeypatch):
    # The switch lives in its own file, read after the tuning one. Auto tune
    # regenerates the tuning file wholesale, and must not undo the decision.
    monkeypatch.setattr(php_tune, "server_facts", lambda: facts_for(2048))
    monkeypatch.setattr(php_tune, "current_values", lambda version: {"opcache.enable": "0"})
    monkeypatch.setattr(php_tune, "pinned_by_php_config", lambda version: {"opcache.enable": "0"})

    result = php_tune.plan("8.4")
    row = next(item for item in result["settings"] if item["key"] == "opcache.enable")

    assert result["opcache_enabled"] is False
    assert row["overridden_value"] == "0"
    assert not (row["changes"] and not row["overridden_value"]), "must not nag to re-enable it"


def test_the_plan_says_whether_this_php_can_do_jit():
    # Drives whether the panel shows the JIT rows at all.
    assert php_tune.supports_jit("8.5") is True
    assert php_tune.supports_jit("5.6") is False
    # A PHP without JIT is never probed for it.
    assert php_tune.jit_status("7.4") == {"supported": False, "usable": False, "blocked_by": ""}


def test_a_server_that_cannot_run_jit_is_told_to_switch_it_off():
    # PHP 8.4 reserves 64 MB for JIT by default and warns on every worker start
    # when an extension blocks it. Leaving the keys unset wastes both.
    blocked = {"supported": True, "usable": False, "blocked_by": "ionCube Loader"}
    rows = php_tune.recommendations(facts_for(2048), "8.4", blocked)

    assert value_of(rows, "opcache.jit") == "disable"
    assert value_of(rows, "opcache.jit_buffer_size") == "0"
    assert "ionCube Loader" in next(r["reason"] for r in rows if r["key"] == "opcache.jit")


def test_php_without_jit_is_told_nothing_about_it():
    rows = php_tune.recommendations(facts_for(2048), "7.4", {"supported": False, "usable": False, "blocked_by": ""})

    assert not any(row["key"].startswith("opcache.jit") for row in rows)


# --- current_pool_settings(): reading FPM pool files back, for display -----


def test_current_pool_settings_reads_back_what_was_written(monkeypatch, tmp_path):
    pool_dir = tmp_path / "pool.d"
    pool_dir.mkdir()
    (pool_dir / "spanel-example-8_4.conf").write_text(
        "[spanel-example-8_4]\n"
        "user = example\n"
        "group = example\n"
        "pm = ondemand\n"
        "pm.max_children = 6\n"
        "pm.process_idle_timeout = 15s\n"
        "pm.max_requests = 400\n"
        "request_terminate_timeout = 300s\n",
        encoding="utf-8",
    )
    # A non-spanel pool file (e.g. the distro default www.conf) must be ignored.
    (pool_dir / "www.conf").write_text("[www]\npm.max_children = 5\n", encoding="utf-8")

    monkeypatch.setattr(php_tune, "_pool_dir", lambda version: pool_dir)

    pools = php_tune.current_pool_settings("8.4")

    assert pools == [{
        "pool": "spanel-example-8_4",
        "max_children": "6",
        "idle_timeout": "15s",
        "max_requests": "400",
        "request_terminate_timeout": "300s",
    }]


def test_current_pool_settings_is_empty_when_the_directory_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(php_tune, "_pool_dir", lambda version: tmp_path / "missing")
    assert php_tune.current_pool_settings("8.4") == []


def test_current_pool_settings_tolerates_a_pool_missing_some_directives(monkeypatch, tmp_path):
    pool_dir = tmp_path / "pool.d"
    pool_dir.mkdir()
    (pool_dir / "spanel-bare-8_4.conf").write_text("[spanel-bare-8_4]\nuser = bare\n", encoding="utf-8")
    monkeypatch.setattr(php_tune, "_pool_dir", lambda version: pool_dir)

    pools = php_tune.current_pool_settings("8.4")

    assert pools == [{
        "pool": "spanel-bare-8_4",
        "max_children": "",
        "idle_timeout": "",
        "max_requests": "",
        "request_terminate_timeout": "",
    }]


def test_plan_includes_pools(monkeypatch, tmp_path):
    # Everything else in plan() degrades to safe defaults off a real machine
    # (no /etc/php here); only current_pool_settings is under test.
    pool_dir = tmp_path / "pool.d"
    pool_dir.mkdir()
    (pool_dir / "spanel-example-8_4.conf").write_text(
        "[spanel-example-8_4]\npm.max_children = 4\n", encoding="utf-8",
    )
    monkeypatch.setattr(php_tune, "_pool_dir", lambda version: pool_dir)

    result = php_tune.plan("8.4")

    assert result["pools"] == [{
        "pool": "spanel-example-8_4",
        "max_children": "4",
        "idle_timeout": "",
        "max_requests": "",
        "request_terminate_timeout": "",
    }]
