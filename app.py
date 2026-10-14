count = Student.query.filter_by(tenant_id=t.id, active=True).count()
    limite_alunos = (
        20 if t.subscription_status == 'trial'
        else PLANS.get(t.plan, PLANS['essencial'])['students']
    )
    if count >= limite_alunos:
        return jsonify(
            error=f'Limite de {limite_alunos} alunos ativos atingido para sua conta.'
        ), 402
    s=Student(tenant_id=t.id,name=name,email=email,phone=d.get('phone','').strip()); db.session.add(s); db.session.commit(); return jsonify(id=s.id),201

@app.post('/api/students/<int:sid>/account')
@tenant_required
def create_student_account(sid):
    t=current_tenant(); s=Student.query.filter_by(id=sid,tenant_id=t.id).first_or_404()
    if StudentAccount.query.filter_by(student_id=s.id).first(): return jsonify(error='Este aluno já tem acesso.'),400
    # senha temporária única e gerada no servidor; entregue por canal seguro.
    temp=secrets.token_urlsafe(9)
    a=StudentAccount(tenant_id=t.id,student_id=s.id,email=s.email,password_hash=generate_password_hash(temp),must_change_password=True)
    db.session.add(a); db.session.commit()
    return jsonify(email=s.email,temporary_password=temp,message='Compartilhe a senha temporária por canal seguro. O aluno deverá trocá-la no primeiro acesso.'),201

@app.get('/api/appointments')
@tenant_required
def appointments_list():
    t=current_tenant(); start=request.args.get('start'); end=request.args.get('end')
    q=Appointment.query.filter_by(tenant_id=t.id).join(Student)
    if start:
        try:q=q.filter(Appointment.starts_at>=datetime.fromisoformat(start))
        except ValueError:return jsonify(error='Data inicial inválida.'),400
    if end:
        try:q=q.filter(Appointment.starts_at<datetime.fromisoformat(end))
        except ValueError:return jsonify(error='Data final inválida.'),400
    return jsonify([{'id':a.id,'student_id':a.student_id,'student_name':a.student.name,'starts_at':a.starts_at.isoformat(timespec='minutes'),'modality':a.modality,'status':a.status} for a in q.order_by(Appointment.starts_at).limit(1000).all()])

@app.post('/api/appointments/generate')
@tenant_required
def generate_appointments():
    t=current_tenant(); d=request.get_json(silent=True) or {}
    try:
        sid=int(d['student_id']); weekdays=sorted(set(int(x) for x in d['weekdays'])); start=date.fromisoformat(d['start_date']); end=date.fromisoformat(d['end_date']); tm=d['time']
        datetime.strptime(tm,'%H:%M')
    except Exception:return jsonify(error='Informe aluno, dias, horário e período válidos.'),400
    s=Student.query.filter_by(id=sid,tenant_id=t.id).first()
    if not s:return jsonify(error='Aluno não encontrado nesta conta.'),404
    if not weekdays or any(w not in range(7) for w in weekdays) or end<start or (end-start).days>370:return jsonify(error='Dias inválidos ou período acima de 12 meses.'),400
    made=dup=0; day=start
    while day<=end:
        if day.weekday() in weekdays:
            stamp=datetime.combine(day,datetime.strptime(tm,'%H:%M').time())
            if not Appointment.query.filter_by(tenant_id=t.id,student_id=sid,starts_at=stamp).first():
                db.session.add(Appointment(tenant_id=t.id,student_id=sid,starts_at=stamp,modality=d.get('modality','Treino'),source='recorrente')); made+=1
            else:dup+=1
        day+=timedelta(days=1)
    db.session.commit(); return jsonify(created=made,skipped_duplicates=dup),201

@app.get('/api/payments')
@tenant_required
def payments_list():
    t=current_tenant(); rows=Payment.query.filter_by(tenant_id=t.id).order_by(Payment.due_date.desc()).limit(500).all()
    return jsonify([{'id':p.id,'student_name':p.student.name,'student_phone':p.student.phone,'amount_cents':p.amount_cents,'due_date':p.due_date.isoformat(),'description':p.description,'status':p.status,'checkout_url':p.checkout_url} for p in rows])

@app.post('/api/payments')
@tenant_required
def payment_create():
    t=current_tenant(); d=request.get_json(silent=True) or {}
    try:sid=int(d['student_id']); cents=int((Decimal(str(d['amount'])).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))*100); due=date.fromisoformat(d['due_date'])
    except Exception:return jsonify(error='Aluno, valor ou vencimento inválido.'),400
    s=Student.query.filter_by(id=sid,tenant_id=t.id).first()
    if not s or cents<=0:return jsonify(error='Aluno ou valor inválido.'),400
    ext=f'moventra-student-payment-{t.id}-{secrets.token_hex(8)}'
    p=Payment(tenant_id=t.id,student_id=sid,amount_cents=cents,due_date=due,description=d.get('description','Mensalidade'),external_reference=ext)
    db.session.add(p); db.session.commit()
    token=os.getenv('MP_ACCESS_TOKEN','').strip(); base=os.getenv('APP_BASE_URL','').rstrip('/')
    if token and base.startswith('https://'):
        payload={'items':[{'title':p.description,'quantity':1,'currency_id':'BRL','unit_price':float(Decimal(cents)/100)}],'payer':{'email':s.email},'external_reference':ext,'notification_url':base+'/webhooks/mercadopago','metadata':{'tenant_id':t.id,'payment_id':p.id}}
        try:
            r=requests.post(MP_API+'/checkout/preferences',headers=mp_headers(),json=payload,timeout=20); r.raise_for_status(); pref=r.json(); p.checkout_url=pref.get('init_point') or pref.get('sandbox_init_point'); db.session.commit()
        except Exception:app.logger.exception('Falha ao criar checkout')
    wa='https://wa.me/'+''.join(ch for ch in s.phone if ch.isdigit())+'?text='+quote(f'Olá {s.name}! Lembrete: {p.description} no valor de {money(cents)}, vencimento {due.strftime("%d/%m/%Y")}. '+(f'Pague por aqui: {p.checkout_url}' if p.checkout_url else 'Entre em contato para combinar o pagamento.')) if s.phone else None
    return jsonify(id=p.id,checkout_url=p.checkout_url,whatsapp_url=wa,status=p.status),201

@app.get('/portal')
@student_required
def student_portal():
    a=StudentAccount.query.filter_by(student_id=session['student_id'],tenant_id=session['tenant_id']).first_or_404()
    s=Student.query.filter_by(id=a.student_id,tenant_id=a.tenant_id).first_or_404()
    appts=Appointment.query.filter_by(tenant_id=a.tenant_id,student_id=s.id).filter(Appointment.starts_at>=datetime.utcnow()-timedelta(days=30)).order_by(Appointment.starts_at).limit(100).all()
    payments=Payment.query.filter_by(tenant_id=a.tenant_id,student_id=s.id).order_by(Payment.due_date.desc()).limit(100).all()
    return render_template('portal.html',student=s,account=a,appointments=appts,payments=payments)

@app.post('/portal/trocar-senha')
@student_required
def change_student_password():
    a=StudentAccount.query.filter_by(student_id=session['student_id'],tenant_id=session['tenant_id']).first_or_404()
    current=request.form.get('current_password',''); new=request.form.get('new_password',''); confirm=request.form.get('confirm_password','')
    if not check_password_hash(a.password_hash,current): flash('Senha temporária atual incorreta.','error')
    elif len(new)<10: flash('Use pelo menos 10 caracteres.','error')
    elif new!=confirm: flash('As senhas não coincidem.','error')
    else:a.password_hash=generate_password_hash(new); a.must_change_password=False; db.session.commit(); session['must_change_password']=False; flash('Senha alterada com sucesso.','success')
    return redirect(url_for('student_portal'))

@app.get('/health')
def health(): return jsonify(status='ok')

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','8000')),debug=os.getenv('FLASK_DEBUG','0')=='1')
