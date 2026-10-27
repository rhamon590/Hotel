"""Permissoes individuais para Hotel PRO.

Modulo instalavel sem mexer no schema original Usuario.
Sem configuracao individual, os acessos antigos continuam conforme o perfil.
"""
import json
from datetime import datetime

from flask import g, session, request, redirect, url_for, flash, render_template


# 'usuarios' e 'dashboard' sao privilegios especiais: nao delegaveis.
MODULOS = {
    'hospedes': ('Hóspedes', 'Cadastro e consulta de dados dos hóspedes', 'Operação'),
    'quartos': ('Quartos', 'Quartos, disponibilidade e valores', 'Operação'),
    'reservas': ('Reservas', 'Reservas, check-in, check-out e recibos', 'Operação'),
    'governanca': ('Governança', 'Limpezas e checklists', 'Operação'),
    'calendario': ('Calendário', 'Calendário de hospedagens e ocupação', 'Recepção'),
    'cartoes': ('Cartões', 'Entrega, devolução e histórico de cartões', 'Recepção'),
    'consumos': ('Consumos', 'Lançamentos e consultas de consumo', 'Operação'),
    'estoque': ('Estoque', 'Produtos e controle de estoque', 'Gestão'),
    'manutencao': ('Manutenção', 'Manutenções, responsáveis e custos', 'Gestão'),
    'funcionarios': ('Funcionários', 'Cadastro e consulta de funcionários', 'Gestão'),
    'empresas': ('Empresas', 'Cadastro de empresas e agências', 'Gestão'),
    'relatorios': ('Relatórios', 'Informações gerenciais e financeiras', 'Gestão'),
    'auditoria': ('Auditoria', 'Registros de movimentações e ações', 'Administração'),
    'backup': ('Backup', 'Cópia de segurança do banco de dados', 'Administração'),
}


def registrar_controle_acesso(ctx):
    app = ctx['app']
    db = ctx['db']
    Usuario = ctx['Usuario']
    Auditoria = ctx['Auditoria']
    defaults = ctx['PERMISSOES_PERFIL']
    login_required = ctx['login_required']
    admin_required = ctx['admin_required']

    class PermissaoIndividual(db.Model):
        __tablename__ = 'permissao_individual_modulo'
        usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), primary_key=True)
        modulos_json = db.Column(db.Text, nullable=False, default='[]')
        atualizado_em = db.Column(db.DateTime, nullable=False, default=datetime.now)
        atualizado_por = db.Column(db.String(80), nullable=False, default='sistema')

    def permissoes_do_usuario(usuario):
        """Retorna conjunto de modulos e se ha escolha individual salva."""
        registro = db.session.get(PermissaoIndividual, usuario.id)
        if registro is None:
            return set(defaults.get((usuario.perfil or '').strip().lower(), set())) & set(MODULOS), False
        try:
            dados = json.loads(registro.modulos_json)
            if not isinstance(dados, list):
                dados = []
        except (ValueError, TypeError):
            app.logger.error('Permissoes individuais invalidas para usuario %s', usuario.id)
            dados = []  # falha fechada em caso de dado corrompido
        return set(x for x in dados if isinstance(x, str)) & set(MODULOS), True

    def usuario_tem_acesso(modulo):
        """Consulta o banco a cada requisicao: alteracoes valem imediatamente."""
        uid = session.get('usuario_id')
        if not uid:
            return False
        if getattr(g, '_permissoes_uid', None) != uid:
            usuario = db.session.get(Usuario, uid)
            if not usuario or not usuario.ativo:
                g._permissoes_uid = uid
                g._permissoes_ativas = set()
                g._permissoes_admin = False
                g._permissoes_validas = False
            else:
                modulos, _ = permissoes_do_usuario(usuario)
                g._permissoes_uid = uid
                g._permissoes_ativas = modulos
                g._permissoes_admin = (usuario.perfil or '').strip().lower() == 'administrador'
                g._permissoes_validas = True
        if not g._permissoes_validas:
            return False
        if modulo == 'dashboard':
            return True
        if modulo == 'usuarios':
            return g._permissoes_admin  # nunca delegar administracao de acessos
        return modulo in MODULOS and modulo in g._permissoes_ativas

    # Substitui a verificacao antiga. Decorators do app consultam o nome global
    # durante cada requisicao, e os modulos externos recebem este contexto.
    ctx['usuario_tem_acesso'] = usuario_tem_acesso
    ctx['PermissaoIndividual'] = PermissaoIndividual

    @app.context_processor
    def disponibilizar_estado_permissoes():
        return {
            'tem_acesso': usuario_tem_acesso,
            'tem_algum_modulo': any(usuario_tem_acesso(k) for k in MODULOS) or usuario_tem_acesso('usuarios'),
        }

    def registrar_auditoria(acao, usuario, antes, depois):
        # Mantem a rastreabilidade sem incluir senha nem dados pessoais adicionais.
        db.session.add(Auditoria(
            usuario=session.get('usuario', 'sistema'),
            acao=acao,
            tabela='usuarios',
            registro=str(usuario.id),
            detalhes=f'usuario={usuario.usuario}; antes={",".join(sorted(antes))}; depois={",".join(sorted(depois))}',
            ip=request.remote_addr,
        ))
        db.session.commit()

    @app.route('/usuarios/<int:usuario_id>/permissoes', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def usuario_permissoes(usuario_id):
        usuario = db.session.get(Usuario, usuario_id)
        if usuario is None:
            flash('Usuário não localizado.', 'danger')
            return redirect(url_for('usuarios'))

        atuais, personalizado = permissoes_do_usuario(usuario)
        if request.method == 'POST':
            acao = request.form.get('acao', 'salvar')
            if acao == 'restaurar':
                antigo = db.session.get(PermissaoIndividual, usuario_id)
                if antigo is not None:
                    db.session.delete(antigo)
                    db.session.flush()
                padrao = set(defaults.get((usuario.perfil or '').strip().lower(), set())) & set(MODULOS)
                registrar_auditoria('Restaurou permissões do perfil', usuario, atuais, padrao)
                flash('Permissões do perfil restauradas.', 'success')
                return redirect(url_for('usuario_permissoes', usuario_id=usuario_id))
            if acao != 'salvar':
                flash('Ação inválida.', 'danger')
                return redirect(url_for('usuario_permissoes', usuario_id=usuario_id))

            marcados = request.form.getlist('modulos')
            if any(m not in MODULOS for m in marcados):
                flash('Solicitação contém módulo inválido.', 'danger')
                return redirect(url_for('usuario_permissoes', usuario_id=usuario_id))
            novos = set(marcados)
            registro = db.session.get(PermissaoIndividual, usuario_id)
            if registro is None:
                registro = PermissaoIndividual(usuario_id=usuario.id)
                db.session.add(registro)
            registro.modulos_json = json.dumps(sorted(novos), ensure_ascii=False)
            registro.atualizado_em = datetime.now()
            registro.atualizado_por = session.get('usuario', 'sistema')[:80]
            registrar_auditoria('Atualizou permissões individuais', usuario, atuais, novos)
            flash('Permissões individuais atualizadas com sucesso.', 'success')
            return redirect(url_for('usuario_permissoes', usuario_id=usuario_id))

        return render_template(
            'usuario_permissoes.html', usuario=usuario, modulos=MODULOS,
            atuais=atuais, personalizado=personalizado,
        )

    return PermissaoIndividual
