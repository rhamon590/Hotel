import os
import shutil
from datetime import datetime, date
from io import BytesIO
from functools import wraps
from flask import jsonify

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.units import mm

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# No Railway será /app/data. No computador continuará usando a pasta local.
DATA_DIR = os.environ.get(
    'RAILWAY_VOLUME_MOUNT_PATH',
    BASE_DIR
)

UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
DATABASE_PATH = os.path.join(DATA_DIR, 'hotel.db')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get(
    'SECRET_KEY',
    'troque-essa-chave-em-producao'
)

app.config['SQLALCHEMY_DATABASE_URI'] = (
    'sqlite:///' + DATABASE_PATH
)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR

db = SQLAlchemy(app)

# ========================= MODELS =========================
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    usuario = db.Column(db.String(60), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    perfil = db.Column(db.String(40), default='RH')
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.now)

class Hospede(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    cpf = db.Column(db.String(20), unique=True)
    rg = db.Column(db.String(30))
    passaporte = db.Column(db.String(40))
    nascimento = db.Column(db.String(20))
    telefone = db.Column(db.String(40))
    whatsapp = db.Column(db.String(40))
    email = db.Column(db.String(120))
    nacionalidade = db.Column(db.String(80))
    endereco = db.Column(db.String(200))
    cidade = db.Column(db.String(80))
    estado = db.Column(db.String(20))
    cep = db.Column(db.String(20))
    profissao = db.Column(db.String(100))
    empresa = db.Column(db.String(120))
    observacoes = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.now)

class Quarto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(20), unique=True, nullable=False)
    andar = db.Column(db.String(20))
    categoria = db.Column(db.String(80), default='Standard')
    capacidade = db.Column(db.Integer, default=1)
    valor_diaria = db.Column(db.Float, default=0)
    status = db.Column(db.String(40), default='Livre')
    descricao = db.Column(db.Text)
    amenidades = db.Column(db.Text)

class Reserva(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hospede_id = db.Column(db.Integer, db.ForeignKey('hospede.id'), nullable=False)
    quarto_id = db.Column(db.Integer, db.ForeignKey('quarto.id'), nullable=False)
    data_entrada = db.Column(db.String(20), nullable=False)
    data_saida = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(40), default='Reservado')
    valor_diaria = db.Column(db.Float, default=0)
    desconto = db.Column(db.Float, default=0)
    observacoes = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.now)
    hospede = db.relationship('Hospede')
    quarto = db.relationship('Quarto')

class Pagamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reserva_id = db.Column(db.Integer, db.ForeignKey('reserva.id'))
    tipo = db.Column(db.String(40), default='PIX')
    valor = db.Column(db.Float, default=0)
    status = db.Column(db.String(40), default='Pago')
    data = db.Column(db.DateTime, default=datetime.now)
    observacoes = db.Column(db.Text)
    reserva = db.relationship('Reserva')

class Consumo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reserva_id = db.Column(db.Integer, db.ForeignKey('reserva.id'))
    produto = db.Column(db.String(120), nullable=False)
    quantidade = db.Column(db.Integer, default=1)
    valor_unitario = db.Column(db.Float, default=0)
    setor = db.Column(db.String(60), default='Frigobar')
    pago = db.Column(db.Boolean, default=False)
    data = db.Column(db.DateTime, default=datetime.now)
    reserva = db.relationship('Reserva')

class Limpeza(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quarto_id = db.Column(db.Integer, db.ForeignKey('quarto.id'))
    responsavel = db.Column(db.String(120))
    status = db.Column(db.String(40), default='Aguardando')
    checklist = db.Column(db.Text)
    observacoes = db.Column(db.Text)
    data = db.Column(db.DateTime, default=datetime.now)
    quarto = db.relationship('Quarto')

class Manutencao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quarto_id = db.Column(db.Integer, db.ForeignKey('quarto.id'))
    problema = db.Column(db.String(200), nullable=False)
    prioridade = db.Column(db.String(40), default='Média')
    status = db.Column(db.String(40), default='Aberto')
    responsavel = db.Column(db.String(120))
    custo = db.Column(db.Float, default=0)
    observacoes = db.Column(db.Text)
    data = db.Column(db.DateTime, default=datetime.now)
    quarto = db.relationship('Quarto')

class Produto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    categoria = db.Column(db.String(80))
    estoque = db.Column(db.Integer, default=0)
    estoque_minimo = db.Column(db.Integer, default=0)
    valor = db.Column(db.Float, default=0)

class Funcionario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(20))
    cargo = db.Column(db.String(80))
    setor = db.Column(db.String(80))
    telefone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    status = db.Column(db.String(40), default='Ativo')

class Empresa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    cnpj = db.Column(db.String(30))
    contato = db.Column(db.String(120))
    telefone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    tarifa_especial = db.Column(db.Float, default=0)

class Auditoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(80))
    acao = db.Column(db.String(120))
    tabela = db.Column(db.String(80))
    registro = db.Column(db.String(80))
    detalhes = db.Column(db.Text)
    ip = db.Column(db.String(80))
    data = db.Column(db.DateTime, default=datetime.now)



class Cartao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=False, index=True)
    descricao = db.Column(db.String(150))
    status = db.Column(db.String(30), default='Disponível', nullable=False, index=True)
    criado_por = db.Column(db.String(80), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    observacoes = db.Column(db.Text)

    movimentacoes = db.relationship(
        'MovimentacaoCartao',
        back_populates='cartao',
        cascade='all, delete-orphan',
        order_by='MovimentacaoCartao.data_evento.desc()'
    )


class MovimentacaoCartao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cartao_id = db.Column(db.Integer, db.ForeignKey('cartao.id'), nullable=False, index=True)
    reserva_id = db.Column(db.Integer, db.ForeignKey('reserva.id'), index=True)
    hospede_id = db.Column(db.Integer, db.ForeignKey('hospede.id'), index=True)
    quarto_id = db.Column(db.Integer, db.ForeignKey('quarto.id'), index=True)

    tipo = db.Column(db.String(30), nullable=False, index=True)
    status_resultante = db.Column(db.String(30), nullable=False)
    usuario_responsavel = db.Column(db.String(80), nullable=False)
    data_evento = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    observacoes = db.Column(db.Text)

    cartao = db.relationship('Cartao', back_populates='movimentacoes')
    reserva = db.relationship('Reserva')
    hospede = db.relationship('Hospede')
    quarto = db.relationship('Quarto')


# ========================= HELPERS =========================
# Perfis disponíveis no sistema.
PERFIS_DISPONIVEIS = ('Administrador', 'RH', 'Porteiro')

# Módulos liberados para cada perfil.
# Os valores são mantidos em minúsculas porque o perfil salvo na sessão
# também é normalizado no momento do login.
PERMISSOES_PERFIL = {
    'administrador': {
        'dashboard', 'hospedes', 'quartos', 'reservas', 'calendario',
        'governanca', 'cartoes', 'manutencao', 'estoque', 'funcionarios',
        'empresas', 'relatorios', 'auditoria', 'usuarios', 'backup',
        'consumos'
    },
    'rh': {
        'dashboard', 'hospedes', 'quartos', 'reservas', 'governanca'
    },
    'porteiro': {
        'dashboard', 'cartoes', 'calendario'
    },
}


def perfil_atual():
    """Retorna o perfil da sessão já normalizado."""
    return (session.get('perfil') or '').strip().lower()


def usuario_tem_acesso(modulo):
    """Verifica se o usuário logado pode acessar determinado módulo."""
    return modulo in PERMISSOES_PERFIL.get(perfil_atual(), set())


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if 'usuario' not in session:
            flash('Faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        return func(*args, **kwargs)
    return wrapper


def permissao_necessaria(modulo):
    """Protege a rota também contra acesso direto pela URL."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if 'usuario' not in session:
                flash('Faça login para acessar o sistema.', 'warning')
                return redirect(url_for('login'))

            if not usuario_tem_acesso(modulo):
                flash('Você não possui permissão para acessar este módulo.', 'danger')
                return redirect(url_for('dashboard'))

            return func(*args, **kwargs)
        return wrapper
    return decorator


def admin_required(func):
    return permissao_necessaria('usuarios')(func)


@app.context_processor
def disponibilizar_permissoes():
    """Permite usar tem_acesso('modulo') em qualquer template Jinja."""
    return {
        'tem_acesso': usuario_tem_acesso,
        'perfil_atual': perfil_atual(),
        'perfis_disponiveis': PERFIS_DISPONIVEIS,
    }


def auditar(acao, tabela='', registro='', detalhes=''):
    db.session.add(Auditoria(
        usuario=session.get('usuario', 'sistema'), acao=acao, tabela=tabela,
        registro=str(registro), detalhes=detalhes, ip=request.remote_addr
    ))
    db.session.commit()

def reservas_ativas_quarto(quarto_id):
    return Reserva.query.filter(
        Reserva.quarto_id == quarto_id,
        Reserva.status.in_(['Reservado', 'Hospedado'])
    )


def lotacao_quarto(quarto):
    """Retorna a ocupação do quarto neste momento.

    Reservas futuras continuam garantidas no período delas, mas não reduzem
    as vagas atuais nem alteram antecipadamente o status do quarto.
    """
    agora = datetime.now()
    reservados = 0
    hospedados = 0

    for reserva in reservas_ativas_quarto(quarto.id).all():
        try:
            entrada = datetime.fromisoformat(reserva.data_entrada)
            saida = datetime.fromisoformat(reserva.data_saida)
        except (TypeError, ValueError):
            # Registro antigo com data inválida não deve bloquear o quarto.
            continue

        if reserva.status == 'Hospedado':
            hospedados += 1
        elif entrada <= agora < saida:
            reservados += 1

    capacidade = max(int(quarto.capacidade or 0), 0)
    comprometidas = reservados + hospedados
    vagas = max(capacidade - comprometidas, 0)

    return {
        'capacidade': capacidade,
        'reservados': reservados,
        'ocupados': hospedados,
        'comprometidas': comprometidas,
        'vagas': vagas,
    }


def vagas_no_periodo(quarto, entrada, saida, ignorar_id=None):
    """Calcula vagas considerando reservas que se sobrepõem ao período."""
    query = reservas_ativas_quarto(quarto.id)

    ocupadas_no_periodo = 0
    for reserva in query.all():
        if ignorar_id and reserva.id == ignorar_id:
            continue

        if entrada < reserva.data_saida and saida > reserva.data_entrada:
            ocupadas_no_periodo += 1

    return max(int(quarto.capacidade or 0) - ocupadas_no_periodo, 0)


def atualizar_status_quarto(quarto):
    """Atualiza o status conforme reservas e ocupação, preservando bloqueios operacionais."""
    if quarto.status in ['Em limpeza', 'Em manutenção', 'Bloqueado']:
        return quarto.status

    lotacao = lotacao_quarto(quarto)

    if lotacao['comprometidas'] <= 0:
        quarto.status = 'Livre'
    elif lotacao['comprometidas'] >= lotacao['capacidade']:
        quarto.status = 'Lotado'
    elif lotacao['ocupados'] > 0:
        quarto.status = 'Parcialmente ocupado'
    else:
        quarto.status = 'Reservado'

    return quarto.status


def gerar_limpeza_se_necessario(quarto, observacao):
    """Envia o quarto para limpeza somente quando não restar reserva ativa."""
    if reservas_ativas_quarto(quarto.id).count() > 0:
        atualizar_status_quarto(quarto)
        return False

    quarto.status = 'Em limpeza'

    limpeza_existente = Limpeza.query.filter(
        Limpeza.quarto_id == quarto.id,
        Limpeza.status.in_(['Aguardando', 'Em limpeza'])
    ).first()

    if not limpeza_existente:
        db.session.add(Limpeza(
            quarto_id=quarto.id,
            responsavel='',
            status='Aguardando',
            checklist='',
            observacoes=observacao,
            data=datetime.now()
        ))

    return True

def export_excel(query, filename):
    dados = []
    for obj in query:
        row = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        dados.append(row)
    df = pd.DataFrame(dados)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

def export_pdf(title, headers, rows, filename):
    output = BytesIO()

    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=24,
        leftMargin=24,
        topMargin=28,
        bottomMargin=28
    )

    styles = getSampleStyleSheet()

    title_style = styles['Title']
    title_style.fontName = 'Helvetica-Bold'
    title_style.fontSize = 18
    title_style.textColor = colors.HexColor('#0f172a')
    title_style.alignment = 1

    subtitle_style = styles['Normal']
    subtitle_style.fontName = 'Helvetica'
    subtitle_style.fontSize = 8
    subtitle_style.textColor = colors.HexColor('#64748b')
    subtitle_style.alignment = 1

    small_style = styles['Normal']
    small_style.fontSize = 8
    small_style.textColor = colors.HexColor('#475569')

    elements = []

    # Cabeçalho profissional
    header_data = [
        [
            Paragraph('<b>HOTEL PRO</b><br/><font size="8">ERP Hoteleiro Profissional</font>', styles['Normal']),
            Paragraph(
                f'<b>{title}</b><br/>'
                f'<font size="8">Gerado em {datetime.now().strftime("%d/%m/%Y às %H:%M")}</font>',
                styles['Normal']
            )
        ]
    ]

    header_table = Table(header_data, colWidths=[180, 560])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#0f172a')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#1e293b')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 14))

    # Cards de resumo
    total_registros = len(rows)

    resumo_data = [
        [
            Paragraph('<b>Total de registros</b><br/><font size="16">%s</font>' % total_registros, styles['Normal']),
            Paragraph('<b>Relatório</b><br/><font size="10">%s</font>' % title, styles['Normal']),
            Paragraph('<b>Sistema</b><br/><font size="10">Hotel Pro</font>', styles['Normal']),
            Paragraph('<b>Usuário</b><br/><font size="10">Administrador</font>', styles['Normal'])
        ]
    ]

    resumo_table = Table(resumo_data, colWidths=[180, 180, 180, 180])
    resumo_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e2e8f0')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0f172a')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))

    elements.append(resumo_table)
    elements.append(Spacer(1, 14))

    # Caso não tenha dados
    if not rows:
        elements.append(Paragraph('Nenhum registro encontrado para este relatório.', small_style))
        doc.build(elements)
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )

    # Limita textos grandes para não quebrar o PDF
    def limpar_texto(valor, limite=35):
        valor = '' if valor is None else str(valor)
        valor = valor.replace('\n', ' ').replace('\r', ' ')
        if len(valor) > limite:
            return valor[:limite] + '...'
        return valor

    headers_limpos = [limpar_texto(h, 25) for h in headers]
    rows_limpas = []

    for row in rows:
        rows_limpas.append([limpar_texto(col, 35) for col in row])

    data = [headers_limpos] + rows_limpas

    # Largura automática das colunas
    largura_total = 790
    qtd_colunas = max(1, len(headers_limpos))
    largura_coluna = largura_total / qtd_colunas
    col_widths = [largura_coluna] * qtd_colunas

    table = Table(
        data,
        colWidths=col_widths,
        repeatRows=1
    )

    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),

        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
            colors.white,
            colors.HexColor('#f8fafc')
        ]),

        ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#1e293b')),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 6),
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),

        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 18))

    elements.append(Paragraph(
        'Documento gerado automaticamente pelo Hotel Pro ERP Hoteleiro.',
        subtitle_style
    ))

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#64748b'))

        largura, altura = landscape(A4)

        canvas.drawString(
            24,
            18,
            f'Hotel Pro ERP Hoteleiro • Gerado em {datetime.now().strftime("%d/%m/%Y %H:%M")}'
        )

        canvas.drawRightString(
            largura - 24,
            18,
            f'Página {doc.page}'
        )

        canvas.restoreState()

    doc.build(
        elements,
        onFirstPage=rodape,
        onLaterPages=rodape
    )

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

from functools import wraps
from flask import session, redirect, url_for, flash

# ========================= AUTH =========================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip()
        senha = request.form.get('senha', '')

        if not usuario or not senha:
            flash('Informe o usuário e a senha.', 'warning')
            return render_template('login.html')

        user = Usuario.query.filter_by(
            usuario=usuario,
            ativo=True
        ).first()

        if user and check_password_hash(user.senha_hash, senha):
            session.clear()

            session['usuario_id'] = user.id
            session['usuario'] = user.usuario
            session['nome'] = user.nome
            session['perfil'] = (
                user.perfil or 'usuario'
            ).strip().lower()

            try:
                auditar(
                    'Login',
                    'usuarios',
                    user.id,
                    'Usuário acessou o sistema'
                )
            except Exception as erro:
                app.logger.error(
                    f'Erro ao registrar auditoria de login: {erro}'
                )

            return redirect(url_for('dashboard'))

        flash('Usuário ou senha inválidos.', 'danger')

    return render_template('login.html')
    
@app.route('/logout')
def logout():
    auditar('Logout')
    session.clear()
    return redirect(url_for('login'))

# ========================= DASHBOARD =========================
@app.route('/')
@login_required
def dashboard():
    hoje = date.today()

    total_quartos = Quarto.query.count()

    livres = Quarto.query.filter_by(status='Livre').count()
    ocupados = Quarto.query.filter(
        Quarto.status.in_(['Parcialmente ocupado', 'Lotado', 'Ocupado'])
    ).count()
    reservados = Quarto.query.filter_by(status='Reservado').count()
    limpeza = Quarto.query.filter_by(status='Em limpeza').count()
    manutencao = Quarto.query.filter_by(status='Em manutenção').count()

    checkins = Reserva.query.filter(
        db.func.date(Reserva.data_entrada) == hoje
    ).count()

    checkouts = Reserva.query.filter(
        db.func.date(Reserva.data_saida) == hoje
    ).count()

    total_hospedes = Hospede.query.count()
    total_reservas = Reserva.query.count()

    receita = db.session.query(
        db.func.coalesce(db.func.sum(Pagamento.valor), 0)
    ).filter(
        Pagamento.status == 'Pago'
    ).scalar()

    ocupacao = round((ocupados / total_quartos) * 100, 1) if total_quartos else 0

    proximas = Reserva.query.order_by(
        Reserva.data_entrada.asc()
    ).limit(8).all()

    cards = {
        'quartos': total_quartos,
        'livres': livres,
        'ocupados': ocupados,
        'reservados': reservados,
        'limpeza': limpeza,
        'manutencao': manutencao,
        'checkins': checkins,
        'checkouts': checkouts,
        'hospedes': total_hospedes,
        'reservas': total_reservas,
    }

    return render_template(
        'dashboard.html',
        cards=cards,
        receita=receita,
        ocupacao=ocupacao,
        proximas=proximas
    )
    
# ========================= CRUD GENERIC PAGES =========================
@app.route('/hospedes', methods=['GET','POST'])
@login_required
@permissao_necessaria('hospedes')
def hospedes():
    if request.method == 'POST':
        h = Hospede(**{k: request.form.get(k) for k in ['nome','cpf','rg','passaporte','nascimento','telefone','whatsapp','email','nacionalidade','endereco','cidade','estado','cep','profissao','empresa','observacoes']})
        db.session.add(h); db.session.commit(); auditar('Criou hóspede','hospedes',h.id,h.nome); flash('Hóspede salvo.', 'success'); return redirect(url_for('hospedes'))
    q = request.args.get('q','')
    dados = Hospede.query.filter(Hospede.nome.contains(q)).order_by(Hospede.nome).all() if q else Hospede.query.order_by(Hospede.nome).all()
    return render_template('hospedes.html', dados=dados, q=q)

@app.route('/quartos', methods=['GET','POST'])
@login_required
@permissao_necessaria('quartos')
def quartos():
    if request.method == 'POST':
        q = Quarto(numero=request.form.get('numero'), andar=request.form.get('andar'), categoria=request.form.get('categoria'), capacidade=int(request.form.get('capacidade') or 1), valor_diaria=float(request.form.get('valor_diaria') or 0), status=request.form.get('status'), descricao=request.form.get('descricao'), amenidades=request.form.get('amenidades'))
        db.session.add(q); db.session.commit(); auditar('Criou quarto','quartos',q.id,q.numero); flash('Quarto salvo.', 'success'); return redirect(url_for('quartos'))
    dados = Quarto.query.order_by(Quarto.numero).all()

    ocupacoes = {}
    for quarto in dados:
        atualizar_status_quarto(quarto)
        ocupacoes[quarto.id] = lotacao_quarto(quarto)

    db.session.commit()

    return render_template(
        'quartos.html',
        dados=dados,
        ocupacoes=ocupacoes
    )


@app.route('/quartos/excluir-todos', methods=['POST'])
@login_required
@admin_required
def excluir_todos_quartos():
    """Exclui todos os quartos e os registros operacionais vinculados."""
    try:
        quartos_ids = [
            item[0]
            for item in db.session.query(Quarto.id).all()
        ]

        reservas_ids = [
            item[0]
            for item in db.session.query(Reserva.id).all()
        ]

        if not quartos_ids:
            flash('Não existem quartos cadastrados para excluir.', 'warning')
            return redirect(url_for('quartos'))

        if reservas_ids:
            Pagamento.query.filter(
                Pagamento.reserva_id.in_(reservas_ids)
            ).delete(synchronize_session=False)

            Consumo.query.filter(
                Consumo.reserva_id.in_(reservas_ids)
            ).delete(synchronize_session=False)

            MovimentacaoCartao.query.filter(
                MovimentacaoCartao.reserva_id.in_(reservas_ids)
            ).delete(synchronize_session=False)

        MovimentacaoCartao.query.filter(
            MovimentacaoCartao.quarto_id.in_(quartos_ids)
        ).delete(synchronize_session=False)

        Reserva.query.delete(synchronize_session=False)
        Limpeza.query.delete(synchronize_session=False)
        Manutencao.query.delete(synchronize_session=False)
        Quarto.query.delete(synchronize_session=False)

        Cartao.query.filter_by(status='Em uso').update(
            {'status': 'Disponível'},
            synchronize_session=False
        )

        db.session.commit()

        try:
            auditar(
                'Excluiu todos os quartos',
                'quartos',
                '',
                (
                    f'{len(quartos_ids)} quarto(s) removido(s), com reservas '
                    'e demais registros operacionais vinculados.'
                )
            )
        except Exception as erro_auditoria:
            app.logger.error(
                f'Erro ao registrar auditoria da exclusão total: {erro_auditoria}'
            )

        flash(
            f'{len(quartos_ids)} quarto(s) e seus registros vinculados foram excluídos.',
            'success'
        )

    except Exception as erro:
        db.session.rollback()
        app.logger.exception('Erro ao excluir todos os quartos.')
        flash(
            f'Não foi possível excluir os quartos: {erro}',
            'danger'
        )

    return redirect(url_for('quartos'))


@app.route('/reservas', methods=['GET', 'POST'])
@login_required
@permissao_necessaria('reservas')
def reservas():

    busca = (request.args.get('q') or '').strip()

    if request.method == 'POST':
        quarto_id = request.form.get('quarto_id', type=int)
        hospede_id = request.form.get('hospede_id', type=int)
        entrada = (request.form.get('data_entrada') or '').strip()
        saida = (request.form.get('data_saida') or '').strip()

        if not quarto_id or not hospede_id:
            flash(
                'Selecione o colaborador e o quarto.',
                'danger'
            )
            return redirect(url_for('reservas'))

        if not entrada or not saida:
            flash(
                'Informe a data e hora de entrada e saída.',
                'danger'
            )
            return redirect(url_for('reservas'))

        try:
            dt_entrada = datetime.fromisoformat(entrada)
            dt_saida = datetime.fromisoformat(saida)

        except ValueError:
            flash(
                'Data de entrada ou saída inválida.',
                'danger'
            )
            return redirect(url_for('reservas'))

        if dt_saida <= dt_entrada:
            flash(
                'A saída deve ser maior que a entrada.',
                'danger'
            )
            return redirect(url_for('reservas'))

        quarto = Quarto.query.get_or_404(quarto_id)
        hospede = Hospede.query.get_or_404(hospede_id)

        if quarto.status in [
            'Em manutenção',
            'Bloqueado',
            'Em limpeza'
        ]:
            flash(
                (
                    f'O quarto {quarto.numero} está indisponível: '
                    f'{quarto.status}.'
                ),
                'danger'
            )
            return redirect(url_for('reservas'))

        vagas_periodo = vagas_no_periodo(
            quarto,
            entrada,
            saida
        )

        if vagas_periodo <= 0:
            flash(
                (
                    f'O quarto {quarto.numero} está lotado '
                    'nesse período.'
                ),
                'danger'
            )
            return redirect(url_for('reservas'))

        # Evita duas reservas ativas para o mesmo colaborador
        reserva_ativa_colaborador = (
            Reserva.query
            .filter(
                Reserva.hospede_id == hospede.id,
                Reserva.status.in_([
                    'Reservado',
                    'Hospedado'
                ])
            )
            .first()
        )

        if reserva_ativa_colaborador:
            flash(
                (
                    f'O colaborador {hospede.nome} já possui uma '
                    f'reserva ativa no quarto '
                    f'{reserva_ativa_colaborador.quarto.numero}.'
                ),
                'warning'
            )
            return redirect(url_for('reservas'))

        try:
            valor_diaria = float(
                request.form.get('valor_diaria') or 0
            )

            desconto = float(
                request.form.get('desconto') or 0
            )

        except ValueError:
            flash(
                'Valor da diária ou desconto inválido.',
                'danger'
            )
            return redirect(url_for('reservas'))

        if valor_diaria < 0 or desconto < 0:
            flash(
                'O valor da diária e o desconto não podem ser negativos.',
                'danger'
            )
            return redirect(url_for('reservas'))

        horas = (
            dt_saida - dt_entrada
        ).total_seconds() / 3600

        diarias = max(
            1,
            int((horas + 23) // 24)
        )

        valor_total = max(
            0,
            (diarias * valor_diaria) - desconto
        )

        observacoes = (
            request.form.get('observacoes')
            or ''
        ).strip()

        calculo_automatico = (
            f'Cálculo automático: {horas:.1f} horas | '
            f'{diarias} diária(s) | '
            f'Total R$ {valor_total:.2f}'
        )

        if observacoes:
            observacoes += f'\n\n{calculo_automatico}'
        else:
            observacoes = calculo_automatico

        reserva = Reserva(
            hospede_id=hospede.id,
            quarto_id=quarto.id,
            data_entrada=entrada,
            data_saida=saida,
            status='Reservado',
            valor_diaria=valor_diaria,
            desconto=desconto,
            observacoes=observacoes
        )

        try:
            db.session.add(reserva)
            db.session.flush()

            atualizar_status_quarto(quarto)

            db.session.commit()

        except Exception as erro:
            db.session.rollback()

            flash(
                f'Erro ao criar a reserva: {erro}',
                'danger'
            )
            return redirect(url_for('reservas'))

        vagas_restantes = vagas_no_periodo(
            quarto,
            entrada,
            saida
        )

        auditar(
            'Criou reserva',
            'reservas',
            reserva.id,
            (
                f'Colaborador {hospede.nome} | '
                f'Quarto {quarto.numero} | '
                f'{diarias} diária(s) | '
                f'R$ {valor_total:.2f} | '
                f'{vagas_restantes} vaga(s) restante(s)'
            )
        )

        flash(
            (
                f'Reserva criada para {hospede.nome} '
                f'no quarto {quarto.numero}. '
                f'Agora restam {vagas_restantes} '
                'vaga(s) nesse período.'
            ),
            'success'
        )

        return redirect(url_for('reservas'))

    # ========================================================
    # PESQUISA POR NOME OU CPF
    # ========================================================

    query = (
        Reserva.query
        .join(
            Hospede,
            Hospede.id == Reserva.hospede_id
        )
    )

    if busca:
        cpf_busca = ''.join(
            caractere
            for caractere in busca
            if caractere.isdigit()
        )

        filtros = [
            Hospede.nome.ilike(f'%{busca}%')
        ]

        if cpf_busca:
            cpf_sem_mascara = db.func.replace(
                db.func.replace(
                    db.func.replace(
                        Hospede.cpf,
                        '.',
                        ''
                    ),
                    '-',
                    ''
                ),
                ' ',
                ''
            )

            filtros.append(
                cpf_sem_mascara.ilike(
                    f'%{cpf_busca}%'
                )
            )

        query = query.filter(
            db.or_(*filtros)
        )

    dados = (
        query
        .order_by(
            Reserva.data_entrada.desc()
        )
        .all()
    )

    quartos_lista = (
        Quarto.query
        .order_by(
            Quarto.numero.asc()
        )
        .all()
    )

    # Recalcula a situação atual antes de montar a lista. Assim, uma reserva
    # futura não deixa o quarto como Lotado antes da data de entrada.
    for quarto in quartos_lista:
        atualizar_status_quarto(quarto)

    db.session.commit()

    ocupacoes = {
        quarto.id: lotacao_quarto(quarto)
        for quarto in quartos_lista
    }

    # Agenda ativa por quarto para o aviso exibido ao selecionar um quarto.
    # Inclui hospedagens em andamento e reservas que ainda não terminaram.
    agora_iso = datetime.now().isoformat(timespec='minutes')
    agenda_quartos = {str(quarto.id): [] for quarto in quartos_lista}

    reservas_agendadas = (
        Reserva.query
        .filter(
            Reserva.status.in_(['Reservado', 'Hospedado']),
            Reserva.data_saida > agora_iso
        )
        .order_by(Reserva.data_entrada.asc())
        .all()
    )

    for reserva in reservas_agendadas:
        agenda_quartos.setdefault(str(reserva.quarto_id), []).append({
            'id': reserva.id,
            'hospede': reserva.hospede.nome,
            'entrada': reserva.data_entrada,
            'saida': reserva.data_saida,
            'status': reserva.status,
        })

    return render_template(
        'reservas.html',
        dados=dados,
        hospedes=(
            Hospede.query
            .order_by(
                Hospede.nome.asc()
            )
            .all()
        ),
        quartos=quartos_lista,
        ocupacoes=ocupacoes,
        agenda_quartos=agenda_quartos,
        q=busca
    )

@app.route('/calendario')
@login_required
@permissao_necessaria('calendario')
def calendario():
    reservas = Reserva.query.order_by(Reserva.data_entrada).all()
    return render_template('calendario.html', reservas=reservas, quartos=Quarto.query.order_by(Quarto.numero).all())

@app.route('/checkin/<int:id>')
@login_required
@permissao_necessaria('reservas')
def checkin(id):
    reserva = Reserva.query.get_or_404(id)

    if reserva.status != 'Reservado':
        flash('Esta reserva não está disponível para check-in.', 'warning')
        return redirect(url_for('reservas'))

    reserva.status = 'Hospedado'
    atualizar_status_quarto(reserva.quarto)
    db.session.commit()

    auditar(
        'Check-in',
        'reservas',
        reserva.id,
        f'Colaborador {reserva.hospede.nome} - Quarto {reserva.quarto.numero}'
    )

    lotacao = lotacao_quarto(reserva.quarto)
    flash(
        (
            f'Check-in realizado. Quarto {reserva.quarto.numero}: '
            f'{lotacao["ocupados"]} ocupado(s) e {lotacao["vagas"]} vaga(s).'
        ),
        'success'
    )
    return redirect(url_for('reservas'))


@app.route('/checkout/<int:id>', methods=['GET','POST'])
@login_required
@permissao_necessaria('reservas')
def checkout(id):
    reserva = Reserva.query.get_or_404(id)
    consumos = Consumo.query.filter_by(reserva_id=id).all()
    total_consumos = sum(c.quantidade * c.valor_unitario for c in consumos)

    if request.method == 'POST':
        pendencia_cartao = cartao_pendente_reserva(reserva.id)

        if pendencia_cartao:
            flash(
                (
                    'Não foi possível finalizar o check-out: o cartão '
                    f'{pendencia_cartao.cartao.codigo} ainda está em uso. '
                    'Registre a devolução, perda ou dano.'
                ),
                'danger'
            )
            return redirect(url_for('cartoes', q=pendencia_cartao.cartao.codigo))

        valor = float(request.form.get('valor') or 0)
        tipo = request.form.get('tipo')

        db.session.add(Pagamento(
            reserva_id=id,
            tipo=tipo,
            valor=valor,
            status='Pago'
        ))

        reserva.status = 'Finalizado'
        db.session.flush()

        foi_para_limpeza = gerar_limpeza_se_necessario(
            reserva.quarto,
            'Gerado automaticamente após a saída do último colaborador.'
        )

        db.session.commit()

        auditar(
            'Check-out',
            'reservas',
            reserva.id,
            f'Pagamento {tipo} R$ {valor:.2f}'
        )

        mensagem = 'Check-out finalizado.'
        if foi_para_limpeza:
            mensagem += ' O quarto foi enviado para limpeza.'
        else:
            lotacao = lotacao_quarto(reserva.quarto)
            mensagem += (
                f' O quarto continua com {lotacao["ocupados"]} ocupado(s), '
                f'{lotacao["reservados"]} reservado(s) e '
                f'{lotacao["vagas"]} vaga(s).'
            )

        flash(mensagem, 'success')
        return redirect(url_for('reservas'))

    dias = max(
        1,
        (datetime.fromisoformat(reserva.data_saida) -
         datetime.fromisoformat(reserva.data_entrada)).days
    )
    total_diarias = dias * reserva.valor_diaria - (reserva.desconto or 0)
    total = total_diarias + total_consumos

    return render_template(
        'checkout.html',
        r=reserva,
        consumos=consumos,
        total_consumos=total_consumos,
        total_diarias=total_diarias,
        total=total
    )


@app.route('/consumos', methods=['GET','POST'])
@login_required
@permissao_necessaria('reservas')
def consumos():
    if request.method == 'POST':
        c = Consumo(reserva_id=int(request.form.get('reserva_id')), produto=request.form.get('produto'), quantidade=int(request.form.get('quantidade') or 1), valor_unitario=float(request.form.get('valor_unitario') or 0), setor=request.form.get('setor'))
        db.session.add(c); db.session.commit(); auditar('Lançou consumo','consumos',c.id,c.produto); flash('Consumo lançado.', 'success'); return redirect(url_for('consumos'))
    return render_template('consumos.html', dados=Consumo.query.order_by(Consumo.data.desc()).all(), reservas=Reserva.query.filter_by(status='Hospedado').all())

@app.route('/quarto_detalhes/<int:id>')
@login_required
@permissao_necessaria('quartos')
def quarto_detalhes(id):
    quarto = Quarto.query.get_or_404(id)

    reservas = Reserva.query.filter_by(quarto_id=id).order_by(Reserva.id.desc()).all()

    historico = []

    for r in reservas:
        hospede_nome = '-'

        if hasattr(r, 'hospede') and r.hospede:
            hospede_nome = r.hospede.nome
        elif hasattr(r, 'hospede_nome'):
            hospede_nome = r.hospede_nome

        historico.append({
            'hospede': hospede_nome,
            'checkin': r.checkin.strftime('%d/%m/%Y') if r.checkin else '',
            'checkout': r.checkout.strftime('%d/%m/%Y') if r.checkout else '',
            'status': r.status if hasattr(r, 'status') else ''
        })

    return jsonify({
        'quarto': {
            'id': quarto.id,
            'numero': quarto.numero,
            'tipo': quarto.tipo,
            'status': quarto.status,
            'andar': quarto.andar,
            'capacidade': quarto.capacidade,
            'valor_diaria': f'{quarto.valor_diaria:.2f}' if quarto.valor_diaria else '0.00',
            'descricao': quarto.descricao
        },
        'historico': historico
    })

@app.route('/quarto_historico/<int:quarto_id>')
@login_required
@permissao_necessaria('quartos')
def quarto_historico(quarto_id):
    quarto = Quarto.query.get_or_404(quarto_id)

    reservas = Reserva.query.filter_by(quarto_id=quarto.id).order_by(Reserva.id.desc()).all()

    historico = []

    for r in reservas:
        nome_hospede = '-'

        if hasattr(r, 'hospede') and r.hospede:
            nome_hospede = r.hospede.nome
        elif hasattr(r, 'hospede_nome'):
            nome_hospede = r.hospede_nome

        entrada = getattr(r, 'entrada', None) or getattr(r, 'checkin', None)
        saida = getattr(r, 'saida', None) or getattr(r, 'checkout', None)

        diaria = getattr(r, 'diaria', None) or getattr(r, 'valor_diaria', None)

        historico.append({
            'hospede': nome_hospede,
            'entrada': entrada.strftime('%d/%m/%Y %H:%M') if entrada else '-',
            'saida': saida.strftime('%d/%m/%Y %H:%M') if saida else '-',
            'diaria': f'{diaria:.2f}' if diaria else '0.00',
            'status': getattr(r, 'status', '-') or '-'
        })

    return jsonify({
        'quarto': quarto.numero,
        'historico': historico
    })

@app.route('/bloquear_quarto/<int:id>')
@login_required
@permissao_necessaria('quartos')
def bloquear_quarto(id):

    quarto = Quarto.query.get_or_404(id)

    quarto.status = "Bloqueado"

    db.session.commit()

    flash("Quarto bloqueado com sucesso!", "success")

    return redirect(url_for("quartos"))

@app.route('/quarto_status/<int:id>/<status>', methods=['POST'])
@login_required
@permissao_necessaria('quartos')
def quarto_status(id, status):
    quarto = Quarto.query.get_or_404(id)

    status_permitidos = [
        'Livre',
        'Reservado',
        'Ocupado',
        'Parcialmente ocupado',
        'Lotado',
        'Em limpeza',
        'Limpo',
        'Em manutenção',
        'Bloqueado'
    ]

    if status not in status_permitidos:
        flash('Status inválido.', 'danger')
        return redirect(url_for('quartos'))

    quarto.status = status
    db.session.commit()

    flash(f'Quarto {quarto.numero} atualizado para {status}.', 'success')
    return redirect(url_for('quartos'))

@app.route('/desbloquear_quarto/<int:id>')
@login_required
@permissao_necessaria('quartos')
def desbloquear_quarto(id):

    quarto = Quarto.query.get_or_404(id)

    quarto.status = "Livre"

    db.session.commit()

    flash("Quarto liberado com sucesso!", "success")

    return redirect(url_for("quartos"))

@app.route('/limpeza', methods=['GET', 'POST'])
@login_required
@permissao_necessaria('governanca')
def limpeza():

    if request.method == 'POST':

        quarto_id = int(request.form.get('quarto_id'))

        quarto = Quarto.query.get_or_404(quarto_id)

        l = Limpeza(
            quarto_id=quarto_id,
            responsavel=request.form.get('responsavel'),
            status=request.form.get('status'),
            checklist=request.form.get('checklist'),
            observacoes=request.form.get('observacoes')
        )

        # Atualiza automaticamente o status do quarto
        if l.status == 'Aguardando':
            quarto.status = 'Em limpeza'

        elif l.status == 'Em limpeza':
            quarto.status = 'Em limpeza'

        elif l.status in ['Limpo', 'Inspecionado']:
            quarto.status = 'Livre'
            atualizar_status_quarto(quarto)

        db.session.add(l)
        db.session.commit()

        auditar(
            'Registrou limpeza',
            'limpeza',
            l.id,
            f'Quarto {quarto.numero} - {l.status}'
        )

        flash('Limpeza registrada com sucesso.', 'success')
        return redirect(url_for('limpeza'))

    # Apenas quartos aguardando limpeza
    quartos = (
        Quarto.query
        .filter_by(status='Em limpeza')
        .order_by(Quarto.numero)
        .all()
    )

    dados = (
        Limpeza.query
        .order_by(Limpeza.data.desc())
        .all()
    )

    return render_template(
        'limpeza.html',
        dados=dados,
        quartos=quartos
    )

@app.route('/manutencao', methods=['GET','POST'])
@login_required
@permissao_necessaria('manutencao')
def manutencao():
    if request.method == 'POST':
        m = Manutencao(quarto_id=int(request.form.get('quarto_id')), problema=request.form.get('problema'), prioridade=request.form.get('prioridade'), status=request.form.get('status'), responsavel=request.form.get('responsavel'), custo=float(request.form.get('custo') or 0), observacoes=request.form.get('observacoes'))
        quarto = Quarto.query.get(m.quarto_id); quarto.status = 'Em manutenção' if m.status != 'Finalizado' else 'Livre'
        db.session.add(m); db.session.commit(); auditar('Registrou manutenção','manutencao',m.id,m.problema); flash('Manutenção registrada.', 'success'); return redirect(url_for('manutencao'))
    return render_template('manutencao.html', dados=Manutencao.query.order_by(Manutencao.data.desc()).all(), quartos=Quarto.query.order_by(Quarto.numero).all())

@app.route('/produtos', methods=['GET','POST'])
@login_required
@permissao_necessaria('estoque')
def produtos():
    if request.method == 'POST':
        p = Produto(nome=request.form.get('nome'), categoria=request.form.get('categoria'), estoque=int(request.form.get('estoque') or 0), estoque_minimo=int(request.form.get('estoque_minimo') or 0), valor=float(request.form.get('valor') or 0))
        db.session.add(p); db.session.commit(); flash('Produto salvo.', 'success'); return redirect(url_for('produtos'))
    return render_template('produtos.html', dados=Produto.query.order_by(Produto.nome).all())

@app.route('/funcionarios', methods=['GET','POST'])
@login_required
@permissao_necessaria('funcionarios')
def funcionarios():
    if request.method == 'POST':
        f = Funcionario(nome=request.form.get('nome'), cpf=request.form.get('cpf'), cargo=request.form.get('cargo'), setor=request.form.get('setor'), telefone=request.form.get('telefone'), email=request.form.get('email'), status=request.form.get('status'))
        db.session.add(f); db.session.commit(); flash('Funcionário salvo.', 'success'); return redirect(url_for('funcionarios'))
    return render_template('funcionarios.html', dados=Funcionario.query.order_by(Funcionario.nome).all())

@app.route('/empresas', methods=['GET','POST'])
@login_required
@permissao_necessaria('empresas')
def empresas():
    if request.method == 'POST':
        e = Empresa(nome=request.form.get('nome'), cnpj=request.form.get('cnpj'), contato=request.form.get('contato'), telefone=request.form.get('telefone'), email=request.form.get('email'), tarifa_especial=float(request.form.get('tarifa_especial') or 0))
        db.session.add(e); db.session.commit(); flash('Empresa/agência salva.', 'success'); return redirect(url_for('empresas'))
    return render_template('empresas.html', dados=Empresa.query.order_by(Empresa.nome).all())

@app.route('/relatorios')
@login_required
@permissao_necessaria('relatorios')
def relatorios():
    receita = sum(p.valor for p in Pagamento.query.filter_by(status='Pago').all())
    despesas = sum(m.custo for m in Manutencao.query.all())
    consumos_total = sum(c.quantidade*c.valor_unitario for c in Consumo.query.all())
    return render_template('relatorios.html', receita=receita, despesas=despesas, consumos_total=consumos_total)

@app.route('/auditoria')
@login_required
@permissao_necessaria('auditoria')
def auditoria():
    return render_template('auditoria.html', dados=Auditoria.query.order_by(Auditoria.data.desc()).limit(300).all())

@app.route('/usuarios', methods=['GET','POST'])
@login_required
@admin_required
def usuarios():
    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        nome_usuario = (request.form.get('usuario') or '').strip()
        senha = request.form.get('senha') or ''
        perfil = (request.form.get('perfil') or '').strip()

        if not nome or not nome_usuario or not senha:
            flash('Preencha nome, usuário e senha.', 'warning')
            return redirect(url_for('usuarios'))

        if perfil not in PERFIS_DISPONIVEIS:
            flash('Selecione um perfil válido: Administrador, RH ou Porteiro.', 'danger')
            return redirect(url_for('usuarios'))

        if Usuario.query.filter(
            db.func.lower(Usuario.usuario) == nome_usuario.lower()
        ).first():
            flash('Este nome de usuário já está cadastrado.', 'danger')
            return redirect(url_for('usuarios'))

        novo_usuario = Usuario(
            nome=nome,
            usuario=nome_usuario,
            senha_hash=generate_password_hash(senha),
            perfil=perfil,
            ativo=True
        )

        db.session.add(novo_usuario)
        db.session.commit()
        auditar(
            'Criou usuário',
            'usuarios',
            novo_usuario.id,
            f'{novo_usuario.nome} - perfil {novo_usuario.perfil}'
        )
        flash('Usuário criado com sucesso.', 'success')
        return redirect(url_for('usuarios'))

    return render_template(
        'usuarios.html',
        dados=Usuario.query.order_by(Usuario.nome).all(),
        perfis=PERFIS_DISPONIVEIS
    )



# ========================= CONTROLE DE CARTÕES =========================
def movimentacao_ativa_cartao(cartao_id):
    return (
        MovimentacaoCartao.query
        .filter_by(cartao_id=cartao_id, tipo='Entrega')
        .order_by(MovimentacaoCartao.data_evento.desc())
        .first()
    )


def cartao_pendente_reserva(reserva_id):
    return (
        db.session.query(MovimentacaoCartao)
        .join(Cartao, Cartao.id == MovimentacaoCartao.cartao_id)
        .filter(
            MovimentacaoCartao.reserva_id == reserva_id,
            Cartao.status == 'Em uso'
        )
        .order_by(MovimentacaoCartao.data_evento.desc())
        .first()
    )


@app.route('/cartoes', methods=['GET', 'POST'])
@login_required
@permissao_necessaria('cartoes')
def cartoes():
    if request.method == 'POST':
        codigo = (request.form.get('codigo') or '').strip().upper()
        descricao = (request.form.get('descricao') or '').strip()
        observacoes = (request.form.get('observacoes') or '').strip()

        if not codigo:
            flash('Informe o código do cartão.', 'danger')
            return redirect(url_for('cartoes'))

        if Cartao.query.filter(db.func.lower(Cartao.codigo) == codigo.lower()).first():
            flash(f'O cartão {codigo} já está cadastrado.', 'danger')
            return redirect(url_for('cartoes'))

        cartao = Cartao(
            codigo=codigo,
            descricao=descricao,
            status='Disponível',
            criado_por=session.get('nome') or session.get('usuario', 'sistema'),
            observacoes=observacoes
        )
        db.session.add(cartao)
        db.session.flush()

        db.session.add(MovimentacaoCartao(
            cartao_id=cartao.id,
            tipo='Cadastro',
            status_resultante='Disponível',
            usuario_responsavel=session.get('nome') or session.get('usuario', 'sistema'),
            observacoes=observacoes or 'Cartão cadastrado no sistema.'
        ))
        db.session.commit()

        auditar('Cadastrou cartão', 'cartoes', cartao.id, f'Cartão {cartao.codigo}')
        flash(f'Cartão {cartao.codigo} cadastrado com sucesso.', 'success')
        return redirect(url_for('cartoes'))

    busca = (request.args.get('q') or '').strip()
    status = (request.args.get('status') or '').strip()

    query = Cartao.query
    if busca:
        termo = f'%{busca}%'
        query = query.filter(db.or_(
            Cartao.codigo.ilike(termo),
            Cartao.descricao.ilike(termo)
        ))
    if status:
        query = query.filter(Cartao.status == status)

    dados = query.order_by(Cartao.codigo.asc()).all()
    reservas_ativas = (
        Reserva.query
        .filter(Reserva.status.in_(['Reservado', 'Hospedado']))
        .order_by(Reserva.data_entrada.desc())
        .all()
    )

    cards = {
        'total': Cartao.query.count(),
        'disponiveis': Cartao.query.filter_by(status='Disponível').count(),
        'em_uso': Cartao.query.filter_by(status='Em uso').count(),
        'perdidos': Cartao.query.filter_by(status='Perdido').count(),
        'danificados': Cartao.query.filter_by(status='Danificado').count(),
        'bloqueados': Cartao.query.filter_by(status='Bloqueado').count(),
    }

    ultimas_movimentacoes = (
        MovimentacaoCartao.query
        .order_by(MovimentacaoCartao.data_evento.desc())
        .limit(20)
        .all()
    )

    return render_template(
        'cartoes.html',
        dados=dados,
        reservas=reservas_ativas,
        cards=cards,
        ultimas_movimentacoes=ultimas_movimentacoes,
        q=busca,
        status_filtro=status
    )


@app.route('/cartoes/<int:cartao_id>/devolver', methods=['POST'])
@login_required
@permissao_necessaria('cartoes')
def devolver_cartao(cartao_id):
    cartao = Cartao.query.get_or_404(cartao_id)

    ultima_movimentacao = (
        MovimentacaoCartao.query
        .filter_by(cartao_id=cartao.id)
        .order_by(MovimentacaoCartao.data_evento.desc())
        .first()
    )

    if not ultima_movimentacao:
        flash('Este cartão não possui movimentação registrada.', 'warning')
        return redirect(url_for('cartoes'))

    if cartao.status != 'Em uso':
        flash(f'O cartão {cartao.codigo} não está em uso.', 'warning')
        return redirect(url_for('cartoes'))

    if ultima_movimentacao.tipo != 'Entrega':
        flash('A última movimentação deste cartão não é uma entrega.', 'warning')
        return redirect(url_for('cartoes'))

    usuario_atual = (
        session.get('nome')
        or session.get('usuario')
        or 'Sistema'
    )

    observacoes = (
        request.form.get('observacoes')
        or 'Cartão devolvido em bom estado.'
    ).strip()

    reserva = ultima_movimentacao.reserva
    hospede = ultima_movimentacao.hospede
    quarto = ultima_movimentacao.quarto

    cartao.status = 'Disponível'

    db.session.add(MovimentacaoCartao(
        cartao_id=cartao.id,
        reserva_id=ultima_movimentacao.reserva_id,
        hospede_id=ultima_movimentacao.hospede_id,
        quarto_id=ultima_movimentacao.quarto_id,
        tipo='Devolução',
        status_resultante='Disponível',
        usuario_responsavel=usuario_atual,
        data_evento=datetime.now(),
        observacoes=observacoes
    ))

    foi_para_limpeza = False

    # Na operação do hotel, devolver o cartão significa check-out.
    if reserva and reserva.status in ['Reservado', 'Hospedado']:
        reserva.status = 'Finalizado'
        db.session.flush()

        if quarto:
            foi_para_limpeza = gerar_limpeza_se_necessario(
                quarto,
                (
                    'Limpeza gerada automaticamente após a saída do '
                    f'último colaborador. Cartão {cartao.codigo}.'
                )
            )

    db.session.commit()

    nome_hospede = hospede.nome if hospede else 'Sem colaborador'
    numero_quarto = quarto.numero if quarto else 'Sem quarto'

    auditar(
        'Devolveu cartão e realizou check-out',
        'cartoes',
        cartao.id,
        (
            f'Cartão {cartao.codigo} devolvido por {nome_hospede}. '
            f'Quarto {numero_quarto}. Reserva finalizada automaticamente.'
        )
    )

    if quarto and not foi_para_limpeza:
        lotacao = lotacao_quarto(quarto)
        flash(
            (
                f'Cartão {cartao.codigo} devolvido e check-out realizado. '
                f'O quarto {quarto.numero} continua com '
                f'{lotacao["ocupados"]} ocupado(s), '
                f'{lotacao["reservados"]} reservado(s) e '
                f'{lotacao["vagas"]} vaga(s).'
            ),
            'success'
        )
    else:
        flash(
            (
                f'Cartão {cartao.codigo} devolvido e check-out realizado. '
                'Como não restaram colaboradores, o quarto foi enviado '
                'para limpeza.'
            ),
            'success'
        )

    return redirect(url_for('cartoes'))


@app.route('/cartoes/<int:cartao_id>/ocorrencia', methods=['POST'])
@login_required
@permissao_necessaria('cartoes')
def ocorrencia_cartao(cartao_id):
    cartao = Cartao.query.get_or_404(cartao_id)
    tipo = (request.form.get('tipo') or '').strip()
    observacoes = (request.form.get('observacoes') or '').strip()

    mapa_status = {
        'Perda': 'Perdido',
        'Dano': 'Danificado',
        'Bloqueio': 'Bloqueado',
        'Liberar': 'Disponível',
    }

    if tipo not in mapa_status:
        flash('Tipo de ocorrência inválido.', 'danger')
        return redirect(url_for('cartoes'))

    if tipo in ('Perda', 'Dano', 'Bloqueio') and not observacoes:
        flash('Informe uma observação para registrar a ocorrência.', 'danger')
        return redirect(url_for('cartoes'))

    ultima = movimentacao_ativa_cartao(cartao.id)
    novo_status = mapa_status[tipo]
    cartao.status = novo_status

    db.session.add(MovimentacaoCartao(
        cartao_id=cartao.id,
        reserva_id=ultima.reserva_id if ultima else None,
        hospede_id=ultima.hospede_id if ultima else None,
        quarto_id=ultima.quarto_id if ultima else None,
        tipo=tipo,
        status_resultante=novo_status,
        usuario_responsavel=session.get('nome') or session.get('usuario', 'sistema'),
        observacoes=observacoes
    ))
    db.session.commit()

    auditar(f'Registrou {tipo.lower()} de cartão', 'cartoes', cartao.id, f'{cartao.codigo}: {observacoes}')
    flash(f'Cartão {cartao.codigo} atualizado para {novo_status}.', 'success')
    return redirect(url_for('cartoes'))


@app.route('/cartoes/<int:cartao_id>/historico')
@login_required
@permissao_necessaria('cartoes')
def historico_cartao(cartao_id):
    cartao = Cartao.query.get_or_404(cartao_id)
    movimentacoes = (
        MovimentacaoCartao.query
        .filter_by(cartao_id=cartao.id)
        .order_by(MovimentacaoCartao.data_evento.desc())
        .all()
    )
    return render_template('historico_cartao.html', cartao=cartao, movimentacoes=movimentacoes)

@app.route('/cartoes/buscar-colaborador')
@login_required
@permissao_necessaria('cartoes')
def buscar_colaborador_cartao():
    termo = (request.args.get('q') or '').strip()

    if len(termo) < 2:
        return jsonify([])

    termo_cpf = ''.join(numero for numero in termo if numero.isdigit())

    query = (
        Reserva.query
        .join(Hospede, Hospede.id == Reserva.hospede_id)
        .join(Quarto, Quarto.id == Reserva.quarto_id)
        .filter(
            Reserva.status.in_([
                'Reservado',
                'Hospedado'
            ])
        )
    )

    filtros = [
        Hospede.nome.ilike(f'%{termo}%')
    ]

    if termo_cpf:
        cpf_sem_mascara = db.func.replace(
            db.func.replace(
                db.func.replace(
                    Hospede.cpf,
                    '.',
                    ''
                ),
                '-',
                ''
            ),
            ' ',
            ''
        )

        filtros.append(
            cpf_sem_mascara.ilike(f'%{termo_cpf}%')
        )

    reservas = (
        query
        .filter(db.or_(*filtros))
        .order_by(Hospede.nome.asc())
        .limit(20)
        .all()
    )

    resultado = []

    for reserva in reservas:
        cartao_em_uso = (
            db.session.query(MovimentacaoCartao)
            .join(
                Cartao,
                Cartao.id == MovimentacaoCartao.cartao_id
            )
            .filter(
                MovimentacaoCartao.reserva_id == reserva.id,
                Cartao.status == 'Em uso'
            )
            .order_by(
                MovimentacaoCartao.data_evento.desc()
            )
            .first()
        )

        resultado.append({
            'reserva_id': reserva.id,
            'nome': reserva.hospede.nome,
            'cpf': reserva.hospede.cpf or '',
            'quarto': reserva.quarto.numero,
            'status_reserva': reserva.status,
            'cartao_atual': (
                cartao_em_uso.cartao.codigo
                if cartao_em_uso
                else ''
            )
        })

    return jsonify(resultado)


# ============================================================
# ROTA DE ENTREGA CORRIGIDA
# Substitua sua função entregar_cartao() por esta
# ============================================================

@app.route('/cartoes/entregar', methods=['POST'])
@login_required
@permissao_necessaria('cartoes')
def entregar_cartao():
    cartao_id = request.form.get('cartao_id', type=int)
    reserva_id = request.form.get('reserva_id', type=int)
    observacoes = (request.form.get('observacoes') or '').strip()

    if not cartao_id:
        flash('Selecione um cartão disponível.', 'danger')
        return redirect(url_for('cartoes'))

    if not reserva_id:
        flash(
            'Pesquise e selecione um colaborador.',
            'danger'
        )
        return redirect(url_for('cartoes'))

    cartao = Cartao.query.get_or_404(cartao_id)
    reserva = Reserva.query.get_or_404(reserva_id)

    if cartao.status != 'Disponível':
        flash(
            (
                f'O cartão {cartao.codigo} não está disponível. '
                f'Status atual: {cartao.status}.'
            ),
            'danger'
        )
        return redirect(url_for('cartoes'))

    if reserva.status not in ['Reservado', 'Hospedado']:
        flash(
            'A reserva selecionada não está ativa.',
            'danger'
        )
        return redirect(url_for('cartoes'))

    cartao_ja_entregue = (
        db.session.query(MovimentacaoCartao)
        .join(
            Cartao,
            Cartao.id == MovimentacaoCartao.cartao_id
        )
        .filter(
            MovimentacaoCartao.reserva_id == reserva.id,
            Cartao.status == 'Em uso'
        )
        .order_by(
            MovimentacaoCartao.data_evento.desc()
        )
        .first()
    )

    if cartao_ja_entregue:
        flash(
            (
                f'O colaborador já está com o cartão '
                f'{cartao_ja_entregue.cartao.codigo}.'
            ),
            'warning'
        )
        return redirect(url_for('cartoes'))

    cartao.status = 'Em uso'

    movimentacao = MovimentacaoCartao(
        cartao_id=cartao.id,
        reserva_id=reserva.id,
        hospede_id=reserva.hospede_id,
        quarto_id=reserva.quarto_id,
        tipo='Entrega',
        status_resultante='Em uso',
        usuario_responsavel=(
            session.get('nome')
            or session.get('usuario')
            or 'Sistema'
        ),
        data_evento=datetime.now(),
        observacoes=observacoes
    )

    db.session.add(movimentacao)
    db.session.commit()

    auditar(
        'Entregou cartão',
        'cartoes',
        cartao.id,
        (
            f'Cartão {cartao.codigo} entregue para '
            f'{reserva.hospede.nome}, '
            f'CPF {reserva.hospede.cpf or "não informado"}, '
            f'quarto {reserva.quarto.numero}, '
            f'reserva #{reserva.id}.'
        )
    )

    flash(
        (
            f'Cartão {cartao.codigo} entregue para '
            f'{reserva.hospede.nome}, '
            f'quarto {reserva.quarto.numero}.'
        ),
        'success'
    )

    return redirect(url_for('cartoes'))

@app.route('/cartoes/exportar/excel')
@login_required
@permissao_necessaria('cartoes')
def exportar_cartoes_excel():
    dados = []
    for cartao in Cartao.query.order_by(Cartao.codigo).all():
        ultima = cartao.movimentacoes[0] if cartao.movimentacoes else None
        dados.append({
            'Código': cartao.codigo,
            'Descrição': cartao.descricao or '',
            'Status': cartao.status,
            'Criado por': cartao.criado_por,
            'Criado em': cartao.criado_em.strftime('%d/%m/%Y %H:%M'),
            'Última movimentação': ultima.tipo if ultima else '',
            'Último responsável': ultima.usuario_responsavel if ultima else '',
            'Último colaborador': ultima.hospede.nome if ultima and ultima.hospede else '',
            'Último quarto': ultima.quarto.numero if ultima and ultima.quarto else '',
            'Data da última movimentação': ultima.data_evento.strftime('%d/%m/%Y %H:%M') if ultima else '',
        })

    output = BytesIO()
    pd.DataFrame(dados).to_excel(output, index=False)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f'controle_cartoes_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ========================= IMPORT EXPORT BACKUP =========================
MODELOS = {'hospedes': Hospede, 'quartos': Quarto, 'reservas': Reserva, 'produtos': Produto, 'funcionarios': Funcionario, 'empresas': Empresa}

@app.route('/exportar/<modelo>/<tipo>')
@login_required
def exportar(modelo, tipo):
    modulo_modelo = {
        'hospedes': 'hospedes',
        'quartos': 'quartos',
        'reservas': 'reservas',
        'produtos': 'estoque',
        'funcionarios': 'funcionarios',
        'empresas': 'empresas',
    }.get(modelo)

    if not modulo_modelo or not usuario_tem_acesso(modulo_modelo):
        flash('Você não possui permissão para exportar estes dados.', 'danger')
        return redirect(url_for('dashboard'))

    cls = MODELOS.get(modelo)
    if not cls: flash('Exportação inválida.', 'danger'); return redirect(url_for('dashboard'))
    dados = cls.query.all()
    if tipo == 'excel': return export_excel(dados, f'{modelo}.xlsx')
    headers = [c.name for c in cls.__table__.columns]
    rows = [[str(getattr(obj, h) or '') for h in headers] for obj in dados]
    return export_pdf(f'Relatório de {modelo.title()}', headers, rows, f'{modelo}.pdf')

@app.route('/importar/<modelo>', methods=['POST'])
@login_required
def importar(modelo):
    modulo_modelo = {
        'hospedes': 'hospedes',
        'quartos': 'quartos',
        'reservas': 'reservas',
        'produtos': 'estoque',
        'funcionarios': 'funcionarios',
        'empresas': 'empresas',
    }.get(modelo)

    if not modulo_modelo or not usuario_tem_acesso(modulo_modelo):
        flash('Você não possui permissão para importar estes dados.', 'danger')
        return redirect(url_for('dashboard'))

    cls = MODELOS.get(modelo)
    if not cls: flash('Importação inválida.', 'danger'); return redirect(url_for('dashboard'))
    arquivo = request.files.get('arquivo')
    if not arquivo: flash('Selecione um arquivo Excel.', 'danger'); return redirect(request.referrer or url_for('dashboard'))
    df = pd.read_excel(arquivo)
    cols = {c.name for c in cls.__table__.columns if c.name != 'id'}
    count = 0
    for _, row in df.iterrows():
        data = {k: (None if pd.isna(row[k]) else row[k]) for k in df.columns if k in cols}
        if data:
            db.session.add(cls(**data)); count += 1
    db.session.commit(); auditar('Importou Excel', modelo, '', f'{count} registros'); flash(f'{count} registros importados.', 'success')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/backup')
@login_required
@permissao_necessaria('backup')
def backup():
    src = os.path.join(BASE_DIR, 'hotel.db')
    nome = f'hotel_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
    destino = os.path.join(BACKUP_DIR, nome)
    if os.path.exists(src): shutil.copy2(src, destino)
    auditar('Gerou backup','backup','',nome)
    return send_file(destino, as_attachment=True, download_name=nome)

@app.route('/recibo/<int:id>')
@login_required
@permissao_necessaria('reservas')
def recibo(id):
    r = Reserva.query.get_or_404(id)

    # Procura a última movimentação de retirada do cartão.
    movimentacao = (
        MovimentacaoCartao.query
        .filter_by(reserva_id=r.id)
        .filter(
            MovimentacaoCartao.tipo.in_([
                'Retirada',
                'Entrega',
                'Em uso'
            ])
        )
        .order_by(MovimentacaoCartao.data_evento.desc())
        .first()
    )

    numero_cartao = (
        movimentacao.cartao.codigo
        if movimentacao and movimentacao.cartao
        else '________________'
    )

    # Caminho da logo.
    logo_path = os.path.join(
        BASE_DIR,
        'static',
        'img',
        'logo_orca.png'
    )

    # Formata as datas da reserva.
    def formatar_data(valor):
        if not valor:
            return '-'

        try:
            return datetime.fromisoformat(str(valor)).strftime('%d/%m/%Y')
        except (ValueError, TypeError):
            return str(valor)

    data_entrada = formatar_data(r.data_entrada)
    data_saida = formatar_data(r.data_saida)

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f'Termo de cartão - Reserva {r.id}',
        author='ORCA'
    )

    estilos = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle(
        'TituloRecibo',
        parent=estilos['Title'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        alignment=TA_CENTER,
        textColor=colors.white,
        spaceAfter=0
    )

    estilo_subtitulo = ParagraphStyle(
        'SubtituloRecibo',
        parent=estilos['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#F5B400')
    )

    estilo_rotulo = ParagraphStyle(
        'RotuloRecibo',
        parent=estilos['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#202020')
    )

    estilo_valor = ParagraphStyle(
        'ValorRecibo',
        parent=estilos['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#171717')
    )

    estilo_texto = ParagraphStyle(
        'TextoRecibo',
        parent=estilos['Normal'],
        fontName='Helvetica',
        fontSize=9.7,
        leading=14.5,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor('#222222')
    )

    estilo_rodape = ParagraphStyle(
        'RodapeRecibo',
        parent=estilos['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#666666')
    )

    # Desenha elementos que ficam no fundo do PDF.
    def desenhar_pagina(canvas_pdf, documento):
        canvas_pdf.saveState()

        largura_pagina, altura_pagina = A4

        # Moldura externa.
        canvas_pdf.setStrokeColor(colors.HexColor('#D2D2D2'))
        canvas_pdf.setLineWidth(0.8)

        canvas_pdf.roundRect(
            12 * mm,
            12 * mm,
            largura_pagina - 24 * mm,
            altura_pagina - 24 * mm,
            3 * mm,
            stroke=1,
            fill=0
        )

        # Linha amarela superior.
        canvas_pdf.setFillColor(colors.HexColor('#F5B400'))

        canvas_pdf.rect(
            12 * mm,
            altura_pagina - 15 * mm,
            largura_pagina - 24 * mm,
            3 * mm,
            stroke=0,
            fill=1
        )

        # Logo como marca-d'água.
        if os.path.exists(logo_path):
            try:
                if hasattr(canvas_pdf, 'setFillAlpha'):
                    canvas_pdf.setFillAlpha(0.055)

                canvas_pdf.drawImage(
                    logo_path,
                    41 * mm,
                    88 * mm,
                    width=128 * mm,
                    height=119.5 * mm,
                    preserveAspectRatio=True,
                    mask='auto'
                )

                if hasattr(canvas_pdf, 'setFillAlpha'):
                    canvas_pdf.setFillAlpha(1)

            except Exception:
                pass

        # Rodapé fixo.
        canvas_pdf.setFillColor(colors.HexColor('#777777'))
        canvas_pdf.setFont('Helvetica', 7)

        canvas_pdf.drawCentredString(
            largura_pagina / 2,
            8 * mm,
            'Documento interno de controle de acesso e hospedagem'
        )

        canvas_pdf.restoreState()

    elementos = []

    # =====================================================
    # CABEÇALHO
    # =====================================================

    titulo_cabecalho = Table(
        [
            [
                Paragraph(
                    'TERMO DE RETIRADA E DEVOLUÇÃO DE CARTÃO',
                    estilo_titulo
                )
            ],
            [
                Paragraph(
                    'CONTROLE DE ACESSO AO ALOJAMENTO',
                    estilo_subtitulo
                )
            ]
        ],
        colWidths=[119 * mm]
    )

    titulo_cabecalho.setStyle(
        TableStyle([
            (
                'BACKGROUND',
                (0, 0),
                (-1, -1),
                colors.HexColor('#171717')
            ),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 3),
            ('TOPPADDING', (0, 1), (-1, 1), 2),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 8)
        ])
    )

    if os.path.exists(logo_path):
        logo_cabecalho = Image(
            logo_path,
            width=25 * mm,
            height=23.3 * mm
        )
    else:
        logo_cabecalho = Paragraph(
            '<b>ORCA</b>',
            ParagraphStyle(
                'LogoTexto',
                parent=estilos['Normal'],
                fontName='Helvetica-Bold',
                fontSize=18,
                alignment=TA_CENTER,
                textColor=colors.black
            )
        )

    cabecalho = Table(
        [
            [
                logo_cabecalho,
                titulo_cabecalho
            ]
        ],
        colWidths=[
            31 * mm,
            119 * mm
        ]
    )

    cabecalho.setStyle(
        TableStyle([
            (
                'BACKGROUND',
                (0, 0),
                (0, 0),
                colors.HexColor('#F5B400')
            ),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            (
                'BOX',
                (0, 0),
                (-1, -1),
                0.9,
                colors.HexColor('#171717')
            ),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3)
        ])
    )

    elementos.append(cabecalho)
    elementos.append(Spacer(1, 6 * mm))

    # =====================================================
    # DADOS DO COLABORADOR E DA RESERVA
    # =====================================================

    dados = [
        [
            Paragraph('COLABORADOR', estilo_rotulo),
            Paragraph('CPF', estilo_rotulo)
        ],
        [
            Paragraph(r.hospede.nome or '-', estilo_valor),
            Paragraph(r.hospede.cpf or '-', estilo_valor)
        ],
        [
            Paragraph('FUNÇÃO', estilo_rotulo),
            Paragraph('NÚMERO DA RESERVA', estilo_rotulo)
        ],
        [
            Paragraph(r.hospede.profissao or '-', estilo_valor),
            Paragraph(f'{r.id:06d}', estilo_valor)
        ],
        [
            Paragraph('QUARTO', estilo_rotulo),
            Paragraph('NÚMERO DO CARTÃO', estilo_rotulo)
        ],
        [
            Paragraph(str(r.quarto.numero), estilo_valor),
            Paragraph(str(numero_cartao), estilo_valor)
        ],
        [
            Paragraph('PERÍODO DA RESERVA', estilo_rotulo),
            ''
        ],
        [
            Paragraph(
                f'{data_entrada} até {data_saida}',
                estilo_valor
            ),
            ''
        ]
    ]

    tabela_dados = Table(
        dados,
        colWidths=[
            85 * mm,
            65 * mm
        ]
    )

    tabela_dados.setStyle(
        TableStyle([
            ('SPAN', (0, 6), (1, 6)),
            ('SPAN', (0, 7), (1, 7)),

            (
                'BACKGROUND',
                (0, 0),
                (-1, 0),
                colors.HexColor('#F5B400')
            ),
            (
                'BACKGROUND',
                (0, 2),
                (-1, 2),
                colors.HexColor('#F5B400')
            ),
            (
                'BACKGROUND',
                (0, 4),
                (-1, 4),
                colors.HexColor('#F5B400')
            ),
            (
                'BACKGROUND',
                (0, 6),
                (-1, 6),
                colors.HexColor('#F5B400')
            ),

            (
                'BOX',
                (0, 0),
                (-1, -1),
                0.9,
                colors.HexColor('#555555')
            ),
            (
                'INNERGRID',
                (0, 0),
                (-1, -1),
                0.4,
                colors.HexColor('#B8B8B8')
            ),

            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8)
        ])
    )

    elementos.append(tabela_dados)
    elementos.append(Spacer(1, 6 * mm))

    # =====================================================
    # TERMO DE RESPONSABILIDADE
    # =====================================================

    declaracao = (
        'Declaro, para os devidos fins, que recebi o cartão de '
        'acesso acima identificado, destinado exclusivamente ao '
        'uso durante o período da reserva informada. Estou ciente '
        'de que o cartão deverá ser conservado sob minha '
        'responsabilidade e devolvido '
        '<b>obrigatoriamente na portaria</b> ao término da '
        'hospedagem, no momento do check-out ou sempre que '
        'solicitado pela empresa. Comprometo-me, ainda, a '
        'comunicar imediatamente qualquer perda, dano ou extravio.'
    )

    caixa_declaracao = Table(
        [
            [
                Paragraph(
                    '<font color="#FFFFFF">'
                    'TERMO DE CIÊNCIA E RESPONSABILIDADE'
                    '</font>',
                    estilo_rotulo
                )
            ],
            [
                Paragraph(
                    declaracao,
                    estilo_texto
                )
            ]
        ],
        colWidths=[150 * mm]
    )

    caixa_declaracao.setStyle(
        TableStyle([
            (
                'BACKGROUND',
                (0, 0),
                (0, 0),
                colors.HexColor('#171717')
            ),
            (
                'BACKGROUND',
                (0, 1),
                (0, 1),
                colors.HexColor('#FAFAFA')
            ),
            (
                'BOX',
                (0, 0),
                (-1, -1),
                0.8,
                colors.HexColor('#777777')
            ),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7)
        ])
    )

    elementos.append(caixa_declaracao)
    elementos.append(Spacer(1, 9 * mm))

    # =====================================================
    # ASSINATURAS
    # =====================================================

    linha = '________________________________________'

    assinaturas = [
        [
            linha,
            linha
        ],
        [
            'Assinatura do colaborador — retirada',
            'Responsável pela entrega'
        ],
        [
            'Data: ____/____/________  Hora: ____:____',
            'Data: ____/____/________  Hora: ____:____'
        ],
        [
            '',
            ''
        ],
        [
            linha,
            linha
        ],
        [
            'Assinatura do colaborador — devolução',
            'Responsável pelo recebimento na portaria'
        ],
        [
            'Data: ____/____/________  Hora: ____:____',
            'Data: ____/____/________  Hora: ____:____'
        ]
    ]

    tabela_assinaturas = Table(
        assinaturas,
        colWidths=[
            75 * mm,
            75 * mm
        ],
        rowHeights=[
            7 * mm,
            5 * mm,
            7 * mm,
            9 * mm,
            7 * mm,
            5 * mm,
            7 * mm
        ]
    )

    tabela_assinaturas.setStyle(
        TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            (
                'BOX',
                (0, 0),
                (0, 2),
                0.6,
                colors.HexColor('#B0B0B0')
            ),
            (
                'BOX',
                (1, 0),
                (1, 2),
                0.6,
                colors.HexColor('#B0B0B0')
            ),
            (
                'BOX',
                (0, 4),
                (0, 6),
                0.6,
                colors.HexColor('#B0B0B0')
            ),
            (
                'BOX',
                (1, 4),
                (1, 6),
                0.6,
                colors.HexColor('#B0B0B0')
            ),

            (
                'BACKGROUND',
                (0, 0),
                (-1, 2),
                colors.HexColor('#FCFCFC')
            ),
            (
                'BACKGROUND',
                (0, 4),
                (-1, 6),
                colors.HexColor('#FCFCFC')
            ),

            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTNAME', (0, 5), (-1, 5), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.3),
            (
                'TEXTCOLOR',
                (0, 0),
                (-1, -1),
                colors.HexColor('#333333')
            )
        ])
    )

    elementos.append(tabela_assinaturas)
    elementos.append(Spacer(1, 6 * mm))

    # =====================================================
    # IDENTIFICAÇÃO DA EMISSÃO
    # =====================================================

    elementos.append(
        Paragraph(
            (
                f'Documento emitido em '
                f'{datetime.now().strftime("%d/%m/%Y às %H:%M")} '
                f'· Reserva nº {r.id:06d}'
            ),
            estilo_rodape
        )
    )

    doc.build(
        elementos,
        onFirstPage=desenhar_pagina,
        onLaterPages=desenhar_pagina
    )

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=False,
        download_name=f'termo_cartao_reserva_{r.id}.pdf',
        mimetype='application/pdf'
    )

def seed():
    admin = Usuario.query.filter_by(usuario='admin').first()

    if not admin:
        admin = Usuario(
            nome='Administrador',
            usuario='admin',
            senha_hash=generate_password_hash('admin123'),
            perfil='Administrador',
            ativo=True
        )

        db.session.add(admin)

    if Quarto.query.count() == 0:
        for numero in range(101, 111):
            db.session.add(
                Quarto(
                    numero=str(numero),
                    andar='1',
                    categoria='Standard',
                    capacidade=2,
                    valor_diaria=180,
                    status='Livre'
                )
            )

        for numero in range(201, 206):
            db.session.add(
                Quarto(
                    numero=str(numero),
                    andar='2',
                    categoria='Luxo',
                    capacidade=3,
                    valor_diaria=260,
                    status='Livre'
                )
            )

    db.session.commit()


with app.app_context():
    db.create_all()
    seed()


if __name__ == '__main__':
    app.run(debug=True)
