# Engine iOS untuk StrikeMirror: desain

Status: **draf menunggu review pemilik**. Sumber dan tingkat keyakinan: [`../../RESEARCH.md`](../../RESEARCH.md).

## 1. Tujuan dan kriteria sukses

Engine yang membuat iPhone terasa seperti perangkat Android di StrikeMirror/scrcpy: colok USB, layar muncul, kontrol dari PC, cukup rendah latensi untuk game. Prioritas pertama (dijawab pemilik): **game + key mapping**.

Kriteria sukses (harus terukur di perangkat nyata):
1. Dari colok sampai frame pertama: **< 10 detik** setelah setup satu kali.
2. Frame ke layar: target **>= 55 fps** terdekode, tanpa antrean yang menumpuk.
3. Tap sampai terlihat di layar: dicatat (target ditentukan di Sprint 0 setelah ada angka dasar).
4. Pulih otomatis dari dekode rusak dan cabut-colok tanpa restart aplikasi.
5. Multitouch **atau** keputusan tertulis bahwa multitouch tidak mungkin dengan bukti probe.

## 2. Pemahaman (yang dikatakan vs asumsi)

Dikatakan pemilik: StrikeMirror miliknya sendiri (Rust/Tauri di repo, Windows), memakai scrcpy-server, punya mesin key mapping; fokus game; ingin hasil "mirip scrcpy 5.x"; repo engine terpisah bernama `ScrpcyiOS`.
Asumsi saya (perlu dikonfirmasi): iPhone target minimal iOS 17.4; PC Windows 10/11 x64; satu iPhone per sesi; tidak ada jailbreak.

## 3. Keputusan

| ID | Keputusan | Alasan (lihat RESEARCH) | Alternatif yang ditolak |
|----|-----------|-------------------------|-------------------------|
| D1 | **Sidecar bridge** (proses terpisah, HTTP lokal) membungkus pymobiledevice3 | #1, #7, #8: tidak ada library Rust yang punya displayservice/HID; menulis ulang besar | Backend Rust native (ditunda, Sprint 7+), WDA (latensi tinggi, perlu app di HP) |
| D2 | Batas lisensi = **batas proses**; klien tidak pernah `import pymobiledevice3` | #6, #12 | Menggabungkan kode ke StrikeMirror (memaksa GPL) |
| D3 | Pemulihan video = **PLI terbatas lalu restart sesi**, bukan PLI terus-menerus | #3 | PLI tiap error (bisa merusak encoder) |
| D4 | Kontrak touch memakai **`pointer_id`** sejak v1; satu jari sampai probe membuktikan lebih | #1, #2 | Menunggu multitouch dulu baru merancang API |
| D5 | Matriks iOS ditentukan **dengan tes perangkat** di Sprint 0, bukan dari klaim README | #4 | Percaya "17.4+" atau "27+" begitu saja |
| D6 | Decoder StrikeMirror diperluas ke **HEVC** (iPhone tidak menawarkan H.264 di jalur ini) | kode StrikeMirror `video_pipeline.rs` hanya menerima H.264 | Transcode di bridge (menambah latensi dan CPU) |
| D7 | Mode Gamepad BLE **bukan** jalur utama | #11 | Menjadikannya rencana A |

## 4. Arsitektur

```mermaid
flowchart LR
  subgraph SM[StrikeMirror - Rust/Tauri]
    KM[Mesin key-mapping]
    BE[Backend iOS - klien HTTP]
    DEC[Decoder HEVC DXVA]
    UI[UI dan render D3D11]
  end
  subgraph BR[Bridge - proses GPL terpisah]
    API[Server HTTP 127.0.0.1 + Basic auth]
    PMD[pymobiledevice3 ScreenStreamServer]
    TUN[Tunnel RSD userspace]
  end
  subgraph IP[iPhone]
    DS[displayservice - RTP/HEVC]
    HID[universalhidservice - touch dan keyboard]
    CB[pasteboard dan orientation]
  end
  KM -->|touch pointer_id, tombol, tombol keyboard| BE
  BE -->|POST /touch /button /key| API
  API --> PMD
  PMD --> TUN
  TUN <-->|USB atau Wi-Fi via Apple Devices usbmuxd| IP
  DS -->|HEVC AU| PMD
  PMD -->|GET /stream.bin| BE
  BE --> DEC --> UI
```

Batas penting: antara `BE` dan `API` hanya HTTP + JSON + aliran biner berbingkai. Tidak ada memori bersama.

## 5. Alur startup

```mermaid
flowchart TD
  A[Mulai bridge] --> B{usbmuxd ada? port 27015}
  B -- tidak --> E1[error driver_missing]
  B -- ya --> C{ada iPhone USB?}
  C -- tidak --> E2[error device_not_found]
  C -- lebih dari satu --> E3[error multiple_devices]
  C -- satu --> D{Trust dan unlock OK?}
  D -- tidak --> E4[error not_trusted]
  D -- ya --> F{iOS minimal sesuai matriks?}
  F -- tidak --> E5[error unsupported_ios]
  F -- ya --> G{Developer Mode aktif?}
  G -- tidak --> E6[error developer_mode_off]
  G -- ya --> H[Buka tunnel RSD]
  H --> I{displayservice tersedia?}
  I -- tidak, percobaan 1 --> J[Mount developer image] --> H
  I -- tidak, percobaan 2 --> E7[error display_service_missing]
  I -- ya --> K[Mulai ScreenStreamServer dengan password acak]
  K --> L[Emit event ready: port dan password]
```

## 6. Urutan video dan input

```mermaid
sequenceDiagram
  participant K as Klien StrikeMirror
  participant B as Bridge HTTP
  participant P as iPhone
  K->>B: GET /codec (ulang sampai 200)
  B->>P: start_video_stream (RTP/HEVC) + RTCP RR berkala
  B-->>K: codec string dan hvcC
  K->>B: GET /stream.bin
  P-->>B: RTP HEVC
  B-->>K: bingkai [len][tipe][NALU berawalan panjang]
  Note over K: tipe 2 = reset decoder, delta sebelum keyframe dibuang
  K->>B: POST /touch contact x y id
  B->>P: laporan HID 58 byte (service 257)
  K->>B: POST /touch release x y id
  alt dekode error
    K->>B: POST /pli (maks 2, jarak 2 detik)
    K->>B: POST /restart (jarak minimal 10 detik)
  end
```

## 7. State machine sesi (klien)

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> Starting: connect()
  Starting --> Ready: event ready
  Starting --> Failed: event error
  Ready --> Streaming: frame pertama
  Streaming --> Recovering: error dekode atau stream putus
  Recovering --> Streaming: keyframe diterima
  Recovering --> Reconnecting: restart tidak menolong
  Streaming --> Reconnecting: bridge mati atau iPhone dicabut
  Reconnecting --> Starting: backoff 0.5-5 detik
  Failed --> Idle: pengguna memperbaiki lalu coba lagi
  Streaming --> Idle: close()
```

## 8. Model data (ERD logis)

Engine hampir seluruhnya dalam memori. Yang disimpan ke disk hanya record pairing dan cache developer image (dikelola pymobiledevice3/OS). ERD ini mendokumentasikan entitas yang dipertukarkan lewat kontrak.

```mermaid
erDiagram
  DEVICE ||--o{ SESSION : "dipakai oleh"
  SESSION ||--|| STREAM_CONFIG : "punya"
  SESSION ||--o{ INPUT_EVENT : "menerima"
  SESSION ||--o{ ERROR_EVENT : "menghasilkan"
  DEVICE ||--|| CAPABILITY : "dinilai sebagai"
  DEVICE {
    string udid PK
    string name
    string product_type
    string ios_version
    string transport "usb atau wifi"
  }
  SESSION {
    string id PK
    string udid FK
    string state
    int port
    datetime started_at
  }
  STREAM_CONFIG {
    string codec "HEVC"
    blob hvcc
    int width
    int height
    int max_bitrate_kbps
  }
  INPUT_EVENT {
    string kind "touch atau button atau key"
    int pointer_id
    int x "0..65535"
    int y "0..65535"
    string phase "contact atau release"
    int ts_ms
  }
  CAPABILITY {
    bool multitouch
    bool audio
    bool wifi
    string min_ios_verified
  }
  ERROR_EVENT {
    string code
    string message
    datetime at
  }
```

## 9. Taksonomi error

`driver_missing`, `device_not_found`, `multiple_devices`, `not_trusted`, `unsupported_ios`, `developer_mode_off`, `display_service_missing`, `stream_not_ready`, `stream_http`, `bad_frame`, `bridge_exited`, `timeout`, `unknown`. Setiap kode punya pesan Indonesia yang memberi tahu pengguna langkah berikutnya (`bridge/ios_bridge.py: MESSAGES`).

## 10. Risiko dan pertanyaan terbuka

| Risiko | Kemungkinan | Dampak | Mitigasi |
|--------|-------------|--------|----------|
| Versi iOS minimum ternyata 27 | sedang | tinggi (pasar sempit) | Sprint 0 matriks nyata; jalur QuickTime USB (iPhoneMirror) hanya dievaluasi bila perlu |
| Multitouch tidak bisa lewat HID | sedang | tinggi untuk game | Probe; fallback WDA (latensi) atau keputusan "satu jari saja" didokumentasikan |
| Apple mengubah protokol di rilis iOS berikutnya | tinggi | sedang | Tes kontrak API pymobiledevice3 + job canary mingguan + versi dikunci |
| PLI diabaikan, gambar rusak lama | sedang | sedang | `RecoveryPolicy` + restart sesi |
| GPL: batas proses diperdebatkan | rendah-sedang | tinggi | Tinjauan hukum sebelum rilis komersial; kontrak HTTP sempit |
| Encoder dibatasi ~6 Mbps, kualitas turun saat gerak | sedang | sedang | Uji; `--max-bitrate`, `motion_idr` dikonfigurasi |
| Kunci layar menghentikan stream | tidak diketahui | sedang | Uji di Sprint 0 |

## 11. Strategi tes

Piramida: **unit** (framing, kebijakan pemulihan, builder laporan HID, pemetaan tombol) -> **konformansi dengan bridge tiruan** (server HTTP yang meniru protokol, video HEVC asli dihasilkan saat tes) -> **kontrak API pymobiledevice3** (deteksi drift) -> **hardware-in-the-loop** (`tools/smoke_test.py` di runner terdaftar yang dicolok iPhone). Bridge tiruan sama dipakai backend Rust nanti untuk tes kontrak.

## 12. Di luar cakupan

Mirror kamera, display virtual, drag-drop file/APK, mode OTG, pilihan codec/FPS, jailbreak, mirror iPad khusus, AirPlay.
