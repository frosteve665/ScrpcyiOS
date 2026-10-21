# Matriks perangkat

Isi dari hasil nyata. Jangan tulis klaim yang belum diuji.

| Tanggal | Model | iOS | Transport | Frame pertama (ms) | fps | Tap pertama | Layar terkunci | Probe multitouch | Catatan |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | belum ada data |

## Daftar uji S0 tambahan (dari S1, semua belum terverifikasi)
- [ ] Cabut-colok USB 3 kali: layar pulih sendiri tanpa menutup player (`BridgeSupervisor`).
- [ ] Tutup paksa player: proses bridge ikut mati (tidak ada `python.exe` yatim).
- [ ] Putar iPhone ke landscape (F6/F7): gambar tegak dan tap mendarat di titik yang benar.
- [ ] Swipe cepat di game: layar tidak menyusut ke pojok (kompensasi crop aktif); coba juga `--no-compensate`.
- [ ] Shortcut MOD (Alt): Q, F, H, P, Up/Down, Left/Right, G, W, I, V; F11 fullscreen; klik tengah = Home.
- [ ] File log terbentuk di `%LOCALAPPDATA%\StrikeMirror\logs` dan berputar.
