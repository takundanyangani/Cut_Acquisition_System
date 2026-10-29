from datetime import timedelta

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import throttle
from .models import DELEGABLE_ROLES, Delegation, Role, User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Email or staff number",
        max_length=254,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "spellcheck": "false"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    error_messages = {
        "invalid_login": "That email or staff number and password do not match an active account.",
        "inactive": "This account is inactive. Contact the ICT Helpdesk.",
    }

    def clean(self):
        identifier = self.cleaned_data.get("username", "")
        ip = throttle.client_ip(self.request)
        if throttle.is_locked(identifier, ip):
            raise ValidationError(throttle.lock_message(), code="locked")
        try:
            cleaned = super().clean()
        except ValidationError:
            throttle.record(identifier, ip, successful=False)
            raise
        throttle.record(identifier, ip, successful=True)
        return cleaned


class StyledPasswordChangeForm(PasswordChangeForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        if commit:
            user.save()
        return user


class StyledSetPasswordForm(SetPasswordForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["campus", "building", "office", "phone_extension"]
        labels = {"campus": "Usual campus", "building": "Building", "office": "Office or room"}
        help_texts = {"campus": "New requests start with this location. You can change it on each request."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.models import Campus

        self.fields["campus"].queryset = Campus.objects.filter(is_active=True)
        self.fields["campus"].empty_label = "Choose a campus"


class SignatureForm(forms.Form):
    """Password re-entry that turns an action into a signed decision."""

    password = forms.CharField(
        label="Your password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Entering your password signs this decision in your name.",
    )

    def __init__(self, *args, user=None, request=None, **kwargs):
        self.user = user
        self.request = request
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        verify_signature(self.request, self.user, password)
        return password


def verify_signature(request, user, password):
    """Raise ValidationError unless `password` belongs to `user`.

    Uses authenticate() so it works with every configured backend, including
    LDAP, and counts failures toward the account lockout.
    """
    ip = throttle.client_ip(request)
    if throttle.is_locked(user.email, ip):
        raise ValidationError(throttle.lock_message(), code="locked")
    checked = authenticate(request, username=user.email, password=password or "")
    if checked is None or checked.pk != user.pk:
        throttle.record(user.email, ip, successful=False, kind="signature")
        raise ValidationError("That password is not correct. The decision was not signed.", code="bad_signature")
    throttle.record(user.email, ip, successful=True, kind="signature")


class DelegationForm(forms.ModelForm):
    starts_at = forms.DateTimeField(
        label="From", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
    )
    ends_at = forms.DateTimeField(
        label="Until", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
    )

    class Meta:
        model = Delegation
        fields = ["role", "delegate", "starts_at", "ends_at", "reason"]
        labels = {"delegate": "Acting officer"}

    def __init__(self, *args, delegator=None, created_by=None, **kwargs):
        self.delegator = delegator
        self.created_by = created_by
        super().__init__(*args, **kwargs)
        held = [r for r in Role if r in DELEGABLE_ROLES and r in delegator.direct_roles]
        self.fields["role"].choices = [(r.value, r.label) for r in held]
        self.fields["delegate"].queryset = User.objects.filter(is_active=True).exclude(pk=delegator.pk)
        self.fields["starts_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["ends_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if not self.is_bound:
            now = timezone.localtime().replace(second=0, microsecond=0)
            self.initial.setdefault("starts_at", now)
            self.initial.setdefault("ends_at", now + timedelta(days=7))
        self.instance.delegator = delegator
        self.instance.created_by = created_by

    def clean(self):
        cleaned = super().clean()
        ends_at = cleaned.get("ends_at")
        if ends_at and ends_at <= timezone.now():
            self.add_error("ends_at", "The end must be in the future.")
        return cleaned
