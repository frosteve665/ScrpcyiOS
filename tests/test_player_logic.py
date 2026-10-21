import player_logic as p


def test_fit_rect_letterboxes_portrait_in_wide_window():
    x, y, w, h = p.fit_rect(1000, 500, 1170, 2532)
    assert h == 500 and 0 < w < 300 and x == (1000 - w) // 2 and y == 0


def test_window_to_norm_inside_outside():
    r = (100, 0, 200, 400)
    assert p.window_to_norm(200, 200, r) == (0.5, 0.5)
    assert p.window_to_norm(0, 0, r, clamp=False) is None
    assert p.window_to_norm(0, 0, r) == (0.0, 0.0)
    assert p.window_to_norm(999, 999, r) == (1.0, 1.0)


def test_letters_digits_symbols():
    assert p.keysym_to_usage("a") == (0x04, False)
    assert p.keysym_to_usage("A") == (0x04, True)
    assert p.keysym_to_usage("0") == (0x27, False)
    assert p.keysym_to_usage("exclam") == (0x1E, True)
    assert p.keysym_to_usage("F9") == (None, False)


def test_ctrl_c_becomes_cmd_c_and_other_ctrl_stays_local():
    k = p.KeyState()
    assert k.press("c", ctrl=True) == [p.CMD, 0x06]
    assert k.release("c") == []
    assert k.press("q", ctrl=True) is None
    assert k.press("w") == [0x1A]
    assert k.press("e") == [0x1A, 0x08]
    assert k.release("w") == [0x08]


def test_shifted_symbol_includes_shift():
    assert p.KeyState().press("exclam") == [p.SHIFT, 0x1E]


def test_clear_releases_everything():
    k = p.KeyState(); k.press("a"); assert k.clear() == [] and k.report() == []


def test_wheel_down_swipes_finger_up_and_stays_in_bounds():
    x1, y1, x2, y2 = p.scroll_swipe(0.5, 0.5, -1)
    assert x1 == x2 == 0.5 and y2 < y1
    for pos in (0.0, 0.5, 1.0):
        for delta in (-3, -1, 1, 3):
            pts = p.scroll_swipe(0.5, pos, delta)
            assert all(0.0 <= v <= 1.0 for v in pts)
            assert abs(pts[3] - pts[1]) > 0.05          # never degenerates to a tap


def test_wheel_up_swipes_finger_down():
    _, y1, _, y2 = p.scroll_swipe(0.5, 0.5, 1)
    assert y2 > y1


def test_more_notches_means_longer_swipe_up_to_a_cap():
    short = abs(p.scroll_swipe(0.5, 0.5, -1)[3] - p.scroll_swipe(0.5, 0.5, -1)[1])
    long_ = abs(p.scroll_swipe(0.5, 0.5, -3)[3] - p.scroll_swipe(0.5, 0.5, -3)[1])
    huge = abs(p.scroll_swipe(0.5, 0.5, -50)[3] - p.scroll_swipe(0.5, 0.5, -50)[1])
    assert short < long_ <= huge <= 0.7


def test_f9_toggles_recording():
    assert p.FUNCTION_ACTIONS["F9"] == "record"
