"""Configurações pessoais e administrativas integradas ao Hotel PRO.
Novas tabelas, sem alterar o esquema das reservas, usuários ou retenção.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy.dialects.sqlite import insert
from werkzeug.security import check_password_hash


def agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def registrar_configuracoes(ctx):
    app, db, Usuario, Auditoria = (ctx[k] for k in ('app', 'db', 'Usuario', 'Auditoria'))

    class Identidade(db.Model):
        __tablename__ = 'hotel_config_identidade'
        id = db.Column(db.Integer, primary_key=True)
        nome = db.Column(db.String(100), nullable=False, default='Hotel PRO')
        subtitulo = db.Column(db.String(140), default='Gestão de hospedagens')
        telefone = db.Column(db.String(30), default='')
        email = db.Column(db.String(120), default='')
        endereco = db.Column(db.String(300), default='')
        logo = db.Column(db.String(64))
        tema = db.Column(db.String(12), nullable=False, default='light')

    class Perfil(db.Model):
        __tablename__ = 'hotel_config_perfil'
        usuario_id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(120), default='')
        telefone = db.Column(db.String(30), default='')
        foto = db.Column(db.String(64))
        tema = db.Column(db.String(12), nullable=False, default='padrao')
        compacto = db.Column(db.Boolean, nullable=False, default=False)
        movimento = db.Column(db.Boolean, nullable=False, default=True)
        popups = db.Column(db.Boolean, nullable=False, default=True)
        revisao = db.Column(db.Integer, nullable=False, default=1)
        trocar_senha = db.Column(db.Boolean, nullable=False, default=False)

    class Acesso(db.Model):
        __tablename__ = 'hotel_config_acesso'
        chave = db.Column(db.String(64), primary_key=True)
        usuario_id = db.Column(db.Integer, nullable=False, index=True)
        revisao = db.Column(db.Integer, nullable=False)
        dispositivo = db.Column(db.String(100), nullable=False)
        ip = db.Column(db.String(80), default='')
        criado_em = db.Column(db.DateTime, nullable=False, default=agora)
        ultimo_em = db.Column(db.DateTime, nullable=False, default=agora)
        encerrado_em = db.Column(db.DateTime)
        motivo = db.Column(db.String(50))

    pasta = Path(ctx['UPLOAD_DIR']) / 'configuracoes'
    models = dict(Identidade=Identidade, Perfil=Perfil, Acesso=Acesso)
    app.extensions['hotel_configuracoes'] = models
    app.config['HC_ENDPOINTS'] = list(app.view_functions)
    app.jinja_env.filters['hc_horario'] = lambda dt: dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y às %H:%M')
    app.jinja_env.filters['hc_expirado'] = lambda acesso: acesso.ultimo_em < agora()-timedelta(minutes=30)

    def perfil(uid):
        p = db.session.get(Perfil, uid)
        if p is None:
            db.session.execute(insert(Perfil).values(usuario_id=uid, tema='padrao',
                compacto=False, movimento=True, popups=True, revisao=1, trocar_senha=False)
                .on_conflict_do_nothing(index_elements=['usuario_id']))
            db.session.commit()
            p = db.session.get(Perfil, uid)
        return p

    def identidade():
        return db.session.get(Identidade, 1)

    def auditar(acao, uid='', detalhes=''):
        db.session.add(Auditoria(usuario=session.get('usuario', 'sistema'), acao=acao,
            tabela='configuracoes', registro=str(uid), detalhes=detalhes, ip=request.remote_addr))

    def transacao():
        db.session.rollback()
        db.session.execute(db.text('BEGIN IMMEDIATE'))

    def proteger(admin=False):
        def decorator(fn):
            @wraps(fn)
            def view(*args, **kwargs):
                u = db.session.get(Usuario, session.get('usuario_id')) if session.get('usuario_id') else None
                if not u or not u.ativo:
                    return redirect(url_for('login'))
                if admin and (u.perfil or '').strip().lower() != 'administrador':
                    abort(403)
                return fn(*args, **kwargs)
            return view
        return decorator

    def senha_administrador():
        u = db.session.get(Usuario, session['usuario_id'])
        if not u or not u.ativo or (u.perfil or '').strip().lower() != 'administrador':
            abort(403)
        if not check_password_hash(u.senha_hash, request.form.get('senha_atual', '')):
            raise ValueError('Confirme sua senha atual para alterar o acesso de outro usuário.')

    def texto(name, minimo=0, maximo=120):
        value = (request.form.get(name) or '').strip()
        if len(value) < minimo or len(value) > maximo:
            raise ValueError(f'Confira o campo {name.replace("_", " ")}: use de {minimo} a {maximo} caracteres.')
        return value

    def contato():
        email, telefone = texto('email', maximo=120), texto('telefone', maximo=30)
        if email and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email):
            raise ValueError('Informe um e-mail válido.')
        if telefone and not 8 <= len(re.sub(r'\D', '', telefone)) <= 15:
            raise ValueError('Informe um telefone válido, incluindo o DDD.')
        return email, telefone

    def guardar_imagem(arquivo, logo=False):
        from PIL import Image, ImageOps, UnidentifiedImageError
        raw = arquivo.read(4*1024*1024+1)
        if len(raw) > 4*1024*1024:
            raise ValueError('A imagem deve ter até 4 MB.')
        try:
            with Image.open(BytesIO(raw)) as original:
                if original.format not in ('JPEG', 'PNG', 'WEBP') or original.width*original.height > 16000000:
                    raise ValueError
                im = ImageOps.exif_transpose(original)
                if logo:
                    im = im.convert('RGBA'); im.thumbnail((800, 400))
                else:
                    im = ImageOps.fit(im.convert('RGB'), (384, 384))
                im.info.clear()
                pasta.mkdir(parents=True, exist_ok=True)
                nome = ('logo_' if logo else 'foto_')+secrets.token_hex(20)+('.png' if logo else '.jpg')
                # Regrava pixels sem metadados ou conteúdo ativo do arquivo enviado.
                im.save(pasta/nome, format='PNG' if logo else 'JPEG', **({} if logo else {'quality':88}))
                return nome
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ValueError('Envie JPG, PNG ou WebP válido, com até 4 MB e 16 megapixels.')

    def remover_imagem(nome):
        if nome and re.fullmatch(r'(foto|logo)_[a-f0-9]{40}\.(jpg|png)', nome):
            try:
                (pasta/nome).unlink(missing_ok=True)
            except OSError:
                app.logger.warning('Configurações: não foi possível remover uma imagem antiga.')

    def encerrar(uid, motivo, exceto=None):
        q = Acesso.query.filter_by(usuario_id=uid, encerrado_em=None)
        if exceto:
            q = q.filter(Acesso.chave != exceto)
        q.update(dict(encerrado_em=agora(), motivo=motivo), synchronize_session=False)
        # Remove assinaturas push dos aparelhos revogados. O usuário pode
        # reativá-las na central após entrar novamente.
        portal = app.extensions.get('hotel_portal', {})
        if 'Assinatura' in portal:
            portal['Assinatura'].query.filter_by(usuario_id=uid).delete()

    def dispositivo():
        ua = request.headers.get('User-Agent', '')[:1000]
        sistema = next((nome for trecho,nome in [('iPhone','iPhone'),('iPad','iPad'),
            ('Android','Android'),('Windows','Windows'),('Macintosh','Mac'),('Linux','Linux')]
            if trecho in ua), 'Aparelho')
        navegador = next((nome for trecho,nome in [('Edg/','Edge'),('OPR/','Opera'),
            ('Firefox/','Firefox'),('Chrome/','Chrome'),('CriOS/','Chrome'),('Safari/','Safari')]
            if trecho in ua), 'Navegador')
        return navegador+' · '+sistema

    def emitir_sessao(uid):
        perfil(uid)
        transacao()
        u = db.session.get(Usuario, uid)
        # Uma revogação/troca de senha pode concorrer com o login legado.
        # Confere a credencial atual sob o mesmo lock antes de emitir acesso.
        if not u or not u.ativo or not check_password_hash(u.senha_hash, request.form.get('senha', '')):
            db.session.rollback();session.clear()
            return False
        p = db.session.get(Perfil, uid)
        token = secrets.token_urlsafe(32)
        chave = hashlib.sha256(token.encode()).hexdigest()
        db.session.add(Acesso(chave=chave, usuario_id=uid, revisao=p.revisao,
            dispositivo=dispositivo(), ip=(request.remote_addr or '')[:80]))
        # Histórico operacional de acessos limitado a 90 dias. Não altera a
        # retenção dos hóspedes/reservas, que permanece no módulo existente.
        Acesso.query.filter(Acesso.ultimo_em < agora()-timedelta(days=90)).delete()
        db.session.commit()
        session['_config_sessao'] = token
        session['_config_revisao'] = p.revisao
        return True

    publicos = {'login', 'politica_privacidade', 'portal_publico', 'portal_quarto',
        'portal_comprovante', 'portal_foto', 'portal_service_worker', 'portal_manifest'}

    def verificar_sessao():
        if request.endpoint == 'static':
            return None
        uid = session.get('usuario_id')
        if not uid:
            return None
        u = db.session.get(Usuario, uid)
        token = session.get('_config_sessao', '')
        chave = hashlib.sha256(token.encode()).hexdigest() if isinstance(token,str) and token else ''
        a = db.session.get(Acesso, chave) if chave else None
        p = db.session.get(Perfil, uid)
        valido = (u and u.ativo and p and a and a.usuario_id == uid and not a.encerrado_em
            and a.revisao == p.revisao and session.get('_config_revisao') == p.revisao
            and a.ultimo_em >= agora()-timedelta(minutes=30))
        if not valido:
            session.clear()
            if request.endpoint not in publicos:
                flash('Entre novamente para continuar. Sua sessão expirou ou foi encerrada.', 'warning')
                return redirect(url_for('login'))
            return None
        g.config_uid, g.config_chave, g.config_hash = uid, chave, u.senha_hash
        g.config_perfil = p
        if agora()-a.ultimo_em > timedelta(seconds=60):
            a.ultimo_em = agora(); db.session.commit()
        if p.trocar_senha and request.endpoint not in ('hotel_alterar_senha','hotel_aviso_atualizacao','logout','politica_privacidade'):
            session['_hotel_trocar_senha'] = True
            return redirect(url_for('hotel_alterar_senha'))
        if request.endpoint in ('hotel_conta', 'hotel_administracao') and request.method == 'POST':
            request.max_content_length = 5*1024*1024
        return None

    # Revogações são verificadas antes dos módulos legados e seus POSTs.
    app.before_request_funcs.setdefault(None, []).insert(0, verificar_sessao)

    @app.context_processor
    def contexto():
        uid = session.get('usuario_id')
        p = db.session.get(Perfil, uid) if uid else None
        cfg = identidade()
        return dict(conta_perfil=p, conta_identidade=cfg,
            conta_iniciais=''.join(s[0] for s in (session.get('nome') or 'Usuário').split()[:2]).upper(),
            conta_admin=(session.get('perfil') or '').lower() == 'administrador')

    @app.route('/minha-conta', methods=['GET','POST'])
    @proteger()
    def hotel_conta():
        uid = session['usuario_id']; nova_imagem = None
        if request.method == 'POST':
            try:
                transacao()
                u, p = db.session.get(Usuario, uid), db.session.get(Perfil, uid)
                acao = request.form.get('acao')
                antiga = None
                if acao == 'perfil':
                    nome = texto('nome', 2, 120)
                    email, telefone = contato()
                    arquivo = request.files.get('foto')
                    if arquivo and arquivo.filename:
                        nova_imagem = guardar_imagem(arquivo)
                        antiga, p.foto = p.foto, nova_imagem
                    elif request.form.get('remover_foto') == '1':
                        antiga, p.foto = p.foto, None
                    u.nome, p.email, p.telefone = nome, email, telefone
                    session['nome'] = nome
                elif acao == 'preferencias':
                    tema = request.form.get('tema')
                    if tema not in ('padrao','light','dark'):
                        raise ValueError('Escolha um tema válido.')
                    p.tema, p.compacto = tema, request.form.get('compacto') == '1'
                    p.movimento, p.popups = request.form.get('movimento') == '1', request.form.get('popups') == '1'
                elif acao == 'encerrar_outros':
                    if not check_password_hash(u.senha_hash, request.form.get('senha_atual', '')):
                        raise ValueError('Informe sua senha atual para encerrar os outros acessos.')
                    encerrar(uid, 'Encerrado pelo titular', g.config_chave)
                elif acao == 'encerrar_acesso':
                    if not check_password_hash(u.senha_hash, request.form.get('senha_atual', '')):
                        raise ValueError('Informe sua senha atual para encerrar o acesso.')
                    a = db.session.get(Acesso, texto('chave', 64, 64))
                    if not a or a.usuario_id != uid or a.chave == g.config_chave:
                        raise ValueError('Escolha outro acesso válido para encerrar.')
                    a.encerrado_em, a.motivo = agora(), 'Encerrado pelo titular'
                    portal=app.extensions.get('hotel_portal',{})
                    if 'Assinatura' in portal:
                        portal['Assinatura'].query.filter_by(usuario_id=uid).delete()
                else:
                    raise ValueError('Ação inválida.')
                auditar('Conta: '+acao, uid)
                db.session.commit()
                remover_imagem(antiga)
                flash('Alterações salvas.', 'success')
                return redirect(url_for('hotel_conta', aba='seguranca' if acao.startswith('encerrar') else 'preferencias' if acao=='preferencias' else 'perfil'))
            except ValueError as exc:
                db.session.rollback(); remover_imagem(nova_imagem)
                flash(str(exc), 'danger')
                return redirect(url_for('hotel_conta', aba='seguranca' if (request.form.get('acao') or '').startswith('encerrar') else 'perfil'))
            except Exception:
                db.session.rollback(); remover_imagem(nova_imagem)
                raise
        acessos = Acesso.query.filter_by(usuario_id=uid).filter(Acesso.ultimo_em >= agora()-timedelta(days=90)).order_by(Acesso.ultimo_em.desc()).limit(30).all()
        return render_template('hotel_conta.html', usuario=db.session.get(Usuario, uid), acessos=acessos,
            atual=g.config_chave, aba=request.args.get('aba','perfil'))

    @app.route('/minha-conta/tema', methods=['POST'])
    @proteger()
    def hotel_conta_tema():
        data = request.get_json(silent=True) or {}
        if not isinstance(data,dict) or data.get('tema') not in ('light','dark'):
            return jsonify(erro='Tema inválido.'), 400
        p = db.session.get(Perfil, session['usuario_id']);p.tema=data['tema']
        db.session.commit()
        return jsonify(salvo=True)

    @app.route('/minha-conta/foto')
    @proteger()
    def hotel_conta_foto():
        p = db.session.get(Perfil, session['usuario_id'])
        if not p.foto or not re.fullmatch(r'foto_[a-f0-9]{40}\.jpg',p.foto) or not (pasta/p.foto).is_file():
            abort(404)
        return send_file(pasta/p.foto,mimetype='image/jpeg',max_age=0)

    @app.route('/configuracoes/logo')
    @proteger()
    def hotel_config_logo():
        cfg = identidade()
        if not cfg.logo or not re.fullmatch(r'logo_[a-f0-9]{40}\.png',cfg.logo) or not (pasta/cfg.logo).is_file():
            abort(404)
        return send_file(pasta/cfg.logo,mimetype='image/png',max_age=0)

    @app.route('/administracao/configuracoes', methods=['GET','POST'])
    @proteger(admin=True)
    def hotel_administracao():
        nova_imagem = None
        if request.method == 'POST':
            try:
                transacao();cfg=identidade();acao=request.form.get('acao');antiga=None
                administrador=db.session.get(Usuario,session['usuario_id'])
                if not administrador or not administrador.ativo or (administrador.perfil or '').strip().lower()!='administrador':
                    abort(403)
                if acao == 'identidade':
                    cfg.nome=texto('nome',2,100);cfg.subtitulo=texto('subtitulo',maximo=140)
                    cfg.email,cfg.telefone=contato();cfg.endereco=texto('endereco',maximo=300)
                    cfg.tema=request.form.get('tema')
                    if cfg.tema not in ('light','dark'):
                        raise ValueError('Escolha um tema padrão válido.')
                    arquivo=request.files.get('logo')
                    if arquivo and arquivo.filename:
                        nova_imagem=guardar_imagem(arquivo,logo=True);antiga,cfg.logo=cfg.logo,nova_imagem
                    elif request.form.get('remover_logo')=='1':
                        antiga,cfg.logo=cfg.logo,None
                    auditar('Administração: alterou identidade')
                elif acao in ('usuario','exigir_senha','revogar_acessos'):
                    senha_administrador()
                    uid=request.form.get('usuario_id',type=int);u=db.session.get(Usuario,uid) if uid else None
                    if not u:
                        raise ValueError('Usuário não encontrado.')
                    if uid==session['usuario_id']:
                        raise ValueError('Use Minha conta para cuidar do seu acesso. As ações administrativas são para outros usuários.')
                    p=db.session.get(Perfil,uid)
                    if p is None:
                        p=Perfil(usuario_id=uid)
                        db.session.add(p);db.session.flush()
                    if acao=='usuario':
                        nome=texto('nome',2,120);login=texto('usuario',2,60);papel=request.form.get('perfil')
                        ativo=request.form.get('ativo')=='1'
                        if papel not in ctx['PERFIS_DISPONIVEIS']:
                            raise ValueError('Selecione um perfil válido.')
                        if not re.fullmatch(r'[A-Za-z0-9_.@-]{2,60}',login):
                            raise ValueError('Use letras sem acento, números, ponto, hífen, @ ou sublinhado no login.')
                        if Usuario.query.filter(db.func.lower(Usuario.usuario)==login.lower(),Usuario.id!=uid).first():
                            raise ValueError('Esse login já está sendo utilizado.')
                        administradores=Usuario.query.filter(db.func.lower(Usuario.perfil)=='administrador',Usuario.ativo.is_(True)).count()
                        if u.ativo and (u.perfil or '').lower()=='administrador' and (not ativo or papel!='Administrador') and administradores<=1:
                            raise ValueError('Mantenha pelo menos um administrador ativo.')
                        u.nome,u.usuario,u.perfil,u.ativo=nome,login,papel,ativo
                    if acao=='exigir_senha':
                        p.trocar_senha=True
                    p.revisao+=1;encerrar(uid,'Alteração administrativa')
                    auditar('Administração: '+acao,uid,'Acessos anteriores invalidados; nenhuma senha foi exibida.')
                else:
                    raise ValueError('Ação inválida.')
                db.session.commit();remover_imagem(antiga)
                flash('Configuração salva.', 'success')
                return redirect(url_for('hotel_administracao',aba='usuarios' if acao!='identidade' else 'identidade'))
            except ValueError as exc:
                db.session.rollback();remover_imagem(nova_imagem);flash(str(exc),'danger')
                return redirect(url_for('hotel_administracao',aba='usuarios' if request.form.get('acao')!='identidade' else 'identidade'))
            except Exception:
                db.session.rollback();remover_imagem(nova_imagem);raise
        q=(request.args.get('q') or '').strip()[:120]
        query=Usuario.query
        if q:
            query=query.filter(db.or_(Usuario.nome.ilike('%'+q+'%'),Usuario.usuario.ilike('%'+q+'%')))
        usuarios=query.order_by(Usuario.nome).paginate(page=max(1,request.args.get('pagina',1,type=int) or 1),per_page=30,error_out=False)
        perfis={p.usuario_id:p for p in Perfil.query.filter(Perfil.usuario_id.in_([u.id for u in usuarios.items])).all()}
        ativos=Usuario.query.filter_by(ativo=True).count()
        return render_template('hotel_administracao.html',usuarios=usuarios,perfis=perfis,
            aba=request.args.get('aba','identidade'),busca=q,ativos=ativos,total=Usuario.query.count())

    # Contador do sino continua disponível. A preferência altera apenas os pop-ups.
    if 'portal_alertas' in app.view_functions:
        original=app.view_functions['portal_alertas']
        @wraps(original)
        def alertas_com_preferencia(*args,**kwargs):
            response=app.make_response(original(*args,**kwargs))
            if response.status_code==200 and response.is_json and session.get('usuario_id'):
                data=response.get_json();data['popups']=perfil(session['usuario_id']).popups
                response.set_data(app.json.dumps(data))
            return response
        app.view_functions['portal_alertas']=alertas_com_preferencia

    @app.after_request
    def finalizar_configuracoes(response):
        if request.endpoint=='login' and request.method=='POST' and response.status_code==302 and session.get('usuario_id'):
            if not emitir_sessao(session['usuario_id']):
                flash('O acesso foi atualizado durante a entrada. Faça login novamente.', 'warning')
                return redirect(url_for('login'))
        uid=getattr(g,'config_uid',None)
        if uid and request.endpoint=='logout' and request.method=='POST' and response.status_code==302:
            a=db.session.get(Acesso,g.config_chave)
            if a:
                a.encerrado_em,a.motivo=agora(),'Saída da conta';db.session.commit()
        if uid and request.endpoint=='hotel_alterar_senha' and request.method=='POST' and response.status_code==302:
            u=db.session.get(Usuario,uid)
            if u and u.senha_hash!=g.config_hash:
                p=db.session.get(Perfil,uid);p.trocar_senha=False;p.revisao+=1
                encerrar(uid,'Senha alterada');db.session.commit()
        public=request.endpoint in publicos or (request.endpoint or '').startswith('portal_public')
        if (session.get('usuario_id') and not public and response.status_code==200
            and response.mimetype=='text/html' and not response.direct_passthrough
            and request.endpoint not in ('hotel_aviso_atualizacao','hotel_alterar_senha')):
            html=response.get_data(as_text=True)
            if 'id="hc-account-menu"' not in html and '</head>' in html.lower():
                html=re.sub(r'</head\s*>',lambda m:render_template('hotel_config_assets.html')+m.group(),html,count=1,flags=re.I)
                html=re.sub(r'</body\s*>',lambda m:render_template('hotel_conta_menu.html')+m.group(),html,count=1,flags=re.I)
                response.set_data(html)
        return response

    class Servico:
        def inicializar(self):
            db.session.execute(insert(Identidade).values(id=1,nome='Hotel PRO',
                subtitulo='Gestão de hospedagens',tema='light',telefone='',email='',endereco='')
                .on_conflict_do_nothing(index_elements=['id']))
            db.session.commit()
    return Servico()
