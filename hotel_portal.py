"""Portal de hospedagem externa para o Hotel PRO (SQLite).

Somente tabelas adicionais; chaves para o legado são verificadas pela aplicação.
Isso evita impedir a rotina existente de retenção de dados por novas FKs.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import (abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError


ATIVOS = ('Reservado', 'Hospedado')
PENDENTES = ('Nova', 'Em atendimento')
RECEPCAO = ('administrador', 'rh', 'porteiro')
BLOQUEIOS = ('Bloqueado', 'Em limpeza', 'Em manutenção', 'Em manutenÃ§Ã£o')
ESTADOS_OFERTAVEIS = ('Livre', 'Limpo', 'Reservado')


def agora():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)


def data_local(value):
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is not None:
            raise ValueError
        return dt
    except (ValueError, TypeError):
        raise ValueError('Informe um período válido, com horário local.')


def moeda(value):
    try:
        v = Decimal(str(value).replace(',', '.'))
        if not v.is_finite() or v < 0 or v > Decimal('9999999.99'):
            raise ValueError
        return v.quantize(Decimal('.01'))
    except (InvalidOperation, ValueError):
        raise ValueError('Informe uma diária válida e não negativa.')


def endpoint_push_valido(endpoint):
    try:
        u = urlsplit(endpoint)
        host = (u.hostname or '').lower()
        dominios = ('fcm.googleapis.com', 'push.services.mozilla.com',
                    'push.apple.com', 'notify.windows.com')
        return (u.scheme == 'https' and not u.username and not u.password
                and u.port in (None, 443) and not u.fragment and len(endpoint) <= 4096
                and any(host == d or host.endswith('.' + d) for d in dominios))
    except ValueError:
        return False


def registrar_portal(ctx):
    app, db = ctx['app'], ctx['db']
    Quarto, Reserva, Hospede, Usuario, Auditoria = (ctx[k] for k in
        ('Quarto', 'Reserva', 'Hospede', 'Usuario', 'Auditoria'))

    class Config(db.Model):
        __tablename__ = 'portal_config'
        id = db.Column(db.Integer, primary_key=True)
        ativo = db.Column(db.Boolean, default=False, nullable=False)
        titulo = db.Column(db.String(100), default='Portal de Hospedagem')
        descricao = db.Column(db.String(1000), default='Conheça as acomodações e solicite sua hospedagem.')
        endereco = db.Column(db.String(300), default='')
        telefone = db.Column(db.String(30), default='')

    class Oferta(db.Model):
        __tablename__ = 'portal_oferta'
        quarto_id = db.Column(db.Integer, primary_key=True)
        ativa = db.Column(db.Boolean, default=False, nullable=False)
        titulo = db.Column(db.String(100), nullable=False)
        descricao = db.Column(db.String(2000), default='')
        amenidades = db.Column(db.String(600), default='')
        diaria = db.Column(db.Numeric(12, 2), nullable=False)
        inicio = db.Column(db.Date, nullable=False)
        fim = db.Column(db.Date, nullable=False)
        foto = db.Column(db.String(80))

    class Solicitacao(db.Model):
        __tablename__ = 'portal_solicitacao'
        id = db.Column(db.Integer, primary_key=True)
        token_hash = db.Column(db.String(64), unique=True, nullable=False)
        envio_hash = db.Column(db.String(64), unique=True, nullable=False)
        quarto_id = db.Column(db.Integer, nullable=False, index=True)
        titulo = db.Column(db.String(100), nullable=False)
        entrada = db.Column(db.DateTime, nullable=False)
        saida = db.Column(db.DateTime, nullable=False)
        pessoas = db.Column(db.Integer, nullable=False)
        nome = db.Column(db.String(160), nullable=False)
        telefone = db.Column(db.String(30), nullable=False)
        observacoes = db.Column(db.String(1000), default='')
        diaria = db.Column(db.Numeric(12, 2), nullable=False)
        status = db.Column(db.String(30), default='Nova', nullable=False, index=True)
        responsavel_id = db.Column(db.Integer)
        responsavel = db.Column(db.String(120))
        reserva_id = db.Column(db.Integer, unique=True, index=True)
        nota_interna = db.Column(db.String(2000), default='')
        criado_em = db.Column(db.DateTime, default=agora, nullable=False)
        atualizado_em = db.Column(db.DateTime, default=agora, nullable=False)
        versao = db.Column(db.Integer, default=1, nullable=False)

    class Limite(db.Model):
        __tablename__ = 'portal_limite'
        chave = db.Column(db.String(64), primary_key=True)
        janela = db.Column(db.Integer, nullable=False)
        quantidade = db.Column(db.Integer, default=0, nullable=False)

    class Assinatura(db.Model):
        __tablename__ = 'portal_push_assinatura'
        id = db.Column(db.Integer, primary_key=True)
        usuario_id = db.Column(db.Integer, nullable=False, index=True)
        endpoint_hash = db.Column(db.String(64), unique=True, nullable=False)
        dados = db.Column(db.Text, nullable=False)
        atualizada_em = db.Column(db.DateTime, default=agora, nullable=False)

    class Entrega(db.Model):
        __tablename__ = 'portal_push_entrega'
        id = db.Column(db.Integer, primary_key=True)
        solicitacao_id = db.Column(db.Integer, nullable=False)
        assinatura_id = db.Column(db.Integer, nullable=False)
        estado = db.Column(db.String(15), default='Pendente', nullable=False)
        tentativas = db.Column(db.Integer, default=0, nullable=False)
        proxima = db.Column(db.DateTime, default=agora, nullable=False)
        lease_ate = db.Column(db.DateTime)
        resultado = db.Column(db.String(40))
        __table_args__ = (db.UniqueConstraint('solicitacao_id', 'assinatura_id'),)

    models = dict(Config=Config, Oferta=Oferta, Solicitacao=Solicitacao,
                  Limite=Limite, Assinatura=Assinatura, Entrega=Entrega)
    app.extensions['hotel_portal'] = models
    fotos = Path(ctx['UPLOAD_DIR']) / 'portal'
    chave_vapid = Path(ctx['DATA_DIR']) / '.hotel_portal_vapid.pem'
    stop = threading.Event()

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def autorizado():
        return bool(session.get('usuario_id') and
                    (session.get('perfil') or '').strip().lower() in RECEPCAO)

    def proteger(admin=False):
        def decorador(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not session.get('usuario_id'):
                    if request.is_json or 'application/json' in request.headers.get('Accept', ''):
                        return jsonify(erro='Faça login novamente.'), 401
                    return redirect(url_for('login'))
                if not autorizado() or (admin and session.get('perfil') != 'administrador'):
                    abort(403)
                return fn(*args, **kwargs)
            return wrapped
        return decorador

    def falha(message, status=400):
        if request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify(erro=message), status
        return render_template('portal_mensagem.html', mensagem=message), status

    def auditar(acao, identificador='', detalhes=''):
        db.session.add(Auditoria(usuario=session.get('usuario', 'portal'), acao=acao,
            tabela='portal', registro=str(identificador), detalhes=detalhes,
            ip=request.remote_addr))

    def transacao():
        # SQLite: serializa confirmação externa e mutações internas de inventário.
        # Antes desta chamada há somente leituras do middleware de autenticação.
        if not getattr(g, 'portal_transacao', False):
            db.session.rollback()
            db.session.execute(db.text('BEGIN IMMEDIATE'))
            g.portal_transacao = True

    def configuracao():
        cfg = db.session.get(Config, 1)
        if not cfg:
            raise RuntimeError('Portal ainda não inicializado.')
        return cfg

    def sobrepoe(r, entrada, saida):
        try:
            a, b = data_local(r.data_entrada), data_local(r.data_saida)
            if b <= a:
                return True
            return a < saida and b > entrada
        except ValueError:
            return True  # Cadastro inválido não é ofertado ao público.

    def conflitos(quarto_id, entrada, saida, ignorar=None):
        rs = Reserva.query.filter(Reserva.quarto_id == quarto_id,
            Reserva.status.in_(ATIVOS)).all()
        return [r for r in rs if r.id != ignorar and
                (r.status == 'Hospedado' or sobrepoe(r, entrada, saida))]

    def apresentacao(q):
        # A oferta guarda somente conteúdo público. Nenhuma autorização ou
        # janela manual é necessária: o inventário vem do cadastro e reservas.
        # Não copia descrições internas, nomes ou observações para o portal.
        o = db.session.get(Oferta, q.id) if q else None
        if o or not q:
            return o
        try:
            diaria = moeda(q.valor_diaria or 0)
        except ValueError:
            diaria = Decimal('0.00')
        return SimpleNamespace(titulo='Quarto '+str(q.numero), descricao='',
            amenidades='', diaria=diaria, foto=None)

    def disponivel(q, oferta, entrada, saida, pessoas=1, ignorar=None, liberar=True):
        return bool(q and (not liberar or configuracao().ativo)
            and q.status in ESTADOS_OFERTAVEIS and (q.capacidade or 0) >= pessoas >= 1
            and not conflitos(q.id, entrada, saida, ignorar))

    def periodo(args):
        a = date.fromisoformat(args.get('entrada') or str(agora().date()))
        b = date.fromisoformat(args.get('saida') or str(a + timedelta(days=1)))
        pessoas = int(args.get('pessoas') or 1)
        if a < agora().date() or b <= a or (b-a).days > 90 or a > agora().date()+timedelta(days=730) or not 1 <= pessoas <= 30:
            raise ValueError('Escolha um período futuro de até 90 noites e informe a quantidade de hóspedes.')
        return datetime.combine(a, datetime.min.time()).replace(hour=14), datetime.combine(b, datetime.min.time()).replace(hour=12), pessoas

    def formatar(valor):
        return f'{Decimal(str(valor or 0)):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    app.jinja_env.filters['portal_moeda'] = formatar

    def itens_publicos(entrada, saida, pessoas):
        itens = []
        for q in Quarto.query.order_by(Quarto.numero).all():
            oferta = apresentacao(q)
            if disponivel(q, oferta, entrada, saida, pessoas):
                itens.append(dict(id=q.id, titulo=oferta.titulo, descricao=oferta.descricao,
                    amenidades=oferta.amenidades, capacidade=q.capacidade, diaria=oferta.diaria,
                    foto=bool(oferta.foto), categoria=q.categoria))
        return itens

    @app.route('/hospedagem')
    def portal_publico():
        erro = None
        try:
            entrada, saida, pessoas = periodo(request.args)
        except (ValueError, TypeError):
            entrada, saida, pessoas = periodo({})
            erro = 'Confira as datas e a quantidade de hóspedes da pesquisa.'
        cfg = configuracao()
        itens = itens_publicos(entrada, saida, pessoas) if cfg.ativo and not erro else []
        return render_template('portal_publico.html', cfg=cfg, itens=itens, entrada=entrada,
            saida=saida, pessoas=pessoas, erro=erro)

    @app.route('/hospedagem/quarto/<int:quarto_id>', methods=['GET', 'POST'])
    def portal_quarto(quarto_id):
        try:
            entrada, saida, pessoas = periodo(request.values)
        except (ValueError, TypeError):
            return falha('Confira as datas e a quantidade de hóspedes.')
        erro = None
        if request.method == 'POST':
            try:
                transacao()
                nonce = request.form.get('envio', '')
                tokens = session.get('_portal_envios', {})
                if not re.fullmatch(r'[A-Za-z0-9_-]{40,60}', nonce) or nonce not in tokens or time.time()-tokens[nonce] > 1800:
                    raise ValueError('O formulário expirou. Reabra o quarto e envie novamente.')
                envio_hash = digest(nonce)
                anterior = Solicitacao.query.filter_by(envio_hash=envio_hash).first()
                if anterior:
                    # O mesmo formulário é idempotente; o token público não é salvo no banco.
                    db.session.rollback()
                    return redirect(url_for('portal_comprovante', token=nonce))
                nome = ' '.join((request.form.get('nome') or '').split())
                telefone = re.sub(r'\D', '', request.form.get('telefone') or '')
                obs = (request.form.get('observacoes') or '').strip()
                if request.form.get('website') or not 2 <= len(nome) <= 160 or not 10 <= len(telefone) <= 15 or len(obs) > 1000:
                    raise ValueError('Informe seu nome e um telefone válido com DDD. A observação pode ter até 1.000 caracteres.')
                q = db.session.get(Quarto, quarto_id)
                oferta = apresentacao(q)
                if not disponivel(q, oferta, entrada, saida, pessoas):
                    raise ValueError('Este quarto não está disponível para o período escolhido. Consulte outras datas ou acomodações.')
                janela = int(time.time()//3600)
                Limite.query.filter(Limite.janela < janela-48).delete()
                session.setdefault('_portal_cliente',secrets.token_urlsafe(24))
                # NAT/proxy podem ser compartilhados: limite menor por sessão e
                # maior por endereço, sem confiar em X-Forwarded-For arbitrário.
                for origem,maximo in [('sessao:'+session['_portal_cliente'],5),
                    ('ip:'+(request.remote_addr or 'local'),100)]:
                    chave=digest(str(app.secret_key)+origem)
                    limite=db.session.get(Limite,chave)
                    if limite and limite.janela==janela and limite.quantidade>=maximo:
                        db.session.rollback()
                        return falha('Muitas solicitações neste período. Tente novamente mais tarde.',429)
                    if not limite:
                        limite=Limite(chave=chave,janela=janela,quantidade=0)
                        db.session.add(limite)
                    if limite.janela!=janela:
                        limite.janela,limite.quantidade=janela,0
                    limite.quantidade+=1
                s = Solicitacao(token_hash=digest(nonce), envio_hash=envio_hash, quarto_id=q.id,
                    titulo=oferta.titulo, entrada=entrada, saida=saida, pessoas=pessoas, nome=nome,
                    telefone=telefone, observacoes=obs, diaria=oferta.diaria, status='Nova')
                db.session.add(s)
                db.session.flush()
                enfileirar(s.id)
                db.session.commit()
                return redirect(url_for('portal_comprovante', token=nonce))
            except ValueError as exc:
                db.session.rollback()
                erro = str(exc)
            except OperationalError:
                db.session.rollback()
                return falha('A recepção está atualizando a disponibilidade. Aguarde alguns instantes e tente novamente.', 503)
        q = db.session.get(Quarto, quarto_id)
        oferta = apresentacao(q)
        if not disponivel(q, oferta, entrada, saida, pessoas):
            return falha(erro or 'Acomodação indisponível para este período.', 409)
        nonce = secrets.token_urlsafe(32)
        envios = {k: v for k, v in session.get('_portal_envios', {}).items() if time.time()-v < 1800}
        envios = dict(list(envios.items())[-4:])
        envios[nonce] = time.time()
        session['_portal_envios'] = envios
        return render_template('portal_quarto.html', cfg=configuracao(), oferta=oferta, quarto=q,
            entrada=entrada, saida=saida, pessoas=pessoas, noites=(saida.date()-entrada.date()).days,
            envio=nonce, erro=erro), (400 if erro else 200)

    @app.route('/hospedagem/solicitacao/<token>')
    def portal_comprovante(token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,60}', token):
            abort(404)
        s = Solicitacao.query.filter_by(token_hash=digest(token)).first_or_404()
        return render_template('portal_comprovante.html', cfg=configuracao(), s=s)

    @app.route('/hospedagem/foto/<int:quarto_id>')
    def portal_foto(quarto_id):
        if 'hotel_galeria_servico' in app.extensions:
            return app.extensions['hotel_galeria_servico']['enviar_capa'](quarto_id)
        o = db.session.get(Oferta, quarto_id)
        if not o or not o.foto or (not autorizado() and not configuracao().ativo):
            abort(404)
        if not re.fullmatch(r'[a-f0-9]{32}\.jpg', o.foto):
            abort(404)
        return send_file(fotos/o.foto, mimetype='image/jpeg', conditional=True, max_age=0)

    @app.route('/recepcao/solicitacoes')
    @proteger()
    def portal_recepcao():
        status = request.args.get('status', '')
        query = Solicitacao.query
        if status:
            query = query.filter_by(status=status)
        pagina = query.order_by(Solicitacao.criado_em.desc(), Solicitacao.id.desc()).paginate(
            page=max(request.args.get('page', 1, type=int) or 1, 1), per_page=30, error_out=False)
        counts = dict(db.session.query(Solicitacao.status, db.func.count()).group_by(Solicitacao.status).all())
        return render_template('portal_recepcao.html', pagina=pagina, status=status, counts=counts)

    @app.route('/recepcao/solicitacoes/<int:id>', methods=['GET', 'POST'])
    @proteger()
    def portal_atender(id):
        if request.method == 'POST':
            try:
                transacao()
                s = db.session.get(Solicitacao, id)
                if not s:
                    abort(404)
                if request.form.get('versao', type=int) != s.versao:
                    raise ValueError('A solicitação foi atualizada em outra tela. Recarregue antes de continuar.')
                acao = request.form.get('acao')
                if acao == 'assumir':
                    if s.status not in PENDENTES or (s.responsavel_id and s.responsavel_id != session['usuario_id']):
                        raise ValueError('Esta solicitação já está em atendimento ou foi encerrada.')
                    s.status = 'Em atendimento'
                    s.responsavel_id, s.responsavel = session['usuario_id'], session.get('nome')
                elif acao=='transferir':
                    if session.get('perfil')!='administrador' or s.status not in PENDENTES:
                        abort(403)
                    s.status='Em atendimento'
                    s.responsavel_id,s.responsavel=session['usuario_id'],session.get('nome')
                elif acao in ('confirmar', 'recusar'):
                    if s.status not in PENDENTES:
                        raise ValueError('Esta solicitação já foi encerrada.')
                    if s.responsavel_id != session['usuario_id']:
                        raise ValueError('Assuma o atendimento antes de concluir esta solicitação.')
                    if acao == 'confirmar':
                        q = db.session.get(Quarto, s.quarto_id)
                        o = apresentacao(q)
                        if s.entrada.date() < agora().date() or not disponivel(q, o, s.entrada, s.saida, s.pessoas):
                            raise ValueError('O quarto deixou de estar disponível ou o portal foi suspenso. A reserva não foi confirmada.')
                        diaria_confirmada = s.diaria
                        if diaria_confirmada <= 0:
                            diaria_confirmada = moeda(request.form.get('diaria_confirmada', ''))
                            if diaria_confirmada <= 0:
                                raise ValueError('Informe a diária combinada com o cliente antes de confirmar.')
                        hid = request.form.get('hospede_id', type=int)
                        h = db.session.get(Hospede, hid) if hid else None
                        if hid and not h:
                            raise ValueError('O cadastro selecionado não foi encontrado.')
                        if h and Reserva.query.filter(Reserva.hospede_id==h.id,Reserva.status.in_(ATIVOS)).first():
                            raise ValueError('O hóspede selecionado já possui uma reserva ativa. Confira o vínculo antes de confirmar.')
                        if not h:
                            h = Hospede(nome=s.nome, telefone=s.telefone, whatsapp=s.telefone,
                                cpf=None, empresa='Hospedagem particular')
                            db.session.add(h)
                            db.session.flush()
                        r = Reserva(hospede_id=h.id, quarto_id=q.id,
                            data_entrada=s.entrada.isoformat(timespec='minutes'),
                            data_saida=s.saida.isoformat(timespec='minutes'), status='Reservado',
                            valor_diaria=float(diaria_confirmada), desconto=0,
                            observacoes=f'Hospedagem externa · Solicitação #{s.id} · Uso exclusivo do quarto · {s.pessoas} pessoa(s).')
                        db.session.add(r)
                        db.session.flush()
                        s.reserva_id, s.status, s.diaria = r.id, 'Confirmada', diaria_confirmada
                        ctx['atualizar_status_quarto'](q)
                    else:
                        s.status = 'Recusada'
                elif acao == 'nota':
                    nota = (request.form.get('nota') or '').strip()
                    if len(nota) > 2000:
                        raise ValueError('A nota pode ter até 2.000 caracteres.')
                    s.nota_interna = nota
                elif acao == 'cancelar':
                    if s.status not in PENDENTES:
                        raise ValueError('Para desfazer uma reserva confirmada, use o cancelamento administrativo abaixo.')
                    if s.responsavel_id not in (None, session['usuario_id']) and session.get('perfil') != 'administrador':
                        raise ValueError('A solicitação está sob responsabilidade de outro atendente.')
                    s.status = 'Cancelada'
                elif acao == 'cancelar_confirmada':
                    if session.get('perfil') != 'administrador' or s.status != 'Confirmada':
                        abort(403)
                    r = db.session.get(Reserva, s.reserva_id)
                    if not r or r.status != 'Reservado':
                        raise ValueError('Somente reservas ainda não hospedadas podem ser canceladas por esta tela.')
                    if ctx['cartao_pendente_reserva'](r.id) or ctx['Pagamento'].query.filter_by(reserva_id=r.id).count() or ctx['Consumo'].query.filter_by(reserva_id=r.id).count():
                        raise ValueError('Regularize cartões, pagamentos e consumos antes do cancelamento.')
                    r.status, s.status = 'Cancelado', 'Cancelada'
                    db.session.flush()
                    ctx['atualizar_status_quarto'](r.quarto)
                elif acao=='anonimizar':
                    if session.get('perfil')!='administrador' or s.status not in ('Recusada','Cancelada') or s.reserva_id:
                        raise ValueError('A anonimização desta tela é exclusiva para solicitações encerradas sem reserva vinculada.')
                    s.nome='Contato removido pela administração'
                    s.telefone=s.observacoes=s.nota_interna=''
                    s.token_hash=secrets.token_hex(32)
                else:
                    raise ValueError('Ação inválida.')
                s.versao += 1
                s.atualizado_em = agora()
                auditar('Portal: '+acao, s.id)
                db.session.commit()
                flash('Solicitação atualizada.', 'success')
                return redirect(url_for('portal_atender', id=id))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return redirect(url_for('portal_atender', id=id))
            except OperationalError:
                db.session.rollback()
                return falha('Outra operação está em andamento. Atualize e tente novamente.', 503)
        s = db.session.get(Solicitacao, id)
        if not s:
            abort(404)
        r = db.session.get(Reserva, s.reserva_id) if s.reserva_id else None
        # Retornos são vinculados somente após conferência humana; nome/telefone
        # não são identificadores suficientes para juntar cadastros automaticamente.
        candidatos = Hospede.query.filter(db.or_(Hospede.nome.ilike(s.nome),
            Hospede.telefone==s.telefone,Hospede.whatsapp==s.telefone)).limit(20).all()
        return render_template('portal_atender.html', s=s, reserva=r, candidatos=candidatos,
            proprio=s.responsavel_id == session['usuario_id'], telefone=s.telefone)

    def imagem(arquivo):
        from PIL import Image, ImageOps
        raw = arquivo.read(5*1024*1024+1)
        if len(raw) > 5*1024*1024:
            raise ValueError('A foto deve ter até 5 MB.')
        try:
            with Image.open(BytesIO(raw)) as original:
                if original.format not in ('JPEG', 'PNG', 'WEBP') or original.width*original.height > 16000000:
                    raise ValueError
                img = ImageOps.exif_transpose(original).convert('RGB')
                img.thumbnail((1600, 1200))
                fotos.mkdir(parents=True, exist_ok=True)
                nome = secrets.token_hex(16)+'.jpg'
                # Regrava os pixels, sem metadados do arquivo original.
                img.save(fotos/nome, format='JPEG', quality=85)
                return nome
        except Exception:
            raise ValueError('Envie uma foto JPG, PNG ou WebP válida, com até 16 megapixels.')

    @app.route('/administracao/hospedagem-externa', methods=['GET', 'POST'])
    @proteger(admin=True)
    def portal_configurar():
        if request.method == 'POST':
            foto_nova = None
            fotos_novas = []
            try:
                transacao()
                cfg = configuracao()
                acao = request.form.get('acao')
                if acao == 'configuracao':
                    cfg.titulo = (request.form.get('titulo') or '').strip()[:100] or 'Portal de Hospedagem'
                    cfg.descricao = (request.form.get('descricao') or '').strip()[:1000]
                    cfg.endereco = (request.form.get('endereco') or '').strip()[:300]
                    cfg.telefone = (request.form.get('telefone') or '').strip()[:30]
                    cfg.ativo = request.form.get('ativo') == '1'
                elif acao == 'oferta':
                    q = db.session.get(Quarto, request.form.get('quarto_id', type=int))
                    if not q:
                        raise ValueError('Quarto não encontrado.')
                    diaria = moeda(request.form.get('diaria', ''))
                    titulo = (request.form.get('titulo') or '').strip()
                    if not 2 <= len(titulo) <= 100:
                        raise ValueError('Informe um título público entre 2 e 100 caracteres.')
                    o = db.session.get(Oferta, q.id)
                    if not o:
                        o = Oferta(quarto_id=q.id)
                        db.session.add(o)
                    o.titulo, o.diaria = titulo, diaria
                    # Mantém as colunas antigas sem alterar o esquema do banco.
                    # Estes campos não participam mais da disponibilidade.
                    o.inicio, o.fim, o.ativa = date(2000, 1, 1), date(9999, 12, 31), True
                    o.descricao = (request.form.get('descricao') or '').strip()[:2000]
                    o.amenidades = (request.form.get('amenidades') or '').strip()[:600]
                    galeria = app.extensions.get('hotel_galeria_servico')
                    if galeria:
                        fotos_novas = galeria['adicionar'](q.id, request.files.getlist('fotos'))
                    arquivo = request.files.get('foto')
                    if arquivo and arquivo.filename:
                        if galeria and not o.foto and len(galeria['listar'](q.id)) >= 10:
                            raise ValueError('O quarto já possui 10 fotos.')
                        foto_nova = imagem(arquivo)
                        foto_antiga, o.foto = o.foto, foto_nova
                        # Arquivos antigos são removidos somente após confirmar a gravação.
                        g.portal_foto_antiga = foto_antiga
                else:
                    raise ValueError('Ação inválida.')
                auditar('Portal: configurou '+acao)
                db.session.commit()
                antiga=getattr(g,'portal_foto_antiga',None)
                if antiga and re.fullmatch(r'[a-f0-9]{32}\.jpg',antiga):
                    try:
                        (fotos/antiga).unlink(missing_ok=True)
                    except OSError:
                        app.logger.warning('Portal: uma miniatura antiga não pôde ser removida.')
                flash('Configuração salva.', 'success')
                return redirect(url_for('portal_configurar'))
            except (ValueError, InvalidOperation) as exc:
                db.session.rollback()
                if fotos_novas:
                    app.extensions['hotel_galeria_servico']['limpar'](fotos_novas)
                if foto_nova:
                    (fotos/foto_nova).unlink(missing_ok=True)
                flash(str(exc) or 'Confira as datas e os valores.', 'danger')
                return redirect(url_for('portal_configurar'))
            except Exception:
                db.session.rollback()
                if fotos_novas:
                    app.extensions['hotel_galeria_servico']['limpar'](fotos_novas)
                if foto_nova:
                    (fotos/foto_nova).unlink(missing_ok=True)
                raise
        try:
            entrada, saida, pessoas = periodo(request.args)
        except (ValueError, TypeError):
            entrada, saida, pessoas = periodo({})
        qs = Quarto.query.order_by(Quarto.numero).all()
        counts = dict(total=len(qs), colaboradores=0, externas=0, bloqueados=0, livres=0, ofertados=0)
        linhas = []
        for q in qs:
            o = apresentacao(q)
            rs = conflitos(q.id, entrada, saida)
            externa = bool(rs and Solicitacao.query.filter(Solicitacao.reserva_id.in_([r.id for r in rs])).first())
            if rs:
                situacao = 'Reserva externa' if externa else 'Uso corporativo'; counts['externas' if externa else 'colaboradores'] += 1
            elif q.status not in ESTADOS_OFERTAVEIS or (q.capacidade or 0) < 1:
                situacao = 'Indisponível: '+str(q.status or 'status não informado'); counts['bloqueados'] += 1
            else:
                situacao = 'Sem reserva'; counts['livres'] += 1
            publico = disponivel(q, o, entrada, saida, pessoas)
            counts['ofertados'] += int(publico)
            linhas.append(dict(quarto=q, oferta=o, situacao=situacao, publico=publico))
        return render_template('portal_configurar.html', cfg=configuracao(), linhas=linhas,
            counts=counts, entrada=entrada, saida=saida, pessoas=pessoas,
            public_url=url_for('portal_publico',_external=True,
                _scheme='https' if ctx.get('EM_RAILWAY') else request.scheme))

    @app.route('/recepcao/solicitacoes/alertas')
    @proteger()
    def portal_alertas():
        pendentes = Solicitacao.query.filter(Solicitacao.status.in_(PENDENTES))
        latest = db.session.query(db.func.max(Solicitacao.id)).scalar() or 0
        return jsonify(pendentes=pendentes.count(), novas=Solicitacao.query.filter_by(status='Nova').count(),
            ultima=latest, usuario_id=session['usuario_id'])

    def push_pronto():
        try:
            import pywebpush  # noqa: F401
            return chave_vapid.is_file()
        except ImportError:
            return False

    def chave_publica():
        from cryptography.hazmat.primitives import serialization
        with chave_vapid.open('rb') as stream:
            private = serialization.load_pem_private_key(stream.read(), password=None)
        raw = private.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    @app.route('/recepcao/notificacoes/configuracao')
    @proteger()
    def portal_push_config():
        pronto = push_pronto()
        return jsonify(pronto=pronto, chave=chave_publica() if pronto else None)

    @app.route('/recepcao/notificacoes/assinar', methods=['POST'])
    @proteger()
    def portal_push_assinar():
        if not push_pronto():
            return falha('Instale as dependências do portal e reinicie o servidor antes de ativar as notificações.', 503)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return falha('Assinatura de notificações inválida.')
        endpoint = data.get('endpoint', '')
        keys = data.get('keys') or {}
        if not isinstance(keys,dict) or not all(isinstance(keys.get(k),str) and len(keys[k])<150 for k in ('p256dh','auth')):
            return falha('Assinatura de notificações inválida.')
        try:
            p = base64.urlsafe_b64decode(keys.get('p256dh', '')+'===' )
            a = base64.urlsafe_b64decode(keys.get('auth', '')+'===')
            from cryptography.hazmat.primitives.asymmetric import ec
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),p)
        except Exception:
            p, a = b'', b''
        if not isinstance(endpoint, str) or not endpoint_push_valido(endpoint) or len(p)!=65 or p[:1]!=b'\x04' or len(a)!=16:
            return falha('Assinatura de notificações inválida.')
        transacao()
        assinatura = Assinatura.query.filter_by(endpoint_hash=digest(endpoint)).first()
        if Assinatura.query.filter_by(usuario_id=session['usuario_id']).count() >= 10 and not (assinatura and assinatura.usuario_id==session['usuario_id']):
            db.session.rollback()
            return falha('Limite de dez aparelhos por usuário atingido.')
        if not assinatura:
            assinatura = Assinatura(endpoint_hash=digest(endpoint))
            db.session.add(assinatura)
        assinatura.usuario_id = session['usuario_id']
        assinatura.dados = json.dumps(dict(endpoint=endpoint, keys={k:keys[k] for k in ('p256dh','auth')}))
        assinatura.atualizada_em = agora()
        session['_portal_push_hash']=digest(endpoint)
        db.session.commit()
        return jsonify(ok=True)

    @app.route('/recepcao/notificacoes/remover', methods=['POST'])
    @proteger()
    def portal_push_remover():
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):
            return falha('Assinatura inválida.')
        endpoint = data.get('endpoint', '')
        if not isinstance(endpoint, str):
            return falha('Assinatura inválida.')
        Assinatura.query.filter_by(usuario_id=session['usuario_id'], endpoint_hash=digest(endpoint)).delete()
        db.session.commit()
        return jsonify(ok=True)

    @app.route('/hotel-portal-sw.js')
    def portal_service_worker():
        response = send_file(Path(ctx['BASE_DIR'])/'static/js/hotel_portal_sw.js', mimetype='application/javascript', max_age=0)
        response.headers['Service-Worker-Allowed'] = '/recepcao/'
        return response

    @app.route('/hotel-portal.webmanifest')
    def portal_manifest():
        response=jsonify(id='/recepcao/solicitacoes', name='Hotel · Recepção', short_name='Recepção',
            start_url=url_for('portal_recepcao'), scope='/', display='standalone',
            background_color='#f1f5f9', theme_color='#13243e',
            icons=[dict(src=url_for('static', filename=f'img/portal-{n}.png'), sizes=f'{n}x{n}', type='image/png') for n in (192,512)])
        response.mimetype='application/manifest+json'
        return response

    def enfileirar(solicitacao_id):
        for sub in Assinatura.query.all():
            user = db.session.get(Usuario, sub.usuario_id)
            if user and user.ativo and (user.perfil or '').lower() in RECEPCAO:
                db.session.add(Entrega(solicitacao_id=solicitacao_id, assinatura_id=sub.id,
                    estado='Pendente', tentativas=0, proxima=agora()))

    def processar_alertas(limite=10):
        if not push_pronto():
            return 0
        from pywebpush import webpush, WebPushException
        processadas = 0
        for _ in range(limite):
            db.session.rollback()
            try:
                db.session.execute(db.text('BEGIN IMMEDIATE'))
                item = Entrega.query.filter(Entrega.proxima <= agora(), or_(
                    Entrega.estado=='Pendente', (Entrega.estado=='Enviando') & (Entrega.lease_ate < agora()))).order_by(Entrega.id).first()
                if not item:
                    db.session.rollback()
                    break
                sub = db.session.get(Assinatura, item.assinatura_id)
                s = db.session.get(Solicitacao, item.solicitacao_id)
                user = db.session.get(Usuario, sub.usuario_id) if sub else None
                if not sub or not s or s.status not in PENDENTES or not user or not user.ativo or (user.perfil or '').lower() not in RECEPCAO:
                    item.estado = 'Cancelada'
                    db.session.commit()
                    continue
                item.estado, item.lease_ate = 'Enviando', agora()+timedelta(seconds=60)
                item.tentativas += 1
                eid, sid, rid, dados = item.id, sub.id, s.id, json.loads(sub.dados)
                db.session.commit()
            except OperationalError:
                db.session.rollback()
                break
            status = None
            try:
                webpush(subscription_info=dados, data=json.dumps(dict(
                    title='Nova solicitação de hospedagem',
                    body='Há uma solicitação aguardando atendimento. Toque para abrir a recepção.',
                    url=f'/recepcao/solicitacoes/{rid}', tag=f'hotel-solicitacao-{rid}')),
                    vapid_private_key=str(chave_vapid), vapid_claims={'sub':os.environ.get('HOTEL_PUSH_CONTATO', 'mailto:rhamon.borges@orcaindustrial.com.br')},
                    ttl=3600, timeout=5)
            except WebPushException as exc:
                status = getattr(exc.response, 'status_code', 0) if exc.response is not None else 0
            except Exception:
                status = 0
            db.session.rollback()
            item = db.session.get(Entrega, eid)
            if not item:
                continue
            item.lease_ate = None
            if status is None:
                item.estado, item.resultado = 'Enviada', 'Aceita pelo serviço push'
            elif status in (404,410):
                item.estado, item.resultado = 'Cancelada', 'Assinatura expirada'
                Assinatura.query.filter_by(id=sid).delete()
            elif item.tentativas >= 5:
                item.estado, item.resultado = 'Falhou', 'Limite de tentativas'
            else:
                item.estado, item.resultado = 'Pendente', f'Falha HTTP {status}'
                item.proxima = agora()+timedelta(seconds=min(300, 15*2**item.tentativas))
            db.session.commit()
            processadas += 1
        return processadas

    def loop_alertas():
        while not stop.wait(10):
            try:
                with app.app_context():
                    processar_alertas()
            except Exception:
                app.logger.warning('Portal: fila push pendente; nova tentativa no próximo ciclo.')

    @app.cli.command('portal-enviar-alertas')
    def enviar_cli():
        print(f'{processar_alertas(100)} entrega(s) processada(s).')

    # Mantém contagem de pessoas, mas reserva toda a capacidade para uso externo.
    def lotacao(quarto):
        capacidade=max(int(quarto.capacidade or 0),0)
        result=dict(capacidade=capacidade,reservados=0,ocupados=0,comprometidas=0,vagas=capacidade)
        externas={s.reserva_id:s for s in Solicitacao.query.filter_by(quarto_id=quarto.id).filter(Solicitacao.reserva_id.isnot(None)).all()}
        for r in Reserva.query.filter(Reserva.quarto_id==quarto.id,Reserva.status.in_(ATIVOS)).all():
            try:
                atual=r.status=='Hospedado' or data_local(r.data_entrada)<=agora()<data_local(r.data_saida)
            except ValueError:
                atual=True
            if atual:
                s=externas.get(r.id)
                result['ocupados' if r.status=='Hospedado' else 'reservados']+=s.pessoas if s else 1
                result['comprometidas']+=capacidade if s else 1
                if s:
                    result['uso_externo']=True
        result['vagas']=max(capacidade-result['comprometidas'],0)
        return result
    ctx['lotacao_quarto'] = lotacao

    def vagas_no_periodo(quarto,entrada,saida,ignorar_id=None):
        inicio,fim=data_local(entrada),data_local(saida)
        if fim<=inicio:
            raise ValueError('A saída deve ser posterior à entrada.')
        capacidade=max(int(quarto.capacidade or 0),0)
        ids={s.reserva_id for s in Solicitacao.query.filter_by(quarto_id=quarto.id).filter(Solicitacao.reserva_id.isnot(None)).all()}
        eventos=[]
        for r in Reserva.query.filter(Reserva.quarto_id==quarto.id,Reserva.status.in_(ATIVOS)).all():
            if r.id==ignorar_id:
                continue
            try:
                a,b=data_local(r.data_entrada),data_local(r.data_saida)
                if b<=a:
                    return 0
            except ValueError:
                return 0
            if r.id in ids and r.status=='Hospedado':
                return 0
            if a<fim and b>inicio:
                peso=capacidade if r.id in ids else 1
                eventos.extend([(max(a,inicio),peso),(min(b,fim),-peso)])
        uso=pico=0
        for _,delta in sorted(eventos):
            uso+=delta;pico=max(pico,uso)
        return max(capacidade-pico,0)
    ctx['vagas_no_periodo']=vagas_no_periodo

    # Todos os caminhos internos que podem concorrer pela mesma acomodação.
    mutacoes = {'reservas','editar_reserva','prorrogar_reserva','checkin','checkout',
        'bloquear_quarto','desbloquear_quarto','quarto_status','limpeza','manutencao',
        'entregar_cartao','devolver_cartao','importar','excluir_todos_quartos'}

    def conflito_externo(qid, entrada, saida, ignorar=None):
        for s in Solicitacao.query.filter(Solicitacao.quarto_id==qid,
                                         Solicitacao.reserva_id.isnot(None)).all():
            r = db.session.get(Reserva, s.reserva_id)
            if r and r.id != ignorar and r.status in ATIVOS and (r.status=='Hospedado' or sobrepoe(r,entrada,saida)):
                return True
        return False

    @app.before_request
    def proteger_inventario():
        if request.endpoint=='logout' and request.method=='POST' and session.get('_portal_push_hash'):
            Assinatura.query.filter_by(usuario_id=session.get('usuario_id'),endpoint_hash=session['_portal_push_hash']).delete()
            db.session.commit()
        if request.method!='POST' or request.endpoint not in mutacoes or not session.get('usuario_id'):
            return None
        try:
            transacao()
            if request.endpoint=='excluir_todos_quartos' and session.get('perfil')=='administrador':
                # A exclusão total do legado permite reutilizar IDs no SQLite.
                # Desfaz os vínculos explícitos para não associar solicitações
                # históricas a futuras reservas/quartos com o mesmo número de ID.
                for s in Solicitacao.query.all():
                    if s.status in PENDENTES or s.status=='Confirmada':
                        s.status='Cancelada'
                        s.versao+=1
                    s.reserva_id=None
                if 'hotel_galeria' in app.extensions:
                    app.extensions['hotel_galeria']['Foto'].query.delete()
                Oferta.query.delete()
                configuracao().ativo=False
            elif request.endpoint in ('reservas','editar_reserva','prorrogar_reserva'):
                id_reserva = (request.view_args or {}).get('id')
                r = db.session.get(Reserva, id_reserva) if id_reserva else None
                qid = request.form.get('quarto_id', type=int) or (r.quarto_id if r else None)
                entrada = data_local(request.form.get('data_entrada') or (r.data_entrada if r else ''))
                saida = data_local(request.form.get('data_saida') or (r.data_saida if r else ''))
                propria_externa = Solicitacao.query.filter_by(reserva_id=r.id).first() if r else None
                if propria_externa:
                    if qid != r.quarto_id or (request.form.get('hospede_id',type=int) not in (None,r.hospede_id)):
                        return falha('Hospedagem externa: a troca de quarto ou titular requer cancelar a reserva ainda não hospedada e confirmar uma nova solicitação.',409)
                    if conflitos(qid, entrada, saida, r.id):
                        return falha('O quarto está reservado para outra hospedagem nesse período. A ocupação externa exige uso exclusivo.',409)
                elif conflito_externo(qid, entrada, saida, r.id if r else None):
                    return falha('Este quarto possui hospedagem externa exclusiva no período. Escolha outra acomodação.',409)
            elif request.endpoint=='checkin':
                r = db.session.get(Reserva,(request.view_args or {}).get('id'))
                if r:
                    if Solicitacao.query.filter_by(reserva_id=r.id).first():
                        conflito = conflitos(r.quarto_id,data_local(r.data_entrada),data_local(r.data_saida),r.id)
                    else:
                        conflito = conflito_externo(r.quarto_id,data_local(r.data_entrada),data_local(r.data_saida),r.id)
                    if conflito:
                        return falha('O quarto possui uma hospedagem incompatível com este check-in. Confira as reservas antes de continuar.',409)
            elif request.endpoint=='importar' and (request.view_args or {}).get('modelo')=='reservas':
                file = request.files.get('arquivo')
                if file:
                    frame = ctx['pd'].read_excel(file)
                    file.seek(0)
                    for _, row in frame.iterrows():
                        if str(row.get('status','Reservado')) in ATIVOS and conflito_externo(int(row['quarto_id']),data_local(row['data_entrada']),data_local(row['data_saida'])):
                            return falha('A importação contém reserva incompatível com uma hospedagem externa exclusiva. Nenhuma linha foi importada.',409)
        except (ValueError,TypeError,KeyError):
            db.session.rollback()
            return falha('Confira o quarto e as datas antes de continuar.',400)
        except OperationalError:
            db.session.rollback()
            return falha('Outra operação está atualizando as reservas. Tente novamente em alguns instantes.',503)

    def limitar_corpo():
        if request.endpoint=='portal_quarto' and request.method=='POST':
            request.max_content_length=65536
        if request.endpoint=='portal_configurar' and request.method=='POST':
            request.max_content_length=51*1024*1024
        if request.endpoint in ('portal_push_assinar','portal_push_remover'):
            request.max_content_length=8192
    app.before_request_funcs.setdefault(None,[]).insert(0,limitar_corpo)

    @app.after_request
    def adicionar_alertas(response):
        if response.mimetype=='text/html' and not response.direct_passthrough and autorizado() and request.endpoint not in ('login','hotel_alterar_senha','hotel_aviso_atualizacao','politica_privacidade') and not (request.endpoint or '').startswith('portal_public') and request.endpoint not in ('portal_quarto','portal_comprovante','portal_foto'):
            html = response.get_data(as_text=True)
            if '</body>' in html and 'id="portal-bell"' not in html:
                html=html.replace('</head>', '<link rel="manifest" href="'+url_for('portal_manifest')+'"><meta name="theme-color" content="#13243e"><meta name="apple-mobile-web-app-capable" content="yes"></head>',1)
                html = html.replace('</body>',render_template('portal_alertas_widget.html')+'</body>')
                response.set_data(html)
        if request.endpoint=='portal_comprovante':
            response.headers['Referrer-Policy']='no-referrer'
        return response

    # Flask executa after_request em ordem inversa: aplica o portal por último.
    finais=app.after_request_funcs[None]
    finais.remove(adicionar_alertas)
    finais.insert(0,adicionar_alertas)

    # Anonimiza a cópia de contato na mesma transação da retenção do legado.
    retencao = ctx.get('servico_retencao')
    if retencao:
        apagar_original = retencao._apagar
        def apagar_com_portal(con, hid):
            ids = [x[0] for x in con.execute('SELECT id FROM reserva WHERE hospede_id=?',(hid,))]
            for rid in ids:
                con.execute("UPDATE portal_solicitacao SET nome='Dados eliminados pela retenção',telefone='',observacoes='',nota_interna='',token_hash=?,reserva_id=NULL WHERE reserva_id=?",(secrets.token_hex(32),rid))
            apagar_original(con,hid)
        retencao._apagar=apagar_com_portal

    class Servico:
        @contextmanager
        def inicializacao_exclusiva(self):
            # Serializa create_all e seed entre workers Gunicorn, sem excluir
            # arquivos ou depender de um processo específico permanecer vivo.
            with (Path(ctx['DATA_DIR'])/'.hotel_portal_init.lock').open('a+b') as lockfile:
                if os.name=='nt':
                    import msvcrt
                    lockfile.seek(0,2)
                    if lockfile.tell()==0:
                        lockfile.write(b'0');lockfile.flush()
                    lockfile.seek(0);msvcrt.locking(lockfile.fileno(),msvcrt.LK_LOCK,1)
                else:
                    import fcntl
                    fcntl.flock(lockfile.fileno(),fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name=='nt':
                        lockfile.seek(0);msvcrt.locking(lockfile.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        fcntl.flock(lockfile.fileno(),fcntl.LOCK_UN)
        def inicializar(self):
            # Executada depois de db.create_all(), sem alterar tabelas do legado.
            from sqlalchemy.dialects.sqlite import insert
            db.session.execute(insert(Config).values(id=1,ativo=False,titulo='Portal de Hospedagem',
                descricao='Conheça as acomodações e solicite sua hospedagem.',endereco='',telefone='').on_conflict_do_nothing(index_elements=['id']))
            db.session.commit()
            if not chave_vapid.exists():
                try:
                    from cryptography.hazmat.primitives import serialization
                    from cryptography.hazmat.primitives.asymmetric import ec
                    from tempfile import NamedTemporaryFile
                    private=ec.generate_private_key(ec.SECP256R1())
                    data=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
                    with NamedTemporaryFile(dir=chave_vapid.parent,delete=False) as stream:
                        stream.write(data);temporario=stream.name
                    try:
                        os.link(temporario,chave_vapid)
                    except FileExistsError:
                        pass
                    finally:
                        os.unlink(temporario)
                except ImportError:
                    app.logger.warning('Portal: instale requirements_portal.txt para habilitar notificações push.')
            if push_pronto() and os.environ.get('HOTEL_PORTAL_PUSH_WORKER','1')=='1':
                thread=threading.Thread(target=loop_alertas,name='hotel-portal-push',daemon=True)
                thread.start()
        def parar(self):
            stop.set()
    models.update(disponivel=disponivel,processar_alertas=processar_alertas,conflitos=conflitos)
    return Servico()
