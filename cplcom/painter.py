
from functools import partial

from kivy.uix.widget import Widget
from kivy.uix.behaviors.focus import FocusBehavior
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import OptionProperty, BooleanProperty
from kivy.graphics import Ellipse


class PaintCanvas(FocusBehavior, Widget):
    ''':attr:`shapes`, :attr:`selected_shapes`, :attr:`draw_mode`,
    :attr:`current_shape`, :attr:`locked`, :attr:`select`, and
    :attr:`selection_shape` are the attributes that make up the state machine.
    '''

    shapes = []

    selected_shapes = []

    draw_mode = OptionProperty('polygon', options=[
        'circle', 'ellipse', 'polygon', 'freeform', 'bezier'])

    current_shape = None
    '''Holds shape currently being edited. Can be a finished shape, e.g. if
    a point is selected.

    Either :attr:`current_shape` or :attr:`selection_shape` or both must be
    None.
    '''

    selected_point_shape = None

    locked = BooleanProperty(False)
    '''When locked, only selection is allowed. We cannot select points
    (except in :attr:`selection_shape`) and :attr:`current_shape` must
    be None.
    '''

    select = BooleanProperty(False)
    '''When selecting, instead of creating new shapes, the new shape will
    act as a selection area.
    '''

    selection_shape = None

    add_selection = BooleanProperty(True)

    min_touch_dist = dp(10)

    long_touch_delay = 1.

    _long_touch_trigger = None

    _ctrl_down = None

    def __init__(self, **kwargs):
        super(PaintCanvas, self).__init__(**kwargs)
        self.shapes = []
        self.selected_shapes = []
        self._ctrl_down = set()

    def on_locked(self, *largs):
        if not self.locked:
            return
        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        self.clear_selected_point_shape(False)
        self.finish_current_shape()
        for shape in self.shapes:
            shape.clean()

    def on_select(self, *largs):
        if self.select:
            self.finish_current_shape()
        else:
            self.finish_selection_shape()

    def on_draw_mode(self, *largs):
        self.finish_selection_shape()
        self.finish_current_shape()

    def finish_current_shape(self):
        '''Returns True if there was a unfinished shape that was finished.
        '''
        shape = self.current_shape
        if shape:
            res = False
            if self.selected_point_shape is shape:
                res = self.clear_selected_point_shape()
            # if it was finished, but just selected, it doesn't count
            res = shape.finish() or res
            shape.clean()
            self.current_shape = None
            return res
        return False

    def finish_selection_shape(self, do_select=False):
        '''Returns True if there was a selection shape that was finished.
        '''
        selection = self.selection_shape
        if selection:
            if self.selected_point_shape is selection:
                self.clear_selected_point_shape()
            selection.finish()
            selection.clean()

            if do_select:
                if not self.add_selection and not self._ctrl_down:
                    self.clear_selected_shapes()
                for shape in self.shapes:
                    shape.select(selection)

            selection.remove_paint_widget()
            self.selection_shape = None
            return True
        return False

    def clear_selected_shapes(self):
        shapes = self.selected_shapes[:]
        for shape in shapes:
            shape.deselect()
        return shapes

    def clear_selected_point_shape(self, clear_selection=True,
                                   exclude_point=(None, None)):
        if self.selected_point_shape:
            if not clear_selection \
                    and self.selected_point_shape is self.selection_shape:
                return False
            eshape, ep = exclude_point
            if self.selected_point_shape is not eshape:
                ep = None
            if self.selected_point_shape.clear_point_selection(ep):
                self.selected_point_shape = None
                return True
        return False

    def delete_selected_point(self):
        if self.selected_point_shape \
                and self.selected_point_shape.delete_selected_point():
            self.selected_point_shape = None
            return True
        return False

    def delete_selected_shapes(self):
        shapes = self.selected_shapes[:]
        for shape in shapes:
            self.remove_shape(shape)
        return shapes

    def remove_shape(self, shape):
        if shape is self.current_shape:
            self.finish_current_shape()
        elif shape is self.selection_shape:
            self.finish_selection_shape()
        if shape is self.selected_point_shape:
            self.clear_selected_point_shape()
        shape.deselect()
        shape.remove_paint_widget()
        self.shapes.remove(shape)

    def on_touch_down(self, touch):
        touch.ud['paint_touch'] = None  # stores the point the touch fell near
        touch.ud['paint_drag'] = None
        touch.ud['paint_long'] = False

        if super(PaintCanvas, self).on_touch_down(touch):
            return True
        if not self.collide_point(touch.x, touch.y):
            return super(PaintCanvas, self).on_touch_down(touch)
        touch.grab(self)

        shape = self.current_shape or self.selection_shape \
            or self.selected_point_shape

        # add freeform shape if nothing is active
        if not shape and (not self.locked or self.select) \
                and self.draw_mode == 'freeform':
            if self.select:
                shape = self.selection_shape = _cls_map['freeform'](
                    paint_widget=self)
                self.selection_shape.add_point(touch, source='down')
            else:
                shape = self.current_shape = _cls_map['freeform'](
                    paint_widget=self)
                self.current_shape.add_point(touch, source='down')

        # check if the touch falls close to point
        if shape:
            p, dist = shape.closest_point(touch.x, touch.y)
            if dist <= self.min_touch_dist:
                touch.ud['paint_touch'] = shape, p
        elif not self.locked and self.shapes:
            x, y = touch.x, touch.y
            shapes = self.selected_shapes or self.shapes

            dists = [(s, s.closest_point(x, y)) for s in reversed(shapes)]
            shape, (p, dist) = min(dists, key=lambda x: x[1][1])
            if dist <= self.min_touch_dist:
                touch.ud['paint_touch'] = shape, p

        if touch.ud['paint_touch']:
            self._long_touch_trigger = Clock.schedule_once(
                partial(self.do_long_touch, touch), self.long_touch_delay)
        else:
            touch.ud['paint_drag'] = False
        return False

    def do_long_touch(self, touch):
        self._long_touch_trigger = None
        shape, p = touch.ud['paint_touch']
        obj_shape = self.selection_shape or self.current_shape \
            or self.selected_point_shape

        if shape is obj_shape:
            if self.clear_selected_point_shape(exclude_point=(shape, p)):
                shape.select_point(p)
            touch.ud['paint_long'] = True
        elif shape in self.shapes and not obj_shape:
            self.clear_selected_point_shape()
            shape.select_point(p)
            touch.ud['paint_long'] = True
            self.selected_point_shape = shape

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super(PaintCanvas, self).on_touch_move(touch)
        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        if touch.ud['paint_drag'] is False:
            if touch.ud['paint_long']:
                return True
            return super(PaintCanvas, self).on_touch_move(touch)

        if not self.collide_point(touch.x, touch.y):
            return True

        # if paint_drag is not False, we have a touch in paint_touch
        # now it could be the first move
        shape, p = touch.ud['paint_touch']
        obj_shape = self.selection_shape or self.current_shape \
            or self.selected_point_shape
        res = False

        if shape is obj_shape or shape in self.shapes and not obj_shape and \
                not self.locked and not self.selected_shapes:
            res = shape.move_point(touch, p)
            if touch.ud['paint_drag'] is None:
                self.clear_selected_point_shape(exclude_point=(shape, p))
        elif not self.locked and not obj_shape and self.selected_shapes:
            for shape in self.selected_shapes:
                res = shape.translate(touch.dx, touch.dy) or res

        if touch.ud['paint_drag'] is None:
            touch.ud['paint_drag'] = bool(res)
        if not touch.ud['paint_drag'] and not touch.ud['paint_long']:
            return super(PaintCanvas, self).on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return super(PaintCanvas, self).on_touch_up(touch)
        if touch.is_double_tap:
            print 1
        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        shape = self.current_shape or self.selection_shape
        if shape and self.draw_mode == 'freeform':
            if self.select:
                self.finish_selection_shape(True)
            else:
                self.finish_current_shape()
            return True

        if touch.ud['paint_drag'] or touch.ud['paint_long']:
            return True
        if not self.collide_point(touch.x, touch.y):
            return True

        select = self.select
        if touch.is_double_tap:
            # no current shape when locked, and don't create one.
            if self.locked and not select:
                return False
            # either current/selection shape is finished or new one is created
            if select:
                if not self.finish_selection_shape(True):
                    self.selection_shape = _cls_map[self.draw_mode](
                        paint_widget=self)
                    self.selection_shape.add_point(touch, source='up')
            else:
                if not self.finish_current_shape():
                    self.current_shape = _cls_map[self.draw_mode](
                        paint_widget=self)
                    self.current_shape.add_point(touch, source='up')
            return True

        if self.clear_selected_point_shape():
            return True

        if shape:
            return shape.add_point(touch, source='up')

        if self.clear_selected_shapes():
            return True
        return super(PaintCanvas, self).on_touch_up(touch)

    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        if keycode[1] in ('lctrl', 'ctrl', 'rctrl'):
            self._ctrl_down.add(keycode[1])
        return super(PaintCanvas, self).keyboard_on_key_down(
            window, keycode, text, modifiers)

    def keyboard_on_key_up(self, window, keycode):
        if keycode[1] in ('lctrl', 'ctrl', 'rctrl'):
            self._ctrl_down.remove(keycode[1])
        if keycode[1] == 'escape':
            if self.clear_selected_point_shape():
                return True
            if self.finish_current_shape() or self.finish_selection_shape():
                return True
            if self.clear_selected_shapes():
                return True
        elif keycode[1] == 'delete':
            if self.delete_selected_point():
                return True
            if not self.current_shape and not self.selection_shape \
                    and self.delete_selected_shapes():
                return True
        elif keycode[1] == 'a' and self._ctrl_down:
            for shape in self.selected_shapes:
                shape.select()
            return True

        return super(PaintCanvas, self).keyboard_on_key_up(
            window, keycode)


class PaintShape(object):

    finished = False

    selected = False

    paint_widget = None

    def __init__(self, paint_widget, **kwargs):
        super(PaintShape, self).__init__(**kwargs)
        self.paint_widget = paint_widget

    def add_point(self, touch, source='down'):
        return False

    def move_point(self, touch, point):
        return False

    def remove_paint_widget(self):
        pass

    def finish(self):
        return False

    def clean(self):
        '''Removes everything, except its selection state.
        '''
        self.clear_point_selection()

    def select(self, stencil_shape=None):
        pass

    def deselect(self):
        pass

    def closest_point(self, x, y):
        pass

    def select_point(self, p):
        pass

    def delete_selected_point(self):
        return False

    def clear_point_selection(self, exclude_point=None):
        return False

    def translate(self, dx, dy):
        return False


class PaintCircle(PaintShape):

    center = None

    radius = None

    ellipse = None

    def add_point(self, touch, source='down'):
        if self.ellipse is None:
            with self.paint_widget.canvas:
                self.ellipse = Ellipse(pos=(touch.x, touch.y), size=(55, 55))


class PaintEllipse(PaintShape):
    pass


class PaintPolygon(PaintShape):
    pass


class PaintFreeform(PaintShape):
    pass


class PaintBezier(PaintShape):
    pass

_cls_map = {
    'circle': PaintCircle, 'ellipse': PaintEllipse,
    'polygon': PaintPolygon, 'freeform': PaintFreeform,
    'bezier': PaintBezier
}
