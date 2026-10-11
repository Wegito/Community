from community.core.identity.models import Role
from community.extensions import db


class Chore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    due_date = db.Column(db.Date)
    is_done = db.Column(db.Boolean, default=False)

    assigned_to = db.relationship('User', lazy='joined')

    def can_edit(self, user):
        """Admins edit everything (later: the Consenior, roadmap M2-3)."""
        return user.is_authenticated and user.role == Role.ADMIN

    def can_toggle_done(self, user):
        """The assignee may mark the chore done or open it again; admins too."""
        return self.can_edit(user) or (user.is_authenticated and self.assigned_to_id == user.id)
