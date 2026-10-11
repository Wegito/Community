from community.extensions import db


class Chore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    due_date = db.Column(db.Date)
    is_done = db.Column(db.Boolean, default=False)

    assigned_to = db.relationship('User', lazy='joined')
