from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label

import os
import shutil

from android import activity
from jnius import autoclass, cast

REQUEST_CODE = 42


class Root(BoxLayout):

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)

        self.label = Label(text="Pulsa 'Elegir EPUB'", size_hint_y=.8)
        self.add_widget(self.label)

        btn = Button(text="Elegir EPUB", size_hint_y=.2)
        btn.bind(on_press=self.pick_epub)
        self.add_widget(btn)

        activity.bind(on_activity_result=self.on_activity_result)

    def pick_epub(self, *args):

        Intent = autoclass("android.content.Intent")
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.setType("application/epub+zip")
        intent.addCategory(Intent.CATEGORY_OPENABLE)

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        currentActivity = PythonActivity.mActivity

        currentActivity.startActivityForResult(intent, REQUEST_CODE)

    def on_activity_result(self, request_code, result_code, intent):

        if request_code != REQUEST_CODE:
            return

        if intent is None:
            self.label.text = "No se seleccionó archivo"
            return

        uri = intent.getData()

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity

        resolver = activity.getContentResolver()
        stream = resolver.openInputStream(uri)

        # carpeta interna de la app
        path = activity.getFilesDir().getAbsolutePath()
        local_file = os.path.join(path, "libro.epub")

        with open(local_file, "wb") as f:
            buf = bytearray(4096)
            while True:
                read = stream.read(buf)
                if read == -1:
                    break
                f.write(buf[:read])

        stream.close()

        self.label.text = f"EPUB cargado:\n{local_file}"


class AudioLibroApp(App):
    def build(self):
        return Root()


if __name__ == "__main__":
    AudioLibroApp().run()
