import ios_client as c


def test_backoff_doubles_up_to_cap():
    p = c.ReconnectPolicy(base=0.5, cap=5.0)
    assert [p.next_delay() for _ in range(6)] == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]


def test_success_resets_backoff():
    p = c.ReconnectPolicy(base=0.5, cap=5.0)
    p.next_delay(); p.next_delay()
    p.on_success()
    assert p.next_delay() == 0.5


def test_gives_up_after_max_attempts():
    p = c.ReconnectPolicy(base=0.1, cap=1.0, max_attempts=3)
    assert [p.next_delay() for _ in range(3)] == [0.1, 0.2, 0.4]
    assert p.next_delay() is None


def test_unbounded_by_default():
    p = c.ReconnectPolicy()
    assert all(p.next_delay() is not None for _ in range(100))
