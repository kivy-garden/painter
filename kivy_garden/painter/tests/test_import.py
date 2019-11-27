import pytest


def test_flower():
    from kivy_garden.painter import PaintCanvasBehavior
    from kivy.uix.widget import Widget

    class Painter(PaintCanvasBehavior, Widget):
        pass

    painter = Painter()
    painter.create_add_shape(
        'polygon', points=[0, 0, 300, 0, 300, 800, 0, 800])
