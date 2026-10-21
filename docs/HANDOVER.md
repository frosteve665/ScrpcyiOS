# Handover prompt (paste ke Claude Code)

```
Kamu melanjutkan pengembangan ScrpcyiOS (https://github.com/frosteve665/ScrpcyiOS, branch main):
engine iPhone untuk StrikeMirror, yaitu mirroring + kontrol iPhone dari PC Windows lewat USB,
target paritas scrcpy 5.x, prioritas "game + key mapping". Pemilik: Alfi (QA automation engineer).
Jawab dalam Bahasa Indonesia, singkat, kode dulu. Pakai workflow Superpowers: TDD (tes gagal dulu),
verification-before-completion, satu code review independen per batch.

## Arsitektur (jangan dilanggar)
- bridge/ (GPL-3.0, import pymobiledevice3 11.40.2): ios_bridge.py membuka tunnel userspace
  (PreferredRsdTunnel, tanpa admin/root) + ScreenStreamServer HTTP di 127.0.0.1, lalu kirim event
  JSON per baris di stdout (status/ready/error). audio_portable.py mengganti dekoder AAC-ELD
  khusus macOS dengan FFmpeg (PyAV) di Windows. mt_reports.py + multitouch_probe.py = eksperimen 2 jari.
- client/ (TIDAK boleh import pymobiledevice3; ada tes AST yang menjaga): ios_client.py
  (BridgeProcess, BridgeSupervisor respawn+backoff, BridgeHttp rebind atomik, VideoSession HEVC
  + RecoveryPolicy + rekam MP4, InputController satu jari, iter_audio, JitterBuffer, IOSDevice),
  player.py (Tk), player_logic.py (keymap, MOD=Alt ala scrcpy, F1-F11), frame_fix.py
  (kompensasi "layar menyusut" saat swipe cepat + CropStabilizer), recorder.py.
- Kontrak protokol: docs/PROTOCOL.md (v1, field `protocol`). Desain: docs/superpowers/specs/
  2026-10-21-ios-engine-design.md (MASIH DRAFT, belum disetujui pemilik; jangan anggap approved).
- Rencana: docs/SPRINTS.md (S0..S6). Checklist uji perangkat: docs/DEVICE-MATRIX.md.
- Paket: packaging/build.py membuat 2 exe TERPISAH (strikemirror-bridge, strikemirror-player;
  player tidak boleh berisi pymobiledevice3). tools/frozen_smoke.py menguji exe tanpa iPhone.

## Lingkungan
- Tes: `python -m pytest -q` (138 lulus, 1 skip GUI tanpa display). GUI: `xvfb-run -a python -m pytest tests/test_player_gui.py`
  butuh Python dengan tkinter (mis. `uv python install 3.12`). Lint: `ruff check .`
- CI (.github/workflows/ci.yml): lint, test ubuntu+windows py3.11-3.13 (Linux di bawah xvfb),
  package-windows (build exe, smoke test, upload artefak ScrpcyiOS-windows-x64), canary, hil.
  Gagal tes muncul sebagai anotasi: `gh api repos/frosteve665/ScrpcyiOS/check-runs/<job_id>/annotations`
  (log mentah sering diblokir proxy). Pakai `--workflow ci` saat `gh run list` (ada workflow Dependabot juga).

## Status per 2026-10-21
- Selesai + teruji otomatis (BELUM diuji di iPhone sama sekali): video HEVC, input satu jari,
  tombol, keyboard, clipboard, rotasi (mapping = identitas, dari viewer.js pymobiledevice3), rekam MP4,
  reconnect, lifecycle bridge (mati via stdin EOF; supervisor respawn; error fatal berhenti retry),
  MOD shortcuts + F11, log berputar, kompensasi crop, paket exe, dekoder audio portable.
- Commit terakhir f95154b memperbaiki CI merah (exit 120 saat stdout ditutup). CEK DULU CI
  `gh run list --repo frosteve665/ScrpcyiOS --workflow ci --limit 1` termasuk job package-windows
  (belum pernah lulus di runner Windows; kemungkinan butuh opsi --collect di build.py dan Tk di player).

## Pekerjaan berikutnya (urut)
1. Pastikan CI hijau termasuk package-windows; perbaiki build/frozen smoke bila merah.
2. S4: pemutar audio di klien (AudioSession: GET /audio.bin -> iter_audio -> JitterBuffer ->
   output, mis. sounddevice), opsi --no-audio/--audio-buffer di player, tes dengan mock /audio.bin.
   Clipboard otomatis dua arah (long-poll /clipboard/events), opsi CLI ala scrcpy.
3. release.yml (build di tag v*, unggah zip ke GitHub Release), uji dengan tag pra-rilis.
4. S2 (crate Rust untuk StrikeMirror) DITAHAN sampai pemilik konfirmasi StrikeMirror Rust/Tauri
   dan menyetujui spec. S3 multitouch menunggu hasil bridge/multitouch_probe.py di perangkat.
5. Jangan klaim "selesai" untuk perilaku iPhone; tandai "belum terverifikasi" sampai ada data di DEVICE-MATRIX.md.

## Menunggu dari pemilik
- Model iPhone + versi iOS; hasil tools/smoke_test.py, client/player.py, bridge/multitouch_probe.py
  (atau versi exe dari artefak CI) di PC Windows dengan "Apple Devices", Developer Mode, Trust.
- Konfirmasi stack StrikeMirror + persetujuan spec.

## Risiko yang diketahui
- Versi iOS minimum belum pasti (17.4 vs 27); PLI diabaikan iPhone (RecoveryPolicy pakai /restart).
- Audio AAC-ELD lewat FFmpeg belum pernah diuji dengan audio iPhone asli (lihat catatan di audio_portable.py).
- --crop-hold default 1.0 s mungkin terlalu pendek untuk aim-drag panjang; ukur di perangkat.
- Batas GPL bridge/klien perlu tinjauan hukum sebelum rilis komersial.
```
