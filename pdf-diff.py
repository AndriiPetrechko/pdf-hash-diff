import io
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import fitz  # PyMuPDF
import imagehash
from PIL import Image, ImageDraw, ImageTk


GRID_ROWS = 5
GRID_COLS = 5

BLOCK_THRESHOLD = 6
MIN_CHANGED_BLOCKS = 1

RENDER_SCALE = 2
HASH_IMAGE_SIZE = (512, 768)

MIN_ZOOM = 0.25
MAX_ZOOM = 4.0
ZOOM_STEP = 0.25

PDF_COUNT = 3  # Максимальна кількість панелей

class PdfGridHashComparatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF diff v0.5.0")
        self.root.geometry("1750x950")

        self.pdf_count = 3
        self.mode_var = tk.IntVar(value=3)
        self.pdf_paths = [None] * self.pdf_count
        self.pdf_images = [[] for _ in range(self.pdf_count)]
        self.pdf_grid_hashes = [[] for _ in range(self.pdf_count)]

        self.changed_blocks_by_page = {}
        self.results = []

        self.current_page_index = None
        self.zoom_factor = 1.0

        self.show_grid = tk.BooleanVar(value=True)
        self.sync_scroll = tk.BooleanVar(value=True)

        self.preview_imgs = [None] * self.pdf_count
        self.canvases = []
        self.canvas_image_ids = []

        self._syncing_scroll = False
        self._drag_start = {}

        self.cancel_event = threading.Event()
        self.compare_thread = None
        self.is_comparing = False

        self.build_ui()

    def build_ui(self):
        self.build_top_bar()
        self.build_file_labels()
        self.build_view_controls()
        self.build_results_table()
        self.build_preview_area()
        self.change_mode()

    def build_top_bar(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(frame, text="Режим:").pack(side="left", padx=(5, 3))
        self.mode_selector = ttk.Combobox(
            frame, textvariable=self.mode_var, values=(2, 3),
            state="readonly", width=4,
        )
        self.mode_selector.pack(side="left", padx=(0, 10))
        self.mode_selector.bind("<<ComboboxSelected>>", self.change_mode)

        self.select_button = tk.Button(
            frame,
            text="Вибрати PDF (3 файли)",
            command=self.select_pdfs,
        )
        self.select_button.pack(side="left", padx=5)

        self.compare_button = tk.Button(
            frame,
            text="Порівняти",
            command=self.compare,
        )
        self.compare_button.pack(side="left", padx=5)

        self.cancel_button = tk.Button(
            frame,
            text="Скасувати",
            command=self.cancel_compare,
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=5)

        self.progress = ttk.Progressbar(
            frame,
            length=320,
            mode="determinate",
        )
        self.progress.pack(side="left", padx=10)

        self.status_label = tk.Label(frame, text="Готово")
        self.status_label.pack(side="left", padx=10)

    def build_file_labels(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=(0, 5))

        self.pdf_labels = []
        for index in range(PDF_COUNT):
            label = tk.Label(
                frame,
                text=f"PDF {index + 1}: не вибрано",
                anchor="w",
            )
            label.pack(side="left", fill="x", expand=True, padx=8)
            self.pdf_labels.append(label)

    def build_view_controls(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(frame, text="Zoom:").pack(side="left", padx=(5, 3))

        tk.Button(
            frame,
            text="−",
            width=3,
            command=self.zoom_out,
        ).pack(side="left", padx=2)

        self.zoom_label = tk.Label(frame, text="100%", width=7)
        self.zoom_label.pack(side="left", padx=2)

        tk.Button(
            frame,
            text="+",
            width=3,
            command=self.zoom_in,
        ).pack(side="left", padx=2)

        tk.Button(
            frame,
            text="Fit page",
            command=self.fit_page,
        ).pack(side="left", padx=(8, 3))

        tk.Button(
            frame,
            text="Fit width",
            command=self.fit_width,
        ).pack(side="left", padx=3)

        ttk.Checkbutton(
            frame,
            text="Синхронна прокрутка",
            variable=self.sync_scroll,
        ).pack(side="left", padx=(15, 5))

        ttk.Checkbutton(
            frame,
            text="Показувати сітку",
            variable=self.show_grid,
            command=self.refresh_preview,
        ).pack(side="left", padx=5)

        tk.Label(
            frame,
            text="Ctrl + колесо — масштаб, ліва кнопка миші — перетягування",
        ).pack(side="left", padx=20)

    def build_results_table(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=5)

        columns = (
            "page",
            "status",
            "changed_blocks",
            "max_distance",
            "total_distance",
            "blocks",
        )

        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=11,
        )

        headings = {
            "page": "Сторінка",
            "status": "Статус",
            "changed_blocks": "Змінено блоків",
            "max_distance": "Max distance",
            "total_distance": "Сума distance",
            "blocks": "Блоки / пари",
        }

        widths = {
            "page": 80,
            "status": 240,
            "changed_blocks": 130,
            "max_distance": 120,
            "total_distance": 120,
            "blocks": 800,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor="center" if column != "blocks" else "w",
            )

        self.tree.tag_configure("ok", background="#d9fdd3")
        self.tree.tag_configure("diff", background="#ffd6d6")
        self.tree.tag_configure("missing", background="#fff3cd")

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_row_select)

    def build_preview_area(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.preview_panels = []
        for index in range(PDF_COUNT):
            panel = tk.LabelFrame(frame, text=f"PDF {index + 1}")
            panel.pack(
                side="left",
                fill="both",
                expand=True,
                padx=(0 if index == 0 else 5, 0 if index == PDF_COUNT - 1 else 5),
            )

            self.preview_panels.append(panel)
            canvas, image_id = self.create_canvas(panel, index)
            self.canvases.append(canvas)
            self.canvas_image_ids.append(image_id)

    def create_canvas(self, parent, side):
        container = tk.Frame(parent)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            container,
            background="#2b2b2b",
            highlightthickness=0,
        )

        h_scroll = ttk.Scrollbar(container, orient="horizontal")
        v_scroll = ttk.Scrollbar(container, orient="vertical")

        canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        image_id = canvas.create_image(0, 0, anchor="nw")

        h_scroll.configure(command=lambda *args: self.scroll_x(side, *args))
        v_scroll.configure(command=lambda *args: self.scroll_y(side, *args))

        canvas.configure(
            xscrollcommand=h_scroll.set,
            yscrollcommand=v_scroll.set,
        )

        canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel)
        canvas.bind("<Control-Button-4>", self.on_ctrl_mousewheel_linux)
        canvas.bind("<Control-Button-5>", self.on_ctrl_mousewheel_linux)

        canvas.bind(
            "<ButtonPress-1>",
            lambda event: self.start_drag(side, event),
        )
        canvas.bind(
            "<B1-Motion>",
            lambda event: self.drag_canvas(side, event),
        )

        return canvas, image_id

    def change_mode(self, _event=None):
        if self.is_comparing:
            return
        self.pdf_count = int(self.mode_var.get())
        self.pdf_paths = [None] * self.pdf_count
        self.pdf_images = [[] for _ in range(self.pdf_count)]
        self.pdf_grid_hashes = [[] for _ in range(self.pdf_count)]
        self.changed_blocks_by_page = {}
        self.clear_table()
        self.progress["value"] = 0
        self.status_label.config(text="Готово")
        self.select_button.config(text=f"Вибрати PDF ({self.pdf_count} файли)")
        for index, label in enumerate(self.pdf_labels):
            if index < self.pdf_count:
                label.config(text=f"PDF {index + 1}: не вибрано")
                label.pack(side="left", fill="x", expand=True, padx=8)
            else:
                label.pack_forget()
        for index, panel in enumerate(self.preview_panels):
            if index < self.pdf_count:
                panel.pack(side="left", fill="both", expand=True, padx=5)
            else:
                panel.pack_forget()
        self.preview_imgs = [None] * self.pdf_count

    def select_pdfs(self):
        paths = filedialog.askopenfilenames(
            title=f"Вибрати рівно {self.pdf_count} PDF",
            filetypes=[("PDF files", "*.pdf")],
        )

        if not paths:
            return

        if len(paths) != self.pdf_count:
            messagebox.showwarning(
                "Помилка",
                f"Потрібно вибрати рівно {self.pdf_count} PDF файли за один раз.",
            )
            return

        self.pdf_paths = list(paths)

        for index, path in enumerate(self.pdf_paths):
            self.pdf_labels[index].config(
                text=f"PDF {index + 1}: {os.path.basename(path)}"
            )

    def compare(self):
        if any(not path for path in self.pdf_paths):
            messagebox.showwarning(
                "Помилка",
                f"Спочатку вибери {self.pdf_count} PDF файли.",
            )
            return

        if self.is_comparing:
            return

        self.clear_table()
        self.progress["value"] = 0
        self.status_label.config(text="Підготовка...")

        self.cancel_event.clear()
        self.set_comparing_state(True)

        self.compare_thread = threading.Thread(
            target=self.compare_worker,
            daemon=True,
        )
        self.compare_thread.start()

    def cancel_compare(self):
        if not self.is_comparing:
            return

        self.cancel_event.set()
        self.cancel_button.config(state="disabled")
        self.status_label.config(text="Скасування...")

    def set_comparing_state(self, comparing):
        self.is_comparing = comparing
        self.select_button.config(state="disabled" if comparing else "normal")
        self.mode_selector.config(state="disabled" if comparing else "readonly")
        self.compare_button.config(state="disabled" if comparing else "normal")
        self.cancel_button.config(state="normal" if comparing else "disabled")

    def compare_worker(self):
        docs = [None] * self.pdf_count

        try:
            for index, path in enumerate(self.pdf_paths):
                if self.cancel_event.is_set():
                    self.finish_cancelled()
                    return
                docs[index] = fitz.open(path)

            total_render_pages = sum(len(doc) for doc in docs)
            max_pages = max(len(doc) for doc in docs)
            total_steps = total_render_pages + max_pages
            processed_steps = 0

            self.pdf_images = [[] for _ in range(self.pdf_count)]
            self.pdf_grid_hashes = [[] for _ in range(self.pdf_count)]
            self.changed_blocks_by_page = {}

            self.root.after(
                0,
                lambda: self.progress.config(maximum=total_steps),
            )

            for pdf_index, doc in enumerate(docs):
                for page_index in range(len(doc)):
                    if self.cancel_event.is_set():
                        self.finish_cancelled()
                        return

                    hashes, image = self.render_single_page(doc, page_index)

                    if self.cancel_event.is_set():
                        self.finish_cancelled()
                        return

                    self.pdf_grid_hashes[pdf_index].append(hashes)
                    self.pdf_images[pdf_index].append(image)

                    processed_steps += 1
                    self.update_progress(
                        processed_steps,
                        total_steps,
                        f"Обробка PDF {pdf_index + 1}: сторінка "
                        f"{page_index + 1}/{len(doc)}",
                    )

            results = []

            for page_index in range(max_pages):
                if self.cancel_event.is_set():
                    self.finish_cancelled()
                    return

                result = self.compare_page(page_index)
                results.append(result)

                processed_steps += 1
                self.update_progress(
                    processed_steps,
                    total_steps,
                    f"Порівняння: сторінка {page_index + 1}/{max_pages}",
                )

            if self.cancel_event.is_set():
                self.finish_cancelled()
                return

            self.root.after(0, lambda: self.fill_table(results))
            self.root.after(0, lambda: self.status_label.config(text="Готово"))
            self.root.after(0, lambda: self.set_comparing_state(False))

        except Exception as exc:
            error_text = str(exc)

            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Помилка",
                    f"Не вдалося обробити PDF:\n{error_text}",
                ),
            )
            self.root.after(0, lambda: self.status_label.config(text="Помилка"))
            self.root.after(0, lambda: self.set_comparing_state(False))

        finally:
            for doc in docs:
                if doc is not None:
                    doc.close()

    def finish_cancelled(self):
        self.root.after(0, lambda: self.status_label.config(text="Скасовано"))
        self.root.after(0, lambda: self.set_comparing_state(False))

    def compare_page(self, page_index):
        page_number = page_index + 1
        available = [
            page_index < len(self.pdf_grid_hashes[pdf_index])
            for pdf_index in range(self.pdf_count)
        ]

        if not all(available):
            missing = [
                f"PDF {index + 1}"
                for index, exists in enumerate(available)
                if not exists
            ]
            return self.missing_result(
                page_number,
                "Відсутня в " + ", ".join(missing),
            )

        pair_definitions = ((0, 1),) if self.pdf_count == 2 else ((0, 1), (0, 2), (1, 2))
        pair_distances = {}

        for first, second in pair_definitions:
            pair_name = f"{first + 1}↔{second + 1}"
            pair_distances[pair_name] = self.compare_grid_hashes(
                self.pdf_grid_hashes[first][page_index],
                self.pdf_grid_hashes[second][page_index],
            )

        combined_blocks = []
        block_count = GRID_ROWS * GRID_COLS

        for block_index in range(block_count):
            sample = pair_distances["1↔2"][block_index]
            distances = {
                pair_name: blocks[block_index]["distance"]
                for pair_name, blocks in pair_distances.items()
            }
            max_distance = max(distances.values())

            if max_distance > BLOCK_THRESHOLD:
                combined_blocks.append({
                    "row": sample["row"],
                    "col": sample["col"],
                    "distance": max_distance,
                    "pair_distances": distances,
                })

        self.changed_blocks_by_page[page_index] = combined_blocks

        changed_count = len(combined_blocks)
        all_distances = [
            block["distance"]
            for blocks in pair_distances.values()
            for block in blocks
        ]

        max_distance = max(all_distances) if all_distances else 0
        total_distance = sum(all_distances)
        is_different = changed_count >= MIN_CHANGED_BLOCKS

        return {
            "page": page_number,
            "status": "DIFFERENT" if is_different else "OK",
            "changed_blocks": f"{changed_count}/{block_count}",
            "max_distance": str(max_distance),
            "total_distance": str(total_distance),
            "blocks": self.format_changed_blocks(combined_blocks),
            "tag": "diff" if is_different else "ok",
        }

    def missing_result(self, page_number, status):
        self.changed_blocks_by_page[page_number - 1] = []
        return {
            "page": page_number,
            "status": status,
            "changed_blocks": "-",
            "max_distance": "-",
            "total_distance": "-",
            "blocks": "-",
            "tag": "missing",
        }

    def render_single_page(self, doc, page_index):
        page = doc[page_index]

        pix = page.get_pixmap(
            matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE),
            alpha=False,
        )

        image = Image.open(
            io.BytesIO(pix.tobytes("png"))
        ).convert("RGB")

        hash_image = image.resize(
            HASH_IMAGE_SIZE,
            Image.Resampling.LANCZOS,
        )

        return self.calculate_grid_hashes(hash_image), image

    def calculate_grid_hashes(self, image):
        width, height = image.size
        hashes = []

        for row in range(GRID_ROWS):
            top = round(row * height / GRID_ROWS)
            bottom = round((row + 1) * height / GRID_ROWS)

            for col in range(GRID_COLS):
                left = round(col * width / GRID_COLS)
                right = round((col + 1) * width / GRID_COLS)

                block = image.crop((left, top, right, bottom))

                hashes.append({
                    "row": row,
                    "col": col,
                    "hash": imagehash.phash(block),
                })

        return hashes

    def compare_grid_hashes(self, hashes1, hashes2):
        return [
            {
                "row": block1["row"],
                "col": block1["col"],
                "distance": block1["hash"] - block2["hash"],
            }
            for block1, block2 in zip(hashes1, hashes2)
        ]

    def format_changed_blocks(self, changed_blocks):
        if not changed_blocks:
            return "немає"

        parts = []
        for block in changed_blocks:
            pairs = ", ".join(
                f"{pair}={distance}"
                for pair, distance in block["pair_distances"].items()
            )
            parts.append(
                f"R{block['row'] + 1}C{block['col'] + 1} "
                f"(max={block['distance']}; {pairs})"
            )

        return "; ".join(parts)

    def fill_table(self, results):
        self.results = results

        for result in results:
            self.tree.insert(
                "",
                "end",
                values=(
                    result["page"],
                    result["status"],
                    result["changed_blocks"],
                    result["max_distance"],
                    result["total_distance"],
                    result["blocks"],
                ),
                tags=(result["tag"],),
            )

    def clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.current_page_index = None

        for index, canvas in enumerate(self.canvases):
            canvas.itemconfig(self.canvas_image_ids[index], image="")
            canvas.configure(scrollregion=(0, 0, 0, 0))

        self.preview_imgs = [None] * self.pdf_count

    def on_row_select(self, _event):
        selected = self.tree.selection()

        if not selected:
            return

        values = self.tree.item(selected[0], "values")
        page_number = int(values[0])

        self.current_page_index = page_number - 1
        self.show_preview(self.current_page_index)

    def show_preview(self, page_index):
        changed_blocks = self.changed_blocks_by_page.get(page_index, [])

        images = [
            self.pdf_images[index][page_index]
            if page_index < len(self.pdf_images[index])
            else None
            for index in range(self.pdf_count)
        ]

        images = self.normalize_preview_images(images)

        for side, image in enumerate(images):
            self.render_canvas_side(
                side=side,
                image=image,
                changed_blocks=changed_blocks,
            )

    def render_canvas_side(self, side, image, changed_blocks):
        canvas = self.canvases[side]
        image_id = self.canvas_image_ids[side]

        if image is None:
            canvas.itemconfig(image_id, image="")
            canvas.configure(scrollregion=(0, 0, 0, 0))
            self.preview_imgs[side] = None
            return

        if self.show_grid.get():
            image = self.draw_grid_and_changes(image, changed_blocks)

        width = max(1, round(image.width * self.zoom_factor))
        height = max(1, round(image.height * self.zoom_factor))

        scaled = image.resize(
            (width, height),
            Image.Resampling.LANCZOS,
        )

        photo = ImageTk.PhotoImage(scaled)
        self.preview_imgs[side] = photo

        canvas.itemconfig(image_id, image=photo)
        canvas.coords(image_id, 0, 0)
        canvas.configure(scrollregion=(0, 0, width, height))

    def normalize_preview_images(self, images):
        existing = [image for image in images if image is not None]
        if not existing:
            return images

        target_height = max(image.height for image in existing)
        return [
            self.resize_by_height(image, target_height)
            if image is not None
            else None
            for image in images
        ]

    def resize_by_height(self, image, target_height):
        ratio = target_height / image.height
        target_width = round(image.width * ratio)

        return image.resize(
            (target_width, target_height),
            Image.Resampling.LANCZOS,
        )

    def draw_grid_and_changes(self, image, changed_blocks):
        marked = image.copy()
        draw = ImageDraw.Draw(marked)

        width, height = marked.size
        changed = {
            (block["row"], block["col"]): block["distance"]
            for block in changed_blocks
        }

        for row in range(1, GRID_ROWS):
            y = round(row * height / GRID_ROWS)
            draw.line(
                [(0, y), (width, y)],
                fill=(80, 80, 80),
                width=2,
            )

        for col in range(1, GRID_COLS):
            x = round(col * width / GRID_COLS)
            draw.line(
                [(x, 0), (x, height)],
                fill=(80, 80, 80),
                width=2,
            )

        for (row, col), distance in changed.items():
            left = round(col * width / GRID_COLS)
            right = round((col + 1) * width / GRID_COLS)
            top = round(row * height / GRID_ROWS)
            bottom = round((row + 1) * height / GRID_ROWS)

            draw.rectangle(
                [(left, top), (right - 1, bottom - 1)],
                outline=(255, 0, 0),
                width=8,
            )

            label = f"R{row + 1}C{col + 1}: {distance}"

            draw.rectangle(
                [
                    (left + 5, top + 5),
                    (left + 190, top + 40),
                ],
                fill=(255, 255, 255),
            )
            draw.text(
                (left + 10, top + 10),
                label,
                fill=(255, 0, 0),
            )

        return marked

    def refresh_preview(self):
        if self.current_page_index is not None:
            self.show_preview(self.current_page_index)

    def zoom_in(self):
        self.set_zoom(self.zoom_factor + ZOOM_STEP)

    def zoom_out(self):
        self.set_zoom(self.zoom_factor - ZOOM_STEP)

    def set_zoom(self, value):
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, value))

        if abs(new_zoom - self.zoom_factor) < 0.001:
            return

        positions = self.get_scroll_positions()

        self.zoom_factor = new_zoom
        self.zoom_label.config(text=f"{round(new_zoom * 100)}%")
        self.refresh_preview()

        self.root.after(
            10,
            lambda: self.restore_scroll_positions(positions),
        )

    def get_scroll_positions(self):
        return [
            (canvas.xview()[0], canvas.yview()[0])
            for canvas in self.canvases
        ]

    def restore_scroll_positions(self, positions):
        for canvas, (x_pos, y_pos) in zip(self.canvases, positions):
            canvas.xview_moveto(x_pos)
            canvas.yview_moveto(y_pos)

    def fit_page(self):
        image = self.get_reference_image()

        if image is None or not self.canvases:
            return

        self.root.update_idletasks()

        available_width = max(1, self.canvases[0].winfo_width() - 20)
        available_height = max(1, self.canvases[0].winfo_height() - 20)

        self.set_zoom(
            min(
                available_width / image.width,
                available_height / image.height,
            )
        )

    def fit_width(self):
        image = self.get_reference_image()

        if image is None or not self.canvases:
            return

        self.root.update_idletasks()

        available_width = max(1, self.canvases[0].winfo_width() - 20)
        self.set_zoom(available_width / image.width)

    def get_reference_image(self):
        if self.current_page_index is None:
            return None

        for images in self.pdf_images:
            if self.current_page_index < len(images):
                return images[self.current_page_index]

        return None

    def on_ctrl_mousewheel(self, event):
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

        return "break"

    def on_ctrl_mousewheel_linux(self, event):
        if event.num == 4:
            self.zoom_in()
        elif event.num == 5:
            self.zoom_out()

        return "break"

    def start_drag(self, side, event):
        self._drag_start[side] = (event.x, event.y)

    def drag_canvas(self, side, event):
        canvas = self.canvases[side]

        if side not in self._drag_start:
            return

        start_x, start_y = self._drag_start[side]
        delta_x = start_x - event.x
        delta_y = start_y - event.y

        canvas.xview_scroll(int(delta_x), "units")
        canvas.yview_scroll(int(delta_y), "units")

        self._drag_start[side] = (event.x, event.y)

        if self.sync_scroll.get():
            self.sync_other_canvases(side)

    def scroll_x(self, side, *args):
        canvas = self.canvases[side]
        canvas.xview(*args)

        if self.sync_scroll.get():
            self.sync_other_canvases(side)

    def scroll_y(self, side, *args):
        canvas = self.canvases[side]
        canvas.yview(*args)

        if self.sync_scroll.get():
            self.sync_other_canvases(side)

    def sync_other_canvases(self, source_side):
        if self._syncing_scroll:
            return

        self._syncing_scroll = True

        try:
            source = self.canvases[source_side]
            x_pos = source.xview()[0]
            y_pos = source.yview()[0]

            for index, target in enumerate(self.canvases):
                if index == source_side:
                    continue
                target.xview_moveto(x_pos)
                target.yview_moveto(y_pos)

        finally:
            self._syncing_scroll = False

    def update_progress(self, processed, total, text):
        def update():
            self.progress["value"] = processed
            self.status_label.config(
                text=f"{text} | {processed}/{total}"
            )

        self.root.after(0, update)


if __name__ == "__main__":
    root = tk.Tk()
    app = PdfGridHashComparatorApp(root)
    root.mainloop()
