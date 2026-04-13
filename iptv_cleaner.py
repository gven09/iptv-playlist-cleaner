#!/usr/bin/env python3
"""
IPTV M3U/M3U8 Playlist Cleaner v3
====================================
- Derin stream doğrulaması (HLS segment kontrolü dahil)
- Kopya kanal tespiti ve silme
"""

import re
import sys
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

# ── Renkler ───────────────────────────────────────────────────────────────────
class C:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
    DIM    = "\033[2m"

VALID_CONTENT_TYPES = (
    "video/", "audio/", "application/vnd.apple.mpegurl",
    "application/x-mpegurl", "application/octet-stream",
    "application/dash+xml", "text/plain",
)
HLS_SIGNATURES = (b"#EXTM3U", b"#EXT-X-")
TS_SYNC_BYTE   = 0x47

# ── Veri yapısı ───────────────────────────────────────────────────────────────
@dataclass
class Channel:
    extinf: str
    url: str
    name: str = ""
    group: str = ""
    alive: Optional[bool] = None
    status_code: int = 0
    error: str = ""
    response_time: float = 0.0
    fail_reason: str = ""
    duplicate: bool = False

# ── Parser ────────────────────────────────────────────────────────────────────
def parse_m3u(content: str) -> list:
    channels = []
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith("#EXTM3U"):
        print(f"{C.RED}[!] Geçerli bir M3U/M3U8 dosyası değil.{C.RESET}")
        sys.exit(1)
    i = 1
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            extinf = line
            name_match  = re.search(r',(.+)$', line)
            name  = name_match.group(1).strip() if name_match else "Bilinmiyor"
            group_match = re.search(r'group-title="([^"]*)"', line)
            group = group_match.group(1) if group_match else ""
            i += 1
            url = ""
            while i < len(lines):
                candidate = lines[i].strip()
                if candidate and not candidate.startswith("#"):
                    url = candidate
                    break
                i += 1
            if url:
                channels.append(Channel(extinf=extinf, url=url,
                                        name=name, group=group))
        i += 1
    return channels

# ── Kopya tespiti ─────────────────────────────────────────────────────────────
def mark_duplicates(channels: list, by_url: bool = True,
                    by_name: bool = True) -> tuple:
    """
    Aynı URL veya aynı isimden (büyük/küçük harf farkı gözetmeksizin)
    ikinci ve sonraki kanalları duplicate olarak işaretler.
    İlk görülen kanal korunur.
    """
    seen_urls:  set = set()
    seen_names: set = set()
    dup_count = 0

    for ch in channels:
        is_dup = False

        if by_url:
            url_key = ch.url.strip().lower()
            if url_key in seen_urls:
                is_dup = True
            else:
                seen_urls.add(url_key)

        if by_name and not is_dup:
            name_key = re.sub(r'\s+', ' ', ch.name.strip().lower())
            if name_key and name_key in seen_names:
                is_dup = True
            else:
                if name_key:
                    seen_names.add(name_key)

        if is_dup:
            ch.duplicate = True
            dup_count += 1

    return channels, dup_count

# ── Stream veri doğrulama ─────────────────────────────────────────────────────
def _is_valid_stream_data(data: bytes, content_type: str) -> tuple:
    if len(data) < 8:
        return False, "Çok az veri geldi (boş stream?)"
    if data.startswith(b"#EXTM3U") or data.startswith(b"#EXT-X-"):
        return True, ""
    if data[0] == TS_SYNC_BYTE:
        return True, ""
    ct = content_type.lower()
    if any(ct.startswith(v) for v in VALID_CONTENT_TYPES):
        if ct.startswith("text/plain"):
            if any(data.startswith(sig) for sig in HLS_SIGNATURES):
                return True, ""
            return False, "text/plain ama HLS imzası yok"
        return True, ""
    if data[:100].lower().lstrip().startswith(b"<!doctype") or \
       data[:100].lower().lstrip().startswith(b"<html"):
        return False, "HTML hata/login sayfası döndü"
    stripped = data[:50].strip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return False, "JSON hata yanıtı döndü"
    return True, ""

# ── HLS manifest parse → ilk segment URL ─────────────────────────────────────
def _extract_first_segment(manifest: bytes, base_url: str) -> Optional[str]:
    """
    HLS manifest içinden ilk .ts veya segment URL'ini çıkarır.
    Göreli URL'leri mutlak URL'e çevirir.
    """
    text = manifest.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Göreli URL → mutlak
        if line.startswith("http://") or line.startswith("https://"):
            return line
        else:
            # base_url'den path kısmını çıkar, segment'i ekle
            from urllib.parse import urljoin
            return urljoin(base_url, line)
    return None

# ── HLS segment kontrolü ──────────────────────────────────────────────────────
def _check_hls_segment(segment_url: str, timeout: int) -> tuple:
    """
    HLS manifest'in içindeki ilk .ts segmentine istek atar.
    (bool: erişilebilir mi, str: hata sebebi)
    """
    headers = {
        "User-Agent": "VLC/3.0 LibVLC/3.0",
        "Accept": "*/*",
        "Connection": "close",
        "Range": "bytes=0-187",   # 1 MPEG-TS paketi = 188 byte
    }
    try:
        req = urllib.request.Request(segment_url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return False, f"Segment HTTP {resp.status}"
            chunk = resp.read(188)
            if len(chunk) < 4:
                return False, "Segment boş geldi"
            # MPEG-TS sync byte kontrolü
            if chunk[0] != TS_SYNC_BYTE:
                return False, f"Geçersiz TS sync byte: 0x{chunk[0]:02x}"
            return True, ""
    except urllib.error.HTTPError as e:
        return False, f"Segment HTTP {e.code}"
    except Exception as e:
        return False, f"Segment hatası: {str(e)[:60]}"

# ── Kanal kontrolü (v3) ───────────────────────────────────────────────────────
def check_channel(ch: Channel, timeout: int = 8, deep: bool = True) -> Channel:
    start = time.time()
    url   = ch.url.strip()
    headers = {
        "User-Agent": "VLC/3.0 LibVLC/3.0",
        "Accept": "*/*",
        "Connection": "close",
    }
    content_type = ""

    # Adım 1: HEAD
    try:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status >= 400:
                ch.alive = False
                ch.status_code = resp.status
                ch.fail_reason = f"HTTP {resp.status}"
                ch.response_time = time.time() - start
                return ch
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            ch.alive = False
            ch.status_code = e.code
            ch.fail_reason = f"HTTP {e.code} (bulunamadı)"
            ch.response_time = time.time() - start
            return ch
    except Exception:
        pass

    # Adım 2: GET + ilk 512 byte
    try:
        req = urllib.request.Request(url, method="GET", headers={
            **headers, "Range": "bytes=0-511",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ch.status_code = resp.status
            if resp.status >= 400:
                ch.alive = False
                ch.fail_reason = f"HTTP {resp.status}"
                ch.response_time = time.time() - start
                return ch

            ct = resp.headers.get("Content-Type", content_type)
            try:
                chunk = resp.read(512)
            except Exception:
                chunk = b""

            if deep:
                valid, reason = _is_valid_stream_data(chunk, ct)
                if not valid:
                    ch.alive = False
                    ch.fail_reason = reason or "Geçersiz stream verisi"
                    ch.response_time = time.time() - start
                    return ch

                # Adım 3: HLS manifest ise segment kontrolü
                is_hls = (
                    chunk.startswith(b"#EXTM3U") or
                    chunk.startswith(b"#EXT-X-") or
                    url.endswith(".m3u8") or
                    "mpegurl" in ct.lower()
                )
                if is_hls and chunk:
                    segment_url = _extract_first_segment(chunk, url)
                    if segment_url:
                        seg_ok, seg_reason = _check_hls_segment(
                            segment_url, timeout)
                        if not seg_ok:
                            ch.alive = False
                            ch.fail_reason = seg_reason
                            ch.response_time = time.time() - start
                            return ch

            ch.alive = True
            ch.response_time = time.time() - start
            return ch

    except urllib.error.HTTPError as e:
        ch.status_code = e.code
        ch.alive = False
        ch.fail_reason = f"HTTP {e.code}"
    except urllib.error.URLError as e:
        ch.alive = False
        ch.fail_reason = f"Bağlantı hatası: {e.reason}"
    except TimeoutError:
        ch.alive = False
        ch.fail_reason = "Zaman aşımı"
    except Exception as e:
        ch.alive = False
        ch.fail_reason = str(e)[:80]

    ch.response_time = time.time() - start
    return ch

# ── Toplu kontrol ─────────────────────────────────────────────────────────────
def check_all(channels: list, workers: int = 20,
              timeout: int = 8, deep: bool = True) -> list:
    # Duplicate'leri kontrol etme — zaten işaretlendi
    to_check = [ch for ch in channels if not ch.duplicate]
    total    = len(to_check)
    done     = 0
    idx_map  = {id(ch): i for i, ch in enumerate(channels)}

    mode_str = f"{C.GREEN}derin + HLS segment{C.RESET}" if deep else "hızlı mod"
    print(f"\n{C.CYAN}{C.BOLD}  {total} kanal kontrol ediliyor "
          f"({workers} iş parçacığı | {timeout}s | {mode_str}){C.RESET}\n")

    results = list(channels)  # kopya listesi, duplicate'ler zaten işaretli

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(check_channel, ch, timeout, deep): ch
            for ch in to_check
        }
        for future in as_completed(future_map):
            ch = future.result()
            done += 1
            icon = f"{C.GREEN}✔{C.RESET}" if ch.alive else f"{C.RED}✘{C.RESET}"
            bar_filled = int(40 * done / total)
            bar  = "█" * bar_filled + "░" * (40 - bar_filled)
            pct  = int(100 * done / total)
            print(
                f"\r  [{bar}] {pct:3d}%  {icon}  "
                f"{C.DIM}{ch.name[:30]:<30}{C.RESET}",
                end="", flush=True
            )

    print()
    return results

# ── M3U yazıcı ────────────────────────────────────────────────────────────────
def write_m3u(channels: list, path: Path):
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(ch.extinf.strip())
        lines.append(ch.url.strip())
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

# ── Rapor ─────────────────────────────────────────────────────────────────────
def print_report(channels: list, output_path: Path, elapsed: float,
                 dup_count: int):
    alive = [c for c in channels if c.alive and not c.duplicate]
    dead  = [c for c in channels if not c.alive and not c.duplicate]
    dups  = [c for c in channels if c.duplicate]

    print(f"\n{'─'*62}")
    print(f"{C.BOLD}  📊  SONUÇ RAPORU{C.RESET}")
    print(f"{'─'*62}")
    print(f"  Toplam kanal       : {len(channels)}")
    print(f"  {C.GREEN}✔ Çalışan           : {len(alive)}{C.RESET}")
    print(f"  {C.RED}✘ Çalışmayan        : {len(dead)}{C.RESET}")
    print(f"  {C.YELLOW}⊘ Kopya (silindi)   : {dup_count}{C.RESET}")
    print(f"  Geçen süre          : {elapsed:.1f}s")
    print(f"  Çıktı dosyası       : {C.CYAN}{output_path}{C.RESET}")
    print(f"{'─'*62}")

    if dead:
        reasons: dict = {}
        for ch in dead:
            r = ch.fail_reason or "bilinmiyor"
            if "HTML"      in r: key = "HTML hata/login sayfası"
            elif "JSON"    in r: key = "JSON hata yanıtı"
            elif "Zaman"   in r: key = "Zaman aşımı"
            elif "Segment" in r: key = "HLS segment erişilemiyor (token süresi?)"
            elif "sync"    in r: key = "Geçersiz video verisi"
            elif "Bağlantı" in r:key = "Bağlantı hatası"
            elif "HTTP"    in r: key = r
            else:                key = r[:45]
            reasons[key] = reasons.get(key, 0) + 1

        print(f"\n{C.YELLOW}  Hata Dağılımı:{C.RESET}")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {C.RED}✘{C.RESET}  {reason:<48} x{count}")

        print(f"\n{C.YELLOW}  İlk 15 çalışmayan:{C.RESET}")
        for ch in dead[:15]:
            print(f"  {C.RED}✘{C.RESET}  {ch.name[:40]:<40}  "
                  f"{C.DIM}{ch.fail_reason[:35]}{C.RESET}")
        if len(dead) > 15:
            print(f"  {C.DIM}... ve {len(dead)-15} kanal daha{C.RESET}")

    if dups:
        print(f"\n{C.YELLOW}  İlk 10 silinen kopya:{C.RESET}")
        for ch in dups[:10]:
            print(f"  {C.YELLOW}⊘{C.RESET}  {ch.name[:40]:<40}  "
                  f"{C.DIM}{ch.url[:45]}{C.RESET}")
        if len(dups) > 10:
            print(f"  {C.DIM}... ve {len(dups)-10} kopya daha{C.RESET}")

    # Grup özeti
    groups: dict = {}
    for ch in channels:
        if ch.duplicate:
            continue
        g = ch.group or "(grup yok)"
        if g not in groups:
            groups[g] = {"ok": 0, "fail": 0}
        if ch.alive:
            groups[g]["ok"] += 1
        else:
            groups[g]["fail"] += 1

    if len(groups) > 1:
        print(f"\n{C.BOLD}  Grup Özeti:{C.RESET}")
        for g, cnt in sorted(groups.items(), key=lambda x: -x[1]["ok"]):
            total_g = cnt["ok"] + cnt["fail"]
            bar = "█" * min(cnt["ok"], 30) + "░" * min(cnt["fail"], 30)
            print(f"  {g[:30]:<30}  {C.GREEN}{cnt['ok']:3d}{C.RESET}/{total_g}  {bar}")

    print()

# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    print(f"""
{C.CYAN}{C.BOLD}
  ██╗██████╗ ████████╗██╗   ██╗     ██████╗██╗     ███████╗ █████╗ ███╗   ██╗███████╗██████╗
  ██║██╔══██╗╚══██╔══╝██║   ██║    ██╔════╝██║     ██╔════╝██╔══██╗████╗  ██║██╔════╝██╔══██╗
  ██║██████╔╝   ██║   ██║   ██║    ██║     ██║     █████╗  ███████║██╔██╗ ██║█████╗  ██████╔╝
  ██║██╔═══╝    ██║   ╚██╗ ██╔╝    ██║     ██║     ██╔══╝  ██╔══██║██║╚██╗██║██╔══╝  ██╔══██╗
  ██║██║        ██║    ╚████╔╝     ╚██████╗███████╗███████╗██║  ██║██║ ╚████║███████╗██║  ██║
  ╚═╝╚═╝        ╚═╝     ╚═══╝       ╚═════╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝
{C.RESET}{C.DIM}  M3U / M3U8 Playlist Temizleyici v3 — HLS segment + kopya tespiti{C.RESET}
""")

    parser = argparse.ArgumentParser(
        description="M3U/M3U8 dosyasındaki çalışmayan ve kopya kanalları temizler.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("input",  help="Giriş .m3u veya .m3u8 dosyası")
    parser.add_argument("-o", "--output", default=None,
                        help="Çıktı dosyası (varsayılan: <giriş>_cleaned.m3u)")
    parser.add_argument("-w", "--workers", type=int, default=30,
                        help="Paralel iş parçacığı sayısı (varsayılan: 30)")
    parser.add_argument("-t", "--timeout", type=int, default=8,
                        help="Zaman aşımı saniye (varsayılan: 8)")
    parser.add_argument("--fast", action="store_true",
                        help="Hızlı mod: sadece HTTP durumu kontrol eder")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Kopya tespitini devre dışı bırak")
    parser.add_argument("--dedup-name-only", action="store_true",
                        help="Sadece isim benzerliğine göre kopya tespit et")
    parser.add_argument("--dedup-url-only", action="store_true",
                        help="Sadece URL eşleşmesine göre kopya tespit et")
    parser.add_argument("--keep-dead", action="store_true",
                        help="Çalışmayan kanalları da çıktıya ekle")
    parser.add_argument("--group", default=None,
                        help="Sadece belirtilen group-title'ı kontrol et")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"{C.RED}[!] Dosya bulunamadı: {input_path}{C.RESET}")
        sys.exit(1)

    print(f"  {C.BOLD}Dosya okunuyor:{C.RESET} {input_path}")
    content  = input_path.read_text(encoding="utf-8", errors="replace")
    channels = parse_m3u(content)

    if args.group:
        channels = [c for c in channels
                    if c.group.lower() == args.group.lower()]
        print(f"  Filtre: grup = {C.YELLOW}{args.group}{C.RESET} "
              f"({len(channels)} kanal)")

    print(f"  {C.BOLD}{len(channels)}{C.RESET} kanal yüklendi.")

    # Kopya tespiti
    dup_count = 0
    if not args.no_dedup:
        by_url  = not args.dedup_name_only
        by_name = not args.dedup_url_only
        channels, dup_count = mark_duplicates(channels,
                                              by_url=by_url,
                                              by_name=by_name)
        print(f"  {C.YELLOW}⊘ {dup_count} kopya kanal tespit edildi{C.RESET} "
              f"(kontrol edilmeyecek)")

    if args.fast:
        print(f"  {C.YELLOW}Hızlı mod — segment kontrolü atlanıyor{C.RESET}")

    t0      = time.time()
    checked = check_all(channels, workers=args.workers,
                        timeout=args.timeout, deep=not args.fast)
    elapsed = time.time() - t0

    # Çıktıya yazılacaklar
    if args.keep_dead:
        to_write = [c for c in checked if not c.duplicate]
    else:
        to_write = [c for c in checked if c.alive and not c.duplicate]

    if args.output:
        output_path = Path(args.output)
    else:
        stem = input_path.stem
        output_path = input_path.parent / f"{stem}_cleaned.m3u"

    write_m3u(to_write, output_path)
    print_report(checked, output_path, elapsed, dup_count)


if __name__ == "__main__":
    main()
