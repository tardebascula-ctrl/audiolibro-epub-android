# main.py (versión corregida: NO inicializa TTS al arrancar para evitar SIGSEGV en jnius/TTS)

import sys
import traceback

def _install_crash_logger():
    def excepthook(exc_type, exc, tb):
        txt = "".join(traceback.format_exception(exc_type, exc, tb))

        # 1) Intenta escribir en app_storage (lo más fiable)
        try:
            from android.storage import app_storage_path
            base = app_storage_path()
            path = base + "/crash.log"
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            pass

        # 2) También a logcat (para verlo con adb logcat)
        try:
            import android
            android.logger.error(txt)
        except Exception:
            pass

    sys.excepthook = excepthook

_install_crash_logger()

import json
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty, ListProperty, NumericProperty, BooleanProperty

from plyer import filechooser


# ---------------------------
# UI
# ---------------------------
KV = r"""
#:import dp kivy.metrics.dp

<SetupScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(10)

        Label:
            text: "📖 Audiolibro EPUB (personal)"
            bold: True
            size_hint_y: None
            height: self.texture_size[1] + dp(8)

        Label:
            text: root.status_text
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Button:
                text: "Elegir EPUB"
                on_release: app.pick_epub()

            Label:
                text: app.selected_epub_name or "Ninguno"
                halign: "left"
                valign: "middle"
                text_size: self.size

        Label:
            text: "Voz"
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        Spinner:
            text: app.voice_selected or "Sistema (es-ES)"
            values: app.voice_names
            disabled: not app.voices_ready
            size_hint_y: None
            height: dp(44)
            on_text: app.set_voice(self.text)

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Label:
                text: "Velocidad"
                size_hint_x: None
                width: dp(90)

            Slider:
                min: 0.5
                max: 2.0
                value: app.rate
                on_value: app.set_rate(self.value)

            Label:
                text: "{:.2f}x".format(app.rate)
                size_hint_x: None
                width: dp(60)

        Button:
            text: "Empezar"
            size_hint_y: None
            height: dp(48)
            disabled: not app.can_start
            on_release: app.start_player()

        Label:
            text: "Tip: Ajustes Android → Texto a voz → instala Español (Google TTS)."
            font_size: "12sp"
            opacity: 0.8

<PlayerScreen>:
    BoxLayout:
        orientation: "vertical"
        padding: dp(12)
        spacing: dp(10)

        Label:
            text: app.book_title or "Sin libro"
            bold: True
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        Label:
            text: app.player_status
            size_hint_y: None
            height: self.texture_size[1] + dp(6)

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Button:
                text: "⟵ Setup"
                on_release: app.go_setup()

            Button:
                text: "▶ Play"
                on_release: app.play()

            Button:
                text: "⏸ Pausa"
                on_release: app.pause()

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Button:
                text: "⏮ Anterior"
                on_release: app.prev_chapter()

            Button:
                text: "⏭ Siguiente"
                on_release: app.next_chapter()

            Button:
                text: "↩ Reanudar"
                on_release: app.resume()

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)

            Label:
                text: "Velocidad"
                size_hint_x: None
                width: dp(90)

            Slider:
                min: 0.5
                max: 2.0
                value: app.rate
                on_value: app.set_rate(self.value)

            Label:
                text: "{:.2f}x".format(app.rate)
                size_hint_x: None
                width: dp(60)

        Label:
            text: "Vista previa (capítulo actual):"
            size_hint_y: None
            height: self.texture_size[1] + dp(4)

        TextInput:
            text: app.preview_text
            readonly: True
            multiline: True
"""


# ---------------------------
# EPUB parsing (stdlib-only)
# ---------------------------
class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data:
            self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def html_to_text(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    text = s.get_text()
    text = " ".join(text.split())
    return text


def _find_opf_path(z: zipfile.ZipFile) -> str:
    container = z.read("META-INF/container.xml")
    root = ET.fromstring(container)
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfiles = root.findall(".//c:rootfile", ns)
    if not rootfiles:
        rootfiles = root.findall(".//rootfile")
    for rf in rootfiles:
        fp = rf.attrib.get("full-path")
        if fp:
            return fp
    raise RuntimeError("No encuentro content.opf en container.xml")


def epub_to_chapters(epub_path: str):
    with zipfile.ZipFile(epub_path, "r") as z:
        opf_path = _find_opf_path(z)
        opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""

        opf_xml = z.read(opf_path)
        opf_root = ET.fromstring(opf_xml)

        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        title = "EPUB"
        t = opf_root.find(".//dc:title", ns)
        if t is not None and (t.text or "").strip():
            title = t.text.strip()

        manifest = {}
        for item in opf_root.findall(".//opf:manifest/opf:item", ns):
            _id = item.attrib.get("id")
            href = item.attrib.get("href")
            if _id and href:
                manifest[_id] = href

        spine_ids = []
        for itemref in opf_root.findall(".//opf:spine/opf:itemref", ns):
            rid = itemref.attrib.get("idref")
            if rid:
                spine_ids.append(rid)

        chapters = []
        for rid in spine_ids:
            href = manifest.get(rid)
            if not href:
                continue

            internal_path = f"{opf_dir}/{href}" if opf_dir else href
            internal_path = internal_path.replace("//", "/")

            try:
                raw = z.read(internal_path).decode("utf-8", errors="ignore")
            except KeyError:
                continue

            txt = html_to_text(raw)
            if len(txt) > 400:
                chapters.append(txt)

        return title, chapters


def chunk_text(text: str, max_len: int = 900):
    words = text.replace("\n", " ").split()
    out, buf, cur = [], [], 0
    for w in words:
        if cur + len(w) + 1 > max_len and buf:
            out.append(" ".join(buf).strip())
            buf, cur = [], 0
        buf.append(w)
        cur += len(w) + 1
    if buf:
        out.append(" ".join(buf).strip())
    return out


# ---------------------------
# Android TTS via PyJNIus (inicialización BAJO DEMANDA)
# ---------------------------
class AndroidTTS:
    def __init__(self):
        self.tts = None
        self.ready = False
        self.rate = 1.0

        try:
            from jnius import autoclass

            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Locale = autoclass("java.util.Locale")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")

            activity = PythonActivity.mActivity

            # ⚠️ En algunos dispositivos: listener / init puede causar SIGSEGV.
            # Creamos sin listener y NO tocamos getVoices.
            self.tts = TextToSpeech(activity, None)

            try:
                self.tts.setLanguage(Locale("es", "ES"))
            except Exception:
                pass

            self.ready = True
        except Exception:
            self.tts = None
            self.ready = False

    def set_rate(self, r: float):
        self.rate = max(0.5, min(2.0, float(r)))
        if self.tts and self.ready:
            try:
                self.tts.setSpeechRate(self.rate)
            except Exception:
                pass

    def speak(self, text: str) -> bool:
        if not self.tts or not self.ready:
            return False
        try:
            from jnius import autoclass
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            # utteranceId NO nulo (mejor para estabilidad)
            self.tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "utt1")
            return True
        except Exception:
            return False

    def stop(self):
        if self.tts and self.ready:
            try:
                self.tts.stop()
            except Exception:
                pass

    def list_voices(self):
        # ⚠️ getVoices rompe en algunos dispositivos → no lo usamos
        return []

    def set_voice_by_name(self, voice_name: str) -> bool:
        return False


# ---------------------------
# Screens
# ---------------------------
class SetupScreen(Screen):
    status_text = StringProperty("Listo. Elige EPUB y pulsa Empezar.")


class PlayerScreen(Screen):
    pass


# ---------------------------
# App
# ---------------------------
class AudioLibroApp(App):
    voice_names = ListProperty(["Sistema (es-ES)"])
    voices_ready = BooleanProperty(True)
    voice_selected = StringProperty("Sistema (es-ES)")
    rate = NumericProperty(1.0)

    selected_epub_path = StringProperty("")
    selected_epub_name = StringProperty("")
    can_start = BooleanProperty(False)

    book_title = StringProperty("")
    player_status = StringProperty("Carga un EPUB en Setup")
    preview_text = StringProperty("")

    def build(self):
        Builder.load_string(KV)

        self.sm = ScreenManager()
        self.setup = SetupScreen(name="setup")
        self.player = PlayerScreen(name="player")
        self.sm.add_widget(self.setup)
        self.sm.add_widget(self.player)

        # ✅ IMPORTANTE: NO inicializar TTS aquí (evita SIGSEGV a los 3–5s en algunos móviles)
        self.tts = None

        self.chapters = []
        self.chapter_idx = 0
        self.chunks = []
        self.chunk_idx = 0
        self.playing = False

        self._load_settings()
        self._refresh_can_start()

        return self.sm

    # ---------- TTS init under demand ----------
    def _ensure_tts(self) -> bool:
        if self.tts is None:
            self.tts = AndroidTTS()
            if self.tts and self.tts.ready:
                self.tts.set_rate(self.rate)
        return bool(self.tts and self.tts.ready)

    # ---------- persistence ----------
    def _settings_file(self):
        return Path(self.user_data_dir) / "settings.json"

    def _progress_file(self):
        return Path(self.user_data_dir) / "progress.json"

    def _load_settings(self):
        try:
            p = self._settings_file()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                self.rate = float(data.get("rate", 1.0))
                self.voice_selected = str(data.get("voice", "Sistema (es-ES)")) or "Sistema (es-ES)"
        except Exception:
            pass

    def _save_settings(self):
        try:
            data = {"rate": float(self.rate), "voice": self.voice_selected}
            self._settings_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _save_progress(self):
        try:
            data = {
                "epub_path": self.selected_epub_path,
                "chapter_idx": self.chapter_idx,
                "chunk_idx": self.chunk_idx,
            }
            self._progress_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _load_progress(self):
        try:
            p = self._progress_file()
            if not p.exists():
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ---------- setup ----------
    def set_voice(self, voice_name: str):
        # En esta versión no cambiamos voces por nombre para evitar llamadas peligrosas (getVoices/setVoice).
        if not voice_name:
            return
        self.voice_selected = voice_name
        self._save_settings()

    def set_rate(self, r: float):
        self.rate = float(r)
        if self.tts and self.tts.ready:
            self.tts.set_rate(self.rate)
        self._save_settings()

    def pick_epub(self):
        filechooser.open_file(
            filters=[("EPUB files", "*.epub"), ("All files", "*.*")],
            on_selection=self._on_file_selected,
        )

    def _on_file_selected(self, selection):
        if not selection:
            self.setup.status_text = "Selección cancelada."
            return
        path = selection[0]
        self.selected_epub_path = path
        self.selected_epub_name = Path(path).name
        self.setup.status_text = "EPUB seleccionado. Pulsa Empezar."
        self._refresh_can_start()

    def _refresh_can_start(self):
        self.can_start = bool(self.selected_epub_path)

    # ---------- player ----------
    def start_player(self):
        self.player_status = "Leyendo EPUB..."
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.playing = False

        try:
            title, chapters = epub_to_chapters(self.selected_epub_path)
        except Exception as e:
            self.player_status = f"Error leyendo EPUB: {e}"
            self.sm.current = "player"
            return

        if not chapters:
            self.player_status = "No encontré capítulos útiles en ese EPUB."
            self.sm.current = "player"
            return

        self.book_title = title
        self.chapters = chapters
        self.chapter_idx = 0
        self.chunk_idx = 0
        self._load_current_chunks()

        prev = self._load_progress()
        if prev and prev.get("epub_path") == self.selected_epub_path:
            self.chapter_idx = int(prev.get("chapter_idx", 0))
            self.chunk_idx = int(prev.get("chunk_idx", 0))
            self._load_current_chunks()
            self.player_status = f"Progreso cargado · Cap {self.chapter_idx+1} · Parte {self.chunk_idx+1}"
        else:
            self.player_status = f"Capítulos: {len(self.chapters)} · En {self.chapter_idx+1}"

        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.sm.current = "player"

    def go_setup(self):
        self.pause()
        self.sm.current = "setup"

    def _current_text(self):
        return self.chapters[self.chapter_idx] if self.chapters else ""

    def _load_current_chunks(self):
        self.chunks = chunk_text(self._current_text())
        if self.chunks:
            self.chunk_idx = max(0, min(self.chunk_idx, len(self.chunks) - 1))

    def _speak_current_chunk(self):
        if not self.chapters:
            self.player_status = "Sin EPUB."
            return

        # ✅ Inicializa TTS justo antes de hablar
        if not self._ensure_tts():
            self.player_status = "TTS no disponible. Instala Google TTS y Español (España)."
            self.playing = False
            return

        # Aplica velocidad actual
        self.tts.set_rate(self.rate)

        if not self.chunks:
            self._load_current_chunks()
        if not self.chunks:
            self.player_status = "Capítulo vacío."
            return

        ok = self.tts.speak(self.chunks[self.chunk_idx])
        if not ok:
            self.player_status = "No puedo iniciar TTS (Ajustes → Texto a voz)."
            self.playing = False
            return

        self.playing = True
        self._save_progress()
        self.player_status = f"▶ Cap {self.chapter_idx+1}/{len(self.chapters)} · Parte {self.chunk_idx+1}/{len(self.chunks)}"

    def play(self):
        self._speak_current_chunk()

    def pause(self):
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.playing = False
        self._save_progress()
        self.player_status = "⏸ Pausado"

    def resume(self):
        self._speak_current_chunk()

    def next_chapter(self):
        if not self.chapters:
            return
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.chapter_idx = min(self.chapter_idx + 1, len(self.chapters) - 1)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.player_status = f"Cap {self.chapter_idx+1}/{len(self.chapters)}"

    def prev_chapter(self):
        if not self.chapters:
            return
        if self.tts and self.tts.ready:
            self.tts.stop()
        self.chapter_idx = max(self.chapter_idx - 1, 0)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.player_status = f"Cap {self.chapter_idx+1}/{len(self.chapters)}"

    # (Este callback queda por si luego implementas utteranceId + listener, pero ahora no lo usamos)
    def _on_utterance_done(self, utterance_id: str):
        if not self.playing:
            return
        if utterance_id.startswith("chunk_"):
            try:
                idx = int(utterance_id.split("_", 1)[1])
            except Exception:
                idx = self.chunk_idx
            self.chunk_idx = idx + 1

        if self.chunk_idx < len(self.chunks):
            Clock.schedule_once(lambda *_: self._speak_current_chunk(), 0)
        else:
            self.playing = False
            self._save_progress()
            self.player_status = "✅ Capítulo terminado (pulsa Siguiente o Play)"


if __name__ == "__main__":
    AudioLibroApp().run()
