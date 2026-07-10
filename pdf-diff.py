import io
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import fitz  # PyMuPDF
from PIL import Image, ImageTk
import imagehash


THRESHOLD = 6
RENDER_SCALE = 2


class PdfHashComparatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Hash Comparator")
        self.root.geometry("1200x850")

        self.pdf1_path = None
        self.pdf2_path = None

        self.pdf1_images = []
        self.pdf2_images = []
        self.results = []

        self.preview_img1 = None
        self.preview_img2 = None

        self.build_ui()

    def build_ui(self):
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill="x", padx=10, pady=10)

        tk.Button(top_frame, text="Вибрати PDF 1", command=self.select_pdf1).pack(side="left", padx=5)
        tk.Button(top_frame, text="Вибрати PDF 2", command=self.select_pdf2).pack(side="left", padx=5)
        tk.Button(top_frame, text="Порівняти", command=self.compare).pack(side="left", padx=5)

        self.progress = ttk.Progressbar(top_frame, length=250, mode="determinate")
        self.progress.pack(side="left", padx=10)

        self.status_label = tk.Label(top_frame, text="Готово")
        self.status_label.pack(side="left", padx=10)

        self.pdf1_label = tk.Label(top_frame, text="PDF 1 не вибрано")
        self.pdf1_label.pack(side="left", padx=15)

        self.pdf2_label = tk.Label(top_frame, text="PDF 2 не вибрано")
        self.pdf2_label.pack(side="left", padx=15)

        table_frame = tk.Frame(self.root)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("page", "status", "distance")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=15)

        self.tree.heading("page", text="Сторінка")
        self.tree.heading("status", text="Статус")
        self.tree.heading("distance", text="Distance")

        self.tree.column("page", width=100, anchor="center")
        self.tree.column("status", width=250, anchor="center")
        self.tree.column("distance", width=120, anchor="center")

        self.tree.tag_configure("ok", background="#d9fdd3")
        self.tree.tag_configure("diff", background="#ffd6d6")
        self.tree.tag_configure("missing", background="#fff3cd")

        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_row_select)

        preview_frame = tk.Frame(self.root)
        preview_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.preview1_label = tk.Label(preview_frame, text="Preview PDF 1", bd=1, relief="solid")
        self.preview1_label.pack(side="left", fill="both", expand=True, padx=5)

        self.preview2_label = tk.Label(preview_frame, text="Preview PDF 2", bd=1, relief="solid")
        self.preview2_label.pack(side="left", fill="both", expand=True, padx=5)

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
            messagebox.showwarning("Помилка", "Вибери обидва PDF файли")
            return

        self.clear_table()

        self.progress["value"] = 0
        self.status_label.config(text="Підготовка...")

        thread = threading.Thread(target=self.compare_worker, daemon=True)
        thread.start()

    def compare_worker(self):
        try:
            doc1 = fitz.open(self.pdf1_path)
            doc2 = fitz.open(self.pdf2_path)

            total_pages = len(doc1) + len(doc2)
            processed_pages = 0

            hashes1 = []
            hashes2 = []

            self.pdf1_images = []
            self.pdf2_images = []

            self.root.after(0, lambda: self.progress.config(maximum=total_pages))

            for page_index in range(len(doc1)):
                page_hash, image = self.render_single_page(doc1, page_index)

                hashes1.append(page_hash)
                self.pdf1_images.append(image)

                processed_pages += 1
                self.update_progress(
                    processed_pages,
                    total_pages,
                    f"Обробка PDF 1: сторінка {page_index + 1}/{len(doc1)}"
                )

            for page_index in range(len(doc2)):
                page_hash, image = self.render_single_page(doc2, page_index)

                hashes2.append(page_hash)
                self.pdf2_images.append(image)

                processed_pages += 1
                self.update_progress(
                    processed_pages,
                    total_pages,
                    f"Обробка PDF 2: сторінка {page_index + 1}/{len(doc2)}"
                )

            results = []
            max_pages = max(len(hashes1), len(hashes2))

            for i in range(max_pages):
                page_number = i + 1

                if i >= len(hashes1):
                    status = "Відсутня в PDF 1"
                    distance = "-"
                    tag = "missing"

                elif i >= len(hashes2):
                    status = "Відсутня в PDF 2"
                    distance = "-"
                    tag = "missing"

                else:
                    distance_value = hashes1[i] - hashes2[i]
                    distance = str(distance_value)

                    if distance_value > THRESHOLD:
                        status = "DIFFERENT"
                        tag = "diff"
                    else:
                        status = "OK"
                        tag = "ok"

                results.append({
                    "page": page_number,
                    "status": status,
                    "distance": distance,
                    "tag": tag,
                })

            self.root.after(0, lambda: self.fill_table(results))
            self.root.after(0, lambda: self.status_label.config(text="Готово"))

            doc1.close()
            doc2.close()

        except Exception as e:
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Помилка",
                    f"Не вдалося обробити PDF:\n{e}"
                )
            )
            self.root.after(0, lambda: self.status_label.config(text="Помилка"))

    def render_single_page(self, doc, page_index):
        page = doc[page_index]

        pix = page.get_pixmap(
            matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE),
            alpha=False
        )

        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        page_hash = imagehash.phash(image)

        return page_hash, image

    def update_progress(self, processed, total, text):
        def update():
            self.progress["value"] = processed
            self.status_label.config(text=f"{text} | {processed}/{total}")

        self.root.after(0, update)

    def fill_table(self, results):
        self.results = results

        for result in results:
            self.tree.insert(
                "",
                "end",
                values=(result["page"], result["status"], result["distance"]),
                tags=(result["tag"],)
            )

    def clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.preview1_label.config(image="", text="Preview PDF 1")
        self.preview2_label.config(image="", text="Preview PDF 2")

        self.preview_img1 = None
        self.preview_img2 = None

    def on_row_select(self, event):
        selected = self.tree.selection()

        if not selected:
            return

        item = selected[0]
        values = self.tree.item(item, "values")

        page_number = int(values[0])
        page_index = page_number - 1

        self.show_preview(page_index)

    def show_preview(self, page_index):
        if page_index < len(self.pdf1_images):
            img1 = self.prepare_preview_image(self.pdf1_images[page_index])
            self.preview_img1 = ImageTk.PhotoImage(img1)
            self.preview1_label.config(image=self.preview_img1, text="")
        else:
            self.preview1_label.config(image="", text="Сторінка відсутня в PDF 1")

        if page_index < len(self.pdf2_images):
            img2 = self.prepare_preview_image(self.pdf2_images[page_index])
            self.preview_img2 = ImageTk.PhotoImage(img2)
            self.preview2_label.config(image=self.preview_img2, text="")
        else:
            self.preview2_label.config(image="", text="Сторінка відсутня в PDF 2")

    def prepare_preview_image(self, image):
        max_width = 520
        max_height = 420

        img = image.copy()
        img.thumbnail((max_width, max_height))

        return img


if __name__ == "__main__":
    root = tk.Tk()
    app = PdfHashComparatorApp(root)
    root.mainloop()