"""The base class of every form: Flask-WTF's FlaskForm with WTForms' German messages."""

from flask_wtf import FlaskForm as _FlaskForm


class FlaskForm(_FlaskForm):
    """Use this instead of flask_wtf.FlaskForm (tests/test_forms.py checks it).

    WTForms ships German translations of its own messages ("Dieses Feld wird benötigt.").
    Flask-WTF would take them from Flask-Babel, which the app does not use (D24), so
    WTF_I18N_ENABLED is off in config.py and the locale is chosen here.
    """

    class Meta:
        locales = ('de_DE', 'de')
