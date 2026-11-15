"""Формы регистрации и логина."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import get_user_model

User = get_user_model()


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={
            "class": "aui-input", "placeholder": "••••••••",
            "autocomplete": "new-password",
        }),
    )
    password2 = forms.CharField(
        label="Пароль (ещё раз)",
        widget=forms.PasswordInput(attrs={
            "class": "aui-input", "placeholder": "••••••••",
            "autocomplete": "new-password",
        }),
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "aui-input", "placeholder": "username", "autocomplete": "username"}),
            "email": forms.EmailInput(attrs={"class": "aui-input", "placeholder": "you@example.com", "autocomplete": "email"}),
            "first_name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "Имя"}),
            "last_name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "Фамилия"}),
        }
        labels = {
            "username": "Логин", "email": "Email",
            "first_name": "Имя", "last_name": "Фамилия",
        }

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Пользователь с таким логином уже существует")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if not email:
            raise forms.ValidationError("Email обязателен")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже существует")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Пароли не совпадают")
        if p1 and len(p1) < 6:
            raise forms.ValidationError("Пароль должен быть минимум 6 символов")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.is_active = False
        user.registration_status = "pending"
        if commit:
            user.save()
        return user


class CerberusLoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Логин",
        widget=forms.TextInput(attrs={
            "class": "aui-input", "placeholder": "username",
            "autocomplete": "username", "autofocus": True,
        }),
    )
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={
            "class": "aui-input", "placeholder": "••••••••",
            "autocomplete": "current-password",
        }),
    )

    def clean(self):
        """Переопределяем clean, чтобы показать «pending» / «rejected»
        даже если is_active=False (Django иначе скрывает причину).
        """
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username and password:
            from django.contrib.auth import authenticate
            from django.contrib.auth import get_user_model
            User = get_user_model()

            # 1. Найдём пользователя вручную (даже если is_active=False)
            user_obj = User.objects.filter(username=username).first()
            if user_obj:
                if user_obj.registration_status == "pending":
                    raise forms.ValidationError(
                        "Ваша регистрация ещё не подтверждена администратором.",
                        code="pending",
                    )
                if user_obj.registration_status == "rejected":
                    raise forms.ValidationError(
                        "Ваша регистрация отклонена. Обратитесь к администратору.",
                        code="rejected",
                    )

            # 2. Стандартная аутентификация
            self.user_cache = authenticate(
                self.request, username=username, password=password,
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data
