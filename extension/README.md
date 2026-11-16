# MangaFlow — Ekstensi Chrome

Ekstensi Manifest V3 untuk menerjemahkan gambar manga langsung dari halaman web. Semua OCR, terjemahan, pembersihan balon, dan penataan huruf tetap dikerjakan oleh server MangaFlow lokal di `http://127.0.0.1:8765`.

Ekstensi ini **bukan** layanan cloud: tidak ada akun, kredit, langganan, atau unggahan gambar ke pihak ketiga. Kamu bisa memakai server MangaFlow milik sendiri, termasuk di port atau alamat LAN lain.

## Fitur

- **Terjemahkan halaman** — mendeteksi semua gambar manga di tab aktif, mengirimnya dalam satu job batch ke server lokal, lalu mengganti setiap gambar asli dengan hasil terjemahannya. Satu batch maksimal 40 gambar dan 450 MB.
- **Terjemahkan otomatis** — jika diaktifkan, semua gambar manga yang sudah termuat di halaman diproses otomatis saat dibuka. Halaman yang sama tidak diproses dua kali dalam satu sesi tab.
- **Screenshot** — tangkap area layar yang terlihat, pilih crop, lalu terjemahkan. Berguna untuk gambar yang dilindungi CSP, canvas, atau halaman tanpa gambar manga yang jelas.
- **Pemilih bahasa target** — bahasa diambil dari `/api/config` server, jadi daftar dan ketersediaan mesin selalu mengikuti server.
- **Status server lokal** — kartu utama menampilkan status server, mesin, bahasa target, dan versi. Tidak ada penghitung kredit karena MangaFlow tidak memakai kredit.
- **Shortcut** — `Ctrl+Shift+S` di Windows/Linux atau `Command+Shift+S` di macOS untuk menangkap layar dan membuka popup.
- **Viewer hasil** — slider sebelum/sesudah, daftar halaman, unduhan PNG, dan tautan ke editor MangaFlow.
- **Pemulihan** — panel di halaman bisa mengembalikan gambar asli atau membuka hasil di viewer/editor.
- **Pengaturan tersimpan** — alamat server, mesin, bahasa, gaya bahasa, dan terjemahkan otomatis disimpan di `chrome.storage.sync`.

## Persyaratan

- **Google Chrome 105+** atau browser Chromium lain yang mendukung Manifest V3 dan `chrome.action.openPopup` (Chrome 127+ disarankan untuk shortcut).
- **Server MangaFlow berjalan** di komputer yang sama, default `http://127.0.0.1:8765`. Lihat [README utama](../README.md).
- Model OCR dan terjemahan sudah tersedia. Jika belum, server akan mengunduhnya saat job pertama; tunggu sampai selesai.
- Ekstensi memakai folder `extension/` apa adanya. Tidak ada langkah build.

## Instalasi

1. Jalankan server MangaFlow:

   ```bat
   run.bat
   ```

   Atau:

   ```bash
   python app.py
   ```

2. Buka `chrome://extensions`.
3. Aktifkan **Developer mode** di kanan atas.
4. Klik **Load unpacked**.
5. Pilih folder `manga-translator/extension` (folder yang berisi `manifest.json`).
6. Sematkan ikon MangaFlow ke toolbar. Ekstensi siap dipakai.

Jika memakai port lain, buka **Pengaturan** di popup dan ubah **Alamat server MangaFlow**, misalnya `http://127.0.0.1:9000`. Klik **Tes** untuk memastikan terhubung.

### Memuat ulang setelah mengubah kode

Buka `chrome://extensions`, klik ikon refresh pada kartu MangaFlow, lalu muat ulang tab manga. Service worker dan content script lama tidak otomatis memakai kode baru.

## Cara pakai

### Terjemahkan halaman

1. Buka halaman manga/manhwa/webtoon di tab biasa.
2. Gulir sampai gambar yang ingin diterjemahkan sudah termuat. Ekstensi hanya memproses gambar yang sudah ada di DOM.
3. Klik ikon MangaFlow di toolbar.
4. Pastikan kartu status bertuliskan **Server lokal aktif** dan hint menyebut jumlah gambar yang terdeteksi. Tombol berubah menjadi **Terjemahkan N gambar** bila ada lebih dari satu gambar.
5. Klik tombol tersebut. Ekstensi mengunggah semua gambar sekaligus dalam satu job batch (maksimal 40 gambar atau 450 MB per batch).
6. Tunggu progres selesai. Setiap gambar di halaman diganti dengan hasil terjemahannya, dan panel MangaFlow muncul di pojok kanan bawah.
7. Dari panel, buka **Editor** untuk memperbaiki dialog, **Unduh** hasil, atau **Pulihkan semua**.

Job batch memakai urutan unggah untuk memetakan halaman hasil kembali ke gambar asli. Jika sebagian gambar gagal, panel menampilkan jumlah yang gagal dan kamu bisa menjalankan terjemahan lagi untuk sisanya.

Jika tidak ada gambar yang terdeteksi, gunakan **Screenshot**. Halaman dengan gambar canvas, background-image, atau perlindungan CSP memang tidak bisa dideteksi sebagai `<img>`.

### Terjemahkan otomatis

Aktifkan **Terjemahkan otomatis** di popup. Setiap tab manga yang dibuka akan diproses otomatis setelah gambar tersedia, termasuk semua gambar yang sudah termuat (bukan hanya satu gambar terbesar). Pengaturan ini juga bisa diubah dari tab **Pengaturan**.

Terjemahkan otomatis hanya berjalan pada halaman `http(s)` biasa, bukan halaman `chrome://`, `chrome-extension://`, atau halaman server MangaFlow sendiri. Halaman yang sama tidak diproses berulang kali dalam satu sesi tab; muat ulang halaman atau gunakan **Terjemahkan ulang** di panel untuk memaksa proses baru.

Gambar yang baru dimuat setelah batch pertama (pemuatan lambat saat menggulir) tidak ikut batch tersebut. Setelah gambar baru muncul, klik **Terjemahkan N gambar** lagi untuk memproses sisanya. Terjemahkan otomatis hanya memindai sekali per sesi tab; untuk melanjutkan sisa gambar, gunakan tombol manual.

### Screenshot

1. Klik kartu **Screenshot** di popup, atau tekan `Ctrl+Shift+S` / `Command+Shift+S`.
2. Area yang terlihat di tab aktif ditangkap.
3. Tarik pada gambar untuk memilih crop. Area penuh dipilih otomatis; klik **Reset** untuk memilih ulang seluruh gambar.
4. Klik **Terjemahkan area**.
5. Setelah selesai, klik **Buka hasil** untuk membuka viewer sebelum/sesudah.

Screenshot hanya menangkap bagian halaman yang **terlihat di layar**. Gulir halaman atau perkecil zoom browser jika area yang diinginkan belum masuk layar. Popup tidak bisa menangkap dirinya sendiri atau halaman `chrome://`.

Shortcut bisa diubah di `chrome://extensions/shortcuts` jika bentrok dengan shortcut lain.

### Viewer

Viewer membuka hasil dalam tab baru:

- **Asli / Bandingkan / Terjemahan** — pilih mode tampilan.
- **Slider** atau tarik garis tengah untuk membandingkan.
- **Daftar halaman** — muncul jika job berisi lebih dari satu halaman.
- **Unduh PNG** — unduh halaman yang sedang dilihat.
- **Editor** — buka editor MangaFlow untuk halaman tersebut.
- **Buka MangaFlow** — buka server lokal.

Viewer memuat data langsung dari `/api/jobs/{id}`. Jika server dimatikan, viewer menampilkan pesan bahwa server tidak bisa dihubungi.

## Pengaturan

| Pengaturan | Fungsi |
| --- | --- |
| **Alamat server MangaFlow** | Alamat server, default `http://127.0.0.1:8765`. Bisa berupa IP LAN, misalnya `http://192.168.1.10:8765`. |
| **Mesin** | Mesin terjemahan yang tersedia di server. Mesin yang butuh kunci tetap terlihat tetapi nonaktif. |
| **Gaya bahasa** | Santai, Netral, atau Baku (berlaku untuk target Indonesia). |
| **Bahasa sumber** | Deteksi otomatis atau bahasa tertentu. |
| **Bahasa target** | Diambil dari `/api/config`; pilihan ini juga muncul di kartu **Terjemahkan ke** pada Beranda. |
| **Terjemahkan otomatis** | Proses otomatis semua gambar yang termuat saat halaman manga dibuka. |

Kunci API DeepL, Gemini, Microsoft, dan pengaturan Ollama/OpenAI **tidak disimpan di ekstensi**. Isi kunci tersebut di halaman MangaFlow → **Pengaturan mesin & kunci API**; server yang menyimpannya di `data/settings.json`.

## Izin ekstensi

| Izin | Alasan |
| --- | --- |
| `tabs` | Membaca tab aktif, judul/URL, menangkap layar, dan membuka tab viewer. |
| `activeTab` | Akses sementara ke tab yang sedang aktif saat kamu memakai ekstensi. |
| `storage` | Menyimpan pengaturan dan job terakhir. |
| `http://*/*`, `https://*/*`, `<all_urls>` | Mengambil gambar dari halaman mana pun, menangkap tab untuk fitur Screenshot, dan menghubungi server MangaFlow lokal. Tanpa izin ini, gambar lintas-origin dan `captureVisibleTab` bisa gagal. |
| Content script di semua situs | Mendeteksi gambar manga dan menggantinya dengan hasil terjemahan. |

Ekstensi **tidak** menyisipkan iklan, tidak mengirim telemetri, dan tidak menghubungi server selain alamat MangaFlow yang kamu tentukan. Izin host yang luas diperlukan karena gambar manga bisa berasal dari CDN mana pun. Kalau hanya memakai satu situs, kamu bisa mempersempit `matches` dan `host_permissions` di `manifest.json`, lalu muat ulang ekstensi.

## Privasi

- Gambar yang diterjemahkan dikirim hanya ke server MangaFlow yang kamu isi di Pengaturan.
- Pengaturan disimpan di `chrome.storage.sync`. Jika sinkronisasi Chrome aktif, pengaturan (bukan gambar atau dialog) ikut tersinkron ke akun Google milikmu.
- Job terakhir disimpan di `chrome.storage.local` dan berisi ID job, judul halaman, URL hasil, serta status.
- Riwayat lengkap, gambar asli, hasil, dan dialog tetap berada di `data/jobs/` pada server MangaFlow.
- Tidak ada akun, kredit, atau pembayaran.

## Batasan yang diketahui

- Hanya mendeteksi `<img>` dengan ukuran minimal 280×280 dan tampilan minimal 220×220. Gambar di dalam `canvas`, `background-image`, iframe lintas-origin, atau shadow DOM tertutup tidak terdeteksi; gunakan Screenshot.
- Satu batch dibatasi 40 gambar dan total 450 MB. Jika halaman punya lebih banyak gambar, jalankan lagi setelah batch pertama selesai; gambar yang sudah diterjemahkan dilewati otomatis.
- Hanya gambar yang sudah termuat di DOM yang ikut diproses. Gambar lazy-load di bawah lipatan baru masuk setelah kamu menggulir dan menjalankan terjemahan lagi.
- Situs dengan CSP ketat bisa memblokir gambar hasil dari `localhost`. Ekstensi mencoba memuat ulang sebagai data URL; jika tetap gagal, buka hasil di viewer.
- Beberapa situs memblokir pengambilan gambar lewat `Referer`/hotlink. Jika server membalas error, buka gambar di tab baru atau gunakan Screenshot.
- Terjemahkan otomatis memproses semua gambar yang sudah termuat saat pemindaian. Untuk chapter sangat panjang atau gambar yang dimuat lambat, gulir lalu jalankan lagi; untuk arsip besar, upload batch/ZIP di MangaFlow.
- Terjemahan otomatis bisa salah pada nama diri, SFX, atau teks kecil. Periksa hasil di editor sebelum dipublikasikan.
- Screenshot hanya menangkap area yang terlihat dan tidak menyertakan kursor atau elemen browser.
- Shortcut `Ctrl+Shift+S` bisa bentrok dengan ekstensi lain; ubah di `chrome://extensions/shortcuts`.
- Ekstensi belum diuji di Safari atau Firefox.

## Troubleshooting

**“Server MangaFlow tidak bisa dihubungi”**  
Pastikan `run.bat` atau `python app.py` masih berjalan, lalu klik **Cek ulang server**. Coba buka `http://127.0.0.1:8765/api/health` di browser. Jika memakai port lain, ubah alamat server di **Pengaturan** dan klik **Tes**.

**Tombol Terjemahkan halaman nonaktif**  
Penyebab umum: server offline, tab aktif bukan halaman web biasa, atau tidak ada gambar manga yang memenuhi ukuran minimum. Buka halaman manga, muat ulang sekali agar content script aktif, lalu buka popup lagi.

**“Muat ulang halaman ini sekali agar ekstensi bisa memindai gambar”**  
Content script belum terpasang saat halaman dibuka (misalnya ekstensi baru dimuat ulang). Refresh tab manga, lalu coba lagi.

**Screenshot gagal**  
Halaman `chrome://`, `chrome-extension://`, halaman error browser, dan PDF viewer tidak bisa ditangkap. Buka halaman web biasa. Pastikan juga jendela browser tidak diminimalkan.

**Hasil tidak muncul di halaman**  
Situs mungkin memblokir gambar hasil lewat CSP. Buka panel MangaFlow di halaman, lalu pilih **Buka viewer**. Jika tetap gagal, unduh hasil dari viewer.

**Shortcut tidak bekerja**  
Buka `chrome://extensions/shortcuts`, cari MangaFlow, dan tetapkan kombinasi lain. Chrome versi lama tidak mengizinkan `chrome.action.openPopup`; popup akan dibuka sebagai jendela kecil.

**Terjemahkan otomatis tidak berjalan atau hanya sebagian gambar**  
Pastikan toggle aktif di popup dan halaman memuat gambar setelah content script siap. Terjemahkan otomatis hanya memindai sekali per sesi tab dan hanya memproses gambar yang sudah termuat. Gulir halaman, lalu klik **Terjemahkan N gambar** untuk memproses gambar yang dimuat lambat atau sisa batch.

**Ukuran popup terpotong**  
Popup dirancang untuk lebar 320 px dan tinggi 584 px. Jika tampilan browser memakai zoom besar, perkecil zoom atau buka pengaturan lewat tab.

## Struktur ekstensi

```text
extension/
├─ manifest.json     # Manifest V3, izin, content script, shortcut
├─ background.js     # jembatan API server, job, screenshot, badge, viewer
├─ content.js        # deteksi gambar, terjemahan, ganti/pulihkan, panel halaman
├─ popup.html        # struktur popup Beranda/Pengaturan/Akun/Screenshot
├─ popup.css         # tema navy/ungu, kartu, sheet screenshot
├─ popup.js          # status server, pengaturan, deteksi halaman, crop, polling
├─ viewer.html       # viewer hasil
├─ viewer.css
├─ viewer.js         # slider sebelum/sesudah, daftar halaman, polling
├─ icons/            # ikon 16/32/48/128 px
└─ assets/fonts/     # Inter untuk tampilan konsisten
```

## Pengembangan

- Tidak ada bundler. Ubah file, lalu klik refresh di `chrome://extensions`.
- Popup punya mode pratinjau untuk pengembangan tampilan:
  - `popup.html?preview=1` — server disimulasikan.
  - `popup.html?preview=offline` — tampilan server offline.
  - `popup.html?preview=1&tab=settings` — langsung ke Pengaturan.
  - `popup.html?preview=1&last=1` — contoh kartu job terakhir.
  - `popup.html?preview=1&shot=1` — contoh sheet screenshot.
  - `viewer.html?demo=1` — viewer dengan data demo.
- Mode pratinjau memakai aset `../web/demo/` dan hanya bekerja jika dibuka lewat server statis dari root proyek, bukan dari `chrome-extension://`.
- Untuk menguji service worker, buka `chrome://extensions` → MangaFlow → **service worker**, lalu panggil `MangaFlowBackground.serverInfo()` di console.

## Lisensi

Kode ekstensi mengikuti lisensi MIT proyek MangaFlow. Lihat [`../LICENSE`](../LICENSE).
