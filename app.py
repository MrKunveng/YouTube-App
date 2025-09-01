import os
import streamlit as st
from pathlib import Path
import platform
import yt_dlp
import logging
import subprocess

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Cloud detection + manual override ----------
def _running_in_cloud() -> bool:
    headless = os.environ.get("STREAMLIT_SERVER_HEADLESS", "").lower() in ("true", "1", "yes")
    home_writable = os.access(Path.home(), os.W_OK)
    return headless or not home_writable

AUTO_CLOUD = _running_in_cloud()

# Sidebar switch lets you force Cloud mode if detection is off
st.sidebar.markdown("### Runtime")
FORCE_CLOUD = st.sidebar.toggle("Force Cloud mode (use /tmp, hide folder UI)", value=AUTO_CLOUD)
IS_CLOUD_DEPLOYMENT = FORCE_CLOUD

# ---------- Paths ----------
def validate_path(path: str) -> Path:
    if IS_CLOUD_DEPLOYMENT:
        return Path("/tmp")
    return Path(path) if path else Path("downloads")

# ---------- FFmpeg ----------
def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return 'ffmpeg'
    except (subprocess.CalledProcessError, FileNotFoundError):
        if platform.system() == "Windows":
            ffmpeg_paths = [
                Path.cwd() / "ffmpeg.exe",
                Path.cwd() / "ffmpeg" / "bin" / "ffmpeg.exe",
                Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe",
            ]
            for p in ffmpeg_paths:
                if p.exists():
                    return str(p)
        return None

def show_ffmpeg_instructions():
    if IS_CLOUD_DEPLOYMENT:
        st.error("❌ FFmpeg not found in the cloud container. Ensure `packages.txt` contains a single line: `ffmpeg`, then redeploy.")
        st.stop()

    st.error("❌ FFmpeg is required but not found!")
    system = platform.system()
    if system == "Windows":
        st.markdown("""
        **Windows**
        - Download: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
        - Or:
        ```powershell
        choco install ffmpeg
        ```
        """)
    elif system == "Darwin":
        st.markdown("""
        **macOS**
        ```bash
        brew install ffmpeg
        ```
        """)
    else:
        st.markdown("""
        **Linux (Debian/Ubuntu)**
        ```bash
        sudo apt update && sudo apt install -y ffmpeg
        ```
        """)
    st.stop()

# ---------- Download ----------
def download_content(url: str, download_type: str = 'video', quality: int = None,
                     download_folder: str = None, cookie_path: Path | None = None):
    ffmpeg_path = check_ffmpeg()
    if not ffmpeg_path:
        show_ffmpeg_instructions()
        return False

    try:
        # Decide target dir
        if IS_CLOUD_DEPLOYMENT:
            target_dir = Path("/tmp")
            download_folder = None  # ensure UI acts like temp
        else:
            if download_folder and os.path.isdir(download_folder):
                target_dir = Path(download_folder)
            else:
                target_dir = Path("temp_downloads")
        target_dir.mkdir(parents=True, exist_ok=True)

        progress_bar = st.progress(0.0)
        status_text = st.empty()
        downloaded_file = None

        # Conservative headers help avoid some 403s
        UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

        ydl_opts = {
            'outtmpl': str(target_dir / '%(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'progress': True,
            'prefer_ffmpeg': True,
            'noplaylist': True,
            'retries': 10,
            'fragment_retries': 10,
            'geo_bypass': True,
            'http_headers': {
                'User-Agent': UA,
                'Referer': 'https://www.youtube.com/',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            # Try alternate player clients if default fails
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        }

        if cookie_path:
            # Use cookies to access age/region/member-gated videos
            ydl_opts['cookiefile'] = str(cookie_path)

        if ffmpeg_path != 'ffmpeg':
            ydl_opts['ffmpeg_location'] = ffmpeg_path

        if download_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            if quality:
                ydl_opts.update({
                    'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
                    'merge_output_format': 'mp4',
                })
            else:
                ydl_opts.update({
                    'format': 'bestvideo+bestaudio/best',
                    'merge_output_format': 'mp4',
                })

        def progress_hook(d):
            nonlocal downloaded_file
            try:
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    downloaded = d.get('downloaded_bytes', 0)
                    if total:
                        progress_bar.progress(min(downloaded / total, 1.0))
                    status_text.text(f"⏳ Downloading: {os.path.basename(d.get('filename',''))}")
                elif d['status'] == 'finished':
                    downloaded_file = d.get('filename', '')
                    status_text.text(f"✅ Processing: {os.path.basename(downloaded_file or '')}")
                    progress_bar.progress(1.0)
            except Exception as e:
                logger.warning(f"Progress hook error: {e}")

        ydl_opts['progress_hooks'] = [progress_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Preflight (metadata)
                info = ydl.extract_info(url, download=False)
                st.write(f"📥 Starting download for: {info.get('title', 'Unknown')}")
                # Actual download
                ydl.download([url])

                if downloaded_file and os.path.exists(downloaded_file):
                    file_size = os.path.getsize(downloaded_file) / (1024 * 1024)
                    if download_folder and not IS_CLOUD_DEPLOYMENT:
                        st.success(f"✅ Saved to: {download_folder}")
                        st.info(f"📁 {os.path.basename(downloaded_file)} ({file_size:.1f} MB)")
                    else:
                        # Stream file object to avoid big RAM usage
                        fobj = open(downloaded_file, 'rb')
                        st.download_button(
                            label=f"⬇️ Download {os.path.basename(downloaded_file)} ({file_size:.1f} MB)",
                            data=fobj,
                            file_name=os.path.basename(downloaded_file),
                            mime='application/octet-stream'
                        )
                    return True
            return False

        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            st.error(f"❌ Download failed: {msg}")
            # Friendly hints for common 403 causes
            with st.expander("Troubleshoot 403 / Forbidden"):
                st.markdown(
                    "- The video may be **age-restricted / region-locked / members-only**.\n"
                    "- Try **uploading cookies** from your browser session (see uploader above).\n"
                    "- If you're on Streamlit Cloud, YouTube may block the server IP for some videos.\n"
                    "- Try a different video or lower quality."
                )
            logger.error(f"yt-dlp 403/DownloadError: {msg}")
            return False

    except Exception as e:
        st.error(f"❌ Download failed: {e}")
        logger.error(f"Download error: {e}")
        return False

# ---------- UI ----------
def main():
    st.set_page_config(page_title="YouTube Downloader", page_icon="🎥", layout="wide")
    st.title("🎥 YouTube Downloader")

    if IS_CLOUD_DEPLOYMENT:
        st.markdown("☁️ **Cloud Mode** — files are prepared in `/tmp` and offered as a download button.")
    else:
        st.markdown("💻 **Local Mode** — choose a download folder or use a temporary local folder.")

    # Optional: cookies uploader to bypass 403 (age/region/member)
    st.sidebar.markdown("### Cookies (optional)")
    cookie_file = st.sidebar.file_uploader("Upload cookies.txt for youtube.com", type=["txt"])
    cookie_path = None
    if cookie_file:
        # Save uploaded cookies to a path yt-dlp can read
        cookie_path = Path("/tmp/cookies.txt") if IS_CLOUD_DEPLOYMENT else Path("cookies.txt")
        with open(cookie_path, "wb") as f:
            f.write(cookie_file.read())
        st.sidebar.success(f"Cookies loaded: {cookie_path}")

    # Folder selection ONLY in local mode
    if not IS_CLOUD_DEPLOYMENT:
        st.subheader("📁 Download Folder Selection")
        if 'selected_folder' not in st.session_state:
            st.session_state.selected_folder = ""

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("📁 Downloads"):
                st.session_state.selected_folder = os.path.expanduser("~/Downloads"); st.rerun()
        with col2:
            if st.button("🖥️ Desktop"):
                st.session_state.selected_folder = os.path.expanduser("~/Desktop"); st.rerun()
        with col3:
            if st.button("📄 Documents"):
                st.session_state.selected_folder = os.path.expanduser("~/Documents"); st.rerun()
        with col4:
            if st.button("🗂️ Custom"):
                st.session_state.selected_folder = ""; st.rerun()

        download_folder = st.text_input(
            "📂 Download Folder Path:",
            value=st.session_state.selected_folder,
            placeholder="Enter full path (e.g., /Users/you/Downloads) or leave empty for temp",
        )

        if download_folder != st.session_state.selected_folder:
            st.session_state.selected_folder = download_folder

        if download_folder:
            if not os.path.exists(download_folder):
                st.error("❌ Selected folder does not exist!")
                download_folder = None
            elif not os.access(download_folder, os.W_OK):
                st.error("❌ No write permission for selected folder!")
                download_folder = None
            else:
                st.success(f"✅ Using folder: {download_folder}")
                st.info("Files will be saved directly to this folder and preserved.")
        else:
            st.info("No folder selected: a temporary local folder will be used.")
    else:
        download_folder = None
        st.info("On Cloud, only `/tmp` is writable. Files will be offered via a download button.")

    # Form
    with st.form("download_form"):
        youtube_url = st.text_input("🔗 Enter YouTube URL:")
        c1, c2 = st.columns(2)
        with c1:
            download_type = st.selectbox("📥 Download Type:", ["video", "audio"])
        with c2:
            if download_type == "video":
                quality_options = [None, 240, 360, 480, 720, 1080]
                quality = st.selectbox("🎬 Video Quality:", quality_options,
                                       format_func=lambda x: "Best" if x is None else f"{x}p")
            else:
                quality = None

        submit_button = st.form_submit_button("⬇️ Download")

    if submit_button:
        if not youtube_url.strip():
            st.error("⚠️ Please enter a YouTube URL")
        else:
            with st.spinner("Processing download..."):
                ok = download_content(
                    youtube_url.strip(),
                    download_type,
                    quality,
                    download_folder,
                    cookie_path=cookie_path
                )
            if ok:
                st.button("🔄 Download Another", on_click=st.rerun)

if __name__ == "__main__":
    main()
