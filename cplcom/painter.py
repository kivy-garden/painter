
from functools import partial
from itertools import product

from kivy.uix.widget import Widget
from kivy.uix.behaviors.focus import FocusBehavior
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import OptionProperty, BooleanProperty, NumericProperty
from kivy.graphics import Ellipse, Line, Color, Point, Mesh
from kivy.event import EventDispatcher

from kivy.garden.collider import CollideEllipse, Collide2DPoly


def eucledian_dist(x1, y1, x2, y2):
    return pow(pow(x1 - x2, 2) + pow(y1 - y2, 2), 0.5)


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
                    if shape.collide_shape(selection):
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

    def remove_shape(self, shape):
        if shape is self.current_shape:
            self.finish_current_shape()
        elif shape is self.selection_shape:
            self.finish_selection_shape()
        if shape is self.selected_point_shape:
            self.clear_selected_point_shape()
        self.deselect_shape(shape)
        shape.remove_paint_widget()
        self.shapes.remove(shape)

    def on_touch_down(self, touch):
        touch.ud['paint_touch'] = None  # stores the point the touch fell near
        touch.ud['paint_drag'] = None
        touch.ud['paint_long'] = False
        touch.ud['paint_up'] = False

        if super(PaintCanvas, self).on_touch_down(touch):
            return True
        if not self.collide_point(touch.x, touch.y):
            return super(PaintCanvas, self).on_touch_down(touch)
        touch.grab(self)

        shape = self.current_shape or self.selection_shape \
            or self.selected_point_shape

        # add freeform shape if nothing is active
        res = False
        if not shape and (not self.locked or self.select) \
                and self.draw_mode == 'freeform':
            self.clear_selected_point_shape()
            res = True
            if self.select:
                shape = self.selection_shape = _cls_map['freeform'](
                    paint_widget=self)
                self.selection_shape.add_point(touch, source='down')
            else:
                shape = self.current_shape = _cls_map['freeform'](
                    paint_widget=self)
                self.shapes.append(shape)
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

        self._long_touch_trigger = Clock.schedule_once(
            partial(self.do_long_touch, touch, touch.x, touch.y),
            self.long_touch_delay)
        if not touch.ud['paint_touch']:
            touch.ud['paint_drag'] = False
        return res

    def do_long_touch(self, touch, x, y, *largs):
        self._long_touch_trigger = None
        if touch.ud['paint_touch']:
            shape, p = touch.ud['paint_touch']
        else:
            shape = p = None

        obj_shape = self.selection_shape or self.current_shape \
            or self.selected_point_shape

        if shape is obj_shape and obj_shape is not None:
            res = False
            if self.clear_selected_point_shape(exclude_point=(shape, p)):
                res = shape.select_point(p)
            if res:
                touch.ud['paint_long'] = True
        elif not obj_shape and self.select:
            for s in reversed(self.shapes):
                if (x, y) in s.inside_points:
                    if not self.add_selection:
                        self.clear_selected_shapes()
                    self.select_shape(s)
                    touch.ud['paint_long'] = True
                    break
        elif shape in self.shapes and not obj_shape:
            self.clear_selected_point_shape()
            if shape.select_point(p):
                touch.ud['paint_long'] = True
                self.selected_point_shape = shape

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            return

        if 'paint_up' not in touch.ud:
            return super(PaintCanvas, self).on_touch_up(touch)

        if self._long_touch_trigger:
            self._long_touch_trigger.cancel()
            self._long_touch_trigger = None

        if touch.ud['paint_drag'] is False:
            if touch.ud['paint_long']:
                return True
            return super(PaintCanvas, self).on_touch_move(touch)

        if not self.collide_point(touch.x, touch.y):
            return touch.ud['paint_long'] or \
                super(PaintCanvas, self).on_touch_move(touch)

        # if paint_drag is not False, we have a touch in paint_touch
        # now it could be the first move
        shape, p = touch.ud['paint_touch']
        draw_shape = self.selection_shape or self.current_shape
        obj_shape = draw_shape or self.selected_point_shape

        if shape is draw_shape and self.draw_mode == 'freeform' \
                and not self.locked:
            shape.add_point(touch, source='move')
        elif shape is obj_shape or shape in self.shapes and not obj_shape and \
                not self.locked and not self.select:
            shape.move_point(touch, p)
            if touch.ud['paint_drag'] is None:
                self.clear_selected_point_shape(exclude_point=(shape, p))
        elif not self.locked and not obj_shape and self.selected_shapes:
            for shape in self.selected_shapes:
                shape.translate(dpos=(touch.dx, touch.dy))

        if touch.ud['paint_drag'] is None:
            touch.ud['paint_drag'] = True
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self and touch.ud['paint_up']:
            return
        if 'paint_up' not in touch.ud:
            if touch.grab_current is not self:
                return super(PaintCanvas, self).on_touch_up(touch)
            return

        touch.ud['paint_up'] = True
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

        if touch.ud['paint_drag']:
            shape, p = touch.ud['paint_touch']
            shape.move_point_done(touch, p)

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
                    s = self.current_shape = _cls_map[self.draw_mode](
                        paint_widget=self)
                    self.shapes.append(s)
                    self.current_shape.add_point(touch, source='up')
            return True

        if self.clear_selected_point_shape():
            return True

        if shape:
            return shape.add_point(touch, source='up')

        if self.clear_selected_shapes():
            return True
        if touch.grab_current is not self:
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
            for shape in self.shapes:
                self.select_shape(shape)
            return True

        return super(PaintCanvas, self).keyboard_on_key_up(
            window, keycode)


class PaintShape(EventDispatcher):

    finished = False

    selected = False

    paint_widget = None

    line_width = 1

    line_color = 0, 1, 0, 1

    line_color_edit = 1, 0, 0, 1

    selection_color = 1, 1, 1, .5

    graphics_name = ''

    graphics_select_name = ''

    graphics_point_select_name = ''

    selected_point = None

    dragging = False

    _inside_points = None

    selected_point = None

    def __init__(self, paint_widget, **kwargs):
        super(PaintShape, self).__init__(**kwargs)
        self.paint_widget = paint_widget
        self.graphics_name = '{}-{}'.format(self.__class__.__name__, id(self))
        self.graphics_select_name = '{}-select'.format(self.graphics_name)
        self.graphics_point_select_name = '{}-point'.format(self.graphics_name)

    def add_point(self, touch, source='down'):
        return False

    def move_point(self, touch, point):
        return False

    def move_point_done(self, touch, point):
        return False

    def remove_paint_widget(self):
        if not self.paint_widget:
            return
        self.paint_widget.canvas.remove_group(self.graphics_name)
        self.paint_widget.canvas.remove_group(self.graphics_select_name)
        self.paint_widget.canvas.remove_group(self.graphics_point_select_name)

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
        self.paint_widget.canvas.remove_group(self.graphics_select_name)
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

    def _get_collider(self, size):
        pass

    @property
    def inside_points(self):
        if self._inside_points is not None:
            return self._inside_points

        collide = self._get_collider(self.paint_widget.size).collide_point
        points = self._inside_points = set()
        w, h = self.paint_widget.size

        for x, y in product(range(int(w)), range(int(h))):
            if collide(x, y):
                points.add((x, y))
        return points


class PaintCircle(PaintShape):

    center = None

    ellipse = None

    center_point = None

    selection_ellipse = None

    ellipse_color = None

    radius = NumericProperty(dp(10))

    def __init__(self, **kwargs):
        super(PaintCircle, self).__init__(**kwargs)
        self.fbind('radius', self._update_radius)

    def add_point(self, touch, source='down'):
        if self.ellipse is None:
            x, y = self.center = touch.x, touch.y
            r = self.radius
            with self.paint_widget.canvas:
                self.ellipse_color = Color(*self.line_color_edit,
                                           group=self.graphics_name)
                self.ellipse = Line(
                    circle=(x, y, r), width=self.line_width,
                    group=self.graphics_name)
            self._inside_points = None
            return True
        return False

    def move_point(self, touch, point):
        if not self.dragging:
            if point == 'center':
                with self.paint_widget.canvas:
                    Color(*self.ellipse_color.rgba,
                          group=self.graphics_point_select_name)
                    self.center_point = Point(
                        points=self.center[:],
                        group=self.graphics_point_select_name,
                        pointsize=max(1, min(self.radius / 2., 2)))
            self.dragging = True

        if point == 'center':
            self.translate(pos=(touch.x, touch.y))
        else:
            x, y = self.center
            ndist = eucledian_dist(x, y, touch.x, touch.y)
            odist = eucledian_dist(x, y, touch.x - touch.dx,
                                   touch.y - touch.dy)
            self.radius = max(1, self.radius + ndist - odist)
        self._inside_points = None
        return True

    def move_point_done(self, touch, point):
        if self.dragging:
            self.paint_widget.canvas.remove_group(
                self.graphics_point_select_name)
            self.center_point = None
            self.dragging = False
            return True
        return False

    def finish(self):
        if super(PaintCircle, self).finish():
            self.ellipse_color.rgba = self.line_color
            return True
        return False

    def select(self):
        if not super(PaintCircle, self).select():
            return False
        x, y = self.center
        r = self.radius
        with self.paint_widget.canvas:
            Color(*self.selection_color, group=self.graphics_select_name)
            self.selection_ellipse = Ellipse(
                size=(r * 2., r * 2.), pos=(x - r, y - r),
                group=self.graphics_select_name)
        self.ellipse.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintCircle, self).deselect():
            self.selection_ellipse = None
            self.ellipse.width = self.line_width
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
        if self.ellipse:
            self.ellipse.circle = x, y, r
        if self.selection_ellipse:
            self.selection_ellipse.pos = x - r, y - r
        if self.center_point:
            self.center_point.points = (x, y)

        self._inside_points = None
        return True

    def _update_radius(self, *largs):
        x, y = self.center
        r = self.radius
        if self.ellipse:
            self.ellipse.circle = x, y, r
        if self.selection_ellipse:
            self.selection_ellipse.size = r * 2., r * 2.
            self.selection_ellipse.pos = x - r, y - r

        self._inside_points = None

    def _get_collider(self, size):
        x, y = self.center
        r = self.radius
        return CollideEllipse(x=x, y=y, rx=r, ry=r)


class PaintEllipse(PaintShape):
    pass


class PaintPolygon(PaintShape):

    perim_inst = None

    selection_inst = None

    selection_point_inst = None

    perim_color_inst = None

    def _locate_point(self, i, x, y):
        points = self.perim_inst.points
        if len(points) > i and points[i] == x and points[i + 1] == y:
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

    def add_point(self, touch, source='down'):
        self._inside_points = None
        line = self.perim_inst
        if line is None:
            with self.paint_widget.canvas:
                self.perim_color_inst = Color(
                    *self.line_color_edit, group=self.graphics_name)
                self.perim_inst = Line(
                    points=[touch.x, touch.y], width=self.line_width,
                    close=False, group=self.graphics_name)
            return True

        points = line.points
        if not points or int(points[-2]) != (touch.x) \
                or int(points[-1]) != (touch.y):
            line.points = points + [touch.x, touch.y]
            return True
        return False

    def move_point(self, touch, point):
        line = self.perim_inst
        points = line.points
        if not points:
            return False

        self._inside_points = None
        i = self._locate_point(*point)

        if not self.dragging:
            with self.paint_widget.canvas:
                assert not self.selection_point_inst
                Color(*self.perim_color_inst.rgba,
                      group=self.graphics_point_select_name)
                self.selection_point_inst = Point(
                    points=[points[i], points[i + 1]],
                    group=self.graphics_point_select_name,
                    pointsize=2)
            self.dragging = True

        ppoints = self.selection_point_inst.points
        points[i] = ppoints[0] = touch.x
        points[i + 1] = ppoints[1] = touch.y
        line.points = points
        self.selection_point_inst.points = ppoints
        return True

    def move_point_done(self, touch, point):
        if self.dragging:
            self.paint_widget.canvas.remove_group(
                self.graphics_point_select_name)
            self.selection_point_inst = None
            self.dragging = False
            return True
        return False

    def finish(self):
        if super(PaintPolygon, self).finish():
            self.perim_color_inst.rgba = self.line_color
            self.perim_inst.close = True
            return True
        return False

    def select(self):
        if not super(PaintPolygon, self).select():
            return False
        points = self.perim_inst.points
        n = len(points) // 2
        if not n:
            return True

        vertices = [0, ] * (n * 4)
        for i in range(n):
            vertices[4 * i] = points[2 * i]
            vertices[4 * i + 1] = points[2 * i + 1]

        with self.paint_widget.canvas:
            Color(*self.selection_color, group=self.graphics_select_name)
            self.selection_inst = Mesh(
                vertices=vertices, indices=range(n), mode='triangle_fan',
                group=self.graphics_select_name)
        self.perim_inst.width = 2 * self.line_width
        return True

    def deselect(self):
        if super(PaintPolygon, self).deselect():
            self.selection_inst = None
            self.perim_inst.width = self.line_width
            return True
        return False

    def closest_point(self, x, y):
        points = self.perim_inst.points
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
        points = self.perim_inst.points

        if i is None or not points:
            return False
        i = self._locate_point(i, x, y)

        if self.selected_point:
            self.clear_point_selection()
        self.selected_point = point

        with self.paint_widget.canvas:
            assert not self.selection_point_inst
            Color(*self.perim_color_inst.rgba,
                  group=self.graphics_point_select_name)
            self.selection_point_inst = Point(
                points=[points[i], points[i + 1]],
                group=self.graphics_point_select_name,
                pointsize=2)
        return True

    def delete_selected_point(self):
        point = self.selected_point
        if point is None:
            return False

        i, x, y = point
        points = self.perim_inst.points
        if i is None or not points:
            return False
        i = self._locate_point(i, x, y)
        self._inside_points = None

        self.clear_point_selection()
        del points[i:i + 2]
        self.perim_inst.points = points
        return True

    def clear_point_selection(self, exclude_point=None):
        point = self.selected_point
        if point is None:
            return False

        if point[0] is None or exclude_point == point:
            return False
        self.paint_widget.canvas.remove_group(
            self.graphics_point_select_name)
        self.selection_point_inst = None
        return True

    def translate(self, dpos):
        dx, dy = dpos
        points = self.perim_color_inst.points
        if not points:
            return False

        for i in range(len(points) // 2):
            i *= 2
            points[i] += dx
            points[i + 1] += dy

        self.perim_color_inst.points = points

        if self.selection_point_inst:
            x, y = self.selection_point_inst.points
            self.selection_point_inst.points = [x + dx, y + dy]

    def _get_collider(self, size):
        return Collide2DPoly(points=self.perim_inst.points, cache=True)


class PaintBezier(PaintShape):
    pass

_cls_map = {
    'circle': PaintCircle, 'ellipse': PaintEllipse,
    'polygon': PaintPolygon, 'freeform': PaintPolygon,
    'bezier': PaintBezier
}
