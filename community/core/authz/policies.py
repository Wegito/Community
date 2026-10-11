"""Ownership and scope rules in one place (roadmap §6.2). Permission codes alone cannot express
"my own chore" or "my own booking"; these functions combine codes with ownership."""

from .service import can


def can_manage_tasks():
    return can('tasks.manage')


def can_complete_task(user, task):
    return can('tasks.manage') or (task.assigned_to_id == user.id and can('tasks.complete_own'))


def can_cancel_booking(user, booking):
    if booking.status != 'active':
        return False
    return can('bookings.manage_any') or (booking.user_id == user.id and can('bookings.create_own'))
