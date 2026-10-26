"""Privacidade e proteções de sessão/formulários para o Hotel Flask."""
import json
import hashlib
import tempfile
import os
import re
import secrets
import time
from collections import OrderedDict
from datetime import timedelta, datetime, timezone
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from threading import RLock
from urllib.parse import urljoin, urlsplit
from flask import request, session, render_template, redirect, url_for, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash


def configurar_privacidade(ctx):
    app, db, Usuario, Auditoria = (ctx[k] for k in ('app', 'db', 'Usuario', 'Auditoria'))
    # Comunicado versionado por conta; só é concluído após trocar a senha.
    # Não é registro de consentimento nem guarda a senha antiga/nova.
    versao_aviso = 'seguranca-2026-10-v1'

    class AvisoSeguranca(db.Model):
        __tablename__ = 'hotel_aviso_seguranca'
        usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), primary_key=True)
        versao = db.Column(db.String(60), primary_key=True)
        concluido_em = db.Column(db.DateTime(timezone=True), nullable=False)

    def aviso_pendente(user_id):
        return db.session.get(AvisoSeguranca, (user_id, versao_aviso)) is None

    # Chave estável no mesmo volume do banco; nenhuma senha/chave entra no ZIP.
    configured = (os.environ.get('SECRET_KEY') or '').strip()
    if configured == 'troque-essa-chave-em-producao':
        configured = ''
    if configured:
        if len(configured) < 32:
            raise RuntimeError('SECRET_KEY deve ter pelo menos 32 caracteres aleatórios. Gere uma nova chave e reinicie.')
        app.config['SECRET_KEY'] = configured
    else:
        path = Path(ctx['DATA_DIR']) / '.hotel_session_key'
        if not path.exists():
            # Publica a chave pronta para que processos concorrentes não leiam arquivo parcial.
            fd, temp_name = tempfile.mkstemp(prefix='.hotel_key_', dir=ctx['DATA_DIR'])
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    stream.write(secrets.token_urlsafe(48))
                try:
                    os.link(temp_name, path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temp_name)
        key = path.read_text(encoding='ascii').strip()
        if len(key) < 32:
            raise RuntimeError('Chave de sessão inválida no volume. Configure SECRET_KEY e reinicie.')
        app.config['SECRET_KEY'] = key
    is_railway = bool(os.environ.get('RAILWAY_ENVIRONMENT_ID') or os.environ.get('RAILWAY_PROJECT_ID'))
    secure = os.environ.get('HOTEL_COOKIE_SECURE', '1' if is_railway else '0') == '1'
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=secure, PERMANENT_SESSION_LIFETIME=timedelta(minutes=30))
    tentativas, lock = OrderedDict(), RLock()
    janela, limite = 900, 5
    alteracoes_get = {
        'checkin': ('Confirmar check-in', 'reservas'),
        'bloquear_quarto': ('Confirmar bloqueio do quarto', 'quartos'),
        'desbloquear_quarto': ('Confirmar liberação do quarto', 'quartos'),
        'logout': ('Encerrar sessão', None),
    }

    def token():
        if '_hotel_csrf' not in session:
            session['_hotel_csrf'] = secrets.token_urlsafe(32)
        return session['_hotel_csrf']

    @app.context_processor
    def contexto_privacidade():
        return {'csrf_token': token, 'privacidade_versao': '1.0 · 22/10/2026'}

    def erro(mensagem, status=400):
        if request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify(erro=mensagem), status
        return render_template('hotel_mensagem_segura.html', titulo='Não foi possível concluir', mensagem=mensagem), status

    def chave_tentativa():
        # Não confia em X-Forwarded-For enviado pelo cliente. Sem armazenar senhas.
        usuario = (request.form.get('usuario') or '').strip().casefold()[:120]
        return (request.remote_addr or '', usuario)

    @app.before_request
    def proteger_requisicao():
        if request.endpoint == 'static':
            return None
        if session.get('usuario_id'):
            user = db.session.get(Usuario, session['usuario_id'])
            ultima = session.get('_hotel_atividade', 0)
            if not user or not user.ativo or (ultima and time.time() - ultima > 1800):
                session.clear()
                if request.endpoint not in ('login', 'politica_privacidade'):
                    flash('Sua sessão expirou ou o acesso foi desativado. Faça login novamente.', 'warning')
                    return redirect(url_for('login'))
            else:
                session['perfil'] = (user.perfil or '').strip().lower()
                session['usuario'] = user.usuario
                session['nome'] = user.nome
                session['_hotel_atividade'] = time.time()
                session.permanent = True
                # Impede reutilizar sessões após mudança de senha (inclusive outra aba).
                fingerprint = hashlib.sha256(user.senha_hash.encode()).hexdigest()[:24]
                previous = session.get('_hotel_senha_v')
                if previous and previous != fingerprint:
                    session.clear()
                    return redirect(url_for('login'))
                session['_hotel_senha_v'] = fingerprint
                pendente = aviso_pendente(user.id)
                if pendente:
                    session['_hotel_trocar_senha'] = True
                    if request.endpoint not in ('hotel_aviso_atualizacao', 'hotel_alterar_senha', 'logout', 'politica_privacidade'):
                        return redirect(url_for('hotel_aviso_atualizacao'))
                if '_hotel_trocar_senha' not in session:
                    session['_hotel_trocar_senha'] = check_password_hash(user.senha_hash, 'admin123')
                if session['_hotel_trocar_senha'] and request.endpoint not in ('hotel_aviso_atualizacao', 'hotel_alterar_senha', 'logout', 'politica_privacidade'):
                    return redirect(url_for('hotel_alterar_senha'))
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.endpoint:
            provided = request.headers.get('X-CSRFToken') or request.form.get('hotel_csrf') or request.form.get('csrf_token') or ''
            expected = session.get('_hotel_csrf', '')
            if not expected or not secrets.compare_digest(provided, expected):
                return erro('A verificação de segurança expirou. Atualize a página e tente novamente.', 400)
        if request.endpoint == 'login' and request.method == 'POST':
            key, agora = chave_tentativa(), time.monotonic()
            with lock:
                for antigo in list(tentativas):
                    if agora - tentativas[antigo][1] > janela:
                        tentativas.pop(antigo, None)
                failures = tentativas.get(key, (0, agora))[0]
                if failures >= limite:
                    resp = app.make_response(erro('Muitas tentativas. Aguarde até 15 minutos antes de tentar novamente.', 429))
                    resp.headers['Retry-After'] = '900'
                    return resp
        if request.endpoint in alteracoes_get and request.method in ('GET', 'HEAD'):
            titulo, modulo = alteracoes_get[request.endpoint]
            if 'usuario' not in session:
                return redirect(url_for('login'))
            if modulo and not ctx['usuario_tem_acesso'](modulo):
                return erro('Você não possui permissão para esta ação.', 403)
            # GET apenas exibe confirmação; somente POST executa a ação original.
            return render_template('hotel_confirmar_acao.html', titulo=titulo,
                                   destino=url_for(request.endpoint, **(request.view_args or {})))

    @app.route('/privacidade')
    def politica_privacidade():
        return render_template('politica_privacidade.html')

    @app.route('/minha-conta/atualizacao')
    def hotel_aviso_atualizacao():
        if not session.get('usuario_id'):
            return redirect(url_for('login'))
        if not aviso_pendente(session['usuario_id']):
            return redirect(url_for('dashboard'))
        return render_template('hotel_aviso_atualizacao.html')

    @app.route('/minha-conta/senha', methods=['GET', 'POST'])
    def hotel_alterar_senha():
        if not session.get('usuario_id'):
            return redirect(url_for('login'))
        user = db.session.get(Usuario, session['usuario_id'])
        if request.method == 'POST':
            atual = request.form.get('senha_atual', '')
            nova = request.form.get('nova_senha', '')
            confirmar = request.form.get('confirmar_senha', '')
            if not check_password_hash(user.senha_hash, atual):
                return render_template('hotel_senha.html', erro='Senha atual incorreta.'), 400
            if len(nova) < 12 or len(nova) > 128 or nova != confirmar or nova == atual or nova.casefold() in ('admin123admin123', '123456789012', 'password1234'):
                return render_template('hotel_senha.html', erro='Use uma senha diferente, com 12 a 128 caracteres, e confirme o mesmo valor.'), 400
            # A nova senha e a conclusão do aviso são gravadas na mesma transação.
            db.session.execute(sqlite_insert(AvisoSeguranca).values(
                usuario_id=user.id, versao=versao_aviso,
                concluido_em=datetime.now(timezone.utc)
            ).on_conflict_do_nothing(index_elements=['usuario_id', 'versao']))
            user.senha_hash = generate_password_hash(nova)
            db.session.add(Auditoria(usuario=user.usuario, acao='Alterou a própria senha', tabela='usuarios', registro=str(user.id), detalhes='Alteração pelo titular da conta; sessões anteriores serão invalidadas.', ip=request.remote_addr))
            db.session.commit()
            session.clear()
            flash('Senha alterada. Faça login novamente.', 'success')
            return redirect(url_for('login'))
        return render_template('hotel_senha.html', erro=None)

    @app.after_request
    def aplicar_protecoes(response):
        if request.endpoint == 'login' and request.method == 'POST' and response.status_code != 429:
            key, agora = chave_tentativa(), time.monotonic()
            with lock:
                if session.get('usuario_id'):
                    tentativas.pop(key, None)
                    session.permanent = True
                    session['_hotel_atividade'] = time.time()
                    user = db.session.get(Usuario, session['usuario_id'])
                    if user:
                        session['_hotel_senha_v'] = hashlib.sha256(user.senha_hash.encode()).hexdigest()[:24]
                        session['_hotel_trocar_senha'] = check_password_hash(user.senha_hash, 'admin123')
                elif response.status_code < 400:
                    count = tentativas.get(key, (0, agora))[0]
                    tentativas[key] = (count + 1, agora)
                    tentativas.move_to_end(key)
                    while len(tentativas) > 10000:
                        tentativas.popitem(last=False)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'same-origin'
        # Não força CSP que quebraria scripts inline e gráficos já existentes.
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self'; base-uri 'self'; object-src 'none'"
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store, private'
            response.headers['Pragma'] = 'no-cache'
        if secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if (response.mimetype == 'text/html' and not response.direct_passthrough
                and request.endpoint not in ('static', 'politica_privacidade')):
            html = response.get_data(as_text=True)
            csrf = escape(token(), quote=True)
            class FormAttrs(HTMLParser):
                def handle_starttag(self, tag, attrs):
                    self.attrs = dict(attrs)
            def inject_form(match):
                tag = match.group(0)
                parser = FormAttrs()
                parser.feed(tag)
                attrs = getattr(parser, 'attrs', {})
                if (attrs.get('method') or 'get').lower() not in ('post', 'put', 'patch', 'delete'):
                    return tag
                dest = urlsplit(urljoin(request.url_root, attrs.get('action') or request.path))
                if dest.netloc != request.host or dest.scheme not in ('http', 'https'):
                    return tag
                return tag + '<input type="hidden" name="hotel_csrf" value="' + csrf + '">'
            html = re.sub(r'<form\b[^>]*>', inject_form, html, flags=re.I)
            if '</head>' in html.lower():
                assets = '<meta name="hotel-csrf" content="'+csrf+'"><script src="'+escape(url_for('static', filename='js/hotel_seguranca.js'), quote=True)+'"></script>'
                html = re.sub(r'</head\s*>', lambda m: assets + m.group(0), html, count=1, flags=re.I)
            if 'id="hotel-privacy-footer"' not in html and '</body>' in html.lower():
                footer = render_template('hotel_privacidade_rodape.html')
                html = re.sub(r'</body\s*>', lambda m: footer + m.group(0), html, count=1, flags=re.I)
            response.set_data(html)
        return response
