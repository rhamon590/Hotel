"""Retenção operacional por cinco anos após o último encerramento real registrado.
Não usa a data de saída prevista para decidir eliminações.
"""
import calendar
from contextlib import closing
import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from flask import render_template, request, redirect, url_for, flash

UTC = timezone.utc

def agora():
    return datetime.now(UTC)

def data_iso(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Data sem fuso não é saída real confiável.')
    return dt.astimezone(UTC)

def vencimento(saida):
    dt = data_iso(saida)
    year = dt.year + 5
    return dt.replace(year=year, day=min(dt.day, calendar.monthrange(year, dt.month)[1]))

class Retencao:
    def __init__(self, ctx):
        self.ctx, self.app, self.path = ctx, ctx['app'], ctx['DATABASE_PATH']

    def conectar(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        return con

    def inicializar(self):
        with closing(self.conectar()) as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS retencao_controle (
                hospede_id INTEGER PRIMARY KEY,
                bloqueado INTEGER NOT NULL DEFAULT 0,
                motivo TEXT NOT NULL DEFAULT '',
                financeiro_hash TEXT,
                financeiro_motivo TEXT,
                revisado_por TEXT,
                revisado_em TEXT
            );
            CREATE TABLE IF NOT EXISTS retencao_saidas (
                reserva_id INTEGER PRIMARY KEY,
                hospede_id INTEGER NOT NULL,
                saida_real TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS retencao_legado (
                reserva_id INTEGER PRIMARY KEY,
                hospede_id INTEGER NOT NULL,
                capturado_em TEXT NOT NULL
            );
            INSERT OR IGNORE INTO retencao_legado(reserva_id,hospede_id,capturado_em)
            SELECT r.id,r.hospede_id,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM reserva r
            WHERE r.status='Finalizado' AND NOT EXISTS(SELECT 1 FROM retencao_saidas s WHERE s.reserva_id=r.id);
            CREATE TABLE IF NOT EXISTS retencao_execucoes (
                id INTEGER PRIMARY KEY,
                data TEXT NOT NULL,
                excluidos INTEGER NOT NULL,
                bloqueados INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_retencao_saidas_hospede ON retencao_saidas(hospede_id);
            CREATE TRIGGER IF NOT EXISTS hotel_retencao_fechamento
            AFTER UPDATE OF status ON reserva
            WHEN NEW.status = 'Finalizado' AND OLD.status != 'Finalizado'
            BEGIN
                INSERT INTO retencao_saidas(reserva_id,hospede_id,saida_real)
                VALUES(NEW.id,NEW.hospede_id,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(reserva_id) DO UPDATE SET hospede_id=excluded.hospede_id,saida_real=excluded.saida_real;
                UPDATE retencao_controle SET financeiro_hash=NULL WHERE hospede_id=NEW.hospede_id;
            END;
            CREATE TRIGGER IF NOT EXISTS hotel_retencao_nova_reserva
            AFTER INSERT ON reserva
            BEGIN
                UPDATE retencao_controle SET financeiro_hash=NULL WHERE hospede_id=NEW.hospede_id;
            END;
            ''')
        # Datas antigas não são inventadas nem substituídas pela saída prevista.
        if os.environ.get('HOTEL_RETENCAO_WORKER', '0') == '1':
            thread = threading.Thread(target=self._worker, daemon=True, name='hotel-retencao')
            thread.start()

    def _worker(self):
        stop = threading.Event()
        stop.wait(60)
        while True:
            try:
                self.executar(diario=True)
            except Exception:
                self.app.logger.exception('Falha na rotina de retenção; transação revertida.')
            stop.wait(3600)

    def financeiro(self, con, hospede_id):
        pagamentos = con.execute('SELECT p.* FROM pagamento p JOIN reserva r ON r.id=p.reserva_id WHERE r.hospede_id=? ORDER BY p.id', (hospede_id,)).fetchall()
        consumos = con.execute('SELECT c.* FROM consumo c JOIN reserva r ON r.id=c.reserva_id WHERE r.hospede_id=? ORDER BY c.id', (hospede_id,)).fetchall()
        blob = json.dumps([[dict(x) for x in pagamentos], [dict(x) for x in consumos]], sort_keys=True, ensure_ascii=False, default=str)
        return pagamentos, consumos, hashlib.sha256(blob.encode()).hexdigest()

    def avaliar(self, con, h, instante=None):
        instante = instante or agora()
        hid = h['id']
        rs = con.execute('SELECT * FROM reserva WHERE hospede_id=? ORDER BY id', (hid,)).fetchall()
        ctl = con.execute('SELECT * FROM retencao_controle WHERE hospede_id=?', (hid,)).fetchone()
        resultado = dict(id=hid, nome=h['nome'], ultima_saida=None, vence=None, elegivel=False, motivo='', financeiro=False)
        if ctl and ctl['bloqueado']:
            resultado['motivo'] = 'Conservação bloqueada pelo administrador: ' + ctl['motivo']
            return resultado
        if any(r['status'] not in ('Finalizado', 'Cancelado') for r in rs):
            resultado['motivo'] = 'Reserva/hospedagem ativa ou situação não encerrada.'
            return resultado
        encerradas = [r for r in rs if r['status'] == 'Finalizado']
        if not encerradas:
            resultado['motivo'] = 'Sem check-out real registrado. Não há início de prazo confirmado.'
            return resultado
        saidas = con.execute('SELECT * FROM retencao_saidas WHERE hospede_id=?', (hid,)).fetchall()
        mapa = {s['reserva_id']: s['saida_real'] for s in saidas}
        try:
            confirmadas = [data_iso(mapa[r['id']]) for r in encerradas if r['id'] in mapa]
        except (ValueError, TypeError):
            resultado['motivo'] = 'Saída real inválida; exige conferência.'
            return resultado
        if not confirmadas:
            resultado['motivo'] = 'Histórico antigo sem data real de encerramento; exige conferência ou novo check-out real.'
            return resultado
        try:
            ultima = max(confirmadas)
            # Um encerramento real posterior à captura do legado prova que o retorno
            # é mais recente, sem inventar a data das hospedagens antigas.
            legado = {x['reserva_id']: data_iso(x['capturado_em']) for x in con.execute('SELECT * FROM retencao_legado WHERE hospede_id=?', (hid,))}
            if any(r['id'] not in mapa and (r['id'] not in legado or legado[r['id']] > ultima) for r in encerradas):
                resultado['motivo'] = 'Histórico antigo sem data real confiável; exige conferência.'
                return resultado
            limite = vencimento(ultima.isoformat())
            resultado.update(ultima_saida=ultima, vence=limite)
        except (ValueError, TypeError, OverflowError):
            resultado['motivo'] = 'Saída real inválida; exige conferência.'
            return resultado
        if ultima > instante:
            resultado['motivo'] = 'Saída real futura/inconsistente; exige conferência.'
            return resultado
        if limite > instante:
            resultado['motivo'] = 'Dentro dos cinco anos após o último check-out real.'
            return resultado
        # Entrega de cartão em uso ainda vinculada ao hóspede impede eliminação.
        pendente = con.execute('''SELECT 1 FROM movimentacao_cartao m JOIN cartao c ON c.id=m.cartao_id
            WHERE c.status='Em uso' AND m.tipo='Entrega' AND m.hospede_id=?
            AND m.id=(SELECT n.id FROM movimentacao_cartao n WHERE n.cartao_id=m.cartao_id ORDER BY n.data_evento DESC,n.id DESC LIMIT 1)
            LIMIT 1''', (hid,)).fetchone()
        if pendente:
            resultado['motivo'] = 'Cartão ainda em uso.'
            return resultado
        p, c, fingerprint = self.financeiro(con, hid)
        resultado['financeiro'] = bool(p or c)
        if any(x['status'] != 'Pago' for x in p) or any(not x['pago'] for x in c):
            resultado['motivo'] = 'Pagamento/consumo pendente de regularização.'
            return resultado
        if (p or c) and (not ctl or ctl['financeiro_hash'] != fingerprint):
            resultado['motivo'] = 'Registros financeiros preservados: falta confirmar liberação de guarda.'
            return resultado
        # FKs de módulos não conhecidos impedem apagar um registro com novos vínculos.
        allowed = {'reserva', 'pagamento', 'consumo', 'movimentacao_cartao'}
        for table in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            name = table['name']
            if name in allowed or name.startswith('sqlite_'):
                continue
            safe = name.replace('"', '""')
            for fk in con.execute('PRAGMA foreign_key_list("' + safe + '")'):
                if fk['table'] in ('hospede', 'reserva'):
                    resultado['motivo'] = 'Há módulo adicional com vínculo; eliminação requer adaptação.'
                    return resultado
        resultado.update(elegivel=True, motivo='Elegível para eliminação automática.')
        return resultado

    def listar(self):
        with closing(self.conectar()) as con:
            return [self.avaliar(con, h) for h in con.execute('SELECT id,nome FROM hospede ORDER BY nome')]

    def _apagar(self, con, hid):
        ids = [x[0] for x in con.execute('SELECT id FROM reserva WHERE hospede_id=?', (hid,))]
        cartoes = [x[0] for x in con.execute('SELECT DISTINCT cartao_id FROM movimentacao_cartao WHERE hospede_id=? OR reserva_id IN (SELECT id FROM reserva WHERE hospede_id=?)', (hid, hid))]
        consumos = [x[0] for x in con.execute('SELECT id FROM consumo WHERE reserva_id IN (SELECT id FROM reserva WHERE hospede_id=?)', (hid,))]
        # Os detalhes antigos podem conter nome/CPF. Mantém metadados da ação e do funcionário,
        # removendo os detalhes dos registros operacionais associados à eliminação.
        vinculos = [('hospedes', [hid]), ('reservas', ids), ('consumos', consumos), ('cartoes', cartoes)]
        for tabela, registros in vinculos:
            for registro in registros:
                con.execute("UPDATE auditoria SET detalhes='Conteúdo operacional removido pela política de retenção.', registro='' WHERE tabela=? AND registro=?", (tabela, str(registro)))
        # Identificadores podem aparecer também em observações livres de auditorias antigas.
        h = con.execute('SELECT nome,cpf,rg,passaporte FROM hospede WHERE id=?', (hid,)).fetchone()
        for valor in h:
            if valor and len(str(valor).strip()) >= 4:
                con.execute("UPDATE auditoria SET detalhes='Conteúdo operacional removido pela política de retenção.' WHERE instr(lower(coalesce(detalhes,'')),lower(?))>0", (str(valor),))
        con.execute('DELETE FROM movimentacao_cartao WHERE hospede_id=? OR reserva_id IN (SELECT id FROM reserva WHERE hospede_id=?)', (hid, hid))
        for tabela in ('pagamento', 'consumo'):
            con.execute('DELETE FROM ' + tabela + ' WHERE reserva_id IN (SELECT id FROM reserva WHERE hospede_id=?)', (hid,))
        con.execute('DELETE FROM retencao_saidas WHERE hospede_id=?', (hid,))
        con.execute('DELETE FROM retencao_legado WHERE hospede_id=?', (hid,))
        con.execute('DELETE FROM reserva WHERE hospede_id=?', (hid,))
        con.execute('DELETE FROM retencao_controle WHERE hospede_id=?', (hid,))
        con.execute('DELETE FROM hospede WHERE id=?', (hid,))

    def executar(self, diario=False, simular=False, instante=None):
        instante = instante or agora()
        con = self.conectar()
        try:
            con.execute('BEGIN IMMEDIATE')
            if diario:
                ultima = con.execute('SELECT data FROM retencao_execucoes ORDER BY id DESC LIMIT 1').fetchone()
                if ultima and data_iso(ultima['data']).date() >= instante.date():
                    con.rollback()
                    return {'excluidos': 0, 'bloqueados': 0, 'ja_executado': True}
            linhas = con.execute('SELECT id,nome FROM hospede ORDER BY id').fetchall()
            excluidos = bloqueados = 0
            for h in linhas:
                status = self.avaliar(con, h, instante)
                if status['elegivel']:
                    if not simular:
                        self._apagar(con, h['id'])
                    excluidos += 1
                else:
                    bloqueados += 1
            if simular:
                con.rollback()
            else:
                con.execute('INSERT INTO retencao_execucoes(data,excluidos,bloqueados) VALUES(?,?,?)', (instante.isoformat(), excluidos, bloqueados))
                con.commit()
            return {'excluidos': excluidos, 'bloqueados': bloqueados, 'ja_executado': False}
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def registrar_retencao(ctx):
    app, db = ctx['app'], ctx['db']
    service = Retencao(ctx)
    from flask import session
    proteger = ctx['permissao_necessaria']('usuarios')

    @app.route('/privacidade/retencao', methods=['GET', 'POST'])
    @proteger
    def hotel_retencao():
        if request.method == 'POST':
            acao = request.form.get('acao')
            hid = request.form.get('hospede_id', type=int)
            motivo = (request.form.get('motivo') or '').strip()[:500]
            if acao in ('bloquear', 'desbloquear', 'financeiro'):
                if not hid or not motivo:
                    flash('Selecione o cadastro e informe a justificativa, sem repetir dados pessoais.', 'warning')
                    return redirect(url_for('hotel_retencao'))
                with closing(service.conectar()) as con:
                    con.execute('BEGIN IMMEDIATE')
                    if not con.execute('SELECT 1 FROM hospede WHERE id=?', (hid,)).fetchone():
                        flash('Cadastro não encontrado.', 'warning')
                        return redirect(url_for('hotel_retencao'))
                    con.execute('INSERT OR IGNORE INTO retencao_controle(hospede_id) VALUES(?)', (hid,))
                    if acao == 'financeiro':
                        if request.form.get('declaracao') != 'confirmado':
                            flash('Confirme que a obrigação de guarda financeira foi verificada.', 'warning')
                            return redirect(url_for('hotel_retencao'))
                        fingerprint = service.financeiro(con, hid)[2]
                        con.execute('UPDATE retencao_controle SET financeiro_hash=?,financeiro_motivo=?,revisado_por=?,revisado_em=? WHERE hospede_id=?', (fingerprint, motivo, session.get('usuario'), agora().isoformat(), hid))
                    else:
                        con.execute('UPDATE retencao_controle SET bloqueado=?,motivo=? WHERE hospede_id=?', (int(acao == 'bloquear'), motivo, hid))
                    con.commit()
                flash('Regra de conservação atualizada.', 'success')
            elif acao == 'simular':
                result = service.executar(simular=True)
                flash(f'Simulação: {result["excluidos"]} cadastro(s) elegível(is); nenhum dado foi excluído.', 'success')
            return redirect(url_for('hotel_retencao'))
        rows = service.listar()
        return render_template('hotel_retencao.html', linhas=rows, automatico=os.environ.get('HOTEL_RETENCAO_WORKER', '0') == '1')

    return service
