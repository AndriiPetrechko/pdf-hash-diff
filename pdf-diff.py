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


class PdfGridHashComparatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF diff v0.3")
        self.root.geometry("1450x950")

        self.pdf1_path = None
        self.pdf2_path = None

        self.pdf1_images = []
        self.pdf2_images = []
        self.pdf1_grid_hashes = []
        self.pdf2_grid_hashes = []

        self.changed_blocks_by_page = {}
        self.results = []

        self.current_page_index = None
        self.zoom_factor = 1.0

        self.show_grid = tk.BooleanVar(value=True)
        self.sync_scroll = tk.BooleanVar(value=True)

        self.preview_img1 = None
        self.preview_img2 = None

        self._syncing_scroll = False
        self._drag_start = {}

        self.build_ui()

    def build_ui(self):
        self.build_top_bar()
        self.build_view_controls()
        self.build_results_table()
        self.build_preview_area()

    def build_top_bar(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Button(
            frame,
            text="Вибрати PDF 1",
            command=self.select_pdf1
        ).pack(side="left", padx=5)

        tk.Button(
            frame,
            text="Вибрати PDF 2",
            command=self.select_pdf2
        ).pack(side="left", padx=5)

        tk.Button(
            frame,
            text="Порівняти",
            command=self.compare
        ).pack(side="left", padx=5)

        self.progress = ttk.Progressbar(
            frame,
            length=260,
            mode="determinate"
        )
        self.progress.pack(side="left", padx=10)

        self.status_label = tk.Label(frame, text="Готово")
        self.status_label.pack(side="left", padx=10)

        self.pdf1_label = tk.Label(frame, text="PDF 1 не вибрано")
        self.pdf1_label.pack(side="left", padx=15)

        self.pdf2_label = tk.Label(frame, text="PDF 2 не вибрано")
        self.pdf2_label.pack(side="left", padx=15)

    def build_view_controls(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(frame, text="Zoom:").pack(side="left", padx=(5, 3))

        tk.Button(
            frame,
            text="−",
            width=3,
            command=self.zoom_out
        ).pack(side="left", padx=2)

        self.zoom_label = tk.Label(frame, text="100%", width=7)
        self.zoom_label.pack(side="left", padx=2)

        tk.Button(
            frame,
            text="+",
            width=3,
            command=self.zoom_in
        ).pack(side="left", padx=2)

        tk.Button(
            frame,
            text="Fit page",
            command=self.fit_page
        ).pack(side="left", padx=(8, 3))

        tk.Button(
            frame,
            text="Fit width",
            command=self.fit_width
        ).pack(side="left", padx=3)

        ttk.Checkbutton(
            frame,
            text="Синхронна прокрутка",
            variable=self.sync_scroll
        ).pack(side="left", padx=(15, 5))

        ttk.Checkbutton(
            frame,
            text="Показувати сітку",
            variable=self.show_grid,
            command=self.refresh_preview
        ).pack(side="left", padx=5)

        tk.Label(
            frame,
            text="Ctrl + колесо — масштаб, ліва кнопка миші — перетягування"
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
            "blocks"
        )

        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=11
        )

        headings = {
            "page": "Сторінка",
            "status": "Статус",
            "changed_blocks": "Змінено блоків",
            "max_distance": "Max distance",
            "total_distance": "Сума distance",
            "blocks": "Блоки",
        }

        widths = {
            "page": 80,
            "status": 180,
            "changed_blocks": 130,
            "max_distance": 120,
            "total_distance": 120,
            "blocks": 500,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor="center" if column != "blocks" else "w"
            )

        self.tree.tag_configure("ok", background="#d9fdd3")
        self.tree.tag_configure("diff", background="#ffd6d6")
        self.tree.tag_configure("missing", background="#fff3cd")

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_row_select)

    def build_preview_area(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        left = tk.LabelFrame(frame, text="PDF 1")
        left.pack(side="left", fill="both", expand=True, padx=(0, 5))

        right = tk.LabelFrame(frame, text="PDF 2")
        right.pack(side="left", fill="both", expand=True, padx=(5, 0))

        self.canvas1, self.canvas1_image_id = self.create_canvas(left, 1)
        self.canvas2, self.canvas2_image_id = self.create_canvas(right, 2)

    def create_canvas(self, parent, side):
        container = tk.Frame(parent)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            container,
            background="#2b2b2b",
            highlightthickness=0
        )

        h_scroll = ttk.Scrollbar(container, orient="horizontal")
        v_scroll = ttk.Scrollbar(container, orient="vertical")

        canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        image_id = canvas.create_image(0, 0, anchor="nw")

        h_scroll.configure(
            command=lambda *args: self.scroll_x(side, *args)
        )
        v_scroll.configure(
            command=lambda *args: self.scroll_y(side, *args)
        )

        canvas.configure(
            xscrollcommand=h_scroll.set,
            yscrollcommand=v_scroll.set
        )

        canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel)
        canvas.bind("<Control-Button-4>", self.on_ctrl_mousewheel_linux)
        canvas.bind("<Control-Button-5>", self.on_ctrl_mousewheel_linux)

        canvas.bind(
            "<ButtonPress-1>",
            lambda event: self.start_drag(side, event)
        )
        canvas.bind(
            "<B1-Motion>",
            lambda event: self.drag_canvas(side, event)
        )

        return canvas, image_id

    def select_pdf1(self):
        path = filedialog.askopenfilename(
            title="Вибрати PDF 1",
            filetypes=[("PDF files", "*.pdf")]
        )

        if path:
            self.pdf1_path = path
            self.pdf1_label.config(text=os.path.basename(path))

    def select_pdf2(self):
        path = filedialog.askopenfilename(
            title="Вибрати PDF 2",
            filetypes=[("PDF files", "*.pdf")]
        )

        if path:
            self.pdf2_path = path
            self.pdf2_label.config(text=os.path.basename(path))

    def compare(self):
        if not self.pdf1_path or not self.pdf2_path:
            messagebox.showwarning(
                "Помилка",
                "Вибери обидва PDF файли"
            )
            return

        self.clear_table()
        self.progress["value"] = 0
        self.status_label.config(text="Підготовка...")

        thread = threading.Thread(
            target=self.compare_worker,
            daemon=True
        )
        thread.start()

    def compare_worker(self):
        doc1 = None
        doc2 = None

        try:
            doc1 = fitz.open(self.pdf1_path)
            doc2 = fitz.open(self.pdf2_path)

            total_pages = len(doc1) + len(doc2)
            processed_pages = 0

            self.pdf1_images = []
            self.pdf2_images = []
            self.pdf1_grid_hashes = []
            self.pdf2_grid_hashes = []
            self.changed_blocks_by_page = {}

            self.root.after(
                0,
                lambda: self.progress.config(maximum=total_pages)
            )

            for page_index in range(len(doc1)):
                hashes, image = self.render_single_page(doc1, page_index)

                self.pdf1_grid_hashes.append(hashes)
                self.pdf1_images.append(image)

                processed_pages += 1
                self.update_progress(
                    processed_pages,
                    total_pages,
                    f"Обробка PDF 1: сторінка "
                    f"{page_index + 1}/{len(doc1)}"
                )

            for page_index in range(len(doc2)):
                hashes, image = self.render_single_page(doc2, page_index)

                self.pdf2_grid_hashes.append(hashes)
                self.pdf2_images.append(image)

                processed_pages += 1
                self.update_progress(
                    processed_pages,
                    total_pages,
                    f"Обробка PDF 2: сторінка "
                    f"{page_index + 1}/{len(doc2)}"
                )

            results = []
            max_pages = max(
                len(self.pdf1_grid_hashes),
                len(self.pdf2_grid_hashes)
            )

            for index in range(max_pages):
                page_number = index + 1

                if index >= len(self.pdf1_grid_hashes):
                    result = self.missing_result(
                        page_number,
                        "Відсутня в PDF 1"
                    )

                elif index >= len(self.pdf2_grid_hashes):
                    result = self.missing_result(
                        page_number,
                        "Відсутня в PDF 2"
                    )

                else:
                    distances = self.compare_grid_hashes(
                        self.pdf1_grid_hashes[index],
                        self.pdf2_grid_hashes[index]
                    )

                    changed_blocks = [
                        block
                        for block in distances
                        if block["distance"] > BLOCK_THRESHOLD
                    ]

                    self.changed_blocks_by_page[index] = changed_blocks

                    changed_count = len(changed_blocks)
                    max_distance = max(
                        block["distance"] for block in distances
                    )
                    total_distance = sum(
                        block["distance"] for block in distances
                    )

                    is_different = changed_count >= MIN_CHANGED_BLOCKS

                    result = {
                        "page": page_number,
                        "status": "DIFFERENT" if is_different else "OK",
                        "changed_blocks": (
                            f"{changed_count}/{GRID_ROWS * GRID_COLS}"
                        ),
                        "max_distance": str(max_distance),
                        "total_distance": str(total_distance),
                        "blocks": self.format_changed_blocks(
                            changed_blocks
                        ),
                        "tag": "diff" if is_different else "ok",
                    }

                results.append(result)

            self.root.after(0, lambda: self.fill_table(results))
            self.root.after(
                0,
                lambda: self.status_label.config(text="Готово")
            )

        except Exception as exc:
            error_text = str(exc)

            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Помилка",
                    f"Не вдалося обробити PDF:\n{error_text}"
                )
            )
            self.root.after(
                0,
                lambda: self.status_label.config(text="Помилка")
            )

        finally:
            if doc1 is not None:
                doc1.close()
            if doc2 is not None:
                doc2.close()

    def missing_result(self, page_number, status):
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
            alpha=False
        )

        image = Image.open(
            io.BytesIO(pix.tobytes("png"))
        ).convert("RGB")

        hash_image = image.resize(
            HASH_IMAGE_SIZE,
            Image.Resampling.LANCZOS
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

        return ", ".join(
            f"R{block['row'] + 1}C{block['col'] + 1}"
            f"={block['distance']}"
            for block in changed_blocks
        )

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
                tags=(result["tag"],)
            )

    def clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.current_page_index = None

        self.canvas1.itemconfig(self.canvas1_image_id, image="")
        self.canvas2.itemconfig(self.canvas2_image_id, image="")

        self.canvas1.configure(scrollregion=(0, 0, 0, 0))
        self.canvas2.configure(scrollregion=(0, 0, 0, 0))

        self.preview_img1 = None
        self.preview_img2 = None

    def on_row_select(self, _event):
        selected = self.tree.selection()

        if not selected:
            return

        values = self.tree.item(selected[0], "values")
        page_number = int(values[0])

        self.current_page_index = page_number - 1
        self.show_preview(self.current_page_index)

    def show_preview(self, page_index):
        changed_blocks = self.changed_blocks_by_page.get(
            page_index,
            []
        )

        if (
            page_index < len(self.pdf1_images)
            and page_index < len(self.pdf2_images)
            and self.pdf1_images[page_index] is not None
            and self.pdf2_images[page_index] is not None
        ):
            image1, image2 = self.normalize_preview_pair(
                self.pdf1_images[page_index],
                self.pdf2_images[page_index]
            )
        else:
            image1 = (
                self.pdf1_images[page_index]
                if page_index < len(self.pdf1_images)
                else None
            )

            image2 = (
                self.pdf2_images[page_index]
                if page_index < len(self.pdf2_images)
                else None
            )

        self.render_canvas_side(
            side=1,
            image=image1,
            changed_blocks=changed_blocks
        )

        self.render_canvas_side(
            side=2,
            image=image2,
            changed_blocks=changed_blocks
        )

    def render_canvas_side(
        self,
        side,
        image,
        changed_blocks
    ):
        canvas = self.canvas1 if side == 1 else self.canvas2
        image_id = (
            self.canvas1_image_id
            if side == 1
            else self.canvas2_image_id
        )

        if image is None:
            canvas.itemconfig(image_id, image="")
            canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        if self.show_grid.get():
            image = self.draw_grid_and_changes(
                image,
                changed_blocks
            )

        width = max(1, round(image.width * self.zoom_factor))
        height = max(1, round(image.height * self.zoom_factor))

        scaled = image.resize(
            (width, height),
            Image.Resampling.LANCZOS
        )

        photo = ImageTk.PhotoImage(scaled)

        if side == 1:
            self.preview_img1 = photo
        else:
            self.preview_img2 = photo

        canvas.itemconfig(image_id, image=photo)
        canvas.coords(image_id, 0, 0)
        canvas.configure(scrollregion=(0, 0, width, height))

    def normalize_preview_pair(self, image1, image2):
        target_height = max(image1.height, image2.height)

        image1 = self.resize_by_height(image1, target_height)
        image2 = self.resize_by_height(image2, target_height)

        return image1, image2


    def resize_by_height(self, image, target_height):
        ratio = target_height / image.height
        target_width = round(image.width * ratio)

        return image.resize(
            (target_width, target_height),
            Image.Resampling.LANCZOS
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
                width=2
            )

        for col in range(1, GRID_COLS):
            x = round(col * width / GRID_COLS)
            draw.line(
                [(x, 0), (x, height)],
                fill=(80, 80, 80),
                width=2
            )

        for (row, col), distance in changed.items():
            left = round(col * width / GRID_COLS)
            right = round((col + 1) * width / GRID_COLS)
            top = round(row * height / GRID_ROWS)
            bottom = round((row + 1) * height / GRID_ROWS)

            draw.rectangle(
                [(left, top), (right - 1, bottom - 1)],
                outline=(255, 0, 0),
                width=8
            )

            label = f"R{row + 1}C{col + 1}: {distance}"

            draw.rectangle(
                [
                    (left + 5, top + 5),
                    (left + 190, top + 40)
                ],
                fill=(255, 255, 255)
            )
            draw.text(
                (left + 10, top + 10),
                label,
                fill=(255, 0, 0)
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
            lambda: self.restore_scroll_positions(positions)
        )

    def get_scroll_positions(self):
        return {
            "x1": self.canvas1.xview()[0],
            "y1": self.canvas1.yview()[0],
            "x2": self.canvas2.xview()[0],
            "y2": self.canvas2.yview()[0],
        }

    def restore_scroll_positions(self, positions):
        self.canvas1.xview_moveto(positions["x1"])
        self.canvas1.yview_moveto(positions["y1"])
        self.canvas2.xview_moveto(positions["x2"])
        self.canvas2.yview_moveto(positions["y2"])

    def fit_page(self):
        image = self.get_reference_image()

        if image is None:
            return

        self.root.update_idletasks()

        available_width = max(1, self.canvas1.winfo_width() - 20)
        available_height = max(1, self.canvas1.winfo_height() - 20)

        self.set_zoom(
            min(
                available_width / image.width,
                available_height / image.height
            )
        )

    def fit_width(self):
        image = self.get_reference_image()

        if image is None:
            return

        self.root.update_idletasks()

        available_width = max(1, self.canvas1.winfo_width() - 20)
        self.set_zoom(available_width / image.width)

    def get_reference_image(self):
        if self.current_page_index is None:
            return None

        if self.current_page_index < len(self.pdf1_images):
            return self.pdf1_images[self.current_page_index]

        if self.current_page_index < len(self.pdf2_images):
            return self.pdf2_images[self.current_page_index]

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
        canvas = self.canvas1 if side == 1 else self.canvas2

        if side not in self._drag_start:
            return

        start_x, start_y = self._drag_start[side]
        delta_x = start_x - event.x
        delta_y = start_y - event.y

        canvas.xview_scroll(int(delta_x), "units")
        canvas.yview_scroll(int(delta_y), "units")

        self._drag_start[side] = (event.x, event.y)

        if self.sync_scroll.get():
            self.sync_other_canvas(side)

    def scroll_x(self, side, *args):
        canvas = self.canvas1 if side == 1 else self.canvas2
        canvas.xview(*args)

        if self.sync_scroll.get():
            self.sync_other_canvas(side)

    def scroll_y(self, side, *args):
        canvas = self.canvas1 if side == 1 else self.canvas2
        canvas.yview(*args)

        if self.sync_scroll.get():
            self.sync_other_canvas(side)

    def sync_other_canvas(self, source_side):
        if self._syncing_scroll:
            return

        self._syncing_scroll = True

        try:
            source = self.canvas1 if source_side == 1 else self.canvas2
            target = self.canvas2 if source_side == 1 else self.canvas1

            target.xview_moveto(source.xview()[0])
            target.yview_moveto(source.yview()[0])

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
