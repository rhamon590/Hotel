"""Extensão de reservas para o app.py fornecido. Não altera o esquema do banco."""
import hashlib
import json
import math
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import request, session, render_template, redirect, url_for, flash


def data_local(valor):
    try:
        dt = datetime.fromisoformat(str(valor))
        if dt.tzinfo is not None:
            raise ValueError
        return dt
    except (ValueError, TypeError):
        raise ValueError('Informe datas válidas, com horário local e sem fuso horário.')


def dinheiro(valor):
    try:
        numero = Decimal(str(valor or 0).replace(',', '.'))
        if not numero.is_finite() or numero < 0 or numero > Decimal('999999999'):
            raise ValueError
        return numero.quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise ValueError('Informe um valor válido, não negativo, com até duas casas decimais.')


def calcular_diarias(entrada, saida):
    segundos = (data_local(saida) - data_local(entrada)).total_seconds()
    if segundos <= 0:
        raise ValueError('A saída deve ser posterior à entrada.')
    return max(1, math.ceil(segundos / 86400))


def total_estadia(entrada, saida, diaria, desconto):
    return max(Decimal('0.00'), calcular_diarias(entrada, saida) * dinheiro(diaria) - dinheiro(desconto))


def vagas_por_eventos(capacidade, entrada, saida, intervalos):
    inicio, fim = data_local(entrada), data_local(saida)
    if fim <= inicio:
        raise ValueError('A saída deve ser posterior à entrada.')
    eventos = []
    for a, b in intervalos:
        a, b = data_local(a), data_local(b)
        if b <= a:
            raise ValueError('Há uma reserva ativa com período inválido. Corrija-a antes de continuar.')
        if a < fim and b > inicio:
            eventos.extend([(max(a, inicio), 1), (min(b, fim), -1)])
    uso = pico = 0
    # Saída libera a vaga antes da entrada no mesmo instante.
    for _, delta in sorted(eventos):
        uso += delta
        pico = max(pico, uso)
    return max(int(capacidade or 0) - pico, 0)


def registrar_edicao_reservas(ctx):
    app, db = ctx['app'], ctx['db']
    Reserva, Quarto, Hospede = ctx['Reserva'], ctx['Quarto'], ctx['Hospede']
    Auditoria = ctx['Auditoria']
    Cartao, Movimento = ctx['Cartao'], ctx['MovimentacaoCartao']
    proteger = ctx['permissao_necessaria']('reservas')
    campos = ('hospede_id', 'quarto_id', 'data_entrada', 'data_saida', 'valor_diaria', 'desconto', 'observacoes', 'status')

    def snapshot(r):
        return {k: getattr(r, k) for k in campos}

    def versao(r):
        return hashlib.sha256(json.dumps(snapshot(r), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def vagas(quarto, entrada, saida, ignorar_id=None):
        rs = Reserva.query.filter(Reserva.quarto_id == quarto.id, Reserva.status.in_(['Reservado', 'Hospedado'])).all()
        return vagas_por_eventos(quarto.capacidade, entrada, saida,
                                [(r.data_entrada, r.data_saida) for r in rs if r.id != ignorar_id])

    ctx['vagas_no_periodo'] = vagas
    ctx['calcular_diarias'] = calcular_diarias

    def cartao_atual(r):
        # Histórico de um cartão reutilizado não representa uma entrega atual.
        for c in Cartao.query.filter_by(status='Em uso').all():
            ultima = Movimento.query.filter_by(cartao_id=c.id).order_by(Movimento.data_evento.desc(), Movimento.id.desc()).first()
            if ultima and ultima.tipo == 'Entrega' and ultima.reserva_id == r.id:
                return c
        return None

    def moeda(valor):
        return f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    app.jinja_env.filters['hotel_moeda'] = moeda

    @app.route('/reservas/gerenciar')
    @proteger
    def gerenciar_reservas():
        busca = (request.args.get('q') or '').strip()
        query = Reserva.query.join(Hospede).join(Quarto)
        if busca:
            query = query.filter(db.or_(Hospede.nome.ilike(f'%{busca}%'), Quarto.numero.ilike(f'%{busca}%')))
        pagina = query.order_by(Reserva.id.desc()).paginate(page=max(request.args.get('page', 1, type=int) or 1, 1), per_page=50, error_out=False)
        return render_template('reservas_gerenciar_ext.html', pagina=pagina, q=busca)

    @app.route('/reservas/<int:id>/editar', methods=['GET', 'POST'])
    @proteger
    def editar_reserva(id):
        return formulario(id, False)

    @app.route('/reservas/<int:id>/prorrogar', methods=['GET', 'POST'])
    @proteger
    def prorrogar_reserva(id):
        return formulario(id, True)

    def formulario(id, prorrogar):
        r = Reserva.query.get_or_404(id)
        if r.status not in ('Reservado', 'Hospedado'):
            flash('Somente reservas ativas podem ser editadas ou prorrogadas.', 'warning')
            return redirect(url_for('reservas'))
        session.setdefault('reserva_csrf', secrets.token_urlsafe(32))
        valores = snapshot(r)
        erro, previa = None, None
        status_http = 200
        if request.method == 'POST':
            try:
                if not secrets.compare_digest(request.form.get('csrf', ''), session['reserva_csrf']):
                    raise ValueError('Formulário expirado. Reabra a reserva.')
                if not secrets.compare_digest(request.form.get('versao', ''), versao(r)):
                    raise ValueError('A reserva foi alterada em outra tela. Reabra-a antes de salvar.')
                antes = snapshot(r)
                if prorrogar:
                    valores['data_saida'] = request.form.get('data_saida', '')
                    if data_local(valores['data_saida']) <= data_local(r.data_saida):
                        raise ValueError('A nova saída deve ser posterior à saída atual.')
                else:
                    for k in campos[:-1]:
                        valores[k] = request.form.get(k, '')
                    try:
                        valores['hospede_id'] = int(valores['hospede_id'])
                        valores['quarto_id'] = int(valores['quarto_id'])
                    except (ValueError, TypeError):
                        raise ValueError('Selecione o colaborador e o quarto.')
                entrada, saida = data_local(valores['data_entrada']), data_local(valores['data_saida'])
                calcular_diarias(entrada, saida)
                quarto = db.session.get(Quarto, valores['quarto_id'])
                hospede = db.session.get(Hospede, valores['hospede_id'])
                if not quarto or not hospede:
                    raise ValueError('Colaborador ou quarto não encontrado.')
                if quarto.status in ('Em limpeza', 'Em manutenção', 'Em manutenÃ§Ã£o', 'Bloqueado'):
                    raise ValueError(f'Quarto indisponível: {quarto.status}.')
                alterou_vinculo = quarto.id != r.quarto_id or hospede.id != r.hospede_id
                if alterou_vinculo and cartao_atual(r):
                    raise ValueError('Há cartão em uso. Para manter o vínculo correto, a troca de quarto ou colaborador precisa ser tratada pela portaria. Não devolva o cartão apenas para editar: no sistema atual, isso encerra a reserva.')
                duplicada = Reserva.query.filter(Reserva.hospede_id == hospede.id, Reserva.id != r.id, Reserva.status.in_(['Reservado', 'Hospedado'])).first()
                if duplicada:
                    raise ValueError('O colaborador já possui outra reserva ativa.')
                if vagas(quarto, entrada, saida, r.id) <= 0:
                    raise ValueError('Não há vaga no quarto durante todo o período solicitado.')
                for k in ('valor_diaria', 'desconto'):
                    valores[k] = float(dinheiro(valores[k]))
                valores['data_entrada'] = entrada.isoformat(timespec='seconds')
                valores['data_saida'] = saida.isoformat(timespec='seconds')
                # Retira apenas o resumo automático antigo, preservando as demais notas.
                notas = '\n'.join(l for l in (valores['observacoes'] or '').splitlines()
                                  if not l.startswith(('Cálculo automático:', 'CÃ¡lculo automÃ¡tico:'))).strip()
                novo_total = total_estadia(entrada, saida, valores['valor_diaria'], valores['desconto'])
                antigo_total = total_estadia(r.data_entrada, r.data_saida, r.valor_diaria, r.desconto)
                qtd = calcular_diarias(entrada, saida)
                valores['observacoes'] = (notas + '\n\n' if notas else '') + f'Cálculo automático: {qtd} diária(s) | Total R$ {novo_total:.2f}'
                previa = dict(total=novo_total, anterior=antigo_total, diferenca=novo_total-antigo_total, diarias=qtd)
                if request.form.get('acao') == 'salvar':
                    quarto_antigo = r.quarto
                    for k in campos[:-1]:
                        setattr(r, k, valores[k])
                    db.session.flush()
                    if quarto_antigo.id != quarto.id and antes['status'] == 'Hospedado':
                        ctx['gerar_limpeza_se_necessario'](quarto_antigo, 'Saída por transferência de quarto.')
                    else:
                        ctx['atualizar_status_quarto'](quarto_antigo)
                    ctx['atualizar_status_quarto'](quarto)
                    db.session.add(Auditoria(usuario=session.get('usuario', 'sistema'),
                        acao='Prorrogou reserva' if prorrogar else 'Editou reserva', tabela='reservas', registro=str(r.id),
                        detalhes=json.dumps({'antes': antes, 'depois': snapshot(r)}, ensure_ascii=False), ip=request.remote_addr))
                    db.session.commit()
                    flash(f'Reserva #{r.id} atualizada. {qtd} diária(s); total de hospedagem R$ {moeda(novo_total)}.', 'success')
                    return redirect(url_for('reservas'))
            except ValueError as exc:
                db.session.rollback()
                erro, status_http = str(exc), 400
            except Exception:
                db.session.rollback()
                app.logger.exception('Erro ao editar/prorrogar reserva')
                erro, status_http = 'Não foi possível salvar. Nenhuma alteração foi confirmada; tente novamente.', 500
        return render_template('reserva_editar_ext.html', r=r, v=valores, versao=versao(r), csrf=session['reserva_csrf'],
                               erro=erro, previa=previa, prorrogar=prorrogar,
                               hospedes=Hospede.query.order_by(Hospede.nome).all(), quartos=Quarto.query.order_by(Quarto.numero).all()), status_http
