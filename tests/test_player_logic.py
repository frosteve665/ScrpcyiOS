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
