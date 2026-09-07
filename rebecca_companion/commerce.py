"""Owner-only, deterministic commerce commands; all writes are transactional."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation


def plain(text):
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower().strip()


def money(text):
    raw = str(text).strip().replace('$', '').replace(' ', '')
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?', raw):
        raw = raw.replace('.', '').replace(',', '.')
    else:
        raw = raw.replace(',', '.')
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError('El importe no es válido.')
    if not value.is_finite() or value < 0 or value > 100000000:
        raise ValueError('El importe debe ser positivo y válido.')
    return float(value.quantize(Decimal('.01')))


def product_key(name):
    tokens = re.findall(r'\w+', plain(name))
    return ' '.join(t[:-1] if t in {'tazas', 'remeras', 'cuadros', 'buzos', 'gorras'} else t for t in tokens)


def schema(conn):
    cols = {r[1] for r in conn.execute('PRAGMA table_info(ventas_dia)')}
    for name, definition in {'precio_unitario': 'REAL', 'cliente': "TEXT DEFAULT ''", 'anulada': 'INTEGER DEFAULT 0'}.items():
        if name not in cols:
            conn.execute(f'ALTER TABLE ventas_dia ADD COLUMN {name} {definition}')
    conn.execute('CREATE TABLE IF NOT EXISTS operaciones_comercio (clave TEXT PRIMARY KEY, solicitud TEXT NOT NULL, resultado TEXT NOT NULL)')


def parse(text):
    if '?' in text or '¿' in text:
        raise ValueError('Recibí una pregunta, no una orden de registro. No cambié nada.')
    text = re.sub(r'^\s*(?:rebecca|rebeca|becca)[\s,;:!-]+', '', text.strip(), flags=re.I)
    value = plain(text)
    if re.fullmatch(r'(?:mostrame|mostrar|lista|listar|ver|consulta|consultar)(?: las)? ventas(?: de hoy| del dia)?[.!?]*', value):
        return ('list',)
    sale = re.match(r'^(?:vendi|registre una venta de|registra(?:r)? (?:una |la )?venta(?: de)?|anota(?:r)? (?:una |la )?venta(?: de)?)\s+(.+)$', value, re.S)
    if sale:
        lines = re.split(r'\n+|;|\s+y\s+(?=(?:un[ao]?|\d+)\s)', sale[1])
        items = []
        for line in lines:
            match = re.fullmatch(r'(?:(\d+|una|uno|un)\s+)?(.+?)(?:\s+(?:a|por)\s+\$?([\d.,]+)(?:\s+(?:cada una|cada uno|c/u))?)?[.!]*', line.strip())
            if not match:
                raise ValueError('Separá los productos en líneas y usá cantidades enteras.')
            qty, product, amount = match.groups()
            if not re.match(r'^(?:tazas?|remeras?|cuadros?|buzos?|gorras?|llaveros?|vasos?|botellas?|mousepads?|stickers?|posavasos|ropa|libretas?|medias|conjuntos?|encendedores?)\b', product):
                raise ValueError('Indicá el producto, por ejemplo: vendí 2 tazas de Goku UI a $8.500.')
            if re.search(r'\b(?:ayer|manana|si|cuando|quiero|total|cliente|para|pagado|sena)\b', product):
                raise ValueError('Necesito separar producto, importe unitario y cliente. Usá: vendí 2 tazas de Goku a $8.500.')
            count = int(qty) if qty and qty.isdigit() else 1
            if count > 1 and re.search(r'\bpor\s+\$?[\d.,]+', line) and not re.search(r'\b(?:cada una|cada uno|c/u)\b', line):
                raise ValueError('¿Ese importe es por unidad o por todo? Indicá el precio unitario: vendí 2 tazas a $8.500 cada una. No registré la venta.')
            if len(product) > 200:
                raise ValueError('El nombre del producto es demasiado largo; separá el producto de las observaciones.')
            if not 1 <= count <= 100 or sum(i[0] for i in items) + count > 100:
                raise ValueError('La cantidad debe estar entre 1 y 100 unidades por mensaje.')
            items.append((count, product, money(amount) if amount else None))
        return ('sale', items)
    price = re.fullmatch(r'(?:las?|los?)\s+(.+?)\s+(?:tienen? un nuevo precio[,;]?\s*)?(?:ahora\s+)?(?:salen?|cuestan?|vale[n]?)\s+\$?([\d.,]+)[.!]?', value)
    price = price or re.fullmatch(r'(?:cambia|actualiza|fija)(?: el)? precio (?:de )?(?:las? |los? )?(.+?)\s+a\s+\$?([\d.,]+)[.!]?', value)
    if price:
        return ('price', price[1], money(price[2]))
    edit = re.fullmatch(r'(?:corrige|corregi|modifica|cambia|actualiza) (?:la )?venta\s+#?(\d+)\s+(producto|precio|cliente)\s*(?:a|:|=)\s*(.+)', value)
    if edit:
        return ('edit', int(edit[1]), edit[2], money(edit[3]) if edit[2] == 'precio' else edit[3].strip())
    cancel = re.fullmatch(r'(?:anula|anular|cancela)(?: la)? venta\s+#?(\d+)[.!]?', value)
    if cancel:
        return ('cancel', int(cancel[1]))
    order = re.fullmatch(r'(?:marca|marcar|pone|pon)(?: el| los)? pedidos?\s+([#\d, y]+)\s+(?:como |en )?(pendiente|en produccion|listo|entregado|cancelado)s?[.!]?', value)
    if order:
        return ('order', list(dict.fromkeys(int(i) for i in re.findall(r'\d+', order[1]))), 'en_proceso' if order[2] == 'en produccion' else order[2])
    raise ValueError('Necesito una instrucción precisa. Ejemplos: «vendí una taza de Goku», «cambia el precio de las tazas a 8.500», «modifica venta #12 precio a 8.500» o «marca pedido #10 como listo». No cambié nada.')


def execute(db, text, request_id=''):
    try:
        operation = parse(text)
        with closing(sqlite3.connect(db, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            schema(conn)
            request = json.dumps(operation, ensure_ascii=False)
            if request_id:
                previous = conn.execute('SELECT solicitud,resultado FROM operaciones_comercio WHERE clave=?', (request_id,)).fetchone()
                if previous:
                    if previous['solicitud'] != request:
                        raise ValueError('Este identificador ya corresponde a otra operación. No cambié nada.')
                    return json.loads(previous['resultado'])
            kind = operation[0]
            ids = []
            if kind == 'sale':
                now = datetime.now()
                catalog = conn.execute('SELECT nombre,precio_unitario FROM productos').fetchall()
                lines = []
                for qty, product, price in operation[1]:
                    if price is None:
                        exact = [r for r in catalog if product_key(r['nombre']) == product_key(product)]
                        # Use a generic product price only when uniquely named in the catalog.
                        generic = [r for r in catalog if product_key(product).startswith(product_key(r['nombre']) + ' ')]
                        if generic:
                            longest = max(len(product_key(r['nombre'])) for r in generic)
                            generic = [r for r in generic if len(product_key(r['nombre'])) == longest]
                        candidates = exact or generic
                        if len(candidates) == 1:
                            price = float(candidates[0]['precio_unitario'])
                    for _ in range(qty):
                        cur = conn.execute('INSERT INTO ventas_dia (producto,diseno,fecha,hora,precio_unitario) VALUES (?,?,?,?,?)',
                                           (product, '', now.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d %H:%M:%S'), price))
                        ids.append(cur.lastrowid)
                        lines.append(f"Venta #{cur.lastrowid}: {product} - " + (f'${price:,.2f}' if price is not None else 'importe pendiente'))
                message = 'Registrado:\n' + '\n'.join(lines)
            elif kind == 'price':
                matches = [r for r in conn.execute('SELECT id,nombre FROM productos') if product_key(r['nombre']) == product_key(operation[1])]
                if len(matches) != 1:
                    raise ValueError('No encontré un único producto de catálogo con ese nombre. Consultá la lista de precios y usá el nombre exacto. No cambié precios.')
                conn.execute('UPDATE productos SET precio_unitario=? WHERE id=?', (operation[2], matches[0]['id']))
                message = f"Precio de {matches[0]['nombre']} actualizado a ${operation[2]:,.2f}. Las ventas anteriores conservan su importe."
            elif kind in {'edit', 'cancel'}:
                row = conn.execute('SELECT * FROM ventas_dia WHERE id=?', (operation[1],)).fetchone()
                if row is None:
                    raise ValueError(f'No existe la venta #{operation[1]}. No cambié nada.')
                if kind == 'cancel':
                    conn.execute('UPDATE ventas_dia SET anulada=1 WHERE id=?', (operation[1],))
                    message = f'Venta #{operation[1]} anulada. El registro se conserva para revisión.'
                else:
                    if row['anulada']:
                        raise ValueError('Esa venta está anulada. No la modifiqué.')
                    field = {'producto': 'producto', 'precio': 'precio_unitario', 'cliente': 'cliente'}[operation[2]]
                    conn.execute(f'UPDATE ventas_dia SET {field}=? WHERE id=?', (operation[3], operation[1]))
                    message = f'Venta #{operation[1]}: {operation[2]} actualizado a {operation[3]}.'
            elif kind == 'order':
                ids, state = operation[1:]
                if not ids or len(ids) > 50:
                    raise ValueError('Indicá entre 1 y 50 números de pedido.')
                existing = {r['id'] for r in conn.execute('SELECT id FROM pedidos WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
                missing = set(ids) - existing
                if missing:
                    raise ValueError('No existen estos pedidos: ' + ', '.join(f'#{i}' for i in sorted(missing)) + '. No cambié ninguno.')
                for pid in ids:
                    conn.execute('UPDATE pedidos SET estado=?, actualizado_en=? WHERE id=?', (state, datetime.now().isoformat(timespec='seconds'), pid))
                message = 'Pedidos ' + ', '.join(f'#{i}' for i in ids) + ': ' + state.replace('_', ' ') + '. El pago no fue modificado.'
            else:
                rows = conn.execute('SELECT * FROM ventas_dia WHERE fecha=? AND COALESCE(anulada,0)=0 ORDER BY id DESC LIMIT 50', (datetime.now().strftime('%Y-%m-%d'),)).fetchall()
                message = 'Ventas de hoy (últimas 50):\n' + '\n'.join(f"#{r['id']} {r['producto']} - " + (f"${r['precio_unitario']:,.2f}" if r['precio_unitario'] is not None else 'importe pendiente') for r in rows) if rows else 'No hay ventas activas registradas hoy.'
            result = {'status': 'success', 'message': message, 'ids': ids}
            if request_id:
                conn.execute('INSERT INTO operaciones_comercio VALUES (?,?,?)', (request_id, request, json.dumps(result, ensure_ascii=False)))
            return result
    except (ValueError, sqlite3.Error) as exc:
        return {'status': 'error', 'message': str(exc)}
