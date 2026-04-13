📺 IPTV Playlist Cleaner
A Python CLI tool that removes dead, fake-alive, and duplicate channels from M3U/M3U8 IPTV playlists — with deep HLS segment verification.
No external dependencies. Just Python 3.10+.

Features

✅ Detects truly dead channels (not just HTTP status)
🔍 Deep HLS segment verification — catches token-expired and geo-blocked streams
⊘ Removes duplicate channels by URL and/or name
⚡ Parallel checking with configurable thread count
📊 Detailed report with error breakdown and group summary


How It Works
Most IPTV checkers only look at HTTP status codes. This tool goes further:

HEAD request — quick reachability check
GET + first 512 bytes — verifies real media data (not an HTML error page or JSON error)
HLS segment check — for .m3u8 streams, fetches the first .ts segment from the manifest to confirm the stream is actually delivering video (catches expired tokens, empty streams, geo-blocks)


Requirements

Python 3.10+
No external libraries needed


Usage
bash# Basic — checks all channels, removes dead & duplicates
python iptv_cleaner.py playlist.m3u

# Faster with more threads and shorter timeout
python iptv_cleaner.py playlist.m3u -w 50 -t 5

# Custom output file
python iptv_cleaner.py playlist.m3u -o my_clean_list.m3u

# Fast mode — skips HLS segment verification (like v1/v2 behavior)
python iptv_cleaner.py playlist.m3u --fast

# Only check a specific group
python iptv_cleaner.py playlist.m3u --group "Sports"

Options
FlagDefaultDescription-w, --workers30Number of parallel threads-t, --timeout8Timeout per channel in seconds-o, --output<input>_cleaned.m3uOutput file path--fastoffSkip HLS segment check (faster but less accurate)--no-dedupoffDisable duplicate detection entirely--dedup-url-onlyoffDeduplicate by URL only--dedup-name-onlyoffDeduplicate by channel name only--keep-deadoffInclude dead channels in output--group—Only process channels matching this group-title

Example Output
  Toplam kanal       : 3240
  ✔ Çalışan          : 1847
  ✘ Çalışmayan       : 1201
  ⊘ Kopya (silindi)  : 192
  Geçen süre         : 143.2s
  Çıktı dosyası      : playlist_cleaned.m3u

  Hata Dağılımı:
  ✘  HLS segment erişilemiyor (token süresi?)       x544
  ✘  Zaman aşımı                                    x310
  ✘  HTTP 404 (bulunamadı)                          x198
  ✘  HTML hata/login sayfası döndü                  x149

License
MIT
