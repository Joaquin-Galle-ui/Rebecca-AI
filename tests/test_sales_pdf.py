import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from rebecca_companion.sales_pdf import generate_sales_pdf


class SalesPdfTests(unittest.TestCase):
    def test_legacy_database_and_long_multipage_report(self):
        with tempfile.TemporaryDirectory() as folder:
            db, output = Path(folder) / 'sales.db', Path(folder) / 'sales.pdf'
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute('CREATE TABLE ventas_dia(id INTEGER PRIMARY KEY,producto TEXT,diseno TEXT,fecha TEXT,hora TEXT)')
                conn.executemany('INSERT INTO ventas_dia VALUES(?,?,?,?,?)', [
                    (i, 'Remera personalizada de algodón con diseño de Goku y nombre del cliente', 'Diseño especial', '2026-09-05', '2026-09-05 20:00:00') for i in range(1, 76)
                ])
            generate_sales_pdf(db, '2026-09-05', output)
            content = output.read_bytes()
            self.assertTrue(content.startswith(b'%PDF'))
            self.assertGreaterEqual(len(re.findall(rb'/Type /Page\b', content)), 3)
