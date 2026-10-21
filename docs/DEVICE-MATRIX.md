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
- [ ] Ukur berapa detik layar tetap menyusut selama aim-drag terus-menerus; setel `--crop-hold` (default 1.0 s) dari angka itu.
- [ ] Versi `.exe` (artefak CI `ScrpcyiOS-windows-x64`) berjalan di PC tanpa Python terpasang.
- [ ] Shortcut MOD (Alt): Q, F, H, P, Up/Down, Left/Right, G, W, I, V; F11 fullscreen; klik tengah = Home.
- [ ] File log terbentuk di `%LOCALAPPDATA%\StrikeMirror\logs` dan berputar.

## Daftar uji S4 (semua belum terverifikasi)
- [ ] Putar musik/video di iPhone: suara keluar di PC tanpa kresek (dekoder FFmpeg AAC-ELD 480). Jika rusak, catat dan coba `ASC_FROM_DEVICE` di `bridge/audio_portable.py`.
- [ ] Ukur jeda audio terhadap video (tepuk tangan di depan kamera / video metronom); target drift < 200 ms setelah 5 menit.
- [ ] `--audio-buffer 30` dan `--audio-buffer 150`: catat apakah ada putus-putus (underrun).
- [ ] Jika suara kresek pada buffer berapa pun: kemungkinan `latency="low"` PortAudio (MME, blok 480) di `sounddevice_output`; coba `latency="high"` dan catat hasilnya.
- [ ] Tutup player saat iPhone diam (tanpa suara): jendela tertutup < 1 s.
- [ ] `--no-audio`: video tetap jalan, tidak ada pesan error audio.
- [ ] Salin teks di iPhone -> muncul di clipboard PC; salin di PC -> tempel di iPhone (Ctrl+V / tahan lama). Tidak ada gema bolak-balik.
- [ ] `--record x.mp4` dan `-f` dari baris perintah.
