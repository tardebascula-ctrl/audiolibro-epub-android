import re
import json
from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import mainthread, Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.metrics import dp
from kivy.properties import StringProperty, ListProperty, NumericProperty, BooleanProperty

from plyer import filechooser

from ebooklib import epub
from bs4 import BeautifulSoup


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
            id: voice_spinner
            text: app.voice_selected or "Cargando voces..."
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
                id: speed
                min: 0.5
                max: 2.0
                value: app.rate
                on_value: app.set_rate(self.value)

            Label:
                text: "{:.2f}x".format(speed.value)
                size_hint_x: None
                width: dp(60)

        Button:
            text: "Empezar"
            size_hint_y: None
            height: dp(48)
            disabled: not app.can_start
            on_release: app.start_player()

        Label:
            text: "Tip: Instala/activa Google TTS en Español si no aparecen voces."
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
# EPUB parsing
# ---------------------------
def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def epub_to_chapters(epub_path: str):
    book = epub.read_epub(epub_path)
    chapters = []
    for item in book.get_items():
        if item.get_type() == 9:  # ITEM_DOCUMENT
            raw = item.get_content().decode("utf-8", errors="ignore")
            txt = html_to_text(raw)
            if len(txt) > 400:
                chapters.append(txt)

    title = "EPUB"
    try:
        metas = book.get_metadata("DC", "title")
        if metas and metas[0] and metas[0][0]:
            title = metas[0][0]
    except Exception:
        pass

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
# Android TTS via PyJNIus
# ---------------------------
class AndroidTTS:
    def __init__(self, on_done=None):
        self.tts = None
        self.ready = False
        self.rate = 1.0
        self.on_done = on_done

        try:
            from jnius import autoclass, PythonJavaClass, java_method
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            Locale = autoclass("java.util.Locale")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")

            class OnInitListener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
                __javacontext__ = "app"

                def __init__(self, outer):
                    super().__init__()
                    self.outer = outer

                @java_method("(I)V")
                def onInit(self, status):
                    if status == 0:
                        self.outer.ready = True
                        try:
                            self.outer.tts.setLanguage(Locale("es", "ES"))
                            self.outer.tts.setSpeechRate(float(self.outer.rate))
                        except Exception:
                            pass

            class ProgListener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/UtteranceProgressListener"]
                __javacontext__ = "app"

                def __init__(self, outer):
                    super().__init__()
                    self.outer = outer

                @java_method("(Ljava/lang/String;)V")
                def onStart(self, utteranceId):
                    return

                @java_method("(Ljava/lang/String;)V")
                def onDone(self, utteranceId):
                    if self.outer.on_done:
                        self.outer.on_done(str(utteranceId))

                @java_method("(Ljava/lang/String;)V")
                def onError(self, utteranceId):
                    if self.outer.on_done:
                        self.outer.on_done(str(utteranceId))

            activity = PythonActivity.mActivity
            self.tts = TextToSpeech(activity, OnInitListener(self))
            self.tts.setOnUtteranceProgressListener(ProgListener(self))

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

    def speak(self, text: str, utterance_id: str):
        if not self.tts or not self.ready:
            return False
        try:
            self.tts.speak(text, 0, None, utterance_id)  # QUEUE_FLUSH
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
        if not self.tts or not self.ready:
            return []
        try:
            voices = self.tts.getVoices().toArray()
            out = []
            for v in voices:
                loc = v.getLocale()
                lang = str(loc.getLanguage())   # "es"
                country = str(loc.getCountry()) # "ES", "US", ...
                name = str(v.getName())
                out.append({"name": name, "lang": lang, "country": country})
            return out
        except Exception:
            return []

    def set_voice_by_name(self, voice_name: str) -> bool:
        if not self.tts or not self.ready:
            return False
        try:
            voices = self.tts.getVoices().toArray()
            for v in voices:
                if str(v.getName()) == voice_name:
                    self.tts.setVoice(v)
                    return True
        except Exception:
            pass
        return False


# ---------------------------
# Screens
# ---------------------------
class SetupScreen(Screen):
    status_text = StringProperty("Cargando voces...")

class PlayerScreen(Screen):
    pass


# ---------------------------
# App
# ---------------------------
class AudioLibroApp(App):
    # Setup state
    voice_names = ListProperty([])
    voices_ready = BooleanProperty(False)
    voice_selected = StringProperty("")
    rate = NumericProperty(1.0)

    selected_epub_path = StringProperty("")
    selected_epub_name = StringProperty("")

    can_start = BooleanProperty(False)

    # Player state
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

        # runtime state
        self.tts = AndroidTTS(on_done=self._on_utterance_done)
        self.chapters = []
        self.chapter_idx = 0
        self.chunks = []
        self.chunk_idx = 0
        self.playing = False

        # load persisted settings
        self._load_settings()

        # populate voices after TTS becomes ready
        Clock.schedule_interval(self._try_load_voices, 0.5)
        self._refresh_can_start()

        return self.sm

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
                self.voice_selected = str(data.get("voice", "")) or ""
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
    def _try_load_voices(self, *_):
        if self.voices_ready:
            return False
        if not self.tts.ready:
            self.setup.status_text = "Esperando a TTS..."
            return True

        self.tts.set_rate(self.rate)

        voices = self.tts.list_voices()
        # preferimos español, pero mostramos todo (por si el motor no etiqueta bien)
        es = [v for v in voices if v["lang"] == "es"]
        ordered = es if es else voices

        self.voice_names = [v["name"] for v in ordered]
        self.voices_ready = True

        if not self.voice_selected and self.voice_names:
            self.voice_selected = self.voice_names[0]

        if self.voice_selected:
            self.tts.set_voice_by_name(self.voice_selected)

        self.setup.status_text = "Listo: elige EPUB, voz y velocidad."
        self._save_settings()
        self._refresh_can_start()
        return False

    def set_voice(self, voice_name: str):
        if not voice_name or not self.tts.ready:
            return
        ok = self.tts.set_voice_by_name(voice_name)
        if ok:
            self.voice_selected = voice_name
            self._save_settings()

    def set_rate(self, r: float):
        self.rate = float(r)
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
        self.can_start = bool(self.voices_ready and self.selected_epub_path)

    # ---------- player ----------
    def start_player(self):
        # parse epub, load chapters, apply progress if same file
        self.player_status = "Leyendo EPUB..."
        self.tts.stop()
        self.playing = False

        title, chapters = epub_to_chapters(self.selected_epub_path)
        if not chapters:
            self.player_status = "No encontré capítulos útiles en ese EPUB."
            return

        self.book_title = title
        self.chapters = chapters
        self.chapter_idx = 0
        self.chunk_idx = 0
        self._load_current_chunks()

        # restore progress if same epub
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
        if not self.tts.ready:
            self.player_status = "TTS no listo (instala voz en español)."
            return
        if self.voice_selected:
            self.tts.set_voice_by_name(self.voice_selected)

        if not self.chunks:
            self._load_current_chunks()
        if not self.chunks:
            self.player_status = "Capítulo vacío."
            return

        ok = self.tts.speak(self.chunks[self.chunk_idx], utterance_id=f"chunk_{self.chunk_idx}")
        if not ok:
            self.player_status = "No puedo iniciar TTS (revísalo en Ajustes del móvil)."
            self.playing = False
            return

        self.playing = True
        self._save_progress()
        self.player_status = f"▶ Cap {self.chapter_idx+1}/{len(self.chapters)} · Parte {self.chunk_idx+1}/{len(self.chunks)}"

    def play(self):
        self._speak_current_chunk()

    def pause(self):
        self.tts.stop()
        self.playing = False
        self._save_progress()
        self.player_status = "⏸ Pausado"

    def resume(self):
        self._speak_current_chunk()

    def next_chapter(self):
        if not self.chapters:
            return
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
        self.tts.stop()
        self.chapter_idx = max(self.chapter_idx - 1, 0)
        self.chunk_idx = 0
        self._load_current_chunks()
        self.preview_text = self._current_text()[:2000]
        self._save_progress()
        self.player_status = f"Cap {self.chapter_idx+1}/{len(self.chapters)}"

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
