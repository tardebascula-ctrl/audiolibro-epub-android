from kivy.app import App
from kivy.uix.label import Label

class AudioLibro(App):
    def build(self):
        return Label(text="Audiolibro EPUB funcionando")

AudioLibro().run()
