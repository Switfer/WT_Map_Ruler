#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GdkX11, GObject, GLib
import math
import cairo
import os
import configparser
import subprocess
import webbrowser

# Список масштабов карт
MAP_SCALES = [150, 170, 180, 190, 200, 225, 250, 275, 300, 325, 350, 400, 450, 500, 550]

class MapRuler(Gtk.Window):
    def __init__(self):
        super().__init__(title="Дальномер для War Thunder")
        self.set_default_size(500, 480)
        self.set_app_paintable(True)
        self.set_skip_taskbar_hint(True)
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_visual(self.get_screen().get_rgba_visual())
        self.set_position(Gtk.WindowPosition.CENTER)

        # Настройки
        self.selected_scale = 225  # Масштаб по умолчанию
        self.scale_factor = 1.0  # Будет установлено после калибровки
        self.start_point = None
        self.end_point = None
        self.temp_point = None
        self.last_focus = None
        self.horizontal_only = False
        self.calibration_mode = False
        self.dragging = False
        self.drag_corner = None
        self.drag_start = None

        # Калибровочные значения
        self.calibrated_scale = None
        self.use_calibrated_scale = False
        self.calibration_base_scale = None  # Масштаб, использованный при калибровке
        self.grid_size = 200  # Начальный размер сетки
        self.grid_pos = (100, 100)  # Начальная позиция сетки

        # Загрузка сохраненных настроек
        self.config_file = os.path.expanduser("~/.wt_map_ruler_calibration.ini")
        self.load_config()

        # Если есть сохранённая калибровка, используем её
        if self.use_calibrated_scale and self.calibrated_scale:
            self.scale_factor = self.calibrated_scale

        # Создаем основной контейнер
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.add(self.box)

        # Создаем панель управления
        self.create_control_panel()

        # Обработчики событий
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_mouse_move)
        self.connect("key-press-event", self.on_key_press)
        self.connect("key-release-event", self.on_key_release)
        self.connect("destroy", self.on_destroy)
        self.connect("realize", self.on_realize)
        self.set_events(Gdk.EventMask.POINTER_MOTION_MASK |
                       Gdk.EventMask.BUTTON_PRESS_MASK |
                       Gdk.EventMask.BUTTON_RELEASE_MASK |
                       Gdk.EventMask.KEY_PRESS_MASK |
                       Gdk.EventMask.KEY_RELEASE_MASK)
        self.set_opacity(0.85)

    def on_realize(self, widget):
        screen = self.get_screen()
        monitor = screen.get_display().get_primary_monitor()
        geometry = monitor.get_geometry()
        width, height = self.get_size()
        x = geometry.x + geometry.width - width
        y = geometry.y + geometry.height - height
        self.move(x, y)
        self.set_keep_above(True)
        self.set_accept_focus(False)

    def load_config(self):
        config = configparser.ConfigParser()
        if os.path.exists(self.config_file):
            config.read(self.config_file)
            try:
                # Калиброванные значения
                self.calibrated_scale = float(config.get('CALIBRATION', 'calibrated_scale', fallback="0"))
                if self.calibrated_scale <= 0:
                    self.calibrated_scale = None
                self.use_calibrated_scale = config.getboolean('CALIBRATION', 'use_calibrated_scale', fallback=False)
                self.calibration_base_scale = config.getint('CALIBRATION', 'calibration_base_scale', fallback=None)

                # Загружаем сохраненные параметры сетки
                self.grid_size = config.getfloat('GRID', 'grid_size', fallback=200)
                self.grid_pos = (
                    config.getfloat('GRID', 'grid_x', fallback=100),
                    config.getfloat('GRID', 'grid_y', fallback=100)
                )
            except (ValueError, configparser.NoOptionError):
                self.calibrated_scale = None
                self.use_calibrated_scale = False
                self.calibration_base_scale = None

    def save_config(self):
        config = configparser.ConfigParser()
        config['CALIBRATION'] = {
            'calibrated_scale': str(self.calibrated_scale) if self.calibrated_scale is not None else "0",
            'use_calibrated_scale': str(self.use_calibrated_scale),
            'calibration_base_scale': str(self.calibration_base_scale) if self.calibration_base_scale is not None else "0"
        }

        # Сохраняем параметры сетки
        config['GRID'] = {
            'grid_size': str(self.grid_size),
            'grid_x': str(self.grid_pos[0]),
            'grid_y': str(self.grid_pos[1])
        }

        with open(self.config_file, 'w') as configfile:
            config.write(configfile)

    def recalculate_scale(self):
        # Обновляем отображение
        if hasattr(self, 'scale_value'):
            if self.use_calibrated_scale and self.calibrated_scale is not None:
                base_text = f" (база: {self.calibration_base_scale} м)" if self.calibration_base_scale else ""
                self.scale_value.set_text(f"{self.scale_factor:.6f} м/пикс{base_text}")
            else:
                self.scale_value.set_text(f"{self.scale_factor:.6f} м/пикс")

        if hasattr(self, 'distance_value') and self.start_point and self.end_point:
            self.update_distance_display()

    def on_destroy(self, widget):
        self.save_config()
        Gtk.main_quit()

    def show_help(self, widget):
        """Показывает окно с инструкцией"""
        dialog = Gtk.Dialog(title="Инструкция", parent=self)
        dialog.set_default_size(450, 400)
        dialog.set_border_width(10)

        # Создаем область содержимого
        content_area = dialog.get_content_area()

        # Создаем текстовый буфер
        text_buffer = Gtk.TextBuffer()
        text_buffer.set_text(
            "📏 Дальномер для War Thunder - Инструкция\n\n"
            "⚙️ Основное использование:\n"
            "1. Правой кнопкой мыши установите начальную точку (точка А)\n"
            "2. Левой кнопкой мыши установите/переместите конечную точку (точка Б)\n"
            "3. Расстояние автоматически отобразится в интерфейсе\n\n"

            "🎯 Калибровка (для точных измерений):\n"
            "1. Нажмите кнопку 'Калибровать'\n"
            "2. Перетащите сетку 7x7 и совместите её с картой в игре\n"
            "   - Зажмите левую кнопку мыши на углу сетки для изменения размера\n"
            "   - Зажмите левую кнопку мыши внутри сетки для перемещения\n"
            "3. Выберите масштаб карты из выпадающего списка\n"
            "4. Нажмите 'Применить калибровку'\n"
            "5. Теперь все измерения будут точными!\n\n"

            "⌨️ Горячие клавиши:\n"
            "• R - Сбросить точки измерения\n"
            "• T - Переключить режим 'Поверх всех окон'\n"
            "• C - Переключить режим калибровка/измерение\n"
            "• Y - Открыть YouTube канал EXTRUD\n"
            "• ESC - Закрыть приложение\n\n"

            "💡 Советы:\n"
            "• Калибровку нужно выполнять один раз для каждого разрешения экрана\n"
            "• Калиброванные значения сохраняются между запусками программы\n"
            "• Для лучшей точности калибруйтесь на максимальном масштабе карты"
        )

        # Создаем текстовое поле
        text_view = Gtk.TextView(buffer=text_buffer)
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)

        # Добавляем текстовое поле в прокручиваемую область
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_min_content_height(300)
        scrolled_window.add(text_view)

        content_area.pack_start(scrolled_window, True, True, 0)

        # Добавляем кнопку закрытия
        dialog.add_button("Закрыть", Gtk.ResponseType.CLOSE)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def create_control_panel(self):
        self.control_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.control_box.set_margin_start(5)
        self.control_box.set_margin_end(5)
        self.control_box.set_margin_top(5)
        self.box.pack_start(self.control_box, False, False, 0)

        # Кнопка переключения режима калибровки/измерения
        self.mode_btn = Gtk.Button(label="Калибровать" if not self.calibration_mode else "Линейка")
        self.mode_btn.set_tooltip_text("Переключить режим калибровки/измерения")
        self.mode_btn.connect("clicked", self.toggle_mode)
        self.control_box.pack_start(self.mode_btn, False, False, 0)

        self.scale_label = Gtk.Label(label="Масштаб карты:")
        self.control_box.pack_start(self.scale_label, False, False, 0)

        self.scale_combo = Gtk.ComboBoxText()
        for scale in MAP_SCALES:
            self.scale_combo.append_text(str(scale))
        # Устанавливаем масштаб по умолчанию (225)
        self.scale_combo.set_active(MAP_SCALES.index(225))
        self.scale_combo.connect("changed", self.on_scale_changed)
        self.control_box.pack_start(self.scale_combo, False, False, 0)

        self.scale_label = Gtk.Label(label="Текущий масштаб:")
        self.control_box.pack_start(self.scale_label, False, False, 0)

        scale_text = f"{self.scale_factor:.6f} м/пикс"
        if self.use_calibrated_scale and self.calibrated_scale is not None:
            scale_text += " (калибр)"
        self.scale_value = Gtk.Label(label=scale_text)
        self.scale_value.get_style_context().add_class("scale-value")
        self.control_box.pack_start(self.scale_value, False, False, 0)

        self.distance_label = Gtk.Label(label="Дистанция:")
        self.control_box.pack_start(self.distance_label, False, False, 0)

        self.distance_value = Gtk.Label(label="0.00 м")
        self.distance_value.get_style_context().add_class("distance-value")
        self.control_box.pack_start(self.distance_value, False, False, 0)

        # Кнопка применения калибровки (видна только в режиме калибровки)
        self.apply_btn = Gtk.Button(label="Применить калибровку")
        self.apply_btn.set_visible(False)
        self.apply_btn.connect("clicked", self.apply_calibration)
        self.control_box.pack_start(self.apply_btn, False, False, 0)

        self.reset_btn = Gtk.Button(label="↺")
        self.reset_btn.get_style_context().add_class("reset-btn")
        self.reset_btn.connect("clicked", self.reset_points)
        self.reset_btn.set_tooltip_text("Сбросить точки измерения (R)")
        self.control_box.pack_end(self.reset_btn, False, False, 0)

        self.youtube_btn = Gtk.Button(label="Y")
        self.youtube_btn.get_style_context().add_class("youtube-btn")
        self.youtube_btn.connect("clicked", lambda w: webbrowser.open("https://www.youtube.com/@EXTRUD/shorts"))
        self.youtube_btn.set_tooltip_text("YouTube канал EXTRUD")
        self.control_box.pack_end(self.youtube_btn, False, False, 0)

        # Кнопка помощи (вопросительный знак)
        self.help_btn = Gtk.Button(label="?")
        self.help_btn.get_style_context().add_class("help-btn")
        self.help_btn.connect("clicked", self.show_help)
        self.help_btn.set_tooltip_text("Показать инструкцию")
        self.control_box.pack_end(self.help_btn, False, False, 0)

        self.top_btn = Gtk.ToggleButton(label="🔝")
        self.top_btn.set_active(True)
        self.top_btn.connect("toggled", self.on_top_toggled)
        self.top_btn.set_tooltip_text("Всегда поверх других окон")
        self.control_box.pack_end(self.top_btn, False, False, 0)

        css_provider = Gtk.CssProvider()
        css = b"""
        * {
            font-family: 'Sans';
            font-size: 10pt;
        }
        .close-btn {
            font-weight: bold;
            font-size: 14px;
            min-width: 20px;
            min-height: 20px;
            border-radius: 10px;
            background-color: #FF6B6B;
            color: white;
            border: none;
        }
        .reset-btn {
            font-weight: bold;
            font-size: 14px;
            min-width: 20px;
            min-height: 20px;
            border-radius: 10px;
            background-color: #3498DB;
            color: white;
            border: none;
        }
        .youtube-btn {
            font-weight: bold;
            font-size: 14px;
            min-width: 20px;
            min-height: 20px;
            border-radius: 10px;
            background-color: #FF0000;
            color: white;
            border: none;
        }
        .help-btn {
            font-weight: bold;
            font-size: 14px;
            min-width: 20px;
            min-height: 20px;
            border-radius: 10px;
            background-color: #9B59B6;
            color: white;
            border: none;
        }
        .distance-value {
            font-weight: bold;
            color: #27AE60;
            min-width: 80px;
        }
        .scale-value {
            font-weight: bold;
            color: #3498DB;
            min-width: 150px;
        }
        .calibration-active {
            background-color: #FF9800;
            color: white;
        }
        """
        css_provider.load_from_data(css)
        style_context = self.control_box.get_style_context()
        style_context.add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def toggle_mode(self, button):
        """Переключает режим калибровка/измерение"""
        self.calibration_mode = not self.calibration_mode
        self.mode_btn.set_label("Линейка" if self.calibration_mode else "Калибровать")
        self.apply_btn.set_visible(self.calibration_mode)

        if not self.calibration_mode:
            # При выходе из калибровки сбрасываем точки
            self.reset_points()

        self.queue_draw()

    def apply_calibration(self, button):
        if self.grid_size > 0:
            # Рассчитываем калиброванный масштаб
            scale_value = self.selected_scale
            # Размер одного квадрата в пикселях
            square_size_px = self.grid_size / 7.0
            # Масштаб: метры на пиксель
            self.calibrated_scale = scale_value / square_size_px
            self.calibration_base_scale = self.selected_scale
            self.use_calibrated_scale = True
            self.scale_factor = self.calibrated_scale

            # Сохраняем настройки
            self.save_config()

            # Обновляем интерфейс
            self.recalculate_scale()
            self.queue_draw()

            # Показываем результат
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Калибровка успешно применена!"
            )
            dialog.format_secondary_text(
                f"Рассчитанный масштаб: {self.calibrated_scale:.6f} м/пикс\n"
                f"Размер сетки: {self.grid_size:.1f} пикс\n"
                f"Размер квадрата: {square_size_px:.1f} пикс\n"
                f"Настройки сохранены и будут использоваться при следующих запусках."
            )
            dialog.run()
            dialog.destroy()

            # Выходим из режима калибровки
            self.calibration_mode = False
            self.mode_btn.set_label("Калибровать")
            self.apply_btn.set_visible(False)
        else:
            # Если сетка не установлена
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Ошибка калибровки"
            )
            dialog.format_secondary_text("Пожалуйста, разместите сетку на карте.")
            dialog.run()
            dialog.destroy()

    def on_top_toggled(self, button):
        self.set_keep_above(button.get_active())
        if self.get_realized():
            self.on_realize(None)

    def on_scale_changed(self, combo):
        scale_str = combo.get_active_text()
        if scale_str:
            try:
                self.selected_scale = int(scale_str)

                # Если есть калиброванное значение и базовый масштаб
                if self.use_calibrated_scale and self.calibration_base_scale:
                    # Рассчитываем коэффициент пересчета
                    scale_ratio = self.selected_scale / self.calibration_base_scale

                    # Применяем новый масштабный коэффициент
                    self.scale_factor = self.calibrated_scale * scale_ratio

                    # Обновляем отображение
                    base_text = f" (база: {self.calibration_base_scale} м)" if self.calibration_base_scale else ""
                    self.scale_value.set_text(f"{self.scale_factor:.6f} м/пикс{base_text}")

                self.recalculate_scale()
                self.queue_draw()
            except ValueError:
                pass

    def draw_corner_marker(self, cr, x, y, corner_type):
        """Рисует уголок для изменения размера сетки"""
        marker_size = 15
        line_width = 2

        cr.set_line_width(line_width)
        cr.set_source_rgba(1, 1, 1, 0.8)  # Прозрачные белые линии

        if corner_type == 'tl':  # Верхний левый
            cr.move_to(x, y)
            cr.line_to(x + marker_size, y)
            cr.move_to(x, y)
            cr.line_to(x, y + marker_size)
        elif corner_type == 'tr':  # Верхний правый
            cr.move_to(x, y)
            cr.line_to(x - marker_size, y)
            cr.move_to(x, y)
            cr.line_to(x, y + marker_size)
        elif corner_type == 'bl':  # Нижний левый
            cr.move_to(x, y)
            cr.line_to(x + marker_size, y)
            cr.move_to(x, y)
            cr.line_to(x, y - marker_size)
        elif corner_type == 'br':  # Нижний правый
            cr.move_to(x, y)
            cr.line_to(x - marker_size, y)
            cr.move_to(x, y)
            cr.line_to(x, y - marker_size)

        cr.stroke()

    def draw_calibration_grid(self, cr, x, y, size):
        """Рисует сетку 7x7 для калибровки"""
        # Рисуем внешний квадрат
        cr.set_source_rgba(1, 1, 1, 0.8)
        cr.set_line_width(2)
        cr.rectangle(x, y, size, size)
        cr.stroke()

        # Устанавливаем пунктир для линий сетки
        cr.set_dash([3, 3], 0)
        cr.set_line_width(1)

        # Рассчитываем шаги для сетки
        step = size / 7.0

        # Рисуем вертикальные линии
        for i in range(1, 7):
            cr.move_to(x + i*step, y)
            cr.line_to(x + i*step, y+size)

        # Рисуем горизонтальные линии
        for i in range(1, 7):
            cr.move_to(x, y + i*step)
            cr.line_to(x+size, y + i*step)

        cr.stroke()
        cr.set_dash([], 0)  # Сбрасываем пунктир

        # Рисуем уголки (прозрачные)
        corners = [
            (x, y, 'tl'),  # top-left
            (x+size, y, 'tr'),  # top-right
            (x, y+size, 'bl'),  # bottom-left
            (x+size, y+size, 'br')  # bottom-right
        ]

        for cx, cy, corner_type in corners:
            self.draw_corner_marker(cr, cx, cy, corner_type)

        # Выводим размер сетки слева
        cr.set_font_size(14)
        cr.set_source_rgba(1, 1, 1, 0.8)
        cr.move_to(x - 120, y + size/2 - 7)
        cr.show_text(f"Размер сетки:")
        cr.move_to(x - 120, y + size/2 + 10)
        cr.show_text(f"{size:.1f} пикс")

    def on_draw(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()

        # Рисуем фон
        cr.set_source_rgba(0.2, 0.2, 0.2, 0.6)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # В режиме калибровки рисуем сетку
        if self.calibration_mode:
            x, y = self.grid_pos
            self.draw_calibration_grid(cr, x, y, self.grid_size)

            # Информация для пользователя
            cr.set_font_size(16)
            cr.set_source_rgba(1, 1, 1, 0.8)
            cr.move_to(10, height - 30)
            cr.show_text("Перетащите сетку на игровую карту и нажмите 'Применить калибровку'")
        else:
            # Рисуем линии и точки для линейки
            if self.start_point:
                # Линия к текущему положению мыши или конечной точке
                if self.end_point:
                    target_point = self.end_point
                elif self.temp_point:
                    target_point = self.temp_point
                else:
                    return

                # Линия между точками
                cr.set_source_rgba(1, 1, 1, 1)
                cr.set_line_width(2)
                cr.move_to(self.start_point.x, self.start_point.y)
                cr.line_to(target_point.x, target_point.y)

                # Для временной линии рисуем пунктир
                if not self.end_point:
                    cr.set_dash([5, 3], 0)
                    cr.stroke()
                    cr.set_dash([], 0)
                else:
                    cr.stroke()

                # Выводим расстояние на линию
                if self.start_point and target_point:
                    dx = target_point.x - self.start_point.x
                    dy = target_point.y - self.start_point.y
                    pixels = math.sqrt(dx**2 + dy**2)
                    meters = pixels * self.scale_factor

                    cr.set_font_size(24)
                    cr.set_source_rgba(1, 1, 1, 1)
                    text_x = (self.start_point.x + target_point.x) / 2
                    text_y = (self.start_point.y + target_point.y) / 2 - 40
                    cr.move_to(text_x, text_y)
                    cr.show_text(f"{meters:.1f} м")

    def get_corner_at(self, x, y):
        """Определяет, в каком углу сетки находится точка"""
        grid_x, grid_y = self.grid_pos
        size = self.grid_size
        threshold = 15  # Радиус чувствительности

        corners = [
            ('tl', grid_x, grid_y),  # top-left
            ('tr', grid_x + size, grid_y),  # top-right
            ('bl', grid_x, grid_y + size),  # bottom-left
            ('br', grid_x + size, grid_y + size)  # bottom-right
        ]

        for corner, cx, cy in corners:
            if math.hypot(x - cx, y - cy) <= threshold:
                return corner
        return None

    def is_inside_grid(self, x, y):
        """Проверяет, находится ли точка внутри сетки"""
        grid_x, grid_y = self.grid_pos
        size = self.grid_size
        return (grid_x <= x <= grid_x + size and
                grid_y <= y <= grid_y + size)

    def on_button_press(self, widget, event):
        if event.button == 3:  # Правая кнопка мыши - точка А
            if not self.calibration_mode:
                self.start_point = Gdk.EventButton()
                self.start_point.x = event.x
                self.start_point.y = event.y
                self.end_point = None
                self.queue_draw()
        elif event.button == 1:  # Левая кнопка мыши
            if self.calibration_mode:
                # Определяем, в каком углу сетки было нажатие
                corner = self.get_corner_at(event.x, event.y)
                if corner:
                    self.dragging = True
                    self.drag_corner = corner
                    self.drag_start = (event.x, event.y, self.grid_pos[0], self.grid_pos[1], self.grid_size)
                elif self.is_inside_grid(event.x, event.y):
                    # Перемещение всей сетки
                    self.dragging = True
                    self.drag_corner = 'move'
                    self.drag_start = (event.x, event.y, self.grid_pos[0], self.grid_pos[1])
            else:
                # Режим линейки - точка Б
                if self.start_point:
                    # Если точка Б уже есть - перемещаем ее
                    if self.end_point:
                        self.end_point.x = event.x
                        self.end_point.y = event.y
                    else:
                        # Иначе создаем новую
                        self.end_point = Gdk.EventButton()
                        self.end_point.x = event.x
                        self.end_point.y = event.y

                    self.update_distance_display()
                    self.queue_draw()

    def on_button_release(self, widget, event):
        if event.button == 1 and self.dragging:
            self.dragging = False
            self.drag_corner = None
            self.drag_start = None

    def on_mouse_move(self, widget, event):
        if self.calibration_mode and self.dragging and self.drag_corner and self.drag_start:
            # Уменьшаем чувствительность в 2 раза
            dx = (event.x - self.drag_start[0]) * 0.5
            dy = (event.y - self.drag_start[1]) * 0.5

            if self.drag_corner == 'move':
                # Перемещение всей сетки
                orig_x, orig_y = self.drag_start[2], self.drag_start[3]
                self.grid_pos = (orig_x + dx, orig_y + dy)
            else:
                # Изменение размера сетки с фиксацией противоположного угла
                orig_x, orig_y, orig_size = self.drag_start[2], self.drag_start[3], self.drag_start[4]

                # Рассчитываем изменение размера с учетом направления
                if self.drag_corner == 'tl':  # Верхний левый
                    new_size = orig_size - dx - dy
                    # Фиксируем правый нижний угол
                    self.grid_pos = (orig_x + dx, orig_y + dy)
                elif self.drag_corner == 'tr':  # Верхний правый
                    new_size = orig_size + dx - dy
                    # Фиксируем левый нижний угол
                    self.grid_pos = (orig_x, orig_y + dy)
                elif self.drag_corner == 'bl':  # Нижний левый
                    new_size = orig_size - dx + dy
                    # Фиксируем правый верхний угол
                    self.grid_pos = (orig_x + dx, orig_y)
                elif self.drag_corner == 'br':  # Нижний правый
                    new_size = orig_size + dx + dy
                    # Фиксируем левый верхний угол
                    self.grid_pos = (orig_x, orig_y)

                # Ограничиваем минимальный размер
                self.grid_size = max(50, new_size)

            self.queue_draw()
        elif self.calibration_mode:
            # Обновляем курсор
            corner = self.get_corner_at(event.x, event.y)
            if corner:
                self.get_window().set_cursor(Gdk.Cursor.new_for_display(
                    self.get_display(), Gdk.CursorType.SIZING))
            elif self.is_inside_grid(event.x, event.y):
                self.get_window().set_cursor(Gdk.Cursor.new_for_display(
                    self.get_display(), Gdk.CursorType.FLEUR))
            else:
                self.get_window().set_cursor(None)
        elif not self.calibration_mode and self.start_point and not self.end_point:
            self.temp_point = Gdk.EventButton()
            self.temp_point.x = event.x
            self.temp_point.y = event.y
            self.queue_draw()

    def update_distance_display(self):
        if self.start_point and self.end_point:
            dx = self.end_point.x - self.start_point.x
            dy = self.end_point.y - self.start_point.y
            pixels = math.sqrt(dx**2 + dy**2)
            meters = pixels * self.scale_factor
            self.distance_value.set_text(f"{meters:.1f} м")

    def on_key_press(self, widget, event):
        keyval = event.keyval
        if keyval == Gdk.KEY_Escape:
            self.destroy()
        elif keyval == Gdk.KEY_r:
            self.reset_points()
        elif keyval == Gdk.KEY_t:
            self.top_btn.set_active(not self.top_btn.get_active())
        elif keyval == Gdk.KEY_c:
            self.toggle_mode(None)
        elif keyval == Gdk.KEY_y:
            webbrowser.open("https://www.youtube.com/@EXTRUD/shorts")
        elif keyval == Gdk.KEY_F1 or keyval == Gdk.KEY_question:
            self.show_help(None)
        return False

    def on_key_release(self, widget, event):
        return False

    def reset_points(self):
        self.start_point = None
        self.end_point = None
        self.temp_point = None
        self.distance_value.set_text("0.00 м")
        self.queue_draw()

win = MapRuler()
win.show_all()
Gtk.main()
