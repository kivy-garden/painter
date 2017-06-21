
from functools import partial
from math import cos, sin, atan2, pi, sqrt
from copy import deepcopy

from kivy.uix.behaviors.focus import FocusBehavior
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import OptionProperty, BooleanProperty, NumericProperty, \
    ListProperty, ObjectProperty, StringProperty
from kivy.graphics import Ellipse, Line, Color, Point, Mesh, PushMatrix, \
    PopMatrix, Rotate, InstructionGroup
from kivy.graphics.tesselator import Tesselator
from kivy.event import EventDispatcher

from kivy.garden.collider import CollideEllipse, Collide2DPoly, CollideBezier


def eucledian_dist(x1, y1, x2, y2):
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


class PaintCanvasBehavior(FocusBehavior, EventDispatcher):
    ''':attr:`shapes`, :attr:`selected_shapes`, :attr:`draw_mode`,
    :attr:`current_shape`, :attr:`locked`, :attr:`select`, and
    :attr:`selection_shape` are the attributes that make up the state machine.
    '''

    shapes = ListProperty([])

    selected_shapes = ListProperty([])

    draw_mode = OptionProperty('freeform', options=[
        'circle', 'ellipse', 'polygon', 'freeform', 'bezier', 'none'])

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

    multiselect = BooleanProperty(False)

    min_touch_dist = dp(10)

    long_touch_delay = .7

    _long_touch_trigger = None

    _ctrl_down = None

    line_color = 0, 1, 0, 1

    line_color_edit = 1, 0, 0, 1

    line_color_locked = .4, .56, .36, 1

    line_color_selector = 1, .4, 0, 1

    selection_color = 1, 1, 1, .5

    shape_cls_map = {}

    def __init__(self, **kwargs):
        super(PaintCanvasBehavior, self).__init__(**kwargs)
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
            self.clear_selected_point_shape(False)
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
            if not shape.is_valid:
                self.remove_shape(shape)
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

            if do_select and selection.is_valid:
                shapes = [
                    shape for shape in self.shapes if not shape.locked and
                    shape.collide_shape(selection)]
                if shapes:
                    if not self.multiselect and not self._ctrl_down:
                        self.clear_selected_shapes()
                    for shape in shapes:
                        self.select_shape(shape)

            selection.remove_paint_widget()
            self.selection_shape = None
            return True
        return False

    def clear_selected_shapes(self):
        shapes = self.selected_shapes[:]
        self.selected_shapes = []
        for shape in shapes:
            self.deselect_shape(shape)
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

    def delete_all_shapes(self, keep_locked_shapes=True):
        shapes = self.shapes[:]
        for shape in shapes:
            if not shape.locked or not keep_locked_shapes:
                self.remove_shape(shape)
        return shapes

    def select_shape(self, shape):
        if shape.select():
            self.selected_shapes.append(shape)
            return True
        return False

    def deselect_shape(self, shape):
        if shape.deselect():
            if shape in self.selected_shapes:
                self.selected_shapes.remove(shape)
            return True
        return False

    def add_shape(self, shape):
        self.shapes.append(shape)
        return True

    def clean_shape(self, shape):
        if shape is self.current_shape:
            self.finish_current_shape()
        elif shape is self.selection_shape:
            self.finish_selection_shape()
        if shape is self.selected_point_shape:
            self.clear_selected_point_shape()
        self.deselect_shape(shape)

    def remove_shape(self, shape):
        self.clean_shape(shape)
        shape.remove_paint_widget()

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
            state = shape.get_state()
            cls = self.shape_cls_map[state['cls']]
            shape = cls(paint_widget=self)
            shape.set_state(state)

            self.add_shape(shape)
            self.select_shape(shape)
            shape.translate(dpos=(5, 5))
        return shapes

    def duplicate_shape(self, shape):
        state = shape.get_state()
        cls = self.shape_cls_map[state['cls']]
        shape = cls(paint_widget=self)
        shape.set_state(state)

        self.add_shape(shape)
        shape.translate(dpos=(5, 5))
        return shape

    def delete_shapes(self):
        if self.delete_selected_point():
            return True
        if not self.current_shape and not self.selection_shape \
                and self.delete_selected_shapes():
            return True
        return False

    def lock_shape(self, shape):
        if shape.locked:
            return False

        self.clean_shape(shape)
        return shape.lock()

    def unlock_shape(self, shape):
        if shape.locked:
            return shape.unlock()
        return False

    def clean_shapes(self):
        self.finish_current_shape()
        self.finish_selection_shape()
        self.clear_selected_point_shape()
        self.clear_selected_shapes()

    def select_shape_with_touch(self, touch, deselect=True):
        pos = int(touch.x), int(touch.y)
        if deselect:
            for s in reversed(self.selected_shapes):
                if pos in s.inside_points:
                    self.deselect_shape(s)
                    return True

        for s in reversed(self.shapes):
            if s.locked:
                continue

            if pos in s.inside_points:
                if not self.multiselect and not self._ctrl_down:
                    self.clear_selected_shapes()
                self.select_shape(s)
                return True
        return False

    def collide_shape(self, x, y, selected=False, include_locked=False):
        pos = int(x), int(y)
        for s in reversed(self.selected_shapes if selected else self.shapes):
            if not include_locked and s.locked:
                continue
            if pos in s.inside_points:
                return s
        return None

    def get_closest_shape_point(self, x, y):
        shapes = self.selected_shapes or self.shapes
        if not shapes:
            return None

        dists = [(s, s.closest_point(x, y)) for s in reversed(shapes)
                 if not s.locked]
        if not dists:
            return None

        shape, (p, dist) = min(dists, key=lambda x: x[1][1])
        if dist <= self.min_touch_dist:
            return shape, p
        return None

    def on_touch_down(self, touch):
        ud = touch.ud
        ud['paint_touch'] = None  # stores the point the touch fell near
        ud['paint_drag'] = None
        ud['paint_long'] = None
        ud['paint_up'] = False
        ud['paint_used'] = False

        if self.locked:
            del ud['paint_used']
            return super(PaintCanvasBehavior, self).on_touch_down(touch)
        if super(PaintCanvasBehavior, self).on_touch_down(touch):
            return True
        if not self.collide_point(touch.x, touch.y):
            return super(PaintCanvasBehavior, self).on_touch_down(touch)
        touch.grab(self)

        self._long_touch_trigger = Clock.schedule_once(
            partial(self.do_long_touch, touch, touch.x, touch.y),
            self.long_touch_delay)
        return False

    def do_long_touch(self, touch, x, y, *largs):
        touch.push()
        touch.apply_transform_2d(self.to_widget)

        self._long_touch_trigger = None
        ud = touch.ud

        # in select mode selected_point_shape can only be selection_shape
        # or None
        shape = self.selection_shape or self.current_shape \
            or self.selected_point_shape

        # if there's a shape you can only interact with it
        res = False
        if shape:
            p, dist = shape.closest_point(touch.x, touch.y)
            if dist <= self.min_touch_dist:
                res = self.clear_selected_point_shape(exclude_point=(shape, p))
                if shape.select_point(p):
                    self.selected_point_shape = shape
                    ud['paint_touch'] = shape, p
                    res = True
            else:
                res = self.clear_selected_point_shape()
            if res:
                ud['paint_long'] = True
        elif self.select:  # in select mode select shape
            if self.select_shape_with_touch(touch):
                ud['paint_long'] = True
        else:  # select any point close enough
            val = self.get_closest_shape_point(touch.x, touch.y)
            res = self.clear_selected_point_shape()
            if val:
                if val[0].select_point(val[1]):
                    res = True
                    self.selected_point_shape = val[0]
                    ud['paint_touch'] = val
            if res or self.select_shape_with_touch(touch):
                ud['paint_long'] = True

        if ud['paint_long'] is None:
            ud['paint_long'] = False
        elif ud['paint_long']:
            ud['paint_used'] = True

        touch.pop()

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            # for move, only use normal touch, not touch outside range
            return

        ud = touch.ud
        if 'paint_used' not in ud:
            return super(PaintCanvasBehavior, self).on_touch_up(touch)

        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        if ud['paint_drag'] is False:
            if ud['paint_used']:
                return True
            return super(PaintCanvasBehavior, self).on_touch_move(touch)

        if not self.collide_point(touch.x, touch.y):
            return ud['paint_used'] or \
                super(PaintCanvasBehavior, self).on_touch_move(touch)

        draw_shape = self.selection_shape or self.current_shape

        # nothing active, then in freeform add new shape
        if ud['paint_drag'] is None and not draw_shape \
                and not ud['paint_long'] and (not self.locked or self.select) \
                and self.draw_mode == 'freeform':
            self.clear_selected_point_shape()
            if not self.select:
                self.clear_selected_shapes()

            if self.select:
                shape = self.selection_shape = self.shape_cls_map['freeform'](
                    paint_widget=self, line_color=self.line_color,
                    line_color_edit=self.line_color_selector,
                    selection_color=self.selection_color,
                    line_color_locked=self.line_color_locked)
            else:
                shape = self.current_shape = self.shape_cls_map['freeform'](
                    paint_widget=self, line_color=self.line_color,
                    line_color_edit=self.line_color_edit,
                    selection_color=self.selection_color,
                    line_color_locked=self.line_color_locked)
                self.add_shape(shape)
            shape.add_point(pos=touch.opos, source='move')
            shape.add_point(touch, source='move')

            ud['paint_drag'] = ud['paint_used'] = True
            return True

        if self.draw_mode == 'freeform' and draw_shape:
            assert ud['paint_drag'] and ud['paint_used']
            draw_shape.add_point(touch, source='move')
            return True

        shape = p = None
        if ud['paint_touch']:
            assert ud['paint_used']
            shape, p = ud['paint_touch']
        elif ud['paint_drag'] is None:
            if draw_shape:
                p, dist = draw_shape.closest_point(touch.ox, touch.oy)
                if dist <= self.min_touch_dist:
                    self.clear_selected_point_shape(
                        exclude_point=(draw_shape, p))
                    ud['paint_touch'] = draw_shape, p
                    shape = draw_shape
            elif not self.locked and not self.select:
                val = self.get_closest_shape_point(touch.ox, touch.oy)
                if val:
                    ud['paint_touch'] = shape, p = val
                    self.clear_selected_point_shape(exclude_point=(shape, p))

        if shape:
            shape.move_point(touch, p)
        elif not self.locked:
            opos = int(touch.ox), int(touch.oy)
            if (ud['paint_drag'] is None and
                    not self.select_shape_with_touch(touch, deselect=False) and
                    self.selected_shapes and
                    not any((opos in s.inside_points
                             for s in self.selected_shapes))):
                ud['paint_drag'] = False
                return False
            for s in self.selected_shapes:
                s.translate(dpos=(touch.dx, touch.dy))
        else:
            ud['paint_drag'] = False
            return False

        if ud['paint_drag'] is None:
            ud['paint_used'] = ud['paint_drag'] = True
        return True

    def on_touch_up(self, touch):
        ud = touch.ud
        if touch.grab_current is self and ud['paint_up']:
            return False
        if 'paint_used' not in ud:
            if touch.grab_current is not self:
                return super(PaintCanvasBehavior, self).on_touch_up(touch)
            return False

        ud['paint_up'] = True  # so that we don't do double on_touch_up
        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        draw_mode = self.draw_mode
        if draw_mode == 'freeform':
            self.finish_selection_shape(True)
            self.finish_current_shape()
            return True

        shape = self.current_shape or self.selection_shape
        if ud['paint_drag'] and ud['paint_touch'] is not None:
            shape, p = ud['paint_touch']
            shape.move_point_done(touch, p)

        if ud['paint_used']:
            return True
        if not self.collide_point(touch.x, touch.y):
            if touch.grab_current is not self:
                return super(PaintCanvasBehavior, self).on_touch_up(touch)
            return False

        select = self.select
        if touch.is_double_tap:
            return self.finish_selection_shape(True) or \
                self.finish_current_shape()

        if self.clear_selected_point_shape():
            return True
        if shape:
            if not select:
                if self.selected_shapes:
                    s = self.collide_shape(touch.x, touch.y, selected=True)
                    if not s and self.clear_selected_shapes():
                        return True
                    if s and self.deselect_shape(s):
                        return True

            return shape.add_point(touch, source='up')
        elif draw_mode != 'none':
            if not select:
                if self.selected_shapes:
                    s = self.collide_shape(touch.x, touch.y, selected=True)
                    if not s and self.clear_selected_shapes():
                        return True
                    if s and self.deselect_shape(s):
                        return True

            shape = self.shape_cls_map[draw_mode](
                paint_widget=self, line_color=self.line_color,
                line_color_edit=self.line_color_selector if select
                else self.line_color_edit,
                selection_color=self.selection_color,
                line_color_locked=self.line_color_locked)

            if select:
                self.selection_shape = shape
            else:
                self.current_shape = shape
                self.add_shape(shape)

            shape.add_point(touch, source='up')
            return True

        if (select or draw_mode == 'none') and \
                self.select_shape_with_touch(touch):
            return True

        if self.clear_selected_shapes():
            return True
        if touch.grab_current is not self:
            return super(PaintCanvasBehavior, self).on_touch_up(touch)
        return False

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

        return super(PaintCanvasBehavior, self).keyboard_on_key_down(
            window, keycode, text, modifiers)

    def keyboard_on_key_up(self, window, keycode):
        if keycode[1] in ('lctrl', 'ctrl', 'rctrl'):
            self._ctrl_down.remove(keycode[1])
        if keycode[1] == 'escape':
            if self.clear_selected_point_shape() or \
                    self.finish_current_shape() or \
                    self.finish_selection_shape() or \
                    self.clear_selected_shapes():
                return True
        elif keycode[1] == 'delete':
            if self.delete_shapes():
                return True
        elif keycode[1] == 'a' and self._ctrl_down:
            for shape in self.shapes:
                if not shape.locked:
                    self.select_shape(shape)
            return True
        elif keycode[1] == 'd' and self._ctrl_down:
            if self.duplicate_selected_shapes():
                return True

        return super(PaintCanvasBehavior, self).keyboard_on_key_up(
            window, keycode)

    def save_shapes(self):
        return [s.get_state() for s in self.shapes]

    def restore_shape(self, state):
        cls = self.shape_cls_map[state['cls']]
        shape = cls(paint_widget=self)
        shape.set_state(state)
        self.add_shape(shape)
        return shape


class PaintShape(EventDispatcher):

    _name_count = 0

    name = StringProperty('')

    finished = False

    selected = False

    is_valid = False

    paint_widget = None

    add_to_canvas = True

    line_width = 1

    line_color = 0, 1, 0, 1

    line_color_edit = 1, 0, 0, 1

    line_color_locked = .4, .56, .36, 1

    selection_color = 1, 1, 1, .5

    graphics_name = ''

    graphics_select_name = ''

    graphics_point_select_name = ''

    selected_point = None

    dragging = False

    _instruction_group = None

    _inside_points = None

    _centroid = None

    _bounding_box = None

    _area = None

    _collider = None

    selected_point = None

    locked = BooleanProperty(False)

    __events__ = ('on_update', )

    def __init__(
            self, paint_widget=None, line_color=(0, 1, 0, 1),
            line_color_edit=(0, 1, 0, 1), selection_color=(1, 1, 1, .5),
            line_width=1, line_color_locked=(.4, .56, .36, 1), **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = 'S{}'.format(PaintShape._name_count)
            PaintShape._name_count += 1
        super(PaintShape, self).__init__(**kwargs)
        self.paint_widget = paint_widget
        self.line_color = line_color
        self.line_color_edit = line_color_edit
        self.line_color_locked = line_color_locked
        self.selection_color = selection_color
        self.line_width = line_width
        self.graphics_name = '{}-{}'.format(self.__class__.__name__, id(self))
        self.graphics_select_name = '{}-select'.format(self.graphics_name)
        self.graphics_point_select_name = '{}-point'.format(self.graphics_name)

    def _add_shape(self):
        if not self.add_to_canvas:
            return False
        with self.paint_widget.canvas:
            self._instruction_group = InstructionGroup()
        return True

    def add_point(self, touch=None, pos=None, source='down'):
        return False

    def move_point(self, touch, point):
        return False

    def move_point_done(self, touch, point):
        return False

    def remove_paint_widget(self):
        if not self.add_to_canvas:
            return
        self._instruction_group.remove_group(self.graphics_name)
        self._instruction_group.remove_group(self.graphics_select_name)
        self._instruction_group.remove_group(self.graphics_point_select_name)

    def finish(self):
        if self.finished:
            return False
        self.finished = True
        return True

    def clean(self):
        '''Removes everything, except its selection state.
        '''
        self.clear_point_selection()

    def select(self):
        if self.selected:
            return False
        self.selected = True
        return True

    def deselect(self):
        if not self.selected:
            return False
        self.selected = False
        self._instruction_group.remove_group(self.graphics_select_name)
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

    def closest_point(self, x, y):
        pass

    def select_point(self, point):
        return False

    def delete_selected_point(self):
        return False

    def clear_point_selection(self, exclude_point=None):
        return False

    def translate(self, dpos):
        return False

    def move_to_top(self):
        if not self.add_to_canvas:
            return False
        self.paint_widget.canvas.remove(self._instruction_group)
        self.paint_widget.canvas.add(self._instruction_group)
        return True

    def collide_shape(self, shape, test_all=True):
        if test_all:
            points = shape.inside_points
            return all((p in points for p in self.inside_points))

        points_a, points_b = self.inside_points, shape.inside_points
        if len(points_a) > len(points_b):
            points_a, points_b = points_b, points_a

        for p in points_a:
            if p in points_b:
                return True
        return False

    def on_update(self, *largs):
        self._inside_points = None
        self._bounding_box = None
        self._centroid = None
        self._area = None
        self._collider = None

    def _get_collider(self, size):
        pass

    @property
    def inside_points(self):
        if not self.is_valid:
            return set()
        if self._inside_points is not None:
            return self._inside_points

        points = self._inside_points = set(self.collider.get_inside_points())
        return points

    @property
    def bounding_box(self):
        if not self.is_valid:
            return 0, 0, 0, 0
        if self._bounding_box is not None:
            return self._bounding_box

        x1, y1, x2, y2 = self.collider.bounding_box()
        box = self._bounding_box = x1, y1, x2 + 1, y2 + 1
        return box

    def get_state(self, state=None):
        d = {'paint_widget': None, 'add_shape_kwargs': {}}
        for k in ['line_color', 'line_color_edit', 'name', '_name_count',
                  'selection_color', 'line_width', 'is_valid', 'locked',
                  'line_color_locked']:
            d[k] = getattr(self, k)
        d['cls'] = self.__class__.__name__[5:].lower()

        if state is None:
            state = d
        else:
            state.update(d)
        return state

    def set_state(self, state={}):
        state.pop('cls', None)
        state.pop('paint_widget', None)
        add_shape_kw = state.pop('add_shape_kwargs', {})
        PaintShape._name_count = max(
            PaintShape._name_count,
            state.pop('_name_count', PaintShape._name_count) + 1)
        for k, v in state.items():
            setattr(self, k, v)
        self._add_shape(**add_shape_kw)
        self.finish()
        self.dispatch('on_update')

    def __deepcopy__(self, memo):
        obj = self.__class__()
        obj.set_state(self.get_state({'paint_widget': self.paint_widget}))
        return obj

    @property
    def centroid(self):
        if not self.is_valid:
            return 0, 0
        if self._centroid is not None:
            return self._centroid

        self._centroid = xc, yc = self.collider.get_centroid()
        return xc, yc

    @property
    def area(self):
        if not self.is_valid:
            return 0
        if self._area is not None:
            return self._area

        self._area = area = float(self.collider.get_area())
        return area

    @property
    def collider(self):
        if not self.is_valid:
            return None
        if self._collider is not None:
            return self._collider

        self._collider = collider = self._get_collider(self.paint_widget.size)
        return collider

    def add_shape_instructions(self, color, name, canvas):
        pass

    def set_area(self, area):
        scale = 1 / sqrt(self.area / float(area))
        self.rescale(scale)

    def rescale(self, scale):
        pass


class PaintCircle(PaintShape):

    center = None

    perim_ellipse_inst = None

    center_point_inst = None

    selection_ellipse_inst = None

    ellipse_color_inst = None

    radius = NumericProperty(dp(10))

    def __init__(self, **kwargs):
        super(PaintCircle, self).__init__(**kwargs)
        self.fbind('radius', self._update_radius)

    def _add_shape(self):
        if not super(PaintCircle, self)._add_shape():
            return False

        x, y = self.center
        r = self.radius
        inst = self.ellipse_color_inst = Color(
            *self.line_color_edit, group=self.graphics_name)
        self._instruction_group.add(inst)
        inst = self.perim_ellipse_inst = Line(
            circle=(x, y, r), width=self.line_width,
            group=self.graphics_name)
        self._instruction_group.add(inst)
        return True

    def add_point(self, touch=None, pos=None, source='down'):
        if self.perim_ellipse_inst is None:
            self.center = pos or (touch.x, touch.y)
            self._add_shape()
            self.is_valid = True
            self.dispatch('on_update')
            return True
        return False

    def move_point(self, touch, point):
        if not self.dragging:
            if point == 'center':
                inst = Color(*self.ellipse_color_inst.rgba,
                      group=self.graphics_point_select_name)
                self._instruction_group.add(inst)
                inst = self.center_point_inst = Point(
                    points=self.center[:],
                    group=self.graphics_point_select_name,
                    pointsize=max(1, min(self.radius / 2., 2)))
                self._instruction_group.add(inst)
            self.dragging = True

        if point == 'center':
            self.translate(pos=(touch.x, touch.y))
        else:
            x, y = self.center
            ndist = eucledian_dist(x, y, touch.x, touch.y)
            odist = eucledian_dist(x, y, touch.x - touch.dx,
                                   touch.y - touch.dy)
            self.radius = max(1, self.radius + ndist - odist)
        self.dispatch('on_update')
        return True

    def move_point_done(self, touch, point):
        if self.dragging:
            self._instruction_group.remove_group(
                self.graphics_point_select_name)
            self.center_point_inst = None
            self.dragging = False
            return True
        return False

    def finish(self):
        if super(PaintCircle, self).finish():
            if self.add_to_canvas:
                self.ellipse_color_inst.rgba = self.line_color
            return True
        return False

    def lock(self):
        if super(PaintCircle, self).lock():
            self.ellipse_color_inst.rgba = self.line_color_locked
            return True
        return False

    def unlock(self):
        if super(PaintCircle, self).unlock():
            self.ellipse_color_inst.rgba = self.line_color
            return True
        return False

    def add_shape_instructions(self, color, name, canvas):
        x, y = self.center
        r = self.radius
        c = Color(*color, group=name)
        e = Ellipse(size=(r * 2., r * 2.), pos=(x - r, y - r), group=name)
        canvas.add(c)
        canvas.add(e)
        return c, e

    def select(self):
        if not super(PaintCircle, self).select():
            return False
        _, self.selection_ellipse_inst = self.add_shape_instructions(
            self.selection_color, self.graphics_select_name,
            self._instruction_group)
        self.perim_ellipse_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintCircle, self).deselect():
            self.selection_ellipse_inst = None
            self.perim_ellipse_inst.width = self.line_width
            return True
        return False

    def closest_point(self, x, y):
        d = eucledian_dist(x, y, *self.center)
        r = self.radius
        if d <= r / 2.:
            return 'center', d

        if d <= r:
            return 'outside', r - d
        return 'outside', d - r

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
        if self.perim_ellipse_inst:
            self.perim_ellipse_inst.circle = x, y, r
        if self.selection_ellipse_inst:
            self.selection_ellipse_inst.pos = x - r, y - r
        if self.center_point_inst:
            self.center_point_inst.points = x, y

        self.dispatch('on_update')
        return True

    def rescale(self, scale):
        x, y = self.center
        r = self.radius = self.radius * scale

        if self.perim_ellipse_inst:
            self.perim_ellipse_inst.circle = x, y, r
        if self.selection_ellipse_inst:
            self.selection_ellipse_inst.pos = x - r, y - r

        self.dispatch('on_update')
        return True

    def _update_radius(self, *largs):
        x, y = self.center
        r = self.radius
        if self.perim_ellipse_inst:
            self.perim_ellipse_inst.circle = x, y, r
        if self.selection_ellipse_inst:
            self.selection_ellipse_inst.size = r * 2., r * 2.
            self.selection_ellipse_inst.pos = x - r, y - r

        self.dispatch('on_update')

    def _get_collider(self, size):
        x, y = self.center
        r = self.radius
        return CollideEllipse(x=x, y=y, rx=r, ry=r)

    def get_state(self, state=None):
        d = super(PaintCircle, self).get_state(state)
        for k in ['center', 'radius']:
            d[k] = getattr(self, k)
        return d


class PaintEllipse(PaintShape):

    center = None

    angle = NumericProperty(0)

    rx = NumericProperty(dp(10))

    ry = NumericProperty(dp(10))

    _second_point = None

    perim_ellipse_inst = None

    perim_rotate = None

    center_point_inst = None

    selection_ellipse_inst = None

    selection_rotate = None

    ellipse_color_inst = None

    def __init__(self, **kwargs):
        super(PaintEllipse, self).__init__(**kwargs)
        self.fbind('rx', self._update_radius)
        self.fbind('ry', self._update_radius)
        self.fbind('angle', self._update_radius)

    def _add_shape(self):
        if not super(PaintEllipse, self)._add_shape():
            return False

        x, y = self.center
        rx, ry = self.rx, self.ry
        i1 = self.ellipse_color_inst = Color(
            *self.line_color_edit, group=self.graphics_name)
        i2 = PushMatrix(group=self.graphics_name)
        i3 = self.perim_rotate = Rotate(
            angle=self.angle, origin=(x, y), group=self.graphics_name)
        i4 = self.perim_ellipse_inst = Line(
            ellipse=(x - rx, y - ry, 2 * rx, 2 * ry),
            width=self.line_width, group=self.graphics_name)
        i5 = PopMatrix(group=self.graphics_name)

        for inst in (i1, i2, i3, i4, i5):
            self._instruction_group.add(inst)
        return True

    def add_point(self, touch=None, pos=None, source='down'):
        if self.perim_ellipse_inst is None:
            self.center = pos or (touch.x, touch.y)
            self._add_shape()
            self.is_valid = True
            self.dispatch('on_update')
            return True
        return False

    def move_point(self, touch, point):
        if not self.dragging:
            if point == 'center':
                inst = Color(*self.ellipse_color_inst.rgba,
                      group=self.graphics_point_select_name)
                self._instruction_group.add(inst)
                inst = self.center_point_inst = Point(
                    points=self.center[:],
                    group=self.graphics_point_select_name,
                    pointsize=max(1, min(min(self.rx, self.ry) / 2., 2)))
                self._instruction_group.add(inst)
            self.dragging = True

        if point == 'center':
            self.translate(pos=(touch.x, touch.y))
            self.dispatch('on_update')
            return True

        if not self._second_point:
            cx, cy = self.center
            self.angle = atan2(
                touch.y - touch.dy - cy, touch.x - touch.dx - cx) * 180. / pi
            self._second_point = True

        x = touch.dx
        y = touch.dy
        if self.angle:
            angle = -self.angle * pi / 180.
            x, y = (x * cos(angle) - y * sin(angle),
                    x * sin(angle) + y * cos(angle))
        self.rx = max(1, self.rx + x)
        self.ry = max(1, self.ry + y)
        self.dispatch('on_update')
        return True

    def move_point_done(self, touch, point):
        if self.dragging:
            self._instruction_group.remove_group(
                self.graphics_point_select_name)
            self.center_point_inst = None
            self.dragging = False
            return True
        return False

    def finish(self):
        if super(PaintEllipse, self).finish():
            if self.add_to_canvas:
                self.ellipse_color_inst.rgba = self.line_color
            return True
        return False

    def lock(self):
        if super(PaintEllipse, self).lock():
            self.ellipse_color_inst.rgba = self.line_color_locked
            return True
        return False

    def unlock(self):
        if super(PaintEllipse, self).unlock():
            self.ellipse_color_inst.rgba = self.line_color
            return True
        return False

    def add_shape_instructions(self, color, name, canvas):
        x, y = self.center
        rx, ry = self.rx, self.ry
        c = Color(*color, group=name)
        p1 = PushMatrix(group=name)
        r = Rotate(angle=self.angle, origin=(x, y), group=name)
        e = Ellipse(
            size=(rx * 2., ry * 2.), pos=(x - rx, y - ry), group=name)
        p2 = PopMatrix(group=name)

        instructions = c, p1, r, e, p2
        for inst in instructions:
            canvas.add(inst)
        return instructions

    def select(self):
        if not super(PaintEllipse, self).select():
            return False
        _, _, self.selection_rotate, self.selection_ellipse_inst, _ = \
            self.add_shape_instructions(
                self.selection_color, self.graphics_select_name,
                self._instruction_group)
        self.perim_ellipse_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintEllipse, self).deselect():
            self.selection_ellipse_inst = None
            self.selection_rotate = None
            self.perim_ellipse_inst.width = self.line_width
            return True
        return False

    def closest_point(self, x, y):
        cx, cy = self.center
        rx, ry = self.rx, self.ry
        collider = CollideEllipse(x=cx, y=cy, rx=rx, ry=ry, angle=self.angle)
        dist = collider.estimate_distance(x, y)
        center_dist = eucledian_dist(cx, cy, x, y)

        if not collider.collide_point(x, y) or dist < center_dist:
            return 'outside', dist
        return 'center', center_dist

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

        rx, ry = self.rx, self.ry
        self.center = x, y
        if self.perim_ellipse_inst:
            self.perim_ellipse_inst.ellipse = x - rx, y - ry, 2 * rx, 2 * ry
            self.perim_rotate.origin = x, y
        if self.selection_ellipse_inst:
            self.selection_ellipse_inst.pos = x - rx, y - ry
            self.selection_rotate.origin = x, y
        if self.center_point_inst:
            self.center_point_inst.points = x, y

        self.dispatch('on_update')
        return True

    def rescale(self, scale):
        x, y = self.center
        rx, ry = self.rx, self.ry = self.rx * scale, self.ry * scale

        if self.perim_ellipse_inst:
            self.perim_ellipse_inst.ellipse = x - rx, y - ry, 2 * rx, 2 * ry
        if self.selection_ellipse_inst:
            self.selection_ellipse_inst.pos = x - rx, y - ry

        self.dispatch('on_update')
        return True

    def _update_radius(self, *largs):
        x, y = self.center
        rx, ry = self.rx, self.ry
        if self.perim_ellipse_inst:
            self.perim_ellipse_inst.ellipse = x - rx, y - ry, 2 * rx, 2 * ry
            self.perim_rotate.angle = self.angle
            self.perim_rotate.origin = x, y
        if self.selection_ellipse_inst:
            self.selection_ellipse_inst.size = rx * 2., ry * 2.
            self.selection_ellipse_inst.pos = x - rx, y - ry
            self.selection_rotate.angle = self.angle
            self.selection_rotate.origin = x, y

        self.dispatch('on_update')

    def _get_collider(self, size):
        x, y = self.center
        rx, ry = self.rx, self.ry
        return CollideEllipse(x=x, y=y, rx=rx, ry=ry, angle=self.angle)

    def get_state(self, state=None):
        d = super(PaintEllipse, self).get_state(state)
        for k in ['center', 'angle', 'rx', 'ry', '_second_point']:
            d[k] = getattr(self, k)
        return d


class PaintPolygon(PaintShape):

    perim_inst = None

    perim_point_inst = None

    selection_inst = None

    selection_point_inst = None

    perim_color_inst = None

    line_type_name = 'points'

    def _locate_point(self, i, x, y):
        points = self.perim_inst.points
        if len(points) > i:
            return i

        try:
            i = 0
            while True:
                i = points.index(x, i)
                if i != len(points) - 1 and points[i + 1] == y:
                    return i
                i += 1
        except ValueError:
            return 0

    def _get_points(self):
        return self.perim_inst and self.perim_inst.points

    def _update_points(self, points):
        self.perim_inst.flag_update()

    def _get_perim_points(self, points):
        return Point(points=points, group=self.graphics_name, pointsize=2)

    def _add_shape(self, points):
        if not super(PaintPolygon, self)._add_shape():
            self.perim_inst = Line(
                width=self.line_width, close=False,
                group=self.graphics_name, **{self.line_type_name: []})
            pts = self._get_points()
            pts += points
            self._update_points(pts)
            return False

        inst = self.perim_color_inst = Color(
            *self.line_color_edit, group=self.graphics_name)
        self._instruction_group.add(inst)

        inst = self.perim_inst = Line(
            width=self.line_width, close=False,
            group=self.graphics_name, **{self.line_type_name: []})
        self._instruction_group.add(inst)
        pts = self._get_points()
        pts += points
        self._update_points(pts)

        inst = self.perim_point_inst = self._get_perim_points(points)
        self._instruction_group.add(inst)
        return True

    def add_point(self, touch=None, pos=None, source='down'):
        line = self.perim_inst
        x, y = pos or (touch.x, touch.y)
        if line is None:
            self._add_shape([x, y])
            self.dispatch('on_update')
            return True

        points = self._get_points()
        if not points or int(points[-2]) != (x) \
                or int(points[-1]) != (y):
            points += [x, y]
            self._update_points(points)
            self.perim_point_inst.points += [x, y]
            self.perim_point_inst.flag_update()
            if self.selection_inst is not None:
                self._update_mesh()
            if not self.is_valid and len(points) >= 6:
                self.is_valid = True
            self.dispatch('on_update')
            return True
        self.dispatch('on_update')
        return False

    def move_point(self, touch, point):
        points = self._get_points()
        perim_points_inst = self.perim_point_inst
        perim_points = perim_points_inst.points
        if not points:
            return False

        i = self._locate_point(*point)

        if not self.dragging:
            self.dragging = True

        points[i] = touch.x
        points[i + 1] = touch.y
        perim_points[i] = touch.x
        perim_points[i + 1] = touch.y

        self._update_points(points)
        perim_points_inst.flag_update()

        if self.selection_inst is not None:
            self._update_mesh()
        self.dispatch('on_update')
        return True

    def move_point_done(self, touch, point):
        if self.dragging:
            self.dragging = False
            return True
        return False

    def finish(self):
        if super(PaintPolygon, self).finish():
            if self.add_to_canvas:
                self.perim_color_inst.rgba = self.line_color
                self.perim_inst.close = True
            return True
        return False

    def lock(self):
        if super(PaintPolygon, self).lock():
            self.perim_color_inst.rgba = self.line_color_locked
            return True
        return False

    def unlock(self):
        if super(PaintPolygon, self).unlock():
            self.perim_color_inst.rgba = self.line_color
            return True
        return False

    def _get_poly_points(self, points):
        return points

    def add_shape_instructions(self, color, name, canvas):
        points = self._get_points()
        if not points or not len(points) // 2:
            return []
        points = self._get_poly_points(points)

        meshes = []
        tess = Tesselator()

        tess.add_contour(points)
        if tess.tesselate():
            meshes.append(Color(*color, group=name))
            for vertices, indices in tess.meshes:
                m = Mesh(
                    vertices=vertices, indices=indices,
                    mode='triangle_fan', group=name)
                meshes.append(m)

        for inst in meshes:
            canvas.add(inst)
        return meshes

    def _update_mesh(self):
        self._instruction_group.remove_group(self.graphics_select_name)
        self.selection_inst = self.add_shape_instructions(
            self.selection_color, self.graphics_select_name,
            self._instruction_group)

    def select(self):
        if not super(PaintPolygon, self).select():
            return False
        self._update_mesh()
        self.perim_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintPolygon, self).deselect():
            self.selection_inst = None
            self.perim_inst.width = self.line_width
            return True
        return False

    def closest_point(self, x, y):
        points = self._get_points()
        if not points:
            return ((None, None, None), 1e12)
        i = min(range(len(points) // 2),
                key=lambda i: eucledian_dist(x, y, points[2 * i],
                                             points[2 * i + 1]))
        i *= 2
        px, py = points[i], points[i + 1]
        return ((i, px, py), eucledian_dist(x, y, px, py))

    def select_point(self, point):
        i, x, y = point
        points = self._get_points()

        if i is None or not points:
            return False
        i = self._locate_point(i, x, y)

        if self.selected_point:
            self.clear_point_selection()
        self.selected_point = point

        assert not self.selection_point_inst
        inst = Color(*self.perim_color_inst.rgba,
              group=self.graphics_point_select_name)
        self._instruction_group.add(inst)
        inst = self.selection_point_inst = Point(
            points=[points[i], points[i + 1]],
            group=self.graphics_point_select_name,
            pointsize=3)
        self._instruction_group.add(inst)
        return True

    def delete_selected_point(self):
        point = self.selected_point
        if point is None:
            return False

        i, x, y = point
        points = self._get_points()
        if i is None or not points:
            return False
        i = self._locate_point(i, x, y)
        self.dispatch('on_update')

        self.clear_point_selection()
        if len(points) <= 6:
            self.dispatch('on_update')
            return True

        del points[i:i + 2]
        del self.perim_point_inst.points[i:i + 2]
        self._update_points(points)
        self.perim_point_inst.flag_update()

        if self.selection_inst is not None:
            self._update_mesh()
        self.dispatch('on_update')
        return True

    def clear_point_selection(self, exclude_point=None):
        point = self.selected_point
        if point is None:
            return False

        if point[0] is None or exclude_point == point:
            return False
        self._instruction_group.remove_group(self.graphics_point_select_name)
        self.selection_point_inst = None
        return True

    def translate(self, dpos):
        dx, dy = dpos
        points = self._get_points()
        if not points:
            return False

        perim_points = self.perim_point_inst.points
        for i in range(len(points) // 2):
            i *= 2
            points[i] += dx
            points[i + 1] += dy
            perim_points[i] += dx
            perim_points[i + 1] += dy

        self._update_points(points)
        self.perim_point_inst.flag_update()

        if self.selection_point_inst:
            x, y = self.selection_point_inst.points
            self.selection_point_inst.points = [x + dx, y + dy]

        if self.selection_inst:
            for mesh in self.selection_inst[1:]:
                verts = mesh.vertices
                for i in range(len(verts) // 4):
                    i *= 4
                    verts[i] += dx
                    verts[i + 1] += dy
                mesh.vertices = verts
        self.dispatch('on_update')
        return True

    def rescale(self, scale):
        points = self._get_points()
        if not points:
            return False

        cx, cy = self.centroid
        perim_points = self.perim_point_inst.points
        for i in range(len(points) // 2):
            i *= 2
            perim_points[i] = points[i] = (points[i] - cx) * scale + cx
            perim_points[i + 1] = points[i + 1] = \
                (points[i + 1] - cy) * scale + cy

        self._update_points(points)
        self.perim_point_inst.flag_update()

        if self.selection_point_inst:
            x, y = self.selection_point_inst.points
            self.selection_point_inst.points = [
                (x - cx) * scale + cx, (y - cy) * scale + cy]

        if self.selection_inst:
            for mesh in self.selection_inst[1:]:
                verts = mesh.vertices
                for i in range(len(verts) // 4):
                    i *= 4
                    verts[i] = (verts[i] - cx) * scale + cx
                    verts[i + 1] = (verts[i + 1] - cy) * scale + cy
                mesh.vertices = verts
        self.dispatch('on_update')
        return True

    def _get_collider(self, size):
        return Collide2DPoly(points=self.perim_inst.points, cache=True)

    def get_state(self, state=None):
        d = super(PaintPolygon, self).get_state(state)
        d['add_shape_kwargs'] = {'points': self._get_points()}
        return d


class PaintBezier(PaintPolygon):

    points = None

    line_type_name = 'bezier'

    def __init__(self, **kwargs):
        super(PaintBezier, self).__init__(**kwargs)
        self.points = []

    def _get_points(self):
        return self.points

    def _update_points(self, points):
        self.perim_inst.bezier = points + points[:2]

    def _get_poly_points(self, points):
        return CollideBezier.convert_to_poly(points + points[:2])

    def _get_collider(self, size):
        return CollideBezier(points=self.points + self.points[:2], cache=True)

PaintCanvasBehavior.shape_cls_map = {
    'circle': PaintCircle, 'ellipse': PaintEllipse,
    'polygon': PaintPolygon, 'freeform': PaintPolygon,
    'bezier': PaintBezier
}
