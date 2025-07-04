import os
import smtplib
from email.mime.text import MIMEText
from flask import (
    Flask, render_template, redirect, url_for,
    request, jsonify, send_from_directory, flash
)
from werkzeug.utils import secure_filename
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin, current_user
)
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, 'serviceAccountKey.json')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

if not os.path.isfile(SERVICE_ACCOUNT_PATH):
    raise FileNotFoundError(f"Missing Firebase service account JSON at {SERVICE_ACCOUNT_PATH}")

# Initialize Firebase Admin
cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://intern-project-db660-default-rtdb.firebaseio.com/'
})

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Flask-Login
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id_, email):
        self.id = id_
        self.email = email

# In-memory fallback user store
users = {
    'test@example.com': {'id': '1', 'password': '12345'},
    'test2@example.com': {'id': '2', 'password': '54321'}
}

@login_manager.user_loader
def load_user(user_id):
    for email, data in users.items():
        if data['id'] == user_id:
            return User(id_=user_id, email=email)
    return None

@app.after_request
def add_permissions_policy_header(response):
    response.headers['Permissions-Policy'] = 'unload=(self)'
    return response

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return render_template('login.html')

@app.route('/login', methods=['GET','POST'])
def login():
    # 1) JSON Firebase login
    if request.method == 'POST' and request.is_json:
        payload = request.get_json()
        id_token = payload.get('idToken')
        if not id_token:
            return jsonify({'error':'No ID token'}), 400
        try:
            decoded = firebase_auth.verify_id_token(id_token)
            email = decoded.get('email')
            uid   = decoded.get('uid')
            if not email or not uid:
                raise ValueError("Invalid token payload")
            if email not in users:
                users[email] = {'id': uid, 'password': None}
            user = User(id_=users[email]['id'], email=email)
            login_user(user)
            return jsonify({'status':'ok'}), 200
        except Exception as e:
            return jsonify({'error':str(e)}), 401

    # 2) Form fallback
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        d = users.get(u)
        if d and d.get('password') == p:
            user = User(id_=d['id'], email=u)
            login_user(user)
            return redirect(url_for('dashboard'))
        return "<h2 style='color:red;'>Invalid credentials</h2>", 401

    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/peer-interaction')
@login_required
def peer_interaction():
    return render_template('peer-interaction.html')

@app.route('/accept-invite')
@login_required
def accept_invite():
    return render_template('peer-interaction.html')

@app.route('/daily-updates')
@login_required
def daily_updates():
    return render_template('daily-updates.html')

@app.route('/notes-sharing')
@login_required
def notes_sharing():
    return render_template('notes-sharing.html')

@app.route('/leaderboard')
@login_required
def leaderboard():
    return render_template('leaderboard.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))

# Serve uploaded PDFs (including peer notes)
@app.route('/uploads/<path:subpath>/<filename>')
@login_required
def uploaded_file(subpath, filename):
    directory = os.path.join(app.config['UPLOAD_FOLDER'], subpath)
    return send_from_directory(directory, filename)

# Video call popup
@app.route('/video-call')
@login_required
def video_call():
    room_code = request.args.get('room')
    if not room_code:
        return "Missing room code", 400
    return render_template('video-call.html', room_code=room_code)

# ─── API Endpoints ─────────────────────────────────────────────────────────────

@app.route('/api/create-room', methods=['POST'])
@login_required
def api_create_room():
    data = request.get_json() or {}
    code = data.get('room_code')
    if not code:
        return jsonify({'error':'Missing room_code'}),400
    ref_ = db.reference(f'rooms/{code}')
    if ref_.get() is not None:
        return jsonify({'status':'exists'}),409
    ref_.set({
        'ownerUid': current_user.id,
        'ownerEmail': current_user.email,
        'members': { current_user.id: True },
        'notes': {},
        'classes': {},
        'topics': {}
    })
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], code), exist_ok=True)
    return jsonify({'status':'created'}),200

@app.route('/api/delete-room', methods=['POST'])
@login_required
def api_delete_room():
    code = request.get_json().get('room_code')
    ref_ = db.reference(f'rooms/{code}')
    info = ref_.get()
    if not info:
        return jsonify({'error':'Not found'}),404
    if info.get('ownerUid') != current_user.id:
        return jsonify({'error':'Forbidden'}),403
    ref_.delete()
    folder = os.path.join(app.config['UPLOAD_FOLDER'], code)
    if os.path.isdir(folder):
        for fn in os.listdir(folder):
            os.remove(os.path.join(folder, fn))
        os.rmdir(folder)
    return jsonify({'status':'deleted'}),200

@app.route('/api/join-room', methods=['POST'])
@login_required
def api_join_room():
    code = request.get_json().get('room_code')
    members_ref = db.reference(f'rooms/{code}/members')
    if members_ref.get() is None:
        return jsonify({'error':'Not found'}),404
    members_ref.update({ current_user.id: True })
    return jsonify({'status':'joined'}),200

@app.route('/api/get-rooms')
@login_required
def api_get_rooms():
    all_rooms = db.reference('rooms').get() or {}
    return jsonify({ c: len(r.get('members',{})) for c,r in all_rooms.items() }),200

@app.route('/api/invite-member', methods=['POST'])
@login_required
def api_invite_member():
    data = request.get_json() or {}
    room  = data.get('room_code')
    email = data.get('email')
    if not room or not email:
        return jsonify({'error':'Missing fields'}),400
    link = url_for('accept_invite', room=room, email=email, _external=True)
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT   = 587
    SMTP_USER   = "aryasubramani.s@gmail.com"
    SMTP_PASS   = "nsclvvtcqqlmkbon"
    msg = MIMEText(f"Join my Connect room {room}:\n\n{link}")
    msg['Subject'] = f"Invitation to room {room}"
    msg['From']    = SMTP_USER
    msg['To']      = email
    try:
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
        s.quit()
        return jsonify({'status':'sent'}),200
    except Exception as e:
        return jsonify({'error':str(e)}),500

# ─── NEW: Save quiz text ──────────────────────────────────────────────────────

@app.route('/api/save_quiz', methods=['POST'])
@login_required
def api_save_quiz():
    data = request.get_json() or {}
    room = data.get('room_code')
    quiz_text = data.get('quiz_text')
    if not room or not quiz_text:
        return jsonify({'error':'Missing fields'}), 400

    quizzes_ref = db.reference(f'rooms/{room}/quizzes')
    quizzes_ref.push({
        'text': quiz_text,
        'createdBy': current_user.email
    })

    return jsonify({'status': 'saved'}), 200

# ─── Owner Upload Notes Endpoint ───────────────────────────────────────────────

@app.route('/upload_note', methods=['POST'])
@login_required
def upload_note():
    room = request.form.get('room_code')
    if not room:
        return "Missing room code",400
    info = db.reference(f'rooms/{room}').get()
    if not info:
        return "Room not found",404
    if info.get('ownerUid') != current_user.id:
        return "Forbidden",403

    f = request.files.get('note')
    if not f:
        return "No file",400

    save_dir = os.path.join(app.config['UPLOAD_FOLDER'], room)
    os.makedirs(save_dir, exist_ok=True)
    filename = secure_filename(f.filename)
    path = os.path.join(save_dir, filename)
    f.save(path)

    file_url = url_for('uploaded_file', subpath=room, filename=filename, _external=True)
    notes_ref = db.reference(f'rooms/{room}/notes')
    notes_ref.push({'name': filename, 'url': file_url})

    flash(f"Uploaded { filename }")
    return redirect(url_for('daily_updates'))

# ─── Email quiz text endpoint ─────────────────────────────────────────────────

@app.route('/api/email_quiz_text', methods=['POST'])
@login_required
def api_email_quiz_text():
    data = request.get_json() or {}
    room       = data.get('room_code')
    user_email = data.get('user_email')
    quiz_text  = data.get('quiz_text')
    subject    = data.get('subject', f"Quiz for room {room}")
    if not room or not user_email or not quiz_text:
        return jsonify({'error':'Missing fields'}),400

    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT   = 587
    SMTP_USER   = "aryasubramani.s@gmail.com"
    SMTP_PASS   = "nsclvvtcqqlmkbon"

    msg = MIMEText(quiz_text)
    msg['Subject'] = subject
    msg['From']    = SMTP_USER
    msg['To']      = user_email

    try:
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
        s.quit()
        return jsonify({'status':'sent'}),200
    except Exception as e:
        return jsonify({'error':str(e)}),500

# ─── NEW: List owner notes ─────────────────────────────────────────────────────

@app.route('/list_notes')
@login_required
def list_notes():
    room = request.args.get('room_code')
    folder = os.path.join(app.config['UPLOAD_FOLDER'], room)
    if not os.path.isdir(folder):
        return jsonify([]), 200
    files = sorted(os.listdir(folder))
    return jsonify(files), 200

# ─── NEW: Delete owner note ────────────────────────────────────────────────────
@app.route('/api/room-info')
@login_required
def api_room_info():
    code = request.args.get('room_code')
    ref = db.reference(f'rooms/{code}').get() or {}
    is_owner = (ref.get('ownerUid') == current_user.id)
    return jsonify({'isOwner': is_owner}), 200


@app.route('/delete_note/<room_code>/<path:filename>', methods=['DELETE'])
@login_required
def delete_note_route(room_code, filename):
    room_ref = db.reference(f'rooms/{room_code}')
    info = room_ref.get() or {}
    if info.get('ownerUid') != current_user.id:
        return "Forbidden", 403

    path = os.path.join(app.config['UPLOAD_FOLDER'], room_code, filename)
    if os.path.exists(path):
        os.remove(path)
    else:
        return "Not found", 404

    notes_ref = db.reference(f'rooms/{room_code}/notes')
    all_notes = notes_ref.get() or {}
    for key, meta in all_notes.items():
        if meta.get('name') == filename:
            notes_ref.child(key).delete()

    return ('', 204)

# ─── NEW: Upload peer note ────────────────────────────────────────────────────

@app.route('/upload_peer_note', methods=['POST'])
@login_required
def upload_peer_note():
    room = request.form.get('room_code')
    f    = request.files.get('note')
    if not room or not f:
        return 'Missing room or file', 400

    folder = os.path.join(app.config['UPLOAD_FOLDER'], room, 'notes-peers')
    os.makedirs(folder, exist_ok=True)
    filename = secure_filename(f.filename)
    path = os.path.join(folder, filename)
    f.save(path)

    notes_ref = db.reference(f'rooms/{room}/peerNotes')
    notes_ref.push({
      'name': filename,
      'url': url_for('uploaded_file',
                     subpath=os.path.join(room, 'notes-peers'),
                     filename=filename,
                     _external=True),
      'uploader': current_user.email,
      'uploaderUid': current_user.id
    })
    return ('', 204)

# ─── NEW: List peer notes ─────────────────────────────────────────────────────

@app.route('/list_peer_notes')
@login_required
def list_peer_notes():
    room = request.args.get('room_code')
    snap = db.reference(f'rooms/{room}/peerNotes').get() or {}
    out = []
    for key, meta in snap.items():
        out.append({
            'key': key,
            'name': meta.get('name'),
            'url':  meta.get('url'),
            'uploader':    meta.get('uploader'),
            'uploaderUid': meta.get('uploaderUid')
        })
    return jsonify(out)

# ─── NEW: Delete peer note ────────────────────────────────────────────────────

@app.route('/delete_peer_note/<room_code>/<path:note_key>', methods=['DELETE'])
@login_required
def delete_peer_note(room_code, note_key):
    room_info = db.reference(f'rooms/{room_code}').get() or {}
    note_ref = db.reference(f'rooms/{room_code}/peerNotes/{note_key}')
    meta = note_ref.get()
    if not meta:
        return "Not found", 404

    if meta.get('uploaderUid') != current_user.id and room_info.get('ownerUid') != current_user.id:
        return "Forbidden", 403

    filename = meta.get('name')
    file_dir = os.path.join(app.config['UPLOAD_FOLDER'], room_code, 'notes-peers')
    fp = os.path.join(file_dir, filename)
    if os.path.exists(fp):
        os.remove(fp)

    note_ref.delete()
    return ('', 204)

# ─── Routes listing & run ─────────────────────────────────────────────────────

@app.route('/images/<path:filename>')
def images(filename):
    return send_from_directory(os.path.join(app.root_path, 'images'), filename)

def list_routes(app):
    import urllib
    print("\n=== ROUTES ===")
    for r in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
        print(urllib.parse.unquote(f"{r.endpoint:30s} {','.join(r.methods):20s} {r}"))
    print("=============\n")

if __name__ == '__main__':
    list_routes(app)
    app.run(debug=True)
