import pytest
from scripts.validation.cgroup_window import parse_counters, compare


def test_normal_and_oom_event_deltas_are_not_causal_verdict():
    result = compare("usage_usec 100\n", "usage_usec 250\n", "oom 0\noom_kill 0\n", "oom 1\noom_kill 1\n")
    assert result["counter_window_valid"] and result["deltas"]["oom_kill"] == 1
    assert result["termination_cause"] == "not_inferred" and not result["trusted_run"]


@pytest.mark.parametrize("text", ["", "usage_usec -1", "usage_usec NaN", "usage_usec 1\nusage_usec 2", "usage_usec 1 extra", True, "x" * 4097])
def test_invalid_counter_data(text):
    with pytest.raises(ValueError): parse_counters(text)


def test_missing_required_key_fails_closed():
    assert not compare("other 0", "other 1", "oom 0\noom_kill 0", "oom 0\noom_kill 0")["counter_window_valid"]


def test_reset_counter_does_not_imply_negative_usage():
    assert not compare("usage_usec 100", "usage_usec 1", "oom 0\noom_kill 0", "oom 0\noom_kill 0")["counter_window_valid"]


def test_current_kernel_dotted_counter_field():
    assert parse_counters("core_sched.force_idle_usec 0\nusage_usec 12")["usage_usec"] == 12
