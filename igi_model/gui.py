from __future__ import annotations

from dataclasses import fields
import math
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .detection import SOIL_CODE_NAMES, apply_coordinates
from .exporter import SOIL_COLUMNS, borehole_matrix, export_xlsx, matrix_tsv, soils_matrix
from .models import AnalysisResult, CoordinateConfig, DetectionProfile, LayerRecord
from .profile_io import load_profile, save_profile
from .service import analyze_files


FILE_TYPES = [("Чертежи и PDF", "*.dwg *.dxf *.pdf"), ("DWG", "*.dwg"), ("DXF", "*.dxf"), ("PDF", "*.pdf"), ("Все файлы", "*.*")]
COORDINATE_FILE_TYPES = [
    ("Ведомость координат", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
    ("PDF", "*.pdf"),
    ("Изображения", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
    ("Все файлы", "*.*"),
]


class EditableTree(ttk.Frame):
    def __init__(self, master, columns: list[tuple[str, str]], on_edit=None):
        super().__init__(master)
        self.on_edit = on_edit
        self.tree = ttk.Treeview(self, columns=[key for key, _ in columns], show="headings")
        self.xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.yscroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(xscrollcommand=self.xscroll.set, yscrollcommand=self.yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.yscroll.grid(row=0, column=1, sticky="ns")
        self.xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        for key, title in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=120 if key not in {"layers", "description", "name"} else 260, minwidth=70, anchor="center")
        self.tree.bind("<Double-1>", self._start_edit)

    def _start_edit(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row or not col:
            return
        box = self.tree.bbox(row, col)
        index = int(col[1:]) - 1
        value = self.tree.item(row, "values")[index]
        entry = ttk.Entry(self.tree)
        entry.insert(0, value)
        entry.select_range(0, "end")
        entry.place(x=box[0], y=box[1], width=box[2], height=box[3])
        entry.focus_set()

        def commit(_event=None):
            values = list(self.tree.item(row, "values"))
            values[index] = entry.get()
            self.tree.item(row, values=values)
            entry.destroy()
            if self.on_edit:
                self.on_edit(row, index, values[index])
        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda _e: entry.destroy())

    def clear(self):
        self.tree.delete(*self.tree.get_children())


class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ИГИ → Грунт ЛИРА-САПР")
        self.geometry("1240x760")
        self.minsize(920, 600)
        self.result: AnalysisResult | None = None
        self.profile = DetectionProfile()
        self.plan_var = tk.StringVar()
        self.columns_var = tk.StringVar()
        self.soils_var = tk.StringVar()
        self.coordinates_var = tk.StringVar()
        self.angle_var = tk.StringVar()
        self.origin_x_var = tk.StringVar()
        self.origin_y_var = tk.StringVar()
        self.scale_var = tk.StringVar(value="1")
        self.calibration_id_var = tk.StringVar()
        self.calibration_x_var = tk.StringVar()
        self.calibration_y_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Выберите план или ведомость координат, колонки и таблицу характеристик грунтов.")
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        self._file_row(toolbar, 0, "План скважин", self.plan_var)
        self._file_row(toolbar, 1, "Колонки ИГЭ", self.columns_var)
        self._file_row(toolbar, 2, "Характеристики", self.soils_var)
        self._file_row(toolbar, 3, "Ведомость координат", self.coordinates_var, COORDINATE_FILE_TYPES)
        buttons = ttk.Frame(toolbar)
        buttons.grid(row=0, column=3, rowspan=4, padx=(12, 0), sticky="ns")
        self.analyze_button = ttk.Button(buttons, text="Распознать", command=self.start_analysis)
        self.analyze_button.pack(fill="x", pady=(0, 4))
        ttk.Button(buttons, text="Открыть профиль", command=self.open_profile).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Сохранить профиль", command=self.save_profile_dialog).pack(fill="x", pady=2)
        toolbar.columnconfigure(1, weight=1)

        coords = ttk.LabelFrame(self, text="Система координат", padding=7)
        coords.pack(fill="x", padx=8, pady=(0, 6))
        for col, (label, var, width) in enumerate([
            ("Угол, °", self.angle_var, 10), ("X₀", self.origin_x_var, 16),
            ("Y₀", self.origin_y_var, 16), ("Масштаб", self.scale_var, 10),
        ]):
            ttk.Label(coords, text=label).grid(row=0, column=col * 2, padx=(4, 2))
            ttk.Entry(coords, textvariable=var, width=width).grid(row=0, column=col * 2 + 1, padx=(0, 8))
        ttk.Button(coords, text="Пересчитать координаты", command=self.reapply_coordinates).grid(row=0, column=8, padx=8)
        ttk.Label(coords, text="Автоопределение можно откорректировать; значения сохраняются в профиль.", foreground="#555").grid(row=0, column=9, sticky="w")
        ttk.Label(coords, text="Калибровка: скважина").grid(row=1, column=0, columnspan=2, sticky="e", pady=(7, 0))
        ttk.Entry(coords, textvariable=self.calibration_id_var, width=9).grid(row=1, column=2, sticky="w", pady=(7, 0))
        ttk.Label(coords, text="известный X").grid(row=1, column=3, sticky="e", pady=(7, 0))
        ttk.Entry(coords, textvariable=self.calibration_x_var, width=12).grid(row=1, column=4, sticky="w", pady=(7, 0))
        ttk.Label(coords, text="Y").grid(row=1, column=5, sticky="e", pady=(7, 0))
        ttk.Entry(coords, textvariable=self.calibration_y_var, width=12).grid(row=1, column=6, sticky="w", pady=(7, 0))
        ttk.Button(coords, text="Привязать начало координат", command=self.calibrate_coordinates).grid(row=1, column=7, columnspan=2, padx=8, pady=(7, 0))
        ttk.Label(coords, text="Достаточно одной известной точки; угол определяется по схеме.", foreground="#555").grid(row=1, column=9, sticky="w", pady=(7, 0))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8)
        self.borehole_table = EditableTree(notebook, [
            ("display_id", "№"), ("source_id", "Исходный ID"), ("x", "X"), ("y", "Y"),
            ("z", "Z"), ("layers", "Слои ИГЭ + мощность"), ("confidence", "Уверенность"),
        ], self._edit_borehole)
        self.soil_table = EditableTree(notebook, SOIL_COLUMNS, self._edit_soil)
        notebook.add(self.borehole_table, text="Скважины")
        notebook.add(self.soil_table, text="Характеристики грунтов")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Экспорт Excel…", command=self.export_dialog).pack(side="left")
        ttk.Button(bottom, text="Копировать скважины для ЛИРА", command=self.copy_boreholes).pack(side="left", padx=5)
        ttk.Button(bottom, text="Копировать грунты для ЛИРА", command=self.copy_soils).pack(side="left")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True, padx=12)
        ttk.Button(bottom, text="Протокол", command=self.show_issues).pack(side="right")

    def _file_row(self, parent, row, label, variable, filetypes=FILE_TYPES):
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="Обзор…", command=lambda: self.choose_file(variable, filetypes)).grid(row=row, column=2, padx=(6, 0))

    def choose_file(self, variable, filetypes=FILE_TYPES):
        filename = filedialog.askopenfilename(filetypes=filetypes)
        if filename:
            variable.set(filename)

    def start_analysis(self):
        plan = self.plan_var.get().strip()
        columns = self.columns_var.get().strip()
        soils = self.soils_var.get().strip()
        coordinates = self.coordinates_var.get().strip()
        plan_file = plan if Path(plan).is_file() else None
        coordinate_file = coordinates if Path(coordinates).is_file() else None
        if not plan_file and not coordinate_file:
            messagebox.showerror("Нет файла", "Выберите план скважин или ведомость координат.")
            return
        if not Path(columns).is_file():
            messagebox.showerror("Нет файла", "Выберите существующий файл инженерно-геологических колонок.")
            return
        self.analyze_button.configure(state="disabled")
        self.status_var.set("Чтение и распознавание…")

        def worker():
            try:
                result = analyze_files(
                    plan_file,
                    columns,
                    self.profile,
                    soils if Path(soils).is_file() else None,
                    coordinate_file,
                )
                self.after(0, lambda: self._analysis_done(result))
            except Exception as exc:
                self.after(0, lambda: self._analysis_failed(exc))
        threading.Thread(target=worker, daemon=True).start()

    def _analysis_done(self, result):
        self.result = result
        self.analyze_button.configure(state="normal")
        self.angle_var.set(f"{result.coordinate.angle_deg:.6g}")
        self.origin_x_var.set(f"{result.coordinate.origin_x:.6f}")
        self.origin_y_var.set(f"{result.coordinate.origin_y:.6f}")
        self.scale_var.set(f"{result.coordinate.scale:.8g}")
        self.refresh_tables()
        warnings = sum(issue.level != "info" for issue in result.issues)
        self.status_var.set(f"Распознано скважин: {len(result.boreholes)}. Замечаний: {warnings}. Проверьте координаты и таблицы.")

    def _analysis_failed(self, exc):
        self.analyze_button.configure(state="normal")
        self.status_var.set("Ошибка распознавания.")
        messagebox.showerror("Ошибка", str(exc))

    def refresh_tables(self):
        if not self.result:
            return
        self.borehole_table.clear()
        for index, item in enumerate(self.result.boreholes):
            layers = "; ".join(f"{layer.ige} + {layer.thickness:g}" for layer in item.layers)
            values = (item.display_id, item.source_id, self._number(item.x), self._number(item.y), self._number(item.elevation), layers, f"{item.confidence:.0%}")
            self.borehole_table.tree.insert("", "end", iid=str(index), values=values)
        self.soil_table.clear()
        for index, soil in enumerate(self.result.soils):
            self.soil_table.tree.insert("", "end", iid=str(index), values=[getattr(soil, key) if getattr(soil, key) is not None else "" for key, _ in SOIL_COLUMNS])

    @staticmethod
    def _number(value):
        return "" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")

    def _edit_borehole(self, row, index, value):
        if not self.result:
            return
        item = self.result.boreholes[int(row)]
        try:
            if index == 0:
                item.display_id = value
            elif index == 2:
                item.x = float(value.replace(",", "."))
            elif index == 3:
                item.y = float(value.replace(",", "."))
            elif index == 4:
                item.elevation = float(value.replace(",", "."))
            elif index == 5:
                layers = []
                for token in value.split(";"):
                    if not token.strip():
                        continue
                    parts = token.split("+", 1)
                    if len(parts) != 2:
                        raise ValueError("Формат слоя: ИГЭ + мощность")
                    layers.append(LayerRecord(parts[0].strip(), float(parts[1].strip().replace(",", "."))))
                item.layers = layers
        except ValueError as exc:
            messagebox.showerror("Некорректное значение", str(exc))
            self.refresh_tables()

    def _edit_soil(self, row, index, value):
        if not self.result:
            return
        soil = self.result.soils[int(row)]
        key = SOIL_COLUMNS[index][0]
        field_type = next(field.type for field in fields(soil) if field.name == key)
        try:
            if "float" in str(field_type):
                setattr(soil, key, None if not value.strip() else float(value.replace(",", ".")))
            else:
                setattr(soil, key, value)
            if key == "code" and value in SOIL_CODE_NAMES:
                self.status_var.set(f"Код {value}: {SOIL_CODE_NAMES[value]}")
        except ValueError:
            messagebox.showerror("Некорректное значение", "Ожидалось число.")
            self.refresh_tables()

    def reapply_coordinates(self):
        if not self.result:
            return
        try:
            config = CoordinateConfig(
                angle_deg=float(self.angle_var.get().replace(",", ".")),
                origin_x=float(self.origin_x_var.get().replace(",", ".")),
                origin_y=float(self.origin_y_var.get().replace(",", ".")),
                scale=float(self.scale_var.get().replace(",", ".")),
            )
        except ValueError:
            messagebox.showerror("Координаты", "Угол, начало координат и масштаб должны быть числами.")
            return
        self.result.coordinate = config
        apply_coordinates(self.result.boreholes, config)
        self.profile.coordinate = config
        self.refresh_tables()
        self.status_var.set("Координаты пересчитаны. Проверьте X и Y.")

    def calibrate_coordinates(self):
        if not self.result:
            return
        requested_id = self.calibration_id_var.get().strip().casefold()
        borehole = next((
            item for item in self.result.boreholes
            if item.display_id.casefold() == requested_id or item.source_id.casefold() == requested_id
        ), None)
        if borehole is None or borehole.x_global is None or borehole.y_global is None:
            messagebox.showerror("Калибровка", "Скважина с таким номером не найдена.")
            return
        try:
            target_x = float(self.calibration_x_var.get().replace(",", "."))
            target_y = float(self.calibration_y_var.get().replace(",", "."))
            angle_deg = float(self.angle_var.get().replace(",", "."))
            scale = float(self.scale_var.get().replace(",", "."))
            if scale == 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Калибровка", "Номер, известные X/Y, угол и масштаб должны быть заполнены корректно.")
            return
        angle = math.radians(angle_deg)
        local_x, local_y = target_x / scale, target_y / scale
        global_dx = math.cos(angle) * local_x + math.sin(angle) * local_y
        global_dy = -math.sin(angle) * local_x + math.cos(angle) * local_y
        self.origin_x_var.set(f"{borehole.x_global - global_dx:.6f}")
        self.origin_y_var.set(f"{borehole.y_global - global_dy:.6f}")
        self.reapply_coordinates()
        self.status_var.set(f"Начало координат привязано по скважине {borehole.display_id}. Сохраните профиль для следующих файлов.")

    def export_dialog(self):
        if not self.result:
            messagebox.showinfo("Нет данных", "Сначала выполните распознавание.")
            return
        filename = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], initialfile="Модель_грунта.xlsx")
        if filename:
            try:
                export_xlsx(self.result, filename)
                self.status_var.set(f"Excel сохранён: {filename}")
                messagebox.showinfo("Готово", f"Файл создан:\n{filename}")
            except Exception as exc:
                messagebox.showerror("Экспорт", str(exc))

    def _copy(self, text, description):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status_var.set(f"{description} скопированы. В ЛИРА выберите первую ячейку и нажмите Ctrl+V.")

    def copy_boreholes(self):
        if self.result:
            self._copy(matrix_tsv(borehole_matrix(self.result.boreholes, include_labels=False)), "Скважины")

    def copy_soils(self):
        if self.result:
            self._copy(matrix_tsv(soils_matrix(self.result.soils, include_header=False)), "Характеристики грунтов")

    def show_issues(self):
        if not self.result:
            return
        text = "\n".join(f"[{issue.level.upper()}] {issue.message}" for issue in self.result.issues) or "Замечаний нет."
        window = tk.Toplevel(self)
        window.title("Протокол распознавания")
        window.geometry("850x450")
        box = tk.Text(window, wrap="word")
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, padx=8, pady=8)

    def open_profile(self):
        filename = filedialog.askopenfilename(filetypes=[("Профиль JSON", "*.json")])
        if not filename:
            return
        try:
            self.profile = load_profile(filename)
            self.status_var.set(f"Профиль загружен: {filename}")
        except Exception as exc:
            messagebox.showerror("Профиль", str(exc))

    def save_profile_dialog(self):
        if self.result:
            self.reapply_coordinates()
        filename = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Профиль JSON", "*.json")], initialfile="профиль_распознавания.json")
        if filename:
            save_profile(self.profile, filename)
            self.status_var.set(f"Профиль сохранён: {filename}")


def run_gui():
    MainWindow().mainloop()
