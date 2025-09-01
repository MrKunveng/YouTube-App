import os
import platform
import subprocess
import logging
from pathlib import Path

import streamlit as st
import yt_dlp

# ----------------- Logging -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- Paths (no folder/export UI) -----------------
def target_dir() -> Path:
    if os.name != "nt":  # Linux/macOS (Streamlit Cloud)
        p = Path("/tmp"); p.mkdir(parents=True, exist_ok=True); return p
    p = Path.cwd() / "downloads"; p.mkdir(parents=True, exist_ok=True); return p

TARGET_DIR = target_dir()

# ----------------- FFmpeg check -----------------
def check_ffmpeg() -> str | None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return "ffmpeg"
    except (subprocess.CalledProcessError, FileNotFoundError):
        if platform.system() == "Windows":
            for p in [
                Path.cwd() / "ffmpeg.exe",
                Path.cwd() / "ffmpeg" / "bin" / "ffmpeg.exe",
                Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe",
            ]:
                if p.exists(): return str(p)
        return None

def show_ffmpeg_instructions():
    st.error("❌ FFmpeg not found.")
    sys = platform.system()
    if sys == "Windows":
        st.markdown("**Windows:** download zip from https://www.gyan.dev/ffmpeg/builds/ (essentials), "
                    "extract, and put `bin/ffmpeg.exe` next to this app. Or `choco install ffmpeg`.")
    elif sys == "Darwin":
        st.markdown("**macOS:** `brew install ffmpeg`")
    else:
        st.markdown("**Linux:** `sudo apt update && sudo apt install -y ffmpeg`")
    st.stop()

# ----------------- yt-dlp session builder -----------------
def make_opts(ffmpeg_path: str, download_type: str, quality: int | None,
              cookie_path: Path | None, player_client: str):
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    opts = {
        "outtmpl": str(TARGET_DIR / "%(title)s.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
        "progress": True,
        "prefer_ffmpeg": True,
        "noplaylist": True,
        "playlist_items": "1",
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 1,
        "skip_unavailable_fragments": True,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": UA,
            "Referer": "https://www.youtube.com/",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {"youtube": {"player_client": player_client}},
    }
    if cookie_path:
        opts["cookiefile"] = str(cookie_path)
    if ffmpeg_path != "ffmpeg":
        opts["ffmpeg_location"] = ffmpeg_path

    if download_type == "audio":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        if quality:
            opts.update({
                "format": f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
                "merge_output_format": "mp4",
            })
        else:
            opts.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })
    return opts

# ----------------- Core download (with 403 failover) -----------------
def download_content(url: str, download_type: str = "video", quality: int | None = None,
                     cookie_path: Path | None = None) -> bool:
    ffmpeg_path = check_ffmpeg()
    if not ffmpeg_path:
        show_ffmpeg_instructions()
        return False

    progress_bar = st.progress(0.0)
    status_text = st.empty()
    downloaded_file = None

    def hook(d):
        nonlocal downloaded_file
        try:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total: progress_bar.progress(min(downloaded / total, 1.0))
                status_text.text(f"⏳ Downloading: {os.path.basename(d.get('filename',''))}")
            elif d["status"] == "finished":
                downloaded_file = d.get("filename", "")
                status_text.text(f"✅ Processing: {os.path.basename(downloaded_file or '')}")
                progress_bar.progress(1.0)
        except Exception as e:
            logger.warning(f"Progress hook error: {e}")

    # Try android client first, then web (helps some 403 cases)
    for client in ("android", "web"):
        try:
            opts = make_opts(ffmpeg_path, download_type, quality, cookie_path, client)
            opts["progress_hooks"] = [hook]
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                st.write(f"📥 Starting download for: {info.get('title', 'Unknown')} (client: {client})")
                ydl.download([url])

                if downloaded_file and os.path.exists(downloaded_file):
                    size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
                    fobj = open(downloaded_file, "rb")  # stream (no big RAM hit)
                    st.download_button(
                        label=f"⬇️ Download {os.path.basename(downloaded_file)} ({size_mb:.1f} MB)",
                        data=fobj,
                        file_name=os.path.basename(downloaded_file),
                        mime="application/octet-stream",
                    )
                    return True
        except yt_dlp.utils.DownloadError as e:
            # If it's a 403, try next client; otherwise show details
            if "403" in str(e):
                logger.warning(f"403 on client '{client}', retrying with next client...")
                continue
            st.error(f"❌ Download failed: {e}")
            logger.error(f"yt-dlp error: {e}")
            return False
        except Exception as e:
            st.error(f"❌ Download failed: {e}")
            logger.error(f"Download error: {e}")
            return False

    # If both clients failed (likely IP/age/region/member restriction)
    st.error("❌ Download failed: HTTP 403 (Forbidden).")
    with st.expander("Troubleshoot 403 / Forbidden"):
        st.markdown(
            "- The video may be **age-restricted / region-locked / members-only**.\n"
            "- Try **uploading cookies** from your browser session (sidebar → cookies.txt).\n"
            "- Some videos block cloud/data-center IPs; try a different video or run locally."
        )
    return False

# ----------------- UI -----------------
def main():
    st.set_page_config(page_title="YouTube Downloader", page_icon="🎥", layout="wide")
    st.title("🎥 YouTube Downloader")
    st.markdown("Files are saved to **/tmp** (Cloud) or **downloads/** (Windows). No folder selection.")

    # Optional cookies uploader
    st.sidebar.header("Cookies (optional)")
    st.sidebar.write("Upload a `cookies.txt` exported for youtube.com (helps bypass age/region/member restrictions).")
    cookie_upload = st.sidebar.file_uploader("Upload cookies.txt", type=["txt"])
    cookie_path = None
    if cookie_upload:
        cookie_path = (Path("/tmp") / "cookies.txt") if os.name != "nt" else (Path.cwd() / "cookies.txt")
        with open(cookie_path, "wb") as f:
            f.write(cookie_upload.read())
        st.sidebar.success(f"Cookies loaded: {cookie_path}")

    with st.form("download_form"):
        url = st.text_input("🔗 YouTube URL")
        c1, c2 = st.columns(2)
        with c1:
            dtype = st.selectbox("📥 Download Type", ["video", "audio"])
        with c2:
            q = st.selectbox("🎬 Video Quality", [None, 240, 360, 480, 720, 1080],
                             format_func=lambda x: "Best" if x is None else f"{x}p") if dtype == "video" else None
        submitted = st.form_submit_button("⬇️ Download")

    if submitted:
        if not url.strip():
            st.error("⚠️ Please enter a YouTube URL")
        else:
            with st.spinner("Processing download..."):
                ok = download_content(url.strip(), dtype, q, cookie_path=cookie_path)
            if ok:
                st.button("🔄 Download Another", on_click=st.rerun)

if __name__ == "__main__":
    main()
