from flask import Blueprint, redirect, render_template, request
from database import get_db, query_db

bp = Blueprint('setup', __name__)


@bp.route('/setup', methods=['GET', 'POST'])
def wizard():
    if query_db('SELECT COUNT(*) FROM Rider', one=True)[0] > 0:
        return redirect('/dashboard')

    if request.method == 'POST':
        name1 = request.form.get('name1', '').strip()
        name2 = request.form.get('name2', '').strip()
        if name1:
            db = get_db()
            db.execute(
                "INSERT INTO Rider (name, avatarPath, isDefault) VALUES (?, 'custard_cream.svg', 1)",
                [name1],
            )
            if name2:
                db.execute(
                    "INSERT INTO Rider (name, isDefault) VALUES (?, 0)",
                    [name2],
                )
            db.commit()
            return redirect('/dashboard')

    return render_template('setup.html')
