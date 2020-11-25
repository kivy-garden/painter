.. _examples:

********
Examples
********

To test, run the example code and start drawing shapes.


Basic Example
-------------

Following is a simple example:

.. code-block:: python

    from kivy.uix.widget import Widget
    from kivy.app import runTouchApp
    from kivy.lang import Builder
    from kivy.uix.behaviors.focus import FocusBehavior

    class PainterWidget(PaintCanvasBehavior, FocusBehavior, Widget):

        def create_shape_with_touch(self, touch):
            shape = super(PainterWidget, self).create_shape_with_touch(touch)
            if shape is not None:
                shape.add_shape_to_canvas(self)
            return shape

        def add_shape(self, shape):
            if super(PainterWidget, self).add_shape(shape):
                shape.add_shape_to_canvas(self)
                return True
            return False


    runTouchApp(Builder.load_string('''
    BoxLayout:
        orientation: 'vertical'
        PainterWidget:
            draw_mode: mode.text or 'freeform'
            locked: lock.state == 'down'
            multiselect: multiselect.state == 'down'
        BoxLayout:
            size_hint_y: None
            height: "50dp"
            spacing: '20dp'
            Spinner:
                id: mode
                values: ['circle', 'ellipse', 'polygon', 'freeform', 'none']
                text: 'freeform'
            ToggleButton:
                id: lock
                text: "Lock"
            ToggleButton:
                id: multiselect
                text: "Multi-select"
    '''))

