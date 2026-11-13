
import os
import sys
import json
import time
import socket
import shutil
import subprocess
from pathlib import Path

from PySide6 import QtCore, QtWidgets


APP_NAME = "Audiat"
APP_DIR = Path.home() / ".local" / "share" / "audiat"
APP_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS = QtCore.QSettings("Audiat", "Audiat")


def find_program(name):
    if name == "mpv":
        candidates = [
            os.environ.get("AUDIAT_MPV"),
            os.path.join(os.environ.get("APPDIR", ""), "usr/bin/mpv"),
        ]
        for path in candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return path

    return shutil.which(name)


def ytdlp_command():
    executable = find_program("yt-dlp")
    if executable:
        return [executable]

    return [sys.executable, "-m", "yt_dlp"]


class Worker(QtCore.QThread):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, function, *args):
        super().__init__()
        self.function = function
        self.args = args

    def run(self):
        try:
            self.completed.emit(self.function(*self.args))
        except Exception as error:
            self.failed.emit(str(error))


def search_youtube(query):
    command = ytdlp_command() + [
        "--dump-json",
        "--flat-playlist",
        f"ytsearch10:{query}"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90
    )

    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Search failed.")

    videos = []

    for line in result.stdout.splitlines():
        try:
            data = json.loads(line)
            video_id = data.get("id")

            if not video_id:
                continue

            videos.append({
                "title": data.get("title", "Unknown title"),
                "url": data.get(
                    "webpage_url",
                    f"https://www.youtube.com/watch?v={video_id}"
                )
            })
        except json.JSONDecodeError:
            continue

    return videos


def download_audio(url, directory):
    command = ytdlp_command() + [
        "--no-playlist",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "-o", str(Path(directory) / "%(title)s.%(ext)s"),
        url
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600
    )

    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Download failed.")

    return "Download complete!"


class Audiat(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Audiat")
        self.resize(650, 500)

        self.download_directory = SETTINGS.value(
            "download_directory",
            str(Path.home() / "Music")
        )

        self.worker = None
        self.results = []
        self.mpv = None
        self.mpv_socket = None

        self.socket_path = (
            f"/tmp/audiat-{os.getuid()}-{os.getpid()}.sock"
        )

        self.build_ui()
        self.update_directory()
        self.refresh_library()

    def build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)

        # Download tab
        download_tab = QtWidgets.QWidget()
        download_layout = QtWidgets.QVBoxLayout(download_tab)

        search_layout = QtWidgets.QHBoxLayout()

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search YouTube...")
        self.search_input.returnPressed.connect(self.search)

        self.search_button = QtWidgets.QPushButton("Search")
        self.search_button.clicked.connect(self.search)

        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_button)

        download_layout.addLayout(search_layout)

        self.results_list = QtWidgets.QListWidget()
        download_layout.addWidget(self.results_list)

        self.directory_label = QtWidgets.QLabel()
        download_layout.addWidget(self.directory_label)

        # Download stays above Choose Directory.
        self.download_button = QtWidgets.QPushButton("Download MP3")
        self.download_button.clicked.connect(self.download)
        download_layout.addWidget(self.download_button)

        self.directory_button = QtWidgets.QPushButton("Choose Directory")
        self.directory_button.clicked.connect(self.choose_directory)
        download_layout.addWidget(self.directory_button)

        self.tabs.addTab(download_tab, "Download")

        # Player tab
        player_tab = QtWidgets.QWidget()
        player_layout = QtWidgets.QVBoxLayout(player_tab)

        self.library = QtWidgets.QListWidget()
        self.library.itemDoubleClicked.connect(self.play_song)
        player_layout.addWidget(self.library)

        refresh_button = QtWidgets.QPushButton("Refresh Library")
        refresh_button.clicked.connect(self.refresh_library)
        player_layout.addWidget(refresh_button)

        controls = QtWidgets.QHBoxLayout()

        play_button = QtWidgets.QPushButton("Play")
        pause_button = QtWidgets.QPushButton("Pause")
        stop_button = QtWidgets.QPushButton("Stop")

        play_button.clicked.connect(self.play_selected)
        pause_button.clicked.connect(self.pause_song)
        stop_button.clicked.connect(self.stop_song)

        controls.addWidget(play_button)
        controls.addWidget(pause_button)
        controls.addWidget(stop_button)

        player_layout.addLayout(controls)
        self.tabs.addTab(player_tab, "Player")

        self.status = QtWidgets.QLabel("Ready")
        layout.addWidget(self.status)

    def update_directory(self):
        self.directory_label.setText(
            f"Download directory: {self.download_directory}"
        )

    def choose_directory(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose Download Directory",
            self.download_directory
        )

        if directory:
            self.download_directory = directory
            SETTINGS.setValue("download_directory", directory)
            self.update_directory()

    def run_worker(self, function, callback, *args):
        if self.worker and self.worker.isRunning():
            return

        self.worker = Worker(function, *args)
        self.worker.completed.connect(callback)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.worker_finished)

        self.set_busy(True)
        self.worker.start()

    def set_busy(self, busy):
        self.search_button.setEnabled(not busy)
        self.download_button.setEnabled(not busy)
        self.status.setText("Working..." if busy else "Ready")

    def worker_finished(self):
        self.set_busy(False)

    def show_error(self, message):
        self.status.setText("Error")
        QtWidgets.QMessageBox.critical(self, "Audiat", message)

    def search(self):
        query = self.search_input.text().strip()

        if not query:
            return

        self.results_list.clear()
        self.status.setText("Searching...")

        self.run_worker(
            search_youtube,
            self.show_results,
            query
        )

    def show_results(self, videos):
        self.results = videos
        self.results_list.clear()

        for video in videos:
            item = QtWidgets.QListWidgetItem(video["title"])
            item.setData(QtCore.Qt.ItemDataRole.UserRole, video["url"])
            self.results_list.addItem(item)

        self.status.setText(f"Found {len(videos)} results")

    def download(self):
        item = self.results_list.currentItem()

        if not item:
            QtWidgets.QMessageBox.information(
                self, "Audiat", "Select a video first."
            )
            return

        Path(self.download_directory).mkdir(
            parents=True,
            exist_ok=True
        )

        url = item.data(QtCore.Qt.ItemDataRole.UserRole)

        self.run_worker(
            download_audio,
            self.download_finished,
            url,
            self.download_directory
        )

    def download_finished(self, message):
        self.status.setText(message)
        self.refresh_library()

        QtWidgets.QMessageBox.information(
            self, "Audiat", message
        )

    def refresh_library(self):
        self.library.clear()

        directory = Path(self.download_directory)

        if not directory.exists():
            return

        for file in sorted(directory.glob("*.mp3")):
            item = QtWidgets.QListWidgetItem(file.name)
            item.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                str(file)
            )
            self.library.addItem(item)

    # -------------------------
    # mpv playback
    # -------------------------

    def start_mpv(self):
        executable = find_program("mpv")

        if not executable:
            raise RuntimeError(
                "Could not find mpv. Install mpv or bundle it with Audiat."
            )

        if self.mpv and self.mpv.poll() is None:
            return

        self.stop_mpv_process()
        Path(self.socket_path).unlink(missing_ok=True)

        log_path = APP_DIR / "mpv.log"

        with open(log_path, "w") as log:
            self.mpv = subprocess.Popen(
                [
                    executable,
                    "--idle=yes",
                    "--no-video",
                    "--no-terminal",
                    "--force-window=no",
                    f"--input-ipc-server={self.socket_path}"
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log
            )

        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            if self.mpv.poll() is not None:
                break

            try:
                connection = socket.socket(
                    socket.AF_UNIX,
                    socket.SOCK_STREAM
                )
                connection.settimeout(0.5)
                connection.connect(self.socket_path)
                connection.close()
                return
            except OSError:
                try:
                    connection.close()
                except (OSError, UnboundLocalError):
                    pass

                time.sleep(0.1)

        log_text = log_path.read_text(errors="replace")[-3000:]

        self.stop_mpv_process()

        raise RuntimeError(
            "mpv failed to start its playback interface.\n\n"
            + log_text
        )

    def mpv_command(self, command):
        self.start_mpv()

        try:
            with socket.socket(
                socket.AF_UNIX,
                socket.SOCK_STREAM
            ) as connection:
                connection.settimeout(2)
                connection.connect(self.socket_path)
                connection.sendall(
                    (json.dumps({"command": command}) + "\n").encode()
                )
        except OSError as error:
            self.stop_mpv_process()
            raise RuntimeError(f"mpv communication failed: {error}")

    def play_song(self, item):
        filepath = item.data(QtCore.Qt.ItemDataRole.UserRole)

        if not filepath or not os.path.isfile(filepath):
            return

        try:
            self.mpv_command(["loadfile", filepath, "replace"])
            self.status.setText(f"Playing: {item.text()}")
        except Exception as error:
            self.show_error(str(error))

    def play_selected(self):
        item = self.library.currentItem()

        if item:
            self.play_song(item)

    def pause_song(self, checked=False):
        try:
            self.mpv_command(["cycle", "pause"])
        except Exception as error:
            self.show_error(str(error))

    def stop_song(self, checked=False):
        try:
            self.mpv_command(["stop"])
        except Exception as error:
            self.show_error(str(error))

    def stop_mpv_process(self):
        if self.mpv:
            if self.mpv.poll() is None:
                self.mpv.terminate()

                try:
                    self.mpv.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.mpv.kill()
                    self.mpv.wait()

            self.mpv = None

        Path(self.socket_path).unlink(missing_ok=True)

    def closeEvent(self, event):
        self.stop_mpv_process()
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = Audiat()
    window.show()
    sys.exit(app.exec())
