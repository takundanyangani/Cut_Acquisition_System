from django import forms

from apps.core.models import Campus

from .models import MANUAL_STATUS_CHANGES, Asset, AssetCategory, AssetStatus


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "category",
            "asset_tag",
            "serial_number",
            "make",
            "model",
            "specification",
            "condition",
            "campus",
            "building",
            "office",
            "supplier",
            "purchase_order_ref",
            "acquired_on",
            "warranty_expires_on",
            "notes",
        ]
        widgets = {
            "specification": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "acquired_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "warranty_expires_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {"campus": "Store location: campus", "building": "Building", "office": "Room"}
        help_texts = {"asset_tag": "Leave blank to generate the next ICT tag."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = AssetCategory.objects.filter(is_active=True)
        self.fields["campus"].queryset = Campus.objects.filter(is_active=True)
        self.fields["category"].empty_label = "Choose a type"
        self.fields["campus"].empty_label = "Choose a campus"
        self.fields["asset_tag"].required = False
        for name in ("acquired_on", "warranty_expires_on"):
            self.fields[name].input_formats = ["%Y-%m-%d"]
        if self.instance.pk:
            # The tag identifies the asset everywhere; keep it fixed once issued.
            self.fields["asset_tag"].disabled = True

    def clean_serial_number(self):
        value = (self.cleaned_data.get("serial_number") or "").strip().upper() or None
        if value and Asset.objects.exclude(pk=self.instance.pk).filter(serial_number=value).exists():
            raise forms.ValidationError("This serial number is already in the register.")
        return value

    def clean_asset_tag(self):
        if self.instance.pk:
            return self.instance.asset_tag
        value = (self.cleaned_data.get("asset_tag") or "").strip().upper()
        if value and Asset.objects.filter(asset_tag=value).exists():
            raise forms.ValidationError("This asset tag is already in use.")
        return value


class StatusChangeForm(forms.Form):
    status = forms.ChoiceField(label="New status")
    note = forms.CharField(label="Reason", widget=forms.Textarea(attrs={"rows": 2}), max_length=1000)

    def __init__(self, *args, asset, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = MANUAL_STATUS_CHANGES.get(AssetStatus(asset.status), set())
        self.fields["status"].choices = [(s.value, s.label) for s in AssetStatus if s in allowed]


class AssetFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search", max_length=100)
    status = forms.ChoiceField(required=False, choices=[("", "All statuses")] + list(AssetStatus.choices))
    category = forms.ModelChoiceField(
        required=False, queryset=AssetCategory.objects.all(), empty_label="All types", label="Type"
    )
