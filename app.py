import os, secrets, hmac, hashlib, logging
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from functools import wraps
from urllib.parse import quote
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, UniqueConstraint
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'DEV-NAO-USE-EM-PRODUCAO-' + secrets.token_hex(16))
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///moventra_dev.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('APP_BASE_URL', '').startswith('https://')
db = SQLAlchemy(app)
MP_API = 'https://api.mercadopago.com'
PLANS = {
    'essencial': {'name':'Essencial','price':Decimal('39.90'),'students':50,'features':'Agenda, alunos, cobranças e portal do aluno'},
    'profissional': {'name':'Profissional','price':Decimal('69.90'),'students':200,'features':'Todos os recursos do Essencial e agenda recorrente'},
    'estudio': {'name':'Estúdio','price':Decimal('119.90'),'students':1000,'features':'Maior capacidade para equipes e estúdios'},
}

class Tenant(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    name=db.Column(db.String(120), nullable=False)
    email=db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash=db.Column(db.String(255), nullable=False)
    plan=db.Column(db.String(30), nullable=False, default='essencial')
    subscription_status=db.Column(db.String(30), nullable=False, default='trial')
    mp_preference_id=db.Column(db.String(120))
    mp_payment_id=db.Column(db.String(120))
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    trial_ends_at=db.Column(db.DateTime, nullable=True)

class Student(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    tenant_id=db.Column(db.Integer, db.ForeignKey('tenant.id', ondelete='CASCADE'), nullable=False, index=True)
    name=db.Column(db.String(120), nullable=False)
    email=db.Column(db.String(255), nullable=False)
    phone=db.Column(db.String(40), default='')
    active=db.Column(db.Boolean, default=True, nullable=False)
    __table_args__=(UniqueConstraint('tenant_id','email',name='uq_student_tenant_email'),)

class StudentAccount(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    tenant_id=db.Column(db.Integer, db.ForeignKey('tenant.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id=db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False, unique=True)
    email=db.Column(db.String(255), nullable=False, index=True)
    password_hash=db.Column(db.String(255), nullable=False)
    must_change_password=db.Column(db.Boolean, default=True, nullable=False)
    __table_args__=(UniqueConstraint('tenant_id','email',name='uq_account_tenant_email'),)

class Appointment(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    tenant_id=db.Column(db.Integer, db.ForeignKey('tenant.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id=db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False, index=True)
    starts_at=db.Column(db.DateTime, nullable=False, index=True)
    modality=db.Column(db.String(100), default='Treino')
    status=db.Column(db.String(30), default='Agendado', nullable=False)
    source=db.Column(db.String(30), default='manual')
    __table_args__=(UniqueConstraint('tenant_id','student_id','starts_at',name='uq_appointment_slot'),)

class StudentNote(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    tenant_id=db.Column(db.Integer, db.ForeignKey('tenant.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id=db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False, index=True)
    kind=db.Column(db.String(20), nullable=False, default='nota')
    title=db.Column(db.String(120), nullable=False)
    content=db.Column(db.Text, nullable=False)
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

class StudentMeasurement(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    tenant_id=db.Column(db.Integer, db.ForeignKey('tenant.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id=db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False, index=True)
    measured_on=db.Column(db.Date, nullable=False, index=True)
    weight_kg=db.Column(db.Numeric(5,2))
    height_cm=db.Column(db.Numeric(5,2))
    waist_cm=db.Column(db.Numeric(5,2))
    hip_cm=db.Column(db.Numeric(5,2))
    chest_cm=db.Column(db.Numeric(5,2))
    body_fat_pct=db.Column(db.Numeric(5,2))
    notes=db.Column(db.String(1000), default='')
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Payment(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    tenant_id=db.Column(db.Integer, db.ForeignKey('tenant.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id=db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False, index=True)
    amount_cents=db.Column(db.Integer, nullable=False)
    due_date=db.Column(db.Date, nullable=False)
    description=db.Column(db.String(255), default='Mensalidade')
    status=db.Column(db.String(30), default='Pendente', nullable=False)
    external_reference=db.Column(db.String(80), unique=True, nullable=False)
    checkout_url=db.Column(db.Text)
    mp_payment_id=db.Column(db.String(120))
    paid_at=db.Column(db.DateTime)
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

with app.app_context():
    db.create_all()

def current_tenant():
    if session.get('role') != 'tenant': return None
    return db.session.get(Tenant, session.get('tenant_id'))

def tenant_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        t=current_tenant()
        if not t:
            return redirect(url_for('login'))

        exempt_endpoints = ('choose_plan', 'subscribe', 'logout', 'subscription_return')
        if request.endpoint not in exempt_endpoints:
            if t.subscription_status == 'trial':
                if not t.trial_ends_at or datetime.utcnow() >= t.trial_ends_at:
                    if request.path.startswith('/api/'):
                        return jsonify(error='Seu período de teste terminou. Escolha um plano para continuar.'), 402
                    return redirect(url_for('choose_plan'))
            elif t.subscription_status != 'active':
                if request.path.startswith('/api/'):
                    return jsonify(error='Assinatura inativa. Escolha um plano para continuar.'), 402
                return redirect(url_for('choose_plan'))
        return f(*args, **kwargs)
    return wrapped

def student_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if session.get('role')!='student': return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped

def money(cents): return f"R$ {cents/100:,.2f}".replace(',','X').replace('.',',').replace('X','.')
app.jinja_env.filters['brl_cents']=money

def mp_headers(): return {'Authorization':'Bearer '+os.getenv('MP_ACCESS_TOKEN','').strip(),'Content-Type':'application/json'}

def safe_return_url(path):
    base=os.getenv('APP_BASE_URL','').rstrip('/')
    if not base.startswith('https://'): return None
    return base+path

@app.after_request
def security_headers(response):
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    response.headers.setdefault('X-Frame-Options','DENY')
    response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
    if request.is_secure: response.headers.setdefault('Strict-Transport-Security','max-age=31536000; includeSubDomains')
    return response

@app.get('/')
def landing():
    if session.get('role')=='tenant': return redirect(url_for('dashboard'))
    return render_template('landing.html', plans=PLANS)

@app.route('/cadastro', methods=['GET','POST'])
def signup():
    if request.method=='POST':
        name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        if not name or '@' not in email or len(password)<10:
            flash('Informe nome, e-mail válido e senha com pelo menos 10 caracteres.','error')
        elif Tenant.query.filter(func.lower(Tenant.email)==email).first(): flash('Este e-mail já possui conta.','error')
        else:
            t=Tenant(name=name,email=email,password_hash=generate_password_hash(password),plan='trial',subscription_status='trial',trial_ends_at=datetime.utcnow()+timedelta(days=14))
            db.session.add(t); db.session.commit(); session.clear(); session.update(role='tenant',tenant_id=t.id,email=t.email)
            return redirect(url_for('choose_plan'))
    return render_template('signup.html')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        t=Tenant.query.filter(func.lower(Tenant.email)==email).first()
        if t and check_password_hash(t.password_hash,password):
            session.clear(); session.update(role='tenant',tenant_id=t.id,email=t.email)
            return redirect(url_for('choose_plan') if (t.subscription_status not in ('active','trial') or (t.subscription_status == 'trial' and (not t.trial_ends_at or datetime.utcnow() >= t.trial_ends_at))) else url_for('dashboard'))
        a=StudentAccount.query.filter(func.lower(StudentAccount.email)==email).first()
        if a and check_password_hash(a.password_hash,password):
            session.clear(); session.update(role='student',student_id=a.student_id,tenant_id=a.tenant_id,email=a.email,must_change_password=a.must_change_password)
            return redirect(url_for('student_portal'))
        flash('E-mail ou senha inválidos.','error')
    return render_template('login.html')

@app.post('/logout')
def logout(): session.clear(); return redirect(url_for('landing'))

@app.get('/planos')
def choose_plan():
    if not current_tenant(): return redirect(url_for('signup'))
    return render_template('plans.html',plans=PLANS)

@app.post('/assinar/<plan_key>')
def subscribe(plan_key):
    t=current_tenant()
    if not t: return redirect(url_for('login'))
    if plan_key not in PLANS: abort(404)
    token=os.getenv('MP_ACCESS_TOKEN','').strip(); base=os.getenv('APP_BASE_URL','').rstrip('/')
    if not token or not base.startswith('https://'):
        flash('Configure MP_ACCESS_TOKEN e APP_BASE_URL HTTPS no servidor antes de ativar pagamentos reais.','error')
        return redirect(url_for('choose_plan'))
    p=PLANS[plan_key]
    payload={'reason':'Moventra - Plano '+p['name'], 'payer_email':t.email,
      'external_reference':f'moventra-tenant-{t.id}-{plan_key}',
      'back_url':base+'/assinatura/retorno', 'notification_url':base+'/webhooks/mercadopago',
      'auto_recurring':{'frequency':1,'frequency_type':'months','transaction_amount':float(p['price']),'currency_id':'BRL'},
      'status':'pending'}
    try:
        # /preapproval cria uma assinatura recorrente; não usar checkout/preferences para mensalidade SaaS.
        r=requests.post(MP_API+'/preapproval',headers=mp_headers(),json=payload,timeout=20)
        if r.status_code>=400:
            app.logger.error('MP preapproval error: %s',r.text[:500]); flash('O Mercado Pago não aceitou a assinatura. Verifique credenciais e disponibilidade da API de assinaturas.','error'); return redirect(url_for('choose_plan'))
        pref=r.json(); t.plan=plan_key; t.mp_preference_id=pref.get('id'); db.session.commit()
        return redirect(pref.get('init_point') or pref.get('sandbox_init_point'))
    except requests.RequestException:
        flash('Não foi possível conectar ao Mercado Pago.','error'); return redirect(url_for('choose_plan'))

@app.get('/assinatura/retorno')
def subscription_return():
    flash('Retorno do pagamento recebido. A assinatura só é ativada após confirmação do webhook.','info')
    return redirect(url_for('dashboard') if current_tenant() else url_for('login'))

@app.post('/webhooks/mercadopago')
def mp_webhook():
    # Confirma o pagamento consultando a API; nunca confia somente no corpo recebido.
    data=request.get_json(silent=True) or {}; item_id=(data.get('data') or {}).get('id') or request.args.get('data.id')
    typ=data.get('type') or request.args.get('type'); token=os.getenv('MP_ACCESS_TOKEN','').strip()
    if not item_id or not token: return jsonify(ok=True)
    try:
        if typ in ('subscription_preapproval','preapproval'):
            r=requests.get(f'{MP_API}/preapproval/{item_id}',headers=mp_headers(),timeout=20); r.raise_for_status(); sub=r.json()
            ext=sub.get('external_reference','')
            if ext.startswith('moventra-tenant-'):
                parts=ext.split('-'); tid=int(parts[2]); plan=parts[3]; t=db.session.get(Tenant,tid)
                if t and plan in PLANS:
                    t.plan=plan
                    t.mp_payment_id=str(sub.get('id'))
                    if sub.get('status') == 'authorized':
                        t.subscription_status = 'active'
                        t.trial_ends_at = None
                    elif sub.get('status') in ('cancelled', 'paused'):
                        t.subscription_status = 'cancelled'
                    elif t.subscription_status != 'trial':
                        t.subscription_status = 'pending'
                    db.session.commit()
        elif typ in ('payment','payments'):
            r=requests.get(f'{MP_API}/v1/payments/{item_id}',headers=mp_headers(),timeout=20); r.raise_for_status(); pay=r.json()
            ext=pay.get('external_reference','')
            if ext.startswith('moventra-student-payment-'):
                row=Payment.query.filter_by(external_reference=ext).first()
                if row:
                    row.status={'approved':'Pago','pending':'Pendente','in_process':'Pendente','rejected':'Recusado','cancelled':'Cancelado','refunded':'Estornado','charged_back':'Estornado'}.get(pay.get('status'),'Pendente')
                    row.mp_payment_id=str(pay.get('id'))
                    if pay.get('status')=='approved': row.paid_at=datetime.utcnow()
                    db.session.commit()
    except Exception:
        app.logger.exception('Falha ao processar webhook Mercado Pago'); return jsonify(ok=False),503
    return jsonify(ok=True)

@app.get('/alunos')
@tenant_required
def students_page():
    return render_template('alunos.html')

@app.get('/alunos/<int:sid>')
@tenant_required
def student_profile(sid):
    t = current_tenant()
    student = Student.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    appointments = Appointment.query.filter_by(tenant_id=t.id, student_id=student.id).order_by(Appointment.starts_at.desc()).limit(50).all()
    payments = Payment.query.filter_by(tenant_id=t.id, student_id=student.id).order_by(Payment.due_date.desc()).limit(50).all()
    account = StudentAccount.query.filter_by(tenant_id=t.id, student_id=student.id).first()
    notes = StudentNote.query.filter_by(tenant_id=t.id, student_id=student.id).order_by(StudentNote.created_at.desc()).limit(100).all()
    measurements = StudentMeasurement.query.filter_by(tenant_id=t.id, student_id=student.id).order_by(StudentMeasurement.measured_on.desc(), StudentMeasurement.id.desc()).limit(100).all()
    # Gráficos usam os registros reais em ordem cronológica, sem alterar avaliações salvas.
    measurements_chronological = list(reversed(measurements))
    chart_measurements = [{
        'date': item.measured_on.strftime('%d/%m/%Y'),
        'weight': float(item.weight_kg) if item.weight_kg is not None else None,
        'waist': float(item.waist_cm) if item.waist_cm is not None else None,
        'hip': float(item.hip_cm) if item.hip_cm is not None else None,
        'chest': float(item.chest_cm) if item.chest_cm is not None else None,
        'body_fat': float(item.body_fat_pct) if item.body_fat_pct is not None else None,
    } for item in measurements_chronological]
    goals = [item for item in notes if item.kind == 'objetivo']
    progress_notes = [item for item in notes if item.kind == 'nota']
    total_paid = sum(p.amount_cents for p in payments if p.status == 'Pago')
    total_pending = sum(p.amount_cents for p in payments if p.status == 'Pendente')
    return render_template('aluno_perfil.html', student=student, appointments=appointments,
                           payments=payments, account=account, total_paid=total_paid,
                           total_pending=total_pending, goals=goals,
                           progress_notes=progress_notes, measurements=measurements,
                           chart_measurements=chart_measurements, now=datetime.utcnow())

@app.post('/alunos/<int:sid>/medidas')
@tenant_required
def student_add_measurement(sid):
    t = current_tenant()
    student = Student.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    def optional_decimal(field, label, maximum):
        raw = request.form.get(field, '').strip().replace(',', '.')
        if not raw:
            return None
        try:
            value = Decimal(raw)
            if not value.is_finite() or value <= 0 or value > Decimal(str(maximum)):
                raise ValueError
            return value.quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            raise ValueError(f'Confira o valor informado em {label}.')
    try:
        measured_on = date.fromisoformat(request.form.get('measured_on', '').strip())
        values = {
            'weight_kg': optional_decimal('weight_kg', 'peso', 500),
            'height_cm': optional_decimal('height_cm', 'altura', 300),
            'waist_cm': optional_decimal('waist_cm', 'cintura', 300),
            'hip_cm': optional_decimal('hip_cm', 'quadril', 300),
            'chest_cm': optional_decimal('chest_cm', 'peito', 300),
            'body_fat_pct': optional_decimal('body_fat_pct', 'percentual de gordura', 100),
        }
    except (ValueError, TypeError):
        flash('Informe uma data válida e confira as medidas.','error')
        return redirect(url_for('student_profile', sid=student.id))
    if not any(v is not None for v in values.values()):
        flash('Informe pelo menos uma medida para salvar a avaliação.','error')
        return redirect(url_for('student_profile', sid=student.id))
    notes = request.form.get('measurement_notes', '').strip()
    if len(notes) > 1000:
        flash('As observações devem ter até 1.000 caracteres.','error')
        return redirect(url_for('student_profile', sid=student.id))
    measurement = StudentMeasurement(tenant_id=t.id, student_id=student.id,
                                    measured_on=measured_on, notes=notes, **values)
    db.session.add(measurement)
    db.session.commit()
    flash('Avaliação corporal salva com sucesso.','success')
    return redirect(url_for('student_profile', sid=student.id))

@app.post('/alunos/<int:sid>/acompanhamento')
@tenant_required
def student_add_followup(sid):
    t = current_tenant()
    student = Student.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    kind = request.form.get('kind', 'nota').strip().lower()
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    if kind not in ('nota', 'objetivo'):
        abort(400)
    if not title or not content:
        flash('Preencha o título e a descrição para salvar.','error')
    elif len(title) > 120 or len(content) > 4000:
        flash('O título deve ter até 120 caracteres e a descrição até 4.000.','error')
    else:
        db.session.add(StudentNote(tenant_id=t.id, student_id=student.id, kind=kind, title=title, content=content))
        db.session.commit()
        flash('Objetivo salvo com sucesso.' if kind == 'objetivo' else 'Anotação salva com sucesso.','success')
    return redirect(url_for('student_profile', sid=student.id))

@app.post('/alunos/<int:sid>/acompanhamento/<int:item_id>/excluir')
@tenant_required
def student_delete_followup(sid, item_id):
    t = current_tenant()
    student = Student.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    item = StudentNote.query.filter_by(id=item_id, tenant_id=t.id, student_id=student.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash('Registro de acompanhamento excluído.','success')
    return redirect(url_for('student_profile', sid=student.id))

@app.get('/agenda')
@tenant_required
def agenda_page():
    return render_template('agenda.html')

@app.get('/cobrancas')
@tenant_required
def payments_page():
    return render_template('cobrancas.html')

@app.get('/app')
@tenant_required
def dashboard():
    t=current_tenant(); start=datetime.combine(date.today(),datetime.min.time()); end=start+timedelta(days=7)
    return render_template('dashboard.html',tenant=t,students=Student.query.filter_by(tenant_id=t.id).count(),appointments=Appointment.query.filter_by(tenant_id=t.id).filter(Appointment.starts_at>=start,Appointment.starts_at<end).count(),pending=Payment.query.filter_by(tenant_id=t.id,status='Pendente').count(),today=date.today())

@app.get('/api/students')
@tenant_required
def students_list():
    t=current_tenant(); return jsonify([{'id':s.id,'name':s.name,'email':s.email,'phone':s.phone,'active':s.active} for s in Student.query.filter_by(tenant_id=t.id).order_by(Student.name).all()])

@app.post('/api/students')
@tenant_required
def students_create():
    t=current_tenant(); d=request.get_json(silent=True) or {}; name=d.get('name','').strip(); email=d.get('email','').strip().lower()
    if not name or '@' not in email: return jsonify(error='Nome e e-mail válido são obrigatórios.'),400
    if Student.query.filter_by(tenant_id=t.id,email=email).first(): return jsonify(error='Este e-mail já está cadastrado nesta conta.'),400
    count=Student.query.filter_by(tenant_id=t.id,active=True).count()
    limite_alunos = 20 if t.subscription_status == 'trial' else PLANS.get(t.plan,PLANS['essencial'])['students']
    if count >= limite_alunos: return jsonify(error=f'Limite de {limite_alunos} alunos ativos atingido para sua conta.'),402
    s=Student(tenant_id=t.id,name=name,email=email,phone=d.get('phone','').strip()); db.session.add(s); db.session.commit(); return jsonify(id=s.id),201

@app.put('/api/students/<int:sid>')
@tenant_required
def students_update(sid):
    t = current_tenant()
    s = Student.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    d = request.get_json(silent=True) or {}
    name = str(d.get('name', '')).strip()
    email = str(d.get('email', '')).strip().lower()
    phone = str(d.get('phone', '')).strip()
    if not name or len(name) > 120:
        return jsonify(error='Informe um nome válido com até 120 caracteres.'), 400
    if not email or '@' not in email or len(email) > 255:
        return jsonify(error='Informe um e-mail válido.'), 400
    duplicate = Student.query.filter(
        Student.tenant_id == t.id,
        Student.email == email,
        Student.id != s.id
    ).first()
    if duplicate:
        return jsonify(error='Outro aluno já usa este e-mail.'), 400
    s.name, s.email, s.phone = name, email, phone[:40]
    # Mantém o e-mail do acesso do aluno sincronizado, se existir.
    account = StudentAccount.query.filter_by(tenant_id=t.id, student_id=s.id).first()
    if account:
        account.email = email
    db.session.commit()
    return jsonify(id=s.id, name=s.name, email=s.email, phone=s.phone, active=s.active)

@app.patch('/api/students/<int:sid>/status')
@tenant_required
def students_update_status(sid):
    t = current_tenant()
    s = Student.query.filter_by(id=sid, tenant_id=t.id).first_or_404()
    d = request.get_json(silent=True) or {}
    if not isinstance(d.get('active'), bool):
        return jsonify(error='Informe o novo status do aluno.'), 400
    if d['active'] and not s.active:
        count = Student.query.filter_by(tenant_id=t.id, active=True).count()
        limit = 20 if t.subscription_status == 'trial' else PLANS.get(t.plan, PLANS['essencial'])['students']
        if count >= limit:
            return jsonify(error=f'Limite de {limit} alunos ativos atingido para sua conta.'), 402
    s.active = d['active']
    db.session.commit()
    return jsonify(id=s.id, active=s.active)

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
    t = current_tenant()
    start = request.args.get('start')
    end = request.args.get('end')
    # Use explicit columns in the tenant filter: both Student and Appointment
    # have tenant_id, so filter_by after a join can target the wrong table.
    q = Appointment.query.join(Student, Appointment.student_id == Student.id).filter(
        Appointment.tenant_id == t.id,
        Student.tenant_id == t.id,
    )
    if start:
        try:
            start_dt = datetime.fromisoformat(start)
        except (TypeError, ValueError):
            return jsonify(error='Data inicial inválida.'), 400
        q = q.filter(Appointment.starts_at >= start_dt)
    if end:
        try:
            end_dt = datetime.fromisoformat(end)
        except (TypeError, ValueError):
            return jsonify(error='Data final inválida.'), 400
        q = q.filter(Appointment.starts_at < end_dt)
    # Appointment não declara relacionamento ORM chamado `student`.
    # Selecionamos explicitamente o nome via JOIN para evitar AttributeError.
    rows = q.with_entities(Appointment, Student.name).order_by(Appointment.starts_at).limit(1000).all()
    return jsonify([
        {
            'id': appointment.id,
            'student_id': appointment.student_id,
            'student_name': student_name,
            'starts_at': appointment.starts_at.isoformat(timespec='minutes'),
            'modality': appointment.modality,
            'status': appointment.status,
        }
        for appointment, student_name in rows
    ])

@app.post('/api/appointments')
@tenant_required
def appointment_create():
    t = current_tenant()
    d = request.get_json(silent=True) or {}
    try:
        sid = int(d.get('student_id'))
        starts_at = datetime.fromisoformat(str(d.get('starts_at', '')).strip())
    except (TypeError, ValueError):
        return jsonify(error='Informe aluno e data/horário válidos.'), 400
    student = Student.query.filter_by(id=sid, tenant_id=t.id).first()
    if not student or not student.active:
        return jsonify(error='Selecione um aluno ativo desta conta.'), 400
    modality = str(d.get('modality') or 'Treino').strip()[:100] or 'Treino'
    # O profissional não pode atender dois alunos no mesmo horário.
    occupied = Appointment.query.filter_by(tenant_id=t.id, starts_at=starts_at).filter(Appointment.status.notin_(['Cancelado', 'Falta'])).first()
    if occupied:
        return jsonify(error='Este horário já está ocupado por outra sessão.'), 409
    duplicate = Appointment.query.filter_by(tenant_id=t.id, student_id=sid, starts_at=starts_at).first()
    if duplicate:
        return jsonify(error='Este aluno já possui uma sessão nesse horário.'), 409
    item = Appointment(tenant_id=t.id, student_id=sid, starts_at=starts_at, modality=modality, status='Agendado', source='manual')
    db.session.add(item)
    db.session.commit()
    return jsonify(id=item.id, message='Sessão agendada.'), 201

@app.patch('/api/appointments/<int:appointment_id>/status')
@tenant_required
def appointment_update_status(appointment_id):
    t = current_tenant()
    item = Appointment.query.filter_by(id=appointment_id, tenant_id=t.id).first_or_404()
    d = request.get_json(silent=True) or {}
    status = str(d.get('status', '')).strip()
    allowed = {'Agendado', 'Realizado', 'Cancelado', 'Falta'}
    if status not in allowed:
        return jsonify(error='Status inválido.'), 400
    item.status = status
    db.session.commit()
    return jsonify(id=item.id, status=item.status)

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
            same_student = Appointment.query.filter_by(tenant_id=t.id, student_id=sid, starts_at=stamp).first()
            occupied = Appointment.query.filter_by(tenant_id=t.id, starts_at=stamp).filter(Appointment.status.notin_(['Cancelado', 'Falta'])).first()
            if not same_student and not occupied:
                db.session.add(Appointment(tenant_id=t.id,student_id=sid,starts_at=stamp,modality=str(d.get('modality','Treino'))[:100],source='recorrente')); made+=1
            else:dup+=1
        day+=timedelta(days=1)
    db.session.commit(); return jsonify(created=made,skipped_duplicates=dup),201

@app.get('/api/payments')
@tenant_required
def payments_list():
    t = current_tenant()
    # Payment não declara relacionamento ORM `student`; faça JOIN explícito
    # para obter nome e telefone sem gerar AttributeError/HTML 500.
    rows = (
        db.session.query(Payment, Student.name, Student.phone)
        .join(Student, Payment.student_id == Student.id)
        .filter(Payment.tenant_id == t.id, Student.tenant_id == t.id)
        .order_by(Payment.due_date.desc())
        .limit(500)
        .all()
    )
    return jsonify([
        {
            'id': payment.id,
            'student_name': student_name,
            'student_phone': student_phone,
            'amount_cents': payment.amount_cents,
            'due_date': payment.due_date.isoformat(),
            'description': payment.description,
            'status': payment.status,
            'paid_at': payment.paid_at.isoformat() if payment.paid_at else None,
            'checkout_url': payment.checkout_url,
        }
        for payment, student_name, student_phone in rows
    ])

@app.patch('/api/payments/<int:payment_id>')
@tenant_required
def payment_update_status(payment_id):
    t=current_tenant(); d=request.get_json(silent=True) or {}
    status=str(d.get('status','')).strip()
    if status not in ('Pago','Pendente'):
        return jsonify(error='Status inválido. Use Pago ou Pendente.'),400
    p=Payment.query.filter_by(id=payment_id,tenant_id=t.id).first()
    if not p:
        return jsonify(error='Cobrança não encontrada nesta conta.'),404
    p.status=status
    p.paid_at=datetime.utcnow() if status=='Pago' else None
    db.session.commit()
    return jsonify(id=p.id,status=p.status,paid_at=p.paid_at.isoformat() if p.paid_at else None)

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
