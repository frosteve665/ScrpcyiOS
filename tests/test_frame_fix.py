import numpy as np
import pytest

import frame_fix as ff

W, H = 197, 344


def noise(h, w, seed=1):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def collapsed(fx, fy):
    a = np.full((H, W, 3), 128, dtype=np.uint8)
    cw, ch = int(W * fx), int(H * fy)
    a[:ch, :cw] = noise(ch, cw)
    return a


def test_full_frame_is_not_cropped():
    assert ff.detect_content_crop(noise(H, W)) is None


def test_collapsed_frame_is_detected_top_left():
    fx, fy = ff.detect_content_crop(collapsed(0.57, 0.47))
    assert fx == pytest.approx(0.57, abs=0.03)
    assert fy == pytest.approx(0.47, abs=0.03)


def test_all_gray_is_degenerate_not_a_crop():
    assert ff.detect_content_crop(np.full((H, W, 3), 128, dtype=np.uint8)) is None


def test_slight_padding_is_ignored():
    assert ff.detect_content_crop(collapsed(0.95, 0.95)) is None


def test_padding_with_small_codec_noise_still_counts_as_gray():
    a = collapsed(0.5, 0.5)
    pad = a[int(H * 0.5) + 2:, :]
    pad += np.random.default_rng(2).integers(0, 4, pad.shape, dtype=np.uint8)  # 128..131
    fx, fy = ff.detect_content_crop(a)
    assert fy == pytest.approx(0.5, abs=0.03)


def test_only_one_dimension_collapsed_is_ignored():
    assert ff.detect_content_crop(collapsed(0.5, 1.0)) is None


def test_crop_box_converts_fraction_to_pixels():
    assert ff.crop_box((0.5, 0.25), 1264, 2752) == (0, 0, 632, 688)
    assert ff.crop_box(None, 1264, 2752) == (0, 0, 1264, 2752)
