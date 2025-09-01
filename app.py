import os
import streamlit as st
from pathlib import Path
import platform
import yt_dlp
import logging
import subprocess

# Configure logging for cloud environment
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Cloud detection ----------
def _running_in_cloud() -> bool:
    # Headless usually true on Streamlit Cloud; also home is not writable there.
    headless = os.environ.get("STREAMLIT_SERVER_HEADLESS", "").lower() in ("true", "1", "yes")
    home_writable = os.access(Path.home(), os.W_OK)
    return headless or not home_writable

IS_CLOUD_DEPLOYMENT = _running_in_cloud()

# ---------- Paths ----------
def validate_path(path: str) -> Path:
    """Validate and return a safe path for downloads."""
    try:
        if IS_CLOUD_DEPLOYMENT:
            return Path("/tmp")  # only writable on Streamlit Cloud
        # local: use provided path if valid, else a relative fallback
        return Path(path) if path else Path("downloads")
    except Exception:
        return Path("downloads")

# ---------- FFmpeg ----------
def check_ffmpeg():
    """Check if ffmpeg is installed and accessible."""
    try:
        # Check ffmpeg on PATH
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return 'ffmpeg'
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Windows: look for local copies
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
    """Show instructions for installing ffmpeg."""
    if IS_CLOUD_DEPLOYMENT:
        # On Streamlit Cloud, include ffmpeg in packages.txt; this should rarely show.
        st.error("❌ FFmpeg not available in the cloud container. Ensure `packages.txt` contains a line with `ffmpeg` and redeploy.")
        st.stop()

    st.error("❌ FFmpeg is required but not found!")
    system = platform.system()
    if system == "Windows":
        st.markdown("""
        ### FFmpeg Installation (Windows)
        **Option 1: Direct Download**
        1. Download: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
        2. Extract and copy `bin/ffmpeg.exe` next to this app

        **Option 2: Chocolatey**
        ```powershell
        choco install ffmpeg
        ```
        """)
    elif system == "Darwin":
        st.markdown("""
        ### FFmpeg Installation (macOS)
        **Homebrew (recommended)**
        ```bash
        brew install ffmpeg
        ```
        **MacPorts**
        ```bash
        sudo port install ffmpeg
        ```
        """)
    else:
        st.markdown("""
        ### FFmpeg Installation (Linux)
        **Ubuntu/Debian**
        ```bash
        sudo apt update && sudo apt install -y ffmpeg
        ```
        **Fedora**
        ```bash
        sudo dnf install -y ffmpeg
        ```
        **Arch**
        ```bash
        sudo pacman -S ffmpeg
        ```
        """)
    st.markdown("After installing, restart the app.")
    st.stop()

# ---------- Download ----------
def download_content(url: str, output_path: str, download_type: str = 'video',
                     quality: int = None, download_folder: str = None):
    """Download video or audio content."""
    ffmpeg_path = check_ffmpeg()
    if not ffmpeg_path:
        show_ffmpeg_instructions()
        return False

    try:
        # Decide target dir
        if IS_CLOUD_DEPLOYMENT:
            temp_dir = Path("/tmp")
            download_folder = None  # force temp-mode UI behavior
        else:
            if download_folder and os.path.isdir(download_folder):
                temp_dir = Path(download_folder)
            else:
                temp_dir = Path("temp_downloads")

        temp_dir.mkdir(parents=True, exist_ok=True)

        # Progress tracking
        progress_bar = st.progress(0.0)
        status_text = st.empty()
        downloaded_file = None

        # yt-dlp options
        ydl_opts = {
            'outtmpl': str(temp_dir / '%(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'progress': True,
            'prefer_ffmpeg': True,
        }
        # Only set ffmpeg_location if it's a specific path
        if ffmpeg_path != 'ffmpeg':
            ydl_opts['ffmpeg_location'] = ffmpeg_path

        # Formats
        if download_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:  # video
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

        def cleanup_temp_files():
            """Clean up only local temp directory, never /tmp on cloud or user-selected folder."""
            try:
                # Only clean our local temp folder
                if not IS_CLOUD_DEPLOYMENT and temp_dir.name == "temp_downloads":
                    for f in temp_dir.glob('*'):
                        f.unlink(missing_ok=True)
                    temp_dir.rmdir()
            except Exception as e:
                logger.warning(f"Cleanup error: {e}")

        def progress_hook(d):
            nonlocal downloaded_file
            try:
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    downloaded = d.get('downloaded_bytes', 0)
                    if total:
                        progress = min(downloaded / total, 1.0)
                        progress_bar.progress(progress)
                    filename = os.path.basename(d.get('filename', ''))
                    status_text.text(f"⏳ Downloading: {filename}")
                elif d['status'] == 'finished':
                    downloaded_file = d.get('filename', '')
                    filename = os.path.basename(downloaded_file or '')
                    status_text.text(f"✅ Processing: {filename}")
                    progress_bar.progress(1.0)
            except Exception as e:
                logger.warning(f"Progress hook error: {e}")

        ydl_opts['progress_hooks'] = [progress_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', 'Unknown')
                st.write(f"📥 Starting download for: {title}")
                ydl.download([url])

                if downloaded_file and os.path.exists(downloaded_file):
                    file_size = os.path.getsize(downloaded_file) / (1024 * 1024)

                    if download_folder and not IS_CLOUD_DEPLOYMENT:
                        st.success(f"✅ Download completed! File saved to: {download_folder}")
                        st.info(f"📁 File: {os.path.basename(downloaded_file)} ({file_size:.1f} MB)")
                    else:
                        # Stream via file object (memory-friendly)
                        fobj = open(downloaded_file, 'rb')
                        st.download_button(
                            label=f"⬇️ Download {os.path.basename(downloaded_file)} ({file_size:.1f} MB)",
                            data=fobj,
                            file_name=os.path.basename(downloaded_file),
                            mime='application/octet-stream'
                        )
                    return True

            return False

        finally:
            # Do NOT clean up on cloud (/tmp) immediately; user needs to click the button.
            # Only clean local temp folder we created ("temp_downloads").
            if not IS_CLOUD_DEPLOYMENT and (download_folder is None or not os.path.isdir(download_folder)):
                cleanup_temp_files()

    except Exception as e:
        st.error(f"❌ Download failed: {str(e)}")
        logger.error(f"Download error: {e}")
        return False

# ---------- UI ----------
def main():
    st.set_page_config(page_title="YouTube Downloader", page_icon="🎥", layout="wide")
    st.title("🎥 YouTube Downloader")

    if IS_CLOUD_DEPLOYMENT:
        st.markdown("Download videos or extract audio from YouTube\n\n☁️ **Cloud Mode** — files are prepared in `/tmp` and offered as a download.")
    else:
        st.markdown("Download videos or extract audio from YouTube\n\n💻 **Local Mode** — choose a download folder or use a temporary local folder.")

    # Folder selection ONLY in local mode
    if not IS_CLOUD_DEPLOYMENT:
        st.subheader("📁 Download Folder Selection")

        if 'selected_folder' not in st.session_state:
            st.session_state.selected_folder = ""

        common_folders = {
            "Downloads": os.path.expanduser("~/Downloads"),
            "Desktop": os.path.expanduser("~/Desktop"),
            "Documents": os.path.expanduser("~/Documents"),
            "Custom Path": ""
        }

        st.write("**Quick Select:**")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("📁 Downloads"):
                st.session_state.selected_folder = common_folders["Downloads"]; st.rerun()
        with col2:
            if st.button("🖥️ Desktop"):
                st.session_state.selected_folder = common_folders["Desktop"]; st.rerun()
        with col3:
            if st.button("📄 Documents"):
                st.session_state.selected_folder = common_folders["Documents"]; st.rerun()
        with col4:
            if st.button("🗂️ Custom"):
                st.session_state.selected_folder = ""; st.rerun()

        download_folder = st.text_input(
            "📂 Download Folder Path:",
            value=st.session_state.selected_folder,
            placeholder="Enter full path (e.g., /Users/username/Downloads) or leave empty for temporary location",
            help="Enter the full path to your desired download folder"
        )

        if download_folder != st.session_state.selected_folder:
            st.session_state.selected_folder = download_folder

        # Validate only in local mode
        if download_folder:
            if not os.path.exists(download_folder):
                st.error("❌ Selected folder does not exist!")
                download_folder = None
            elif not os.access(download_folder, os.W_OK):
                st.error("❌ No write permission for selected folder!")
                download_folder = None
            else:
                st.success(f"✅ Using folder: {download_folder}")
                st.info("💡 Files will be saved directly to this folder and preserved.")
        else:
            st.info("💡 No folder selected: a temporary local folder will be used.")
    else:
        # Cloud mode: no folder UI
        download_folder = None
        st.info("☁️ Files are saved to `/tmp` during your session and offered via a download button.")

    # Form
    with st.form("download_form"):
        youtube_url = st.text_input("🔗 Enter YouTube URL:")
        col1, col2 = st.columns(2)
        with col1:
            download_type = st.selectbox("📥 Download Type:", ["video", "audio"])
        with col2:
            if download_type == "video":
                quality_options = [None, 240, 360, 480, 720, 1080]
                quality = st.selectbox("🎬 Video Quality:", quality_options,
                                       format_func=lambda x: "Best" if x is None else f"{x}p")
            else:
                quality = None

        submit_button = st.form_submit_button("⬇️ Download")

    if submit_button:
        if not youtube_url:
            st.error("⚠️ Please enter a YouTube URL")
        else:
            with st.spinner("Processing download..."):
                success = download_content(
                    youtube_url,
                    "temp_downloads",
                    download_type,
                    quality,
                    download_folder
                )
            if success:
                st.button("🔄 Download Another", on_click=st.rerun)

if __name__ == "__main__":
    main()
