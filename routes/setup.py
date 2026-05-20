from flask import Blueprint, redirect, render_template, request
from database import get_db, query_db

bp = Blueprint('setup', __name__)


@bp.route('/setup', methods=['GET', 'POST'])
def wizard():
    if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] > 0:
        return redirect('/dashboard')

    if request.method == 'POST':
        names = [n.strip() for n in request.form.getlist('riders[]') if n.strip()]
        if names:
            db = get_db()
            for i, name in enumerate(names):
                db.execute(
                    "INSERT INTO Rider (name, avatarPath, isDefault) VALUES (?, 'custard_cream.svg', ?)",
                    [name, 1 if i == 0 else 0],
                )
            db.commit()
            return redirect('/dashboard')

    return render_template('setup.html')
