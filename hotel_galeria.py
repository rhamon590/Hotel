"""Galeria de acomodações: envio em lote, revisão por imagem e publicação controlada."""
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import OperationalError

LIMITE_FOTOS = 10
LIMITE_BYTES = 5 * 1024 * 1024


def registrar_galeria(ctx, ControleLegado):
    app, db, Usuario, Quarto, Auditoria = (ctx[n] for n in ('app', 'db', 'Usuario', 'Quarto', 'Auditoria'))
    portal = app.extensions['hotel_portal']
    Oferta, Config = portal['Oferta'], portal['Config']
    pasta = Path(ctx['UPLOAD_DIR']) / 'portal'

    class Foto(db.Model):
        __tablename__ = 'hotel_galeria_foto'
        # Sem vínculos para hóspedes/reservas: a galeria contém só conteúdo dos quartos.
        arquivo = db.Column(db.String(80), primary_key=True)
        quarto_id = db.Column(db.Integer, nullable=False, index=True)
        criada_em = db.Column(db.DateTime, nullable=False)
        origem = db.Column(db.String(500))
        alternativo = db.Column(db.String(200))
        responsavel = db.Column(db.String(80))
        autorizada_em = db.Column(db.DateTime)

    app.extensions['hotel_galeria'] = {'Foto': Foto}

    def agora():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def administrador():
        u = db.session.get(Usuario, session.get('usuario_id')) if session.get('usuario_id') else None
        return u if u and u.ativo and (u.perfil or '').strip().lower() == 'administrador' else None

    def admin(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get('usuario_id'):
                return redirect(url_for('login'))
            if not administrador():
                abort(403)
            return fn(*args, **kwargs)
        return wrapper

    def transacao():
        db.session.rollback()
        db.session.execute(db.text('BEGIN IMMEDIATE'))
        u = administrador()
        if not u:
            abort(403)
        return u

    def auditar(acao, qid, detalhes):
        db.session.add(Auditoria(usuario=session.get('usuario', 'sistema'), acao=acao,
            tabela='galeria', registro=str(qid), detalhes=detalhes, ip=request.remote_addr))

    def listar(qid):
        if not db.session.get(Quarto, qid):
            return []
        itens = []
        o = db.session.get(Oferta, qid)
        if o and o.foto and re.fullmatch(r'[a-f0-9]{32}\.jpg', o.foto):
            controle = db.session.get(ControleLegado, qid)
            valida = bool(controle and controle.arquivo == o.foto)
            itens.append(dict(arquivo=o.foto, token=o.foto[:-4], quarto_id=qid, legado=True,
                autorizada=valida, origem=controle.origem if valida else '',
                alternativo=controle.alternativo if valida else '',
                responsavel=controle.responsavel if valida else '',
                data=controle.data if valida else None))
        for f in Foto.query.filter_by(quarto_id=qid).order_by(Foto.criada_em, Foto.arquivo).all():
            itens.append(dict(arquivo=f.arquivo, token=f.arquivo[:-4], quarto_id=qid, legado=False,
                autorizada=f.autorizada_em is not None, origem=f.origem or '',
                alternativo=f.alternativo or '', responsavel=f.responsavel or '', data=f.autorizada_em))
        return itens

    def visiveis(qid, publico=True):
        itens = listar(qid)
        return [f for f in itens if f['autorizada']] if publico or not administrador() else itens

    def capa(qid):
        itens = visiveis(qid)
        return itens[0] if itens else None

    def enviar(f):
        if not f or not re.fullmatch(r'[a-f0-9]{32}\.jpg', f['arquivo']):
            abort(404)
        cfg = db.session.get(Config, 1)
        if not administrador() and (not f['autorizada'] or not cfg or not cfg.ativo):
            abort(404)
        if not (pasta / f['arquivo']).is_file():
            abort(404)
        resposta = send_file(pasta / f['arquivo'], mimetype='image/jpeg', conditional=False)
        # A revogação deve valer também após uma visualização anterior.
        resposta.headers['Cache-Control'] = 'private, no-store, max-age=0'
        resposta.headers['X-Content-Type-Options'] = 'nosniff'
        return resposta

    @app.route('/hospedagem/galeria/<int:quarto_id>/<token>')
    def hotel_galeria_foto(quarto_id, token):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            abort(404)
        return enviar(next((f for f in listar(quarto_id) if f['token'] == token), None))

    def enviar_capa(qid):
        itens = listar(qid) if administrador() else visiveis(qid)
        return enviar(itens[0] if itens else None)

    def processar_imagem(arquivo):
        raw = arquivo.read(LIMITE_BYTES + 1)
        if not raw or len(raw) > LIMITE_BYTES:
            raise ValueError('Cada foto deve ter até 5 MB. Nenhuma foto deste envio foi salva.')
        try:
            with Image.open(BytesIO(raw)) as original:
                if original.format not in ('JPEG', 'PNG', 'WEBP') or original.width * original.height > 16000000:
                    raise ValueError
                img = ImageOps.exif_transpose(original).convert('RGB')
                img.thumbnail((1600, 1200))
                # Recria pixels, sem EXIF, GPS ou outros metadados da origem.
                limpa = Image.new('RGB', img.size)
                limpa.paste(img)
                saida = BytesIO()
                limpa.save(saida, format='JPEG', quality=85)
                return saida.getvalue()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise ValueError('Uma imagem é inválida. Envie JPG, PNG ou WebP com até 16 megapixels. Nenhuma foto deste envio foi salva.') from None

    def adicionar(qid, arquivos):
        # Chamado dentro da mesma transação da apresentação do quarto.
        if not administrador():
            abort(403)
        arquivos = [f for f in arquivos if f and f.filename]
        if len(listar(qid)) + len(arquivos) > LIMITE_FOTOS:
            raise ValueError('O quarto pode ter até 10 fotos, contando as já cadastradas. Remova alguma ou selecione menos arquivos.')
        # Valida todas antes de criar o primeiro arquivo no volume.
        dados = [processar_imagem(f) for f in arquivos]
        novos = []
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            for pixels in dados:
                nome = secrets.token_hex(16) + '.jpg'
                novos.append(nome)
                with (pasta / nome).open('xb') as destino:
                    destino.write(pixels)
                db.session.add(Foto(arquivo=nome, quarto_id=qid, criada_em=agora()))
            if novos:
                auditar('Galeria: enviou fotos', qid, f'{len(novos)} foto(s), pendentes de revisão.')
            return novos
        except Exception:
            limpar(novos)
            raise

    def limpar(nomes):
        for nome in nomes:
            try:
                (pasta / nome).unlink(missing_ok=True)
            except OSError:
                app.logger.warning('Galeria: não foi possível limpar arquivo de envio interrompido.')

    @app.context_processor
    def contexto():
        return dict(galeria_fotos=visiveis, galeria_capa=capa, galeria_limite=LIMITE_FOTOS,
            galeria_quartos=lambda: Quarto.query.order_by(Quarto.numero).all())

    @app.route('/administracao/galeria/revisar', methods=['POST'])
    @admin
    def hotel_galeria_revisar():
        try:
            u = transacao()
            tokens = request.form.getlist('fotos')
            if not tokens or len(tokens) > 100 or len(set(tokens)) != len(tokens):
                raise ValueError('Selecione entre 1 e 100 fotos, sem repetir imagens.')
            # A seleção contém os arquivos vistos, nunca somente números de quartos.
            if any(not re.fullmatch(r'[a-f0-9]{32}', t) for t in tokens):
                raise ValueError('Seleção inválida. Atualize a página e confira as fotos.')
            escolhidas = {}
            for q in Quarto.query.all():
                for f in listar(q.id):
                    if f['token'] in tokens:
                        escolhidas[f['token']] = f
            if set(escolhidas) != set(tokens):
                raise ValueError('Uma foto foi removida ou substituída. Nenhuma autorização foi alterada; atualize a página.')
            acao = request.form.get('acao')
            if acao == 'aprovar':
                origem = (request.form.get('origem') or '').strip()
                if not 5 <= len(origem) <= 500 or request.form.get('direitos') != '1':
                    raise ValueError('Informe a origem comum e confirme os direitos para TODAS as fotos selecionadas.')
                descricoes = {t: (request.form.get('alt_' + t) or '').strip() for t in tokens}
                if any(not 8 <= len(a) <= 200 for a in descricoes.values()):
                    raise ValueError('Revise a descrição de cada foto selecionada: entre 8 e 200 caracteres. Nenhuma foto foi aprovada.')
                if any(not (pasta / f['arquivo']).is_file() for f in escolhidas.values()):
                    raise ValueError('Um arquivo não foi encontrado no volume. Nenhuma foto foi aprovada.')
            elif acao != 'ocultar':
                raise ValueError('Ação inválida.')
            for token, f in escolhidas.items():
                if f['legado']:
                    if acao == 'aprovar':
                        db.session.merge(ControleLegado(quarto_id=f['quarto_id'], arquivo=f['arquivo'],
                            origem=origem, alternativo=descricoes[token], responsavel=u.usuario, data=agora()))
                    else:
                        ControleLegado.query.filter_by(quarto_id=f['quarto_id'], arquivo=f['arquivo']).delete()
                else:
                    item = db.session.get(Foto, f['arquivo'])
                    if acao == 'aprovar':
                        item.origem, item.alternativo = origem, descricoes[token]
                        item.responsavel, item.autorizada_em = u.usuario, agora()
                    else:
                        item.autorizada_em = None
                auditar('Galeria: aprovou foto' if acao == 'aprovar' else 'Galeria: ocultou foto',
                    f['quarto_id'], f['arquivo'] + (' | ' + origem if acao == 'aprovar' else ''))
            db.session.commit()
            flash(f'{len(tokens)} foto(s) ' + ('aprovada(s) para publicação.' if acao == 'aprovar' else 'oculta(s) do público.'), 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except OperationalError:
            db.session.rollback()
            flash('Outra operação está atualizando as fotos. Aguarde e tente novamente.', 'warning')
        return redirect(url_for('hotel_transparencia_admin', _anchor='fotos'))

    @app.route('/administracao/galeria/remover', methods=['POST'])
    @admin
    def hotel_galeria_remover():
        try:
            transacao()
            qid = request.form.get('quarto_id', type=int)
            token = request.form.get('foto', '')
            f = next((x for x in listar(qid) if x['token'] == token), None) if qid else None
            if not f:
                raise ValueError('A foto não foi encontrada. Atualize a página.')
            if f['legado']:
                db.session.get(Oferta, qid).foto = None
                ControleLegado.query.filter_by(quarto_id=qid).delete()
            else:
                db.session.delete(db.session.get(Foto, f['arquivo']))
            auditar('Galeria: removeu foto', qid, f['arquivo'])
            db.session.commit()
            limpar([f['arquivo']])
            flash('Foto removida da galeria.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        return redirect(url_for('hotel_transparencia_admin', _anchor='fotos'))

    class Servico:
        def inicializar(self):
            # As fotos antigas continuam nos campos existentes; não são copiadas ou reautorizadas.
            pass

    app.extensions['hotel_galeria_servico'] = dict(adicionar=adicionar, limpar=limpar,
        capa=capa, enviar_capa=enviar_capa, listar=listar)
    return Servico()
