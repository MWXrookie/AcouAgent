"""Offline Linux cgroup counter diagnostics; never evidence of physics trust."""

import re


def parse_counters(text):
    if not isinstance(text, str) or len(text.encode()) > 4096:
        raise ValueError("invalid_counter_size")
    counters = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or re.fullmatch(r"[a-z][a-z0-9_.]*", fields[0]) is None:
            raise ValueError("invalid_counter_line")
        key, value = fields
        if key in counters or not value.isascii() or not value.isdigit():
            raise ValueError("invalid_counter_value")
        counters[key] = int(value)
    if not counters:
        raise ValueError("missing_counters")
    return counters


def compare(before_cpu, after_cpu, before_memory, after_memory):
    """Total container usage includes parent/other children, not exact child CPU."""
    cpu_before, cpu_after = parse_counters(before_cpu), parse_counters(after_cpu)
    mem_before, mem_after = parse_counters(before_memory), parse_counters(after_memory)
    result = {"counter_window_valid": False, "trusted_run": False,
              "termination_cause": "not_inferred", "cpu_scope": "whole_cgroup"}
    try:
        deltas = {"cpu_usage_usec": cpu_after["usage_usec"] - cpu_before["usage_usec"],
                  "oom": mem_after["oom"] - mem_before["oom"],
                  "oom_kill": mem_after["oom_kill"] - mem_before["oom_kill"]}
    except KeyError:
        return result
    if any(value < 0 for value in deltas.values()):
        return result
    result.update(counter_window_valid=True, deltas=deltas)
    return result
