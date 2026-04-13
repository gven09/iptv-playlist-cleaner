# 📺 IPTV Playlist Cleaner

A Python CLI tool that removes dead, fake-alive, and duplicate channels 
from M3U/M3U8 IPTV playlists — with deep HLS segment verification.

## Features
- ✅ Detects truly dead channels (not just HTTP status)
- 🔍 Deep HLS segment verification — catches token-expired streams
- ⊘ Removes duplicate channels by URL and/or name
- ⚡ Parallel checking with configurable thread count
- 📊 Detailed report with error breakdown and group summary

## Requirements
- Python 3.10+
- No external dependencies

## Usage
\`\`\`bash
# Basic usage
python iptv_cleaner.py playlist.m3u

# Fast with more threads
python iptv_cleaner.py playlist.m3u -w 50 -t 5

# Custom output file
python iptv_cleaner.py playlist.m3u -o cleaned.m3u

# Skip duplicate removal
python iptv_cleaner.py playlist.m3u --no-dedup

# Fast mode (no segment check)
python iptv_cleaner.py playlist.m3u --fast
\`\`\`

## Options
| Flag | Description |
|------|-------------|
| `-w` | Parallel workers (default: 30) |
| `-t` | Timeout in seconds (default: 8) |
| `-o` | Output file path |
| `--fast` | Skip HLS segment verification |
| `--no-dedup` | Disable duplicate detection |
| `--dedup-url-only` | Deduplicate by URL only |
| `--dedup-name-only` | Deduplicate by name only |
| `--keep-dead` | Keep dead channels in output |
| `--group` | Filter by group-title |

## License
MIT
