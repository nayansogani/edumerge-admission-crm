import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import csv
from flask import Response

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/admissions.db'
app.config['SECRET_KEY'] = 'supersecretkey'
db = SQLAlchemy(app)

# --- LOGGER CONFIGURATION ---
if not os.path.exists('logs'):
    os.mkdir('logs')
file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

# --- MODELS ---
class Institution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), default="My University")
    jnk_cap = db.Column(db.Integer, default=5) # Global Institution Cap

class Program(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False) 
    course_type = db.Column(db.String(50), nullable=False) 
    total_intake = db.Column(db.Integer, nullable=False)
    kcet_quota = db.Column(db.Integer, nullable=False)
    comedk_quota = db.Column(db.Integer, nullable=False)
    mgmt_quota = db.Column(db.Integer, nullable=False)
    snq_quota = db.Column(db.Integer, nullable=False, default=0) # Supernumerary seats
    
    applicants = db.relationship('Applicant', backref='program', lazy=True)

class Applicant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    category = db.Column(db.String(50), nullable=False) 
    entry_type = db.Column(db.String(50), nullable=False) 
    quota_type = db.Column(db.String(50), nullable=False) # KCET, COMEDK, Management, SNQ, J&K
    allotment_number = db.Column(db.String(50), nullable=True)
    marks = db.Column(db.Float, nullable=False)
    doc_status = db.Column(db.String(50), default='Pending') 
    fee_status = db.Column(db.String(50), default='Pending') 
    status = db.Column(db.String(50), default='Pending') 
    admission_number = db.Column(db.String(100), unique=True, nullable=True)
    program_id = db.Column(db.Integer, db.ForeignKey('program.id'), nullable=False)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(50), nullable=False) 

# ACCESS CONTROL DECORATORS 
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session:
                return redirect(url_for('login'))
            if session['role'] not in allowed_roles:
                flash("Access Denied: Insufficient permissions.", 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def get_filled_seats(program_id, quota_type):
    return Applicant.query.filter_by(program_id=program_id, quota_type=quota_type, status='Admitted').count()

def get_global_jnk_filled():
    return Applicant.query.filter_by(quota_type='J&K', status='Admitted').count()

# --- ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            session['username'], session['role'] = user.username, user.role
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    programs = Program.query.all()
    inst = Institution.query.first()
    jnk_filled = get_global_jnk_filled()
    stats = []
    
    for p in programs:
        kcet = get_filled_seats(p.id, 'KCET')
        comedk = get_filled_seats(p.id, 'COMEDK')
        mgmt = get_filled_seats(p.id, 'Management')
        snq = get_filled_seats(p.id, 'SNQ')
        total_admitted = kcet + comedk + mgmt # Base admissions
        
        stats.append({
            'program': p.name,
            'intake': p.total_intake,
            'admitted': total_admitted,
            'remaining': p.total_intake - total_admitted,
            'kcet': f"{kcet}/{p.kcet_quota}",
            'comedk': f"{comedk}/{p.comedk_quota}",
            'mgmt': f"{mgmt}/{p.mgmt_quota}",
            'snq': f"{snq}/{p.snq_quota}"
        })
    
    return render_template('dashboard.html', stats=stats, jnk_filled=jnk_filled, jnk_cap=inst.jnk_cap)

@app.route('/setup', methods=['GET', 'POST'])
@role_required('Admin')
def setup():
    inst = Institution.query.first()
    if request.method == 'POST':
        if 'update_inst' in request.form:
            inst.jnk_cap = int(request.form['jnk_cap'])
            db.session.commit()
            flash('Institution settings updated.', 'success')
        else:
            intake = int(request.form['total_intake'])
            kcet, comedk, mgmt = int(request.form['kcet_quota']), int(request.form['comedk_quota']), int(request.form['mgmt_quota'])
            
            if (kcet + comedk + mgmt) != intake:
                flash('Error: Sum of base quotas (KCET+COMEDK+Mgmt) must equal total intake.', 'error')
            else:
                new_program = Program(
                    name=request.form['name'], course_type=request.form['course_type'], 
                    total_intake=intake, kcet_quota=kcet, comedk_quota=comedk, 
                    mgmt_quota=mgmt, snq_quota=int(request.form['snq_quota'])
                )
                db.session.add(new_program)
                db.session.commit()
                flash('Program added!', 'success')
        return redirect(url_for('setup'))
    
    return render_template('setup.html', programs=Program.query.all(), inst=inst)

@app.route('/applicants', methods=['GET', 'POST'])
@role_required('Officer')
def applicants():
    if request.method == 'POST':
        db.session.add(Applicant(
            name=request.form['name'], phone=request.form['phone'], category=request.form['category'],
            entry_type=request.form['entry_type'], quota_type=request.form['quota_type'],
            allotment_number=request.form.get('allotment_number', ''), marks=float(request.form['marks']),
            program_id=int(request.form['program_id'])
        ))
        db.session.commit()
        flash('Applicant created.', 'success')
        return redirect(url_for('applicants'))
        
    return render_template('applicants.html', applicants=Applicant.query.all(), programs=Program.query.all())

@app.route('/update_status/<int:id>', methods=['POST'])
@role_required('Officer')
def update_status(id):
    applicant = Applicant.query.get_or_404(id)
    applicant.doc_status, applicant.fee_status = request.form['doc_status'], request.form['fee_status']
    db.session.commit()
    return redirect(url_for('applicants'))

@app.route('/admit/<int:id>', methods=['POST'])
@role_required('Officer')
def admit(id):
    applicant = Applicant.query.get_or_404(id)
    program = applicant.program
    
    if applicant.fee_status != 'Paid' or applicant.doc_status != 'Verified':
        flash('Blocked: Fee must be paid and documents verified.', 'error')
        return redirect(url_for('applicants'))
        
    # Global J&K Cap Check
    if applicant.quota_type == 'J&K':
        inst = Institution.query.first()
        if get_global_jnk_filled() >= inst.jnk_cap:
            flash(f'Blocked: Institution-wide J&K quota limit ({inst.jnk_cap}) reached.', 'error')
            return redirect(url_for('applicants'))
    # Program-level Quota Check (Dynamic check based on applicant's quota type)
    elif applicant.quota_type in ['KCET', 'COMEDK', 'Management', 'SNQ']:
        filled = get_filled_seats(program.id, applicant.quota_type)
        # We use getattr to dynamically check program.kcet_quota, program.snq_quota, etc.
        limit = getattr(program, f"{applicant.quota_type.lower()}_quota", 0) 
        if filled >= limit:
            flash(f'Blocked: {applicant.quota_type} quota full for {program.name}.', 'error')
            return redirect(url_for('applicants'))

    # Confirm Admission
    seq = Applicant.query.filter_by(program_id=program.id, status='Admitted').count() + 1
    applicant.admission_number = f"INST/{datetime.now().year}/{program.course_type}/{program.name}/{applicant.quota_type}/{seq:04d}"
    applicant.status = 'Admitted'
    db.session.commit()
    flash(f'Admitted! Reg No: {applicant.admission_number}', 'success')
    return redirect(url_for('applicants'))

@app.route('/export')
@login_required
def export_csv():
    try:
        # Fetch only the admitted students
        applicants = Applicant.query.filter_by(status='Admitted').all()
        
        def generate():
            # CSV Header row
            yield 'Admission No,Student Name,Phone,Category,Program,Quota,Marks (%)\n'
            
            # CSV Data rows
            for a in applicants:
                yield f"{a.admission_number},{a.name},{a.phone},{a.category},{a.program.name},{a.quota_type},{a.marks}\n"

        app.logger.info(f"User '{session.get('username')}' downloaded the admissions CSV report.")
        return Response(generate(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=admissions_report.csv'})
        
    except Exception as e:
        app.logger.error(f"Error exporting CSV: {str(e)}")
        flash("Failed to generate report.", "error")
        return redirect(url_for('dashboard'))
    
with app.app_context():
    db.create_all()
    if not Institution.query.first():
        db.session.add(Institution(name="Global Tech University", jnk_cap=5))
    if not User.query.first():
        db.session.bulk_save_objects([
            User(username='admin', password_hash=generate_password_hash('admin123'), role='Admin'),
            User(username='officer', password_hash=generate_password_hash('officer123'), role='Officer'),
            User(username='mgmt', password_hash=generate_password_hash('mgmt123'), role='Management')
        ])
    db.session.commit()

# --- LOCAL RUNNER ---
if __name__ == '__main__':
    app.run(debug=True)
    
# if __name__ == '__main__':
#     with app.app_context():
#         db.create_all()
#         if not Institution.query.first():
#             db.session.add(Institution(name="Global Tech University", jnk_cap=5))
#         if not User.query.first():
#             db.session.bulk_save_objects([
#                 User(username='admin', password_hash=generate_password_hash('admin123'), role='Admin'),
#                 User(username='officer', password_hash=generate_password_hash('officer123'), role='Officer'),
#                 User(username='mgmt', password_hash=generate_password_hash('mgmt123'), role='Management')
#             ])
#         db.session.commit()
#     app.run(debug=True)