"""Sales report using the same PDF engine already installed with Rebecca."""
from collections import Counter
from datetime import datetime
from pathlib import Path
import sqlite3
from contextlib import closing

from fpdf import FPDF


def safe(value):
    return str(value or '').encode('latin-1', 'replace').decode('latin-1')


def pesos(value):
    return '$ ' + f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


class SalesPDF(FPDF):
    def header(self):
        self.set_fill_color(20, 35, 55)
        self.rect(0, 0, 210, 32, 'F')
        self.set_xy(14, 10)
        self.set_text_color(255, 255, 255)
        self.set_font('Arial', 'B', 20)
        self.cell(120, 8, 'SUBLIZEN')
        self.set_font('Arial', '', 9)
        self.cell(62, 8, 'REPORTE DE VENTAS', 0, 1, 'R')
        self.set_xy(14, 21)
        self.set_text_color(120, 220, 213)
        self.cell(182, 5, 'Taller creativo / Control diario', 0, 1)
        self.set_text_color(30, 45, 60)
        self.set_y(40)

    def footer(self):
        self.set_y(-16)
        self.set_draw_color(215, 225, 232)
        self.line(14, self.get_y(), 196, self.get_y())
        self.set_font('Arial', '', 8)
        self.set_text_color(100, 115, 125)
        self.cell(140, 9, 'Control interno de ventas. No es una factura fiscal.')
        self.cell(42, 9, f'Página {self.page_no()} / {{nb}}', 0, 0, 'R')

    def table(self, headers, widths, rows):
        def head():
            self.set_font('Arial', 'B', 9)
            self.set_fill_color(224, 242, 240)
            self.set_text_color(20, 60, 65)
            for label, width in zip(headers, widths):
                self.cell(width, 9, safe(label), 0, 0, 'L', True)
            self.ln(9)
        head()
        for idx, row in enumerate(rows):
            self.set_font('Arial', '', 9)
            lines = [self.multi_cell(w - 4, 5, safe(v), split_only=True) for w, v in zip(widths, row)]
            height = max(10, max(len(cell) for cell in lines) * 5 + 4)
            if self.get_y() + height > 272:
                self.add_page()
                head()
                self.set_font('Arial', '', 9)
            x, y = self.get_x(), self.get_y()
            self.set_fill_color(*( (246, 248, 250) if idx % 2 == 0 else (255, 255, 255)))
            self.rect(x, y, sum(widths), height, 'F')
            self.set_text_color(30, 45, 60)
            for width, cell in zip(widths, lines):
                self.set_xy(x + 2, y + 2)
                self.multi_cell(width - 4, 5, '\n'.join(cell), 0, 'L')
                x += width
            self.set_xy(14, y + height)


def generate_sales_pdf(db, date, output):
    datetime.strptime(date, '%Y-%m-%d')
    with closing(sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        all_rows = [dict(r) for r in conn.execute('SELECT * FROM ventas_dia WHERE fecha=? ORDER BY hora,id', (date,))]
    rows = [r for r in all_rows if not r.get('anulada')]
    missing = sum(r.get('precio_unitario') is None for r in rows)
    total = sum(r.get('precio_unitario') or 0 for r in rows)
    pdf = SalesPDF()
    pdf.set_margins(14, 40, 14)
    pdf.set_auto_page_break(True, 23)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('Arial', 'B', 18)
    pdf.cell(182, 10, 'Ventas del día', 0, 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(182, 7, datetime.strptime(date, '%Y-%m-%d').strftime('%d / %m / %Y'), 0, 1)
    pdf.ln(5)
    start_y = pdf.get_y()
    for x, label, value in [(14, 'UNIDADES', str(len(rows))), (76, 'PRODUCTOS', str(len(Counter(r['producto'] for r in rows)))) , (138, 'IMPORTE REGISTRADO', 'Sin registrar' if rows and missing == len(rows) else pesos(total))]:
        pdf.set_fill_color(240, 245, 248)
        pdf.rect(x, start_y, 58, 24, 'F')
        pdf.set_xy(x + 4, start_y + 3)
        pdf.set_font('Arial', '', 8)
        pdf.cell(50, 5, label)
        pdf.set_xy(x + 4, start_y + 11)
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(50, 8, value)
    pdf.set_xy(14, start_y + 30)
    pdf.set_font('Arial', '', 9)
    note = f'{missing} unidad(es) sin importe registrado. ' if missing else ''
    note += 'Importes de venta; no acreditan cobros ni transferencias.'
    pdf.multi_cell(182, 5, safe(note))
    if len(all_rows) != len(rows):
        pdf.multi_cell(182, 5, f'Ventas anuladas excluidas: {len(all_rows) - len(rows)}.')
    pdf.ln(6)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(182, 8, 'Resumen por producto', 0, 1)
    grouped = Counter(r['producto'] for r in rows)
    pdf.table(['Producto', 'Unidades'], [152, 30], [(name, qty) for name, qty in grouped.items()] or [('Sin ventas registradas', 0)])
    pdf.ln(7)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(182, 8, 'Detalle / una fila por unidad', 0, 1)
    detail = []
    for r in rows:
        design = r.get('diseno') or ''
        if design.lower() in {'cliente sin nombre', 'sin cliente', 'sin nombre'}:
            design = ''
        description = r['producto'] + (' / ' + design if design else '')
        detail.append((f"#{r['id']}", str(r['hora'])[-8:-3], description, r.get('cliente') or 'Sin registrar', pesos(r['precio_unitario']) if r.get('precio_unitario') is not None else 'Pendiente'))
    pdf.table(['Venta', 'Hora', 'Producto / diseño', 'Cliente', 'Importe'], [18, 17, 77, 36, 34], detail)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))
    return str(output)
