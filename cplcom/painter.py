"""
Fix name uniqueness to just check rather than counting with _name_count.
"""
from functools import partial
from math import cos, sin, atan2, pi, sqrt, isclose, acos

from kivy.uix.behaviors.focus import FocusBehavior
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import OptionProperty, BooleanProperty, NumericProperty, \
    ListProperty, ObjectProperty, StringProperty
from kivy.graphics import Ellipse, Line, Color, Point, Mesh, PushMatrix, \
    PopMatrix, Rotate, InstructionGroup
from kivy.graphics.tesselator import Tesselator
from kivy.event import EventDispatcher
import copy

from kivy.garden.collider import CollideEllipse, Collide2DPoly, CollideBezier


def euclidean_dist(x1, y1, x2, y2):
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


def rotate_pos(x, y, cx, cy, angle, base_angle=0.):
    hyp = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    xp = hyp * cos(angle + base_angle)
    yp = hyp * sin(angle + base_angle)
    return xp + cx, yp + cy


class PaintCanvasBehaviorBase(EventDispatcher):
    ''':attr:`shapes`, :attr:`selected_shapes`, :attr:`draw_mode`,
    :attr:`current_shape`, :attr:`locked`, :attr:`select`, and
    :attr:`selection_shape` are the attributes that make up the state machine.

    Internal Logic
    ---------------

    Each shape has a single point by which it is dragged. However, one can
    interact with other parts of the shape as determined by the shape instance.
    Selection happens by the controller when that point is touched. If
    multi-select or ctrl is held down, multiple shapes can be selected this
    way. The currently selected objects may be dragged by dragging any of their
    selection points.

    First we check if a current_shape is active, if so, all touches are sent to
    it. On touch_up, it checks if done and if so finishes it.

    Next we check if we need to select a shape by the selection points.
    If so, the touch will select or add to selection a shape. If no shape
    is near enough the selection point, the selection will be cleared when the
    touch moves or comes up
    '''

    shapes = ListProperty([])

    selected_shapes = ListProperty([])

    current_shape = None
    '''Holds shape currently being edited. Can be a finished shape, e.g. if
    a point is selected.

    Either :attr:`current_shape` or :attr:`selection_shape` or both must be
    None.
    '''

    locked = BooleanProperty(False)
    '''When locked, only selection is allowed. We cannot select points
    (except in :attr:`selection_shape`) and :attr:`current_shape` must
    be None.
    '''

    multiselect = BooleanProperty(False)

    min_touch_dist = dp(10)

    long_touch_delay = .7

    _long_touch_trigger = None

    _ctrl_down = None

    _processing_touch = None

    shapes_canvas = False

    def __init__(self, **kwargs):
        super(PaintCanvasBehaviorBase, self).__init__(**kwargs)
        self._ctrl_down = set()
        self.fbind('locked', self._handle_locked)

        def set_focus(*largs):
            if not self.focus:
                self.finish_current_shape()
        if hasattr(self, 'focus'):
            self.fbind('focus', set_focus)

    def _handle_locked(self, *largs):
        if not self.locked:
            return
        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        self.finish_current_shape()
        self.clear_selected_shapes()

    def finish_current_shape(self):
        '''Returns True if there was a unfinished shape that was finished.
        '''
        shape = self.current_shape
        if shape:
            if shape.finished:
                self.end_shape_interaction()
            else:
                shape.finish()
                self.current_shape = None

                if shape.is_valid:
                    self.add_shape(shape)
                else:
                    shape.remove_shape_from_canvas()

            return True
        return False

    def start_shape_interaction(self, shape, pos=None):
        assert self.current_shape is None
        self.current_shape = shape
        shape.start_interaction(pos)

    def end_shape_interaction(self):
        shape = self.current_shape
        if shape is not None:
            self.current_shape = None
            shape.stop_interaction()

    def clear_selected_shapes(self):
        shapes = self.selected_shapes[:]
        for shape in shapes:
            self.deselect_shape(shape)
        return shapes

    def delete_selected_shapes(self):
        shapes = self.selected_shapes[:]
        self.clear_selected_shapes()
        if self.current_shape is not None:
            shapes.append(self.current_shape)

        for shape in shapes:
            self.remove_shape(shape)
        return shapes

    def delete_all_shapes(self, keep_locked_shapes=True):
        self.finish_current_shape()
        shapes = self.shapes[:]
        for shape in shapes:
            if not shape.locked or not keep_locked_shapes:
                self.remove_shape(shape)
        return shapes

    def select_shape(self, shape):
        if shape.select():
            self.finish_current_shape()
            self.selected_shapes.append(shape)
            return True
        return False

    def deselect_shape(self, shape):
        if shape.deselect():
            self.selected_shapes.remove(shape)
            return True
        return False

    def add_shape(self, shape):
        self.shapes.append(shape)
        return True

    def remove_shape(self, shape):
        self.deselect_shape(shape)

        if shape is self.current_shape:
            self.finish_current_shape()
        shape.remove_shape_from_canvas()

        if shape in self.shapes:
            self.shapes.remove(shape)
            return True
        return False

    def reorder_shape(self, shape, before_shape=None):
        self.shapes.remove(shape)
        if before_shape is None:
            self.shapes.append(shape)
            shape.move_to_top()
        else:
            i = self.shapes.index(before_shape)
            self.shapes.insert(i, shape)

            for s in self.shapes[i:]:
                s.move_to_top()

    def duplicate_selected_shapes(self):
        shapes = self.selected_shapes[:]
        self.clear_selected_shapes()
        for shape in shapes:
            self.duplicate_shape(shape)
        return shapes

    def duplicate_shape(self, shape):
        new_shape = copy.deepcopy(shape)
        self.add_shape(new_shape)
        shape.translate(dpos=(15, 15))
        return shape

    def create_shape_with_touch(self, touch):
        raise NotImplementedError

    def check_new_shape_done(self, shape, state):
        return not shape.finished and shape.ready_to_finish

    def lock_shape(self, shape):
        if shape.locked:
            return False

        res = shape is self.current_shape and self.finish_current_shape()

        if shape.selected:
            res = self.deselect_shape(shape)

        return shape.lock() or res

    def unlock_shape(self, shape):
        if shape.locked:
            return shape.unlock()
        return False

    def get_closest_selection_point_shape(self, x, y):
        min_dist = self.min_touch_dist
        closest_shape = None
        for shape in reversed(self.shapes):  # upper shape takes pref
            if shape.locked:
                continue

            dist = shape.get_selection_point_dist((x, y))
            if dist < min_dist:
                closest_shape = shape
                min_dist = dist

        return closest_shape

    def get_closest_shape(self, x, y):
        min_dist = self.min_touch_dist
        closest_shape = None
        for shape in reversed(self.shapes):  # upper shape takes pref
            if shape.locked:
                continue

            dist = shape.get_interaction_point_dist((x, y))
            if dist < min_dist:
                closest_shape = shape
                min_dist = dist

        return closest_shape

    def on_touch_down(self, touch):
        ud = touch.ud
        ud['paint_interacted'] = False
        ud['paint_interaction'] = ''
        ud['paint_touch_moved'] = False
        ud['paint_selected_shape'] = None
        ud['paint_selected_empty'] = False
        ud['paint_cleared_selection'] = False

        if self.locked or self._processing_touch is not None:
            return super(PaintCanvasBehaviorBase, self).on_touch_down(touch)

        if super(PaintCanvasBehaviorBase, self).on_touch_down(touch):
            return True

        if not self.collide_point(touch.x, touch.y):
            return False

        ud['paint_interacted'] = True
        self._processing_touch = touch
        touch.grab(self)

        # if we have a current shape, all touch will go to it
        current_shape = self.current_shape
        if current_shape is not None:
            ud['paint_cleared_selection'] = current_shape.finished and \
                current_shape.get_interaction_point_dist(touch.pos) \
                >= self.min_touch_dist
            if ud['paint_cleared_selection']:
                self.finish_current_shape()

            else:
                ud['paint_interaction'] = 'current'
                current_shape.handle_touch_down(touch)
                return True

        # next try to interact by selecting or interacting with selected shapes
        shape = self.get_closest_selection_point_shape(touch.x, touch.y)
        if shape is not None:
            ud['paint_interaction'] = 'selected'
            ud['paint_selected_shape'] = shape
            ud['paint_selected_empty'] = not self.selected_shapes
            self._long_touch_trigger = Clock.schedule_once(
                partial(self.do_long_touch, touch), self.long_touch_delay)
            return True

        if self._ctrl_down:
            ud['paint_interaction'] = 'done'
            return True

        self._long_touch_trigger = Clock.schedule_once(
            partial(self.do_long_touch, touch), self.long_touch_delay)
        return True

    def do_long_touch(self, touch, *largs):
        assert self._processing_touch
        touch.push()
        touch.apply_transform_2d(self.to_widget)

        self._long_touch_trigger = None
        ud = touch.ud
        if ud['paint_interaction'] == 'selected':
            if self._ctrl_down:
                ud['paint_interaction'] = 'done'
                touch.pop()
                return
            ud['paint_interaction'] = ''

        assert ud['paint_interacted']
        assert not ud['paint_interaction']

        self.clear_selected_shapes()
        shape = self.get_closest_shape(touch.x, touch.y)
        if shape is not None:
            ud['paint_interaction'] = 'current'
            self.start_shape_interaction(shape, (touch.x, touch.y))
        else:
            ud['paint_interaction'] = 'done'
        touch.pop()

    def on_touch_move(self, touch):
        # if touch.grab_current is not None:  ????????
        #     return False

        ud = touch.ud
        if 'paint_interacted' not in ud or not ud['paint_interacted']:
            return super(PaintCanvasBehaviorBase, self).on_touch_move(touch)

        if self._long_touch_trigger is not None:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        if touch.grab_current is self:
            # for move, only use normal touch, not touch outside range
            return False

        if ud['paint_interaction'] == 'done':
            return True

        ud['paint_touch_moved'] = True
        if not self.collide_point(touch.x, touch.y):
            return True

        if not ud['paint_interaction']:
            if ud['paint_cleared_selection'] or self.clear_selected_shapes():
                ud['paint_interaction'] = 'done'
                return True

            # finally try creating a new shape
            # touch must have originally collided otherwise we wouldn't be here
            shape = self.create_shape_with_touch(touch)
            if shape is not None:
                shape.handle_touch_down(touch, opos=touch.opos)
                self.current_shape = shape
                if self.check_new_shape_done(shape, 'down'):
                    self.finish_current_shape()
                    ud['paint_interaction'] = 'done'
                    return True

                ud['paint_interaction'] = 'current_new'
            else:
                ud['paint_interaction'] = 'done'
                return True

        if ud['paint_interaction'] in ('current', 'current_new'):
            if self.current_shape is None:
                ud['paint_interaction'] = 'done'
            else:
                self.current_shape.handle_touch_move(touch)
            return True

        assert ud['paint_interaction'] == 'selected'

        shape = ud['paint_selected_shape']
        if shape not in self.shapes:
            ud['paint_interaction'] = 'done'
            return True

        if self._ctrl_down or self.multiselect:
            if shape not in self.selected_shapes:
                self.select_shape(shape)
        else:
            if len(self.selected_shapes) != 1 or \
                    self.selected_shapes[0] != shape:
                self.clear_selected_shapes()
                self.select_shape(shape)

        for s in self.selected_shapes:
            s.translate(dpos=(touch.dx, touch.dy))
        return True

    def on_touch_up(self, touch):
        ud = touch.ud
        if 'paint_interacted' not in ud or not ud['paint_interacted']:
            return super(PaintCanvasBehaviorBase, self).on_touch_up(touch)

        if self._long_touch_trigger is not None:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        touch.ungrab(self)

        self._processing_touch = None
        if ud['paint_interaction'] == 'done':
            return True

        if not ud['paint_interaction']:
            if ud['paint_cleared_selection'] or self.clear_selected_shapes():
                ud['paint_interaction'] = 'done'
                return True

            # finally try creating a new shape
            # touch must have originally collided otherwise we wouldn't be here
            shape = self.create_shape_with_touch(touch)
            if shape is not None:
                shape.handle_touch_down(touch, opos=touch.opos)
                self.current_shape = shape
                if self.check_new_shape_done(shape, 'down'):
                    self.finish_current_shape()
                    ud['paint_interaction'] = 'done'
                    return True

                ud['paint_interaction'] = 'current_new'
            else:
                ud['paint_interaction'] = 'done'
                return True

        if ud['paint_interaction'] in ('current', 'current_new'):
            if self.current_shape is not None:
                self.current_shape.handle_touch_up(
                    touch, outside=not self.collide_point(touch.x, touch.y))
                if self.check_new_shape_done(self.current_shape, 'up'):
                    self.finish_current_shape()
                    ud['paint_interaction'] = 'done'
            return True

        if not self.collide_point(touch.x, touch.y):
            ud['paint_interaction'] = 'done'
            return True

        assert ud['paint_interaction'] == 'selected'
        if ud['paint_touch_moved']:
            ud['paint_interaction'] = 'done'
            if ud['paint_selected_empty'] and \
                    len(self.selected_shapes) == 1 and \
                    self.selected_shapes[0] == ud['paint_selected_shape']:
                self.clear_selected_shapes()
            return True

        shape = ud['paint_selected_shape']
        if shape not in self.shapes:
            ud['paint_interaction'] = 'done'
            return True

        if self._ctrl_down or self.multiselect:
            if shape in self.selected_shapes:
                self.deselect_shape(shape)
        else:
            if len(self.selected_shapes) != 1 or \
                    self.selected_shapes[0] != shape:
                self.clear_selected_shapes()
                self.select_shape(shape)

        return True

    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        if keycode[1] in ('lctrl', 'ctrl', 'rctrl'):
            self._ctrl_down.add(keycode[1])

        arrows = {
            'left': (-1, 0), 'right': (1, 0), 'up': (0, 1), 'down': (0, -1)}
        if keycode[1] in arrows and self.selected_shapes:
            dpos = arrows[keycode[1]]
            for shape in self.selected_shapes:
                shape.translate(dpos=dpos)
            return True

        return False

    def keyboard_on_key_up(self, window, keycode):
        if keycode[1] in ('lctrl', 'ctrl', 'rctrl'):
            self._ctrl_down.remove(keycode[1])

        if keycode[1] == 'escape':
            if self.finish_current_shape() or self.clear_selected_shapes():
                return True
        elif keycode[1] == 'delete':
            if self.delete_selected_shapes():
                return True
        elif keycode[1] == 'a' and self._ctrl_down:
            for shape in self.shapes:
                if not shape.locked:
                    self.select_shape(shape)
            return True
        elif keycode[1] == 'd' and self._ctrl_down:
            if self.duplicate_selected_shapes():
                return True

        return False


class PaintShape(EventDispatcher):

    finished = False

    selected = False

    interacting = False

    ready_to_finish = False

    is_valid = False

    paint_widget = None

    line_width = 1

    line_color = 0, 1, 0, 1

    selection_point_color = 1, .5, .31, 1

    line_color_locked = .4, .56, .36, 1

    pointsize = 3

    graphics_name = ''

    graphics_point_select_name = ''

    _instruction_group = None

    locked = BooleanProperty(False)

    color_instructions = []

    __events__ = ('on_update', )

    def __init__(
            self, line_color=(0, 1, 0, 1),
            line_width=1, line_color_locked=(.4, .56, .36, 1),
            pointsize=2, selection_point_color=(1, .5, .31, 1), **kwargs):
        super(PaintShape, self).__init__(**kwargs)
        self.pointsize = pointsize
        self.line_color = line_color
        self.line_color_locked = line_color_locked
        self.line_width = line_width
        self.selection_point_color = selection_point_color
        self.color_instructions = []

        self.graphics_name = '{}-{}'.format(self.__class__.__name__, id(self))
        self.graphics_point_select_name = '{}-point'.format(self.graphics_name)

    def on_update(self, *largs):
        pass

    def set_valid(self):
        pass

    def add_shape_to_canvas(self, paint_widget):
        if self._instruction_group is not None:
            return False

        self.paint_widget = paint_widget
        with paint_widget.canvas:
            self._instruction_group = InstructionGroup()
        return True

    def remove_shape_from_canvas(self):
        if self._instruction_group is None:
            return False

        self.paint_widget.canvas.remove(self._instruction_group)
        self._instruction_group = None
        self.paint_widget = None
        return True

    def handle_touch_down(self, touch, opos=None):
        raise NotImplementedError

    def handle_touch_move(self, touch):
        # if ready to finish, it needs to ignore until touch is up
        raise NotImplementedError

    def handle_touch_up(self, touch, outside=False):
        raise NotImplementedError

    def start_interaction(self, pos):
        if self.interacting:
            return False
        self.interacting = True
        return True

    def stop_interaction(self):
        if not self.interacting:
            return False
        self.interacting = False
        return True

    def get_selection_point_dist(self, pos):
        pass

    def get_interaction_point_dist(self, pos):
        pass

    def finish(self):
        if self.finished:
            return False
        self.finished = True
        return True

    def select(self):
        if self.selected:
            return False
        self.selected = True
        return True

    def deselect(self):
        if not self.selected:
            return False
        self.selected = False
        return True

    def lock(self):
        if self.locked:
            return False

        self.locked = True
        return True

    def unlock(self):
        if not self.locked:
            return False

        self.locked = False
        return True

    def translate(self, dpos):
        return False

    def move_to_top(self):
        if self._instruction_group is None:
            return

        self.paint_widget.canvas.remove(self._instruction_group)
        self.paint_widget.canvas.add(self._instruction_group)
        return True

    def get_state(self, state=None):
        d = {} if state is None else state
        for k in ['line_color', 'line_width', 'is_valid', 'locked',
                  'line_color_locked']:
            d[k] = getattr(self, k)
        d['cls'] = self.__class__.__name__

        return d

    def set_state(self, state):
        state = dict(state)
        lock = None
        for k, v in state.items():
            if k == 'locked':
                lock = bool(v)
                continue
            elif k == 'cls':
                continue
            setattr(self, k, v)

        self.finish()

        if lock is True:
            self.lock()
        elif lock is False:
            self.unlock()
        self.dispatch('on_update')

    def __deepcopy__(self, memo):
        obj = self.__class__()
        obj.set_state(self.get_state())

        obj.set_valid()
        obj.finish()
        return obj

    def add_area_graphics_to_canvas(self, name, canvas):
        pass

    def show_shape_in_canvas(self):
        for color in self.color_instructions:
            color.rgba = [color.r, color.g, color.b, 1.]

    def hide_shape_in_canvas(self):
        for color in self.color_instructions:
            color.rgba = [color.r, color.g, color.b, 0.]


class PaintCircle(PaintShape):

    center = ListProperty([0, 0])

    perim_ellipse_inst = None

    ellipse_color_inst = None

    selection_point_inst = None

    ready_to_finish = True

    is_valid = True

    radius = NumericProperty(dp(10))

    def __init__(self, **kwargs):
        super(PaintCircle, self).__init__(**kwargs)

        def update(*largs):
            self.translate()
        self.fbind('radius', update)
        self.fbind('center', update)

    def add_area_graphics_to_canvas(self, name, canvas):
        with canvas:
            x, y = self.center
            r = self.radius
            Ellipse(size=(r * 2., r * 2.), pos=(x - r, y - r), group=name)

    def add_shape_to_canvas(self, paint_widget):
        if not super(PaintCircle, self).add_shape_to_canvas(paint_widget):
            return False

        colors = self.color_instructions = []

        x, y = self.center
        r = self.radius
        inst = self.ellipse_color_inst = Color(
            *self.line_color, group=self.graphics_name)
        colors.append(inst)

        self._instruction_group.add(inst)
        inst = self.perim_ellipse_inst = Line(
            circle=(x, y, r), width=self.line_width,
            group=self.graphics_name)
        self._instruction_group.add(inst)
        inst = Color(*self.selection_point_color, group=self.graphics_name)
        self._instruction_group.add(inst)
        colors.append(inst)

        inst = self.selection_point_inst = Point(
            points=[x + r, y], pointsize=self.pointsize,
            group=self.graphics_name)
        self._instruction_group.add(inst)
        return True

    def remove_shape_from_canvas(self):
        if super(PaintCircle, self).remove_shape_from_canvas():
            self.ellipse_color_inst = None
            self.perim_ellipse_inst = None
            self.selection_point_inst = None
            return True
        return False

    def handle_touch_down(self, touch, opos=None):
        if not self.finished:
            self.center = opos or tuple(touch.pos)

    def handle_touch_move(self, touch):
        if not self.finished:
            return
        if self.interacting:
            dx = touch.dx if touch.x >= self.center[0] else -touch.dx
            self.radius = max(self.radius + dx, dp(2))

    def handle_touch_up(self, touch, outside=False):
        if not self.finished:
            return

    def start_interaction(self, pos):
        if super(PaintCircle, self).start_interaction(pos):
            if self.selection_point_inst is not None:
                self.selection_point_inst.pointsize = 2 * self.pointsize
            return True
        return False

    def stop_interaction(self):
        if super(PaintCircle, self).stop_interaction():
            if self.selection_point_inst is not None:
                self.selection_point_inst.pointsize = self.pointsize
            return True
        return False

    def get_selection_point_dist(self, pos):
        x1, y1 = pos
        x2, y2 = self.center
        x2 += self.radius
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5

    def get_interaction_point_dist(self, pos):
        return self.get_selection_point_dist(pos)

    def lock(self):
        if super(PaintCircle, self).lock():
            if self._instruction_group is not None:
                self.ellipse_color_inst.rgb = self.line_color_locked[:3]
            return True
        return False

    def unlock(self):
        if super(PaintCircle, self).unlock():
            if self._instruction_group is not None:
                self.ellipse_color_inst.rgb = self.line_color[:3]
            return True
        return False

    def select(self):
        if not super(PaintCircle, self).select():
            return False
        if self._instruction_group is not None:
            self.perim_ellipse_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintCircle, self).deselect():
            if self._instruction_group is not None:
                self.perim_ellipse_inst.width = self.line_width
            return True
        return False

    def translate(self, dpos=None, pos=None):
        if dpos is not None:
            x, y = self.center
            dx, dy = dpos
            x += dx
            y += dy
        elif pos is not None:
            x, y = pos
        else:
            x, y = self.center

        r = self.radius
        self.center = x, y
        if self.perim_ellipse_inst is not None:
            self.perim_ellipse_inst.circle = x, y, r
        if self.selection_point_inst is not None:
            self.selection_point_inst.points = [x + r, y]

        self.dispatch('on_update')
        return True

    def get_state(self, state=None):
        d = super(PaintCircle, self).get_state(state)
        for k in ['center', 'radius']:
            d[k] = getattr(self, k)
        return d

    def rescale(self, scale):
        self.radius *= scale


class PaintEllipse(PaintShape):

    center = ListProperty([0, 0])

    perim_ellipse_inst = None

    ellipse_color_inst = None

    selection_point_inst = None

    selection_point_inst2 = None

    rotate_inst = None

    ready_to_finish = True

    is_valid = True

    radius_x = NumericProperty(dp(10))

    radius_y = NumericProperty(dp(15))

    angle = NumericProperty(0)
    '''radians
    '''

    def __init__(self, **kwargs):
        super(PaintEllipse, self).__init__(**kwargs)

        def update(*largs):
            self.translate()
        self.fbind('radius_x', update)
        self.fbind('radius_y', update)
        self.fbind('angle', update)
        self.fbind('center', update)

    def add_area_graphics_to_canvas(self, name, canvas):
        with canvas:
            x, y = self.center
            rx, ry = self.radius_x, self.radius_y
            angle = self.angle

            PushMatrix(group=name)
            Rotate(angle=angle / pi * 180., origin=(x, y), group=name)
            Ellipse(size=(rx * 2., ry * 2.), pos=(x - rx, y - ry), group=name)
            PopMatrix(group=self.graphics_name)

    def add_shape_to_canvas(self, paint_widget):
        if not super(PaintEllipse, self).add_shape_to_canvas(paint_widget):
            return False

        colors = self.color_instructions = []

        x, y = self.center
        rx, ry = self.radius_x, self.radius_y
        angle = self.angle

        i1 = self.ellipse_color_inst = Color(
            *self.line_color, group=self.graphics_name)
        colors.append(i1)

        i2 = PushMatrix(group=self.graphics_name)
        i3 = self.rotate_inst = Rotate(
            angle=angle / pi * 180., origin=(x, y), group=self.graphics_name)

        i4 = self.perim_ellipse_inst = Line(
            ellipse=(x - rx, y - ry, 2 * rx, 2 * ry), width=self.line_width,
            group=self.graphics_name)
        i6 = self.selection_point_inst2 = Point(
            points=[x, y + ry], pointsize=self.pointsize,
            group=self.graphics_name)
        i8 = Color(*self.selection_point_color, group=self.graphics_name)
        colors.append(i8)

        i5 = self.selection_point_inst = Point(
            points=[x + rx, y], pointsize=self.pointsize,
            group=self.graphics_name)
        i7 = PopMatrix(group=self.graphics_name)

        for inst in (i1, i2, i3, i4, i6, i8, i5, i7):
            self._instruction_group.add(inst)
        return True

    def remove_shape_from_canvas(self):
        if super(PaintEllipse, self).remove_shape_from_canvas():
            self.ellipse_color_inst = None
            self.perim_ellipse_inst = None
            self.selection_point_inst = None
            self.selection_point_inst2 = None
            self.rotate_inst = None
            return True
        return False

    def handle_touch_down(self, touch, opos=None):
        if not self.finished:
            self.center = opos or tuple(touch.pos)

    def handle_touch_move(self, touch):
        if not self.finished:
            return
        if self.interacting:
            dp2 = dp(2)
            px, py = touch.ppos
            x, y = touch.pos
            cx, cy = self.center

            px, py = px - cx, py - cy
            x, y = x - cx, y - cy

            d1, d2 = self._get_interaction_points_dist(touch.pos)
            if d1 <= d2:
                angle = self.angle
            else:
                angle = self.angle + pi / 2.0

            rrx, rry = cos(angle), sin(angle)
            prev_r = px * rrx + py * rry
            r = x * rrx + y * rry
            if r <= dp2 or prev_r <= dp2:
                return

            prev_theta = atan2(py, px)
            theta = atan2(y, x)
            self.angle = (self.angle + theta - prev_theta) % (2 * pi)

            if d1 <= d2:
                self.radius_x = max(self.radius_x + r - prev_r, dp2)
            else:
                self.radius_y = max(self.radius_y + r - prev_r, dp2)

    def handle_touch_up(self, touch, outside=False):
        if not self.finished:
            return

    def start_interaction(self, pos):
        if super(PaintEllipse, self).start_interaction(pos):
            if self.selection_point_inst is not None:
                self.selection_point_inst.pointsize = 2 * self.pointsize
                self.selection_point_inst2.pointsize = 2 * self.pointsize
            return True
        return False

    def stop_interaction(self):
        if super(PaintEllipse, self).stop_interaction():
            if self.selection_point_inst is not None:
                self.selection_point_inst.pointsize = self.pointsize
                self.selection_point_inst2.pointsize = self.pointsize
            return True
        return False

    def get_selection_point_dist(self, pos):
        x1, y1 = pos

        x2, y2 = self.center
        x2, y2 = rotate_pos(x2 + self.radius_x, y2, x2, y2, self.angle)
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5

    def get_interaction_point_dist(self, pos):
        d1, d2 = self._get_interaction_points_dist(pos)
        return min(d1, d2)

    def _get_interaction_points_dist(self, pos):
        x1, y1 = pos

        x2, y2 = self.center
        x_, y_ = rotate_pos(x2 + self.radius_x, y2, x2, y2, self.angle)
        d1 = ((x1 - x_) ** 2 + (y1 - y_) ** 2) ** 0.5

        x_, y_ = rotate_pos(
            x2, y2 + self.radius_y, x2, y2, self.angle, base_angle=pi / 2.0)
        d2 = ((x1 - x_) ** 2 + (y1 - y_) ** 2) ** 0.5
        return d1, d2

    def lock(self):
        if super(PaintEllipse, self).lock():
            if self._instruction_group is not None:
                self.ellipse_color_inst.rgb = self.line_color_locked[:3]
            return True
        return False

    def unlock(self):
        if super(PaintEllipse, self).unlock():
            if self._instruction_group is not None:
                self.ellipse_color_inst.rgb = self.line_color[:3]
            return True
        return False

    def select(self):
        if not super(PaintEllipse, self).select():
            return False
        if self._instruction_group is not None:
            self.perim_ellipse_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintEllipse, self).deselect():
            if self._instruction_group is not None:
                self.perim_ellipse_inst.width = self.line_width
            return True
        return False

    def translate(self, dpos=None, pos=None):
        if dpos is not None:
            x, y = self.center
            dx, dy = dpos
            x += dx
            y += dy
        elif pos is not None:
            x, y = pos
        else:
            x, y = self.center

        rx, ry = self.radius_x, self.radius_y
        angle = self.angle
        self.center = x, y
        if self.rotate_inst is not None:
            self.rotate_inst.angle = angle / pi * 180.
            self.rotate_inst.origin = x, y
            self.perim_ellipse_inst.ellipse = x - rx, y - ry, 2 * rx, 2 * ry
            self.selection_point_inst.points = [x + rx, y]
            self.selection_point_inst2.points = [x, y + ry]

        self.dispatch('on_update')
        return True

    def get_state(self, state=None):
        d = super(PaintEllipse, self).get_state(state)
        for k in ['center', 'radius_x', 'radius_y', 'angle']:
            d[k] = getattr(self, k)
        return d

    def rescale(self, scale):
        self.radius_x *= scale
        self.radius_y *= scale


class PaintPolygon(PaintShape):

    points = ListProperty([])

    selection_point = []

    perim_line_inst = None

    perim_points_inst = None

    perim_color_inst = None

    selection_point_inst = None

    perim_close_inst = None

    ready_to_finish = False

    is_valid = False

    _last_point_moved = None

    def __init__(self, **kwargs):
        super(PaintPolygon, self).__init__(**kwargs)

        def update(*largs):
            if self.perim_line_inst is not None:
                self.perim_line_inst.points = self.points
                self.perim_points_inst.points = self.points
                self.selection_point_inst.points = self.selection_point
            self.dispatch('on_update')

        self.fbind('points', update)
        update()

    def add_area_graphics_to_canvas(self, name, canvas):
        with canvas:
            points = self.points
            if not points:
                return

            tess = Tesselator()
            tess.add_contour(points)

            if tess.tesselate():
                for vertices, indices in tess.meshes:
                    Mesh(
                        vertices=vertices, indices=indices,
                        mode='triangle_fan', group=name)

    def add_shape_to_canvas(self, paint_widget):
        if not super(PaintPolygon, self).add_shape_to_canvas(paint_widget):
            return False

        colors = self.color_instructions = []

        i1 = self.perim_color_inst = Color(
            *self.line_color, group=self.graphics_name)
        colors.append(i1)

        i2 = self.perim_line_inst = Line(
            points=self.points, width=self.line_width,
            close=self.finished, group=self.graphics_name)
        i3 = self.perim_points_inst = Point(
            points=self.points, pointsize=self.pointsize,
            group=self.graphics_name)

        insts = [i1, i2, i3]
        if not self.finished:
            points = self.points[-2:] + self.points[:2]
            line = self.perim_close_inst = Line(
                points=points, width=self.line_width,
                close=False, group=self.graphics_name)
            line.dash_offset = 4
            line.dash_length = 4
            insts.append(line)

        i4 = Color(*self.selection_point_color, group=self.graphics_name)
        colors.append(i4)

        i5 = self.selection_point_inst = Point(
            points=self.selection_point, pointsize=self.pointsize,
            group=self.graphics_name)

        for inst in insts + [i4, i5]:
            self._instruction_group.add(inst)

        return True

    def remove_shape_from_canvas(self):
        if super(PaintPolygon, self).remove_shape_from_canvas():
            self.perim_color_inst = None
            self.perim_points_inst = None
            self.perim_line_inst = None
            self.selection_point_inst = None
            self.perim_close_inst = None
            return True
        return False

    def set_valid(self):
        self.is_valid = len(self.points) >= 6

    def handle_touch_down(self, touch, opos=None):
        return

    def handle_touch_move(self, touch):
        if not self.finished:
            return

        i = self._last_point_moved
        if i is None:
            i, dist = self._get_interaction_point(touch.pos)
            if dist is None:
                return
            self._last_point_moved = i

        x, y = self.points[2 * i: 2 * i + 2]
        x += touch.dx
        y += touch.dy
        self.points[2 * i: 2 * i + 2] = x, y
        if not i:
            self.selection_point = [x, y]

    def handle_touch_up(self, touch, outside=False):
        if not self.finished:
            if touch.is_double_tap:
                self.ready_to_finish = True
                return

            if not outside:
                if not self.selection_point:
                    self.selection_point = touch.pos[:]
                self.points.extend(touch.pos)
                if self.perim_close_inst is not None:
                    self.perim_close_inst.points = \
                        self.points[-2:] + self.points[:2]
                if len(self.points) >= 6:
                    self.is_valid = True
        else:
            self._last_point_moved = None

    def start_interaction(self, pos):
        if super(PaintPolygon, self).start_interaction(pos):
            if self.selection_point_inst is not None:
                self.selection_point_inst.pointsize = 2 * self.pointsize
                self.perim_points_inst.pointsize = 2 * self.pointsize
            return True
        return False

    def stop_interaction(self):
        if super(PaintPolygon, self).stop_interaction():
            if self.selection_point_inst is not None:
                self.selection_point_inst.pointsize = self.pointsize
                self.perim_points_inst.pointsize = self.pointsize
            return True
        return False

    def get_selection_point_dist(self, pos):
        x1, y1 = pos
        if not self.selection_point:
            return 10000.0

        x2, y2 = self.selection_point
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5

    def get_interaction_point_dist(self, pos):
        i, dist = self._get_interaction_point(pos)
        if dist is None:
            return 10000.0
        return dist

    def _get_interaction_point(self, pos):
        x1, y1 = pos
        points = self.points
        if not points:
            return None, None

        min_i = 0
        min_d = 10000.0
        for i in range(len(points) // 2):
            x, y = points[2 * i], points[2 * i + 1]
            dist = ((x1 - x) ** 2 + (y1 - y) ** 2) ** 0.5
            if dist < min_d:
                min_d = dist
                min_i = i

        return min_i, min_d

    def finish(self):
        if super(PaintPolygon, self).finish():
            if self.perim_close_inst is not None:
                self._instruction_group.remove(self.perim_close_inst)
                self.perim_close_inst = None
                self.perim_line_inst.close = True
            return True
        return False

    def lock(self):
        if super(PaintPolygon, self).lock():
            if self._instruction_group is not None:
                self.perim_color_inst.rgb = self.line_color_locked[:3]
            return True
        return False

    def unlock(self):
        if super(PaintPolygon, self).unlock():
            if self._instruction_group is not None:
                self.perim_color_inst.rgb = self.line_color[:3]
            return True
        return False

    def select(self):
        if not super(PaintPolygon, self).select():
            return False
        if self._instruction_group is not None:
            self.perim_line_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintPolygon, self).deselect():
            if self._instruction_group is not None:
                self.perim_line_inst.width = self.line_width
            return True
        return False

    def translate(self, dpos=None, pos=None):
        if dpos is not None:
            dx, dy = dpos
        elif pos is not None:
            px, py = self.selection_point
            x, y = pos
            dx, dy = x - px, y - py
        else:
            assert False

        points = self.points
        new_points = [None, ] * len(points)
        for i in range(len(points) // 2):
            new_points[2 * i] = points[2 * i] + dx
            new_points[2 * i + 1] = points[2 * i + 1] + dy
        self.selection_point = new_points[:2]
        self.points = new_points

        return True

    def get_state(self, state=None):
        d = super(PaintPolygon, self).get_state(state)
        for k in ['points', 'selection_point']:
            d[k] = getattr(self, k)
        return d

    def rescale(self, scale):
        points = self.points
        if not points:
            return

        n = len(points) / 2.0
        cx = sum(points[::2]) / n
        cy = sum(points[1::2]) / n
        x_vals = ((x_val - cx) * scale + cx for x_val in points[::2])
        y_vals = ((y_val - cy) * scale + cy for y_val in points[1::2])

        points = [val for point in zip(x_vals, y_vals) for val in point]
        self.points = points
        self.selection_point = points[:2]


class PaintFreeformPolygon(PaintPolygon):

    def handle_touch_down(self, touch, opos=None):
        if not self.finished:
            pos = opos or touch.pos
            if not self.selection_point:
                self.selection_point = pos[:]

            self.points.extend(pos)
            if self.perim_close_inst is not None:
                self.perim_close_inst.points = \
                    self.points[-2:] + self.points[:2]
            if len(self.points) >= 6:
                self.is_valid = True

    def handle_touch_move(self, touch):
        if self.finished:
            return super(PaintFreeformPolygon, self).handle_touch_move(touch)

        if not self.selection_point:
            self.selection_point = touch.pos[:]

        self.points.extend(touch.pos)
        if self.perim_close_inst is not None:
            self.perim_close_inst.points = self.points[-2:] + self.points[:2]
        if len(self.points) >= 6:
            self.is_valid = True

    def handle_touch_up(self, touch, outside=False):
        if self.finished:
            return super(PaintFreeformPolygon, self).handle_touch_up(touch)
        self.ready_to_finish = True


class PaintCanvasBehavior(PaintCanvasBehaviorBase):

    draw_mode = OptionProperty('freeform', options=[
        'circle', 'ellipse', 'polygon', 'freeform', 'none'])

    shape_cls_map = {
        'circle': PaintCircle, 'ellipse': PaintEllipse,
        'polygon': PaintPolygon, 'freeform': PaintFreeformPolygon,
        'none': None
    }

    shape_cls_name_map = {}

    def __init__(self, **kwargs):
        super(PaintCanvasBehavior, self).__init__(**kwargs)
        self.shape_cls_name_map = {
            cls.__name__: cls for cls in self.shape_cls_map.values()
            if cls is not None}
        self.fbind('draw_mode', self._handle_draw_mode)

    def _handle_draw_mode(self, *largs):
        self.finish_current_shape()

    def create_shape_with_touch(self, touch):
        draw_mode = self.draw_mode
        if draw_mode is None:
            raise TypeError('Cannot create a shape when the draw mode is none')

        shape_cls = self.shape_cls_map[draw_mode]

        if shape_cls is None:
            return None
        return shape_cls()

    def create_add_shape(self, cls_name, **inst_kwargs):
        shape = self.shape_cls_map[cls_name](**inst_kwargs)
        shape.set_valid()
        shape.finish()
        self.add_shape(shape)
        shape.add_shape_to_canvas(self)
        return shape

    def create_shape_from_state(self, state):
        cls = self.shape_cls_name_map[state['cls']]
        shape = cls()
        shape.set_state(state)
        shape.set_valid()
        shape.finish()
        self.add_shape(shape)
        return shape


if __name__ == '__main__':
    from kivy.uix.widget import Widget
    from kivy.app import runTouchApp
    from kivy.lang import Builder

    class PainterWidget(PaintCanvasBehavior, Widget):

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


    runTouchApp(Builder.load_string("""
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
            text: "Multiselect"
    """))
