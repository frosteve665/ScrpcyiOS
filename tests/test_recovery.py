import ios_client as c


def make():
    return c.RecoveryPolicy(pli_interval=2.0, max_pli=2, restart_interval=10.0)


def test_first_error_asks_for_keyframe():
    assert make().on_error(0.0) == "pli"


def test_errors_inside_interval_wait_instead_of_storming():
    p = make()
    assert p.on_error(0.0) == "pli"
    assert p.on_error(0.5) == "wait"
    assert p.on_error(1.9) == "wait"


def test_escalates_to_restart_after_max_plis():
    p = make()
    assert [p.on_error(t) for t in (0.0, 2.1)] == ["pli", "pli"]
    assert p.on_error(4.2) == "restart"


def test_restart_is_rate_limited():
    p = make()
    for t in (0.0, 2.1):
        p.on_error(t)
    assert p.on_error(4.2) == "restart"
    assert p.on_error(6.3) == "pli"
    assert p.on_error(8.4) == "pli"
    assert p.on_error(10.5) == "wait"       # only 6.3 s since the last restart
    assert p.on_error(14.3) == "restart"


def test_healthy_stream_resets_escalation():
    p = make()
    p.on_error(0.0); p.on_error(2.1)
    p.on_healthy()
    assert p.on_error(4.2) == "pli"
