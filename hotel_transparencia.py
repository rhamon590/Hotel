"""Transparência do portal e controle editorial. Não certifica conformidade jurídica."""
import hashlib
import json
import re
from datetime import datetime,timezone
from functools import wraps
from html.parser import HTMLParser
from pathlib import Path

from flask import abort,flash,g,redirect,render_template,request,session,url_for
from sqlalchemy.dialects.sqlite import insert
from werkzeug.security import check_password_hash

TERMOS = '''Este portal permite consultar acomodações e solicitar contato da recepção. O envio do formulário não confirma hospedagem nem bloqueia o quarto. A recepção verifica novamente a disponibilidade antes de confirmar.

São solicitados nome e telefone, além das datas e da quantidade de hóspedes necessários ao atendimento. A observação é opcional. Não informe senhas, dados bancários completos, documentos ou informações sensíveis nesse campo.

A tarifa exibida é por diária do quarto inteiro. O total mostrado é uma estimativa para o período selecionado. Quando o valor estiver sob consulta, a recepção informará a tarifa antes da confirmação. Os serviços incluídos, valores adicionais e condições aplicáveis devem ser esclarecidos antes da contratação.

O portal não recebe pagamentos on-line. O cliente deve conferir o estabelecimento e as condições antes de realizar qualquer pagamento por outro canal. Confirmação, orientações de chegada e eventual pagamento são tratados pela recepção.

O uso dos dados necessários à solicitação não autoriza publicidade. Consulte a política de privacidade e o canal indicado para assuntos relacionados aos seus dados.

As informações desta página não afastam direitos previstos na legislação aplicável. Em caso de dúvida sobre a hospedagem, entre em contato com o estabelecimento identificado abaixo.'''
CANCELAMENTO = '''Antes da confirmação, a solicitação pode ser encerrada pela recepção mediante pedido do interessado. Este portal não recebe pagamentos on-line e o envio do formulário, por si só, não gera cobrança.

Para uma reserva já confirmada ou um pagamento feito por outro canal, entre em contato com a recepção. As condições aplicáveis devem ser apresentadas de forma clara antes da contratação e não podem afastar direitos previstos na legislação, inclusive o direito de arrependimento quando aplicável.

[DEFINIR PELA EMPRESA: condições de cancelamento de reservas confirmadas, eventual sinal, não comparecimento, procedimento e critérios de reembolso. Não publique enquanto essas regras não estiverem definidas e revisadas.]'''


def registrar_transparencia(ctx):
    app,db,Usuario,Auditoria = (ctx[k] for k in ('app','db','Usuario','Auditoria'))
    portal=app.extensions['hotel_portal'];Oferta=portal['Oferta']

    class Config(db.Model):
        __tablename__='hotel_transparencia_config'
        id=db.Column(db.Integer,primary_key=True)
        rascunho=db.Column(db.Text,nullable=False)
        publicado_id=db.Column(db.Integer)

    class Versao(db.Model):
        __tablename__='hotel_transparencia_versao'
        id=db.Column(db.Integer,primary_key=True)
        conteudo=db.Column(db.Text,nullable=False)
        resumo=db.Column(db.String(64),nullable=False)
        responsavel=db.Column(db.String(80),nullable=False)
        data=db.Column(db.DateTime,nullable=False)

    class Foto(db.Model):
        __tablename__='hotel_transparencia_foto'
        quarto_id=db.Column(db.Integer,primary_key=True)
        arquivo=db.Column(db.String(80),nullable=False)
        origem=db.Column(db.String(500),nullable=False)
        alternativo=db.Column(db.String(200),nullable=False)
        responsavel=db.Column(db.String(80),nullable=False)
        data=db.Column(db.DateTime,nullable=False)

    app.extensions['hotel_transparencia']=dict(Config=Config,Versao=Versao,Foto=Foto)
    publicos={'portal_publico','portal_quarto','portal_comprovante','politica_privacidade',
        'hotel_cookies','hotel_acessibilidade','hotel_termos','hotel_cancelamento'}

    def cfg():return db.session.get(Config,1)
    def rascunho():return json.loads(cfg().rascunho)
    def publicada():
        v=db.session.get(Versao,cfg().publicado_id) if cfg().publicado_id else None
        return (v,json.loads(v.conteudo)) if v else (None,None)
    def agora():return datetime.now(timezone.utc).replace(tzinfo=None)
    def admin(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            u=db.session.get(Usuario,session.get('usuario_id')) if session.get('usuario_id') else None
            if not u or not u.ativo:return redirect(url_for('login'))
            if (u.perfil or '').strip().lower()!='administrador':abort(403)
            return fn(*args,**kwargs)
        return wrapped
    def auditar(acao,registro='',detalhes=''):
        db.session.add(Auditoria(usuario=session.get('usuario','sistema'),acao=acao,
            tabela='transparencia',registro=str(registro),detalhes=detalhes,ip=request.remote_addr))
    def transacao():
        db.session.rollback();db.session.execute(db.text('BEGIN IMMEDIATE'))
        u=db.session.get(Usuario,session.get('usuario_id'))
        if not u or not u.ativo or (u.perfil or '').strip().lower()!='administrador':abort(403)
        return u
    def senha(u):
        if not check_password_hash(u.senha_hash,request.form.get('senha_atual','')):
            raise ValueError('Confirme sua senha de administrador antes de publicar ou retirar a publicação.')
    def campo(nome,limite):
        value=(request.form.get(nome) or '').strip()
        if len(value)>limite:raise ValueError('O campo '+nome+' excedeu o tamanho permitido.')
        return value
    def cnpj_valido(value):
        # Este campo aceita CNPJ numérico. Não presume ou altera o CNPJ da empresa.
        d=re.sub(r'\D','',value)
        if not re.fullmatch(r'[0-9./ \-]+',value):return False
        if len(d)!=14 or len(set(d))==1:return False
        for pos,pesos in [(12,[5,4,3,2,9,8,7,6,5,4,3,2]),(13,[6,5,4,3,2,9,8,7,6,5,4,3,2])]:
            resto=sum(int(a)*b for a,b in zip(d[:pos],pesos))%11
            if int(d[pos])!=(0 if resto<2 else 11-resto):return False
        return True
    def validar(d):
        if not 2<=len(d['empresa'])<=160 or not cnpj_valido(d['cnpj']):
            raise ValueError('Informe a empresa que responde pela hospedagem e um CNPJ numérico válido. O dígito verificador não comprova situação cadastral.')
        if len(d['endereco'])<10 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',d['email']):
            raise ValueError('Informe o endereço físico e o e-mail de atendimento do estabelecimento.')
        if not 10<=len(re.sub(r'\D','',d['telefone']))<=15:
            raise ValueError('Informe telefone de atendimento com DDD.')
        if len(d['termos'])<300 or len(d['cancelamento'])<150 or re.search(r'\[(DEFINIR|PREENCHER)',d['termos']+' '+d['cancelamento'],re.I):
            raise ValueError('Conclua a revisão dos termos e das regras de cancelamento. Remova os campos de definição somente após estabelecer as regras da empresa.')
    from hotel_galeria import registrar_galeria
    galeria = registrar_galeria(ctx, Foto)

    def foto_publica(qid):
        if app.extensions['hotel_galeria_servico']['capa'](qid):return True
        o=db.session.get(Oferta,qid);f=db.session.get(Foto,qid)
        return bool(o and o.foto and f and f.arquivo==o.foto)
    def foto_alt(qid,padrao='Foto da acomodação'):
        capa=app.extensions['hotel_galeria_servico']['capa'](qid)
        if capa:return capa['alternativo']
        f=db.session.get(Foto,qid)
        return f.alternativo if f and foto_publica(qid) else padrao

    @app.context_processor
    def contexto():
        v,d=publicada()
        return dict(transparencia_versao=v,transparencia_empresa=d,
            foto_publica_autorizada=foto_publica,foto_texto_alternativo=foto_alt)

    @app.route('/hospedagem/cookies')
    def hotel_cookies():return render_template('hotel_transparencia_documento.html',tipo='cookies',titulo='Cookies e armazenamento local',recursos_cookies=inventario())
    @app.route('/hospedagem/acessibilidade')
    def hotel_acessibilidade():return render_template('hotel_transparencia_documento.html',tipo='acessibilidade',titulo='Acessibilidade e atendimento')
    def documento(tipo,titulo):
        v,d=publicada()
        if not v:abort(404)
        return render_template('hotel_transparencia_documento.html',tipo=tipo,titulo=titulo,documento=d,versao=v)
    @app.route('/hospedagem/termos')
    def hotel_termos():return documento('termos','Termos de uso do portal')
    @app.route('/hospedagem/cancelamento')
    def hotel_cancelamento():return documento('cancelamento','Cancelamento e reembolso')

    @app.route('/administracao/transparencia',methods=['GET','POST'])
    @admin
    def hotel_transparencia_admin():
        if request.method=='POST':
            try:
                u=transacao();c=cfg();acao=request.form.get('acao')
                if acao=='rascunho':
                    d={n:campo(n,maximo) for n,maximo in [('empresa',160),('cnpj',30),('endereco',300),('email',120),('telefone',30),('termos',15000),('cancelamento',10000)]}
                    c.rascunho=json.dumps(d,ensure_ascii=False)
                elif acao=='publicar':
                    senha(u);d=rascunho();validar(d)
                    if request.form.get('confirmar_revisao')!='1':
                        raise ValueError('Confirme que os dados e as regras foram revisados pela empresa antes da publicação.')
                    conteudo=json.dumps(d,ensure_ascii=False,sort_keys=True)
                    v=Versao(conteudo=conteudo,resumo=hashlib.sha256(conteudo.encode()).hexdigest(),
                        responsavel=u.usuario,data=agora())
                    db.session.add(v);db.session.flush();c.publicado_id=v.id
                elif acao=='retirar':
                    senha(u);c.publicado_id=None
                elif acao=='foto':
                    qid=request.form.get('quarto_id',type=int);o=db.session.get(Oferta,qid) if qid else None
                    if not o or not o.foto:raise ValueError('A foto atual não foi encontrada. Recarregue a página.')
                    if request.form.get('arquivo')!=o.foto:raise ValueError('A foto mudou. Confira a imagem atual antes de autorizar.')
                    if request.form.get('direitos')!='1':raise ValueError('Confirme a autorização de publicação da foto.')
                    origem,alt=campo('origem',500),campo('alternativo',200)
                    if len(origem)<5 or len(alt)<8:raise ValueError('Informe a origem/autorização e uma descrição objetiva da foto.')
                    db.session.merge(Foto(quarto_id=qid,arquivo=o.foto,origem=origem,alternativo=alt,responsavel=u.usuario,data=agora()))
                elif acao=='retirar_foto':
                    qid=request.form.get('quarto_id',type=int)
                    Foto.query.filter_by(quarto_id=qid).delete()
                else:raise ValueError('Ação inválida.')
                auditar('Transparência: '+acao,str(c.publicado_id or ''),'Revisão editorial; não representa certificação jurídica.')
                db.session.commit();flash('Alteração salva.','success')
                return redirect(url_for('hotel_transparencia_admin'))
            except ValueError as exc:
                db.session.rollback();flash(str(exc),'danger')
                return redirect(url_for('hotel_transparencia_admin'))
        fotos=[]
        for o in Oferta.query.filter(Oferta.foto.isnot(None)).all():
            q=db.session.get(ctx['Quarto'],o.quarto_id)
            if q:fotos.append(dict(oferta=o,quarto=q,controle=db.session.get(Foto,q.id),autorizada=foto_publica(q.id)))
        return render_template('hotel_transparencia_admin.html',rascunho=rascunho(),fotos=fotos,
            versoes=Versao.query.order_by(Versao.id.desc()).limit(20).all(),recursos=inventario())

    def inventario():
        class Scripts(HTMLParser):
            def __init__(self):super().__init__();self.urls=[]
            def handle_starttag(self,tag,attrs):
                a=dict(attrs)
                if tag=='script' and a.get('src','').startswith(('https://','http://','//')):self.urls.append(a['src'])
        rows=[]
        for name in ('base.html','portal_base.html','hotel_legal_base.html'):
            try:text=app.jinja_env.loader.get_source(app.jinja_env,name)[0]
            except Exception:continue
            parser=Scripts();parser.feed(text)
            rows.extend(dict(template=name,url=url) for url in parser.urls)
        return rows

    @app.before_request
    def proteger_fotos():
        if request.endpoint=='portal_foto' and (session.get('perfil') or '').lower()!='administrador':
            if not foto_publica((request.view_args or {}).get('quarto_id')):abort(404)

    @app.after_request
    def finalizar(response):
        if request.endpoint in publicos:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
            if response.mimetype=='text/html' and not response.direct_passthrough:
                html=response.get_data(as_text=True)
                if 'hotel_transparencia.css' not in html:
                    html=re.sub(r'</head\s*>',lambda m:render_template('hotel_transparencia_assets.html')+m.group(),html,count=1,flags=re.I)
                response.set_data(html)
        return response
    # Executa depois das proteções existentes, sem ter a CSP substituída por elas.
    handlers=app.after_request_funcs.setdefault(None,[]);handlers.remove(finalizar);handlers.insert(0,finalizar)

    class Servico:
        def inicializar(self):
            galeria.inicializar()
            d=dict(empresa='',cnpj='',endereco='',email='',telefone='',termos=TERMOS,cancelamento=CANCELAMENTO)
            db.session.execute(insert(Config).values(id=1,rascunho=json.dumps(d,ensure_ascii=False)).on_conflict_do_nothing(index_elements=['id']))
            db.session.commit()
    return Servico()
