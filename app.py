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

# ----------------- Paths (no folder picker) -----------------
def resolve_target_dir() -> Path:
    # On Streamlit Cloud (Linux), /tmp is the right place.
    if os.name != "nt":
        p = Path("/tmp")
        p.mkdir(parents=True, exist_ok=True)
        return p
    # On Windows (local dev), use a repo-local "downloads" folder.
    p = Path.cwd() / "downloads"
    p.mkdir(parents=True, exist_ok=True)
    return p

TARGET_DIR = resolve_target_dir()

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
                if p.exists():
                    return str(p)
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

# ----------------- Downloader -----------------
def download_content(url: str, download_type: str = "video", quality: int | None = None, cookie_path: Path | None = None) -> bool:
    ffmpeg_path = check_ffmpeg()
    if not ffmpeg_path:
        show_ffmpeg_instructions()
        return False

    progress_bar = st.progress(0.0)
    status_text = st.empty()
    downloaded_file = None

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    ydl_opts = {
        "outtmpl": str(TARGET_DIR / "%(title)s.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
        "progress": True,
        "prefer_ffmpeg": True,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 1,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": UA,
            "Referer": "https://www.youtube.com/",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    if cookie_path:
        ydl_opts["cookiefile"] = str(cookie_path)

    if ffmpeg_path != "ffmpeg":
        ydl_opts["ffmpeg_location"] = ffmpeg_path

    if download_type == "audio":
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        if quality:
            ydl_opts.update({
                "format": f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
                "merge_output_format": "mp4",
            })
        else:
            ydl_opts.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

    def progress_hook(d):
        nonlocal downloaded_file
        try:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total:
                    progress_bar.progress(min(downloaded / total, 1.0))
                status_text.text(f"⏳ Downloading: {os.path.basename(d.get('filename', ''))}")
            elif d["status"] == "finished":
                downloaded_file = d.get("filename", "")
                status_text.text(f"✅ Processing: {os.path.basename(downloaded_file or '')}")
                progress_bar.progress(1.0)
        except Exception as e:
            logger.warning(f"Progress hook error: {e}")

    ydl_opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            st.write(f"📥 Starting download for: {info.get('title', 'Unknown')}")
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
        return False

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        st.error(f"❌ Download failed: {msg}")
        with st.expander("Troubleshoot 403 / Forbidden"):
            st.markdown(
                "- Video might be **age-restricted / region-locked / members-only**.\n"
                "- Try **uploading cookies** from your browser session (see left sidebar).\n"
                "- Some videos block data-center IPs (Streamlit Cloud). Try another video or run locally."
            )
        logger.error(f"yt-dlp error: {msg}")
        return False
    except Exception as e:
        st.error(f"❌ Download failed: {e}")
        logger.error(f"Download error: {e}")
        return False

# ----------------- UI -----------------
def main():
    st.set_page_config(page_title="YouTube Downloader", page_icon="🎥", layout="wide")
    st.title("🎥 YouTube Downloader")
    st.markdown("This app saves files to **/tmp** (Cloud) or **downloads/** (Windows). No folder selection needed.")

    # Optional cookies uploader
    st.sidebar.header("Cookies (optional)")
    st.sidebar.write("Upload a `cookies.txt` exported for youtube.com to access age/region/member-restricted videos.")
    cookie_upload = st.sidebar.file_uploader("Upload cookies.txt", type=["txt"])
    cookie_path = None
    if cookie_upload:
        cookie_path = (Path("/tmp") / "cookies.txt") if os.name != "nt" else (Path.cwd() / "cookies.txt")
        with open(cookie_path, "wb") as f:
            f.write(cookie_upload.read())
        st.sidebar.success(f"Cookies loaded: {cookie_path}")

    with st.form("download_form"):
        url = st.text_input("🔗 YouTube URL")
        col1, col2 = st.columns(2)
        with col1:
            dtype = st.selectbox("📥 Download Type", ["video", "audio"])
        with col2:
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
