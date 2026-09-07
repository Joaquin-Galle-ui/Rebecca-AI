import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from rebecca_companion.commerce import execute, money


class CommerceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE ventas_dia(id INTEGER PRIMARY KEY,producto TEXT,diseno TEXT,fecha TEXT,hora TEXT);
                CREATE TABLE productos(id INTEGER PRIMARY KEY,nombre TEXT,precio_unitario REAL,costo_unitario REAL);
                INSERT INTO productos VALUES(1,'Taza',7500,2300);
                CREATE TABLE pedidos(id INTEGER PRIMARY KEY,estado TEXT,actualizado_en TEXT);
                INSERT INTO pedidos VALUES(10,'pendiente','');
            """)

    def rows(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql).fetchall()

    def test_real_messages_and_retries(self):
        text = 'Vendí una taza de Goku UI\nUna remera de Goku UI\nUn cuadro de Gohan Beast'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(self.db, text, 'telegram:1:24'), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.rows('SELECT * FROM ventas_dia')), 3)
        self.assertIn('importe pendiente', results[0]['message'])
        self.assertEqual(self.rows('SELECT precio_unitario FROM ventas_dia ORDER BY id'), [(7500,), (None,), (None,)])
        self.assertEqual(execute(self.db, 'vendí una taza', 'telegram:1:24')['status'], 'error')

    def test_price_preserves_cost_and_historical_sale(self):
        execute(self.db, 'vendí una taza de goku')
        result = execute(self.db, 'las tazas tienen un nuevo precio, ahora salen 8.500')
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(self.rows('SELECT precio_unitario,costo_unitario FROM productos'), [(8500,2300)])
        self.assertEqual(self.rows('SELECT precio_unitario FROM ventas_dia'), [(7500,)])

    def test_variant_uses_specific_catalog_price(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO productos VALUES(2,'Taza mágica',9000,3000)")
        execute(self.db, 'vendí una taza mágica de Goku')
        self.assertEqual(self.rows('SELECT precio_unitario FROM ventas_dia'), [(9000,)])

    def test_invalid_batch_does_not_partially_save(self):
        result = execute(self.db, 'vendí una taza\nquiero una remera')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.rows('SELECT * FROM ventas_dia'), [])

    def test_order_ids_all_or_nothing_and_production_state(self):
        self.assertEqual(execute(self.db, 'marca pedidos #10 y #11 como listo')['status'], 'error')
        self.assertEqual(self.rows('SELECT estado FROM pedidos'), [('pendiente',)])
        self.assertEqual(execute(self.db, 'marca pedido #10 en produccion')['status'], 'success')
        self.assertEqual(self.rows('SELECT estado FROM pedidos'), [('en_proceso',)])

    def test_edit_and_cancel_keep_sale_record(self):
        execute(self.db, 'vendí 2 tazas a $8.500')
        self.assertEqual(execute(self.db, 'modifica venta #1 precio a 9.000')['status'], 'success')
        self.assertEqual(execute(self.db, 'anula venta #2')['status'], 'success')
        self.assertEqual(self.rows('SELECT precio_unitario,anulada FROM ventas_dia ORDER BY id'), [(9000,0), (8500,1)])

    def test_list_sales_accepts_natural_infinitive(self):
        result = execute(self.db, 'listar ventas de hoy')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['message'], 'No hay ventas activas registradas hoy.')

    def test_rejects_hypothetical_and_invalid_amounts(self):
        for text in ('si vendí una taza', '¿vendí una taza?', 'quiero vender una taza'):
            self.assertEqual(execute(self.db, text)['status'], 'error')
        for text in ('-1', 'NaN', 'Infinity', '1.2.3'):
            with self.assertRaises(ValueError):
                money(text)
        self.assertEqual(money('8.500,50'), 8500.5)
