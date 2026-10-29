from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from apps.accounts.models import Role
from apps.assets.models import Asset, AssetCategory, AssetStatus, Condition
from apps.core.models import Campus
from apps.core.workdays import add_working_days

from .models import Attachment, Kind, Requisition, RequisitionItem


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


class RequisitionForm(forms.ModelForm):
    attachments = MultipleFileField(
        label="Supporting documents",
        required=False,
        help_text="Optional. PDF, Word, Excel, PNG or JPEG, up to 10 MB each.",
    )

    class Meta:
        model = Requisition
        fields = [
            "title",
            "justification",
            "priority",
            "needed_by",
            "campus",
            "building",
            "office",
            "asset_to_replace",
            "replaced_item_description",
            "replacement_reason",
        ]
        widgets = {
            "justification": forms.Textarea(attrs={"rows": 4}),
            "needed_by": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {
            "title": "What do you need?",
            "justification": "Why do you need it?",
            "needed_by": "Needed by (optional)",
            "campus": "Campus",
            "building": "Building",
            "office": "Office or room",
            "asset_to_replace": "Machine to replace",
            "replaced_item_description": "Not in the list? Describe it",
            "replacement_reason": "Reason for replacement",
        }
        help_texts = {
            "title": "A short name, for example: Laptop for new lecturer.",
            "justification": "Say what the equipment is for. Reviewers use this to decide.",
        }

    def __init__(self, *args, kind, user, **kwargs):
        self.kind = kind
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["campus"].queryset = Campus.objects.filter(is_active=True)
        self.fields["campus"].required = True
        self.fields["building"].required = True
        self.fields["needed_by"].input_formats = ["%Y-%m-%d"]
        if kind == Kind.REPLACEMENT:
            from .services import replaceable_assets

            self.fields["asset_to_replace"].queryset = replaceable_assets(user)
            self.fields["asset_to_replace"].required = False
            self.fields["asset_to_replace"].empty_label = "Choose one of your machines"
            self.fields["replacement_reason"].required = True
            self.fields["justification"].label = "Explain the reasons for replacement"
            self.fields[
                "justification"
            ].help_text = "CUT ICT 42 §5.1 asks you to state the reasons. Describe the fault or limitation."
        else:
            for name in ("asset_to_replace", "replaced_item_description", "replacement_reason"):
                del self.fields[name]
        self.fields["campus"].empty_label = "Choose a campus"
        if "replacement_reason" in self.fields:
            self.fields["replacement_reason"].choices = [("", "Choose a reason")] + list(
                self.fields["replacement_reason"].choices
            )[1:]
        if not self.instance.pk:
            # New requests start from the location on the user's profile.
            for name, value in (("campus", user.campus_id), ("building", user.building), ("office", user.office)):
                if not self.initial.get(name):
                    self.initial[name] = value

    def clean_needed_by(self):
        value = self.cleaned_data.get("needed_by")
        if value and value < timezone.localdate():
            raise forms.ValidationError("Choose today or a later date.")
        return value

    def clean(self):
        cleaned = super().clean()
        if self.kind == Kind.REPLACEMENT:
            if not cleaned.get("asset_to_replace") and not (cleaned.get("replaced_item_description") or "").strip():
                self.add_error("asset_to_replace", "Choose the machine to replace, or describe it below.")
        return cleaned


class ItemForm(forms.ModelForm):
    class Meta:
        model = RequisitionItem
        fields = ["category", "description", "quantity", "requester_notes"]
        labels = {
            "category": "Type",
            "description": "Item",
            "quantity": "Quantity",
            "requester_notes": "Notes for the technician",
        }
        widgets = {
            "requester_notes": forms.Textarea(attrs={"rows": 2}),
            "quantity": forms.NumberInput(attrs={"min": 1, "max": 200, "inputmode": "numeric"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = AssetCategory.objects.filter(is_active=True)
        self.fields["category"].empty_label = "Choose a type"
        self.fields["requester_notes"].required = False
        self.fields["description"].widget.attrs["placeholder"] = "For example: laptop with at least 16 GB memory"


class BaseItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        live = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not live:
            raise forms.ValidationError("Add at least one item.")


ItemFormSet = inlineformset_factory(
    Requisition,
    RequisitionItem,
    form=ItemForm,
    formset=BaseItemFormSet,
    extra=0,
    min_num=1,
    validate_min=False,
    max_num=30,
    validate_max=True,
    can_delete=True,
)


# -- Decision forms ------------------------------------------------------------


class DecisionForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    comment = forms.CharField(label="Comment", widget=forms.Textarea(attrs={"rows": 4}), required=False)
    password = forms.CharField(
        label="Your password",
        strip=False,
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Entering your password signs this decision in your name.",
    )

    def __init__(self, *args, transition, requisition, user, signing, **kwargs):
        self.transition = transition
        self.requisition = requisition
        self.user = user
        super().__init__(*args, **kwargs)
        self.initial.setdefault("version", requisition.version)
        if transition.comment == "none":
            del self.fields["comment"]
        elif transition.comment_required:
            self.fields["comment"].required = True
            self.fields["comment"].label = "Reason" if transition.tone in {"danger", "secondary"} else "Comment"
        if signing:
            self.fields["password"].required = True
        else:
            del self.fields["password"]

    def workflow_data(self):
        return {"comment": self.cleaned_data.get("comment", "")}


def technician_queryset(exclude_user_id=None):
    User = get_user_model()
    qs = User.objects.filter(is_active=True, role_grants__role=Role.TECHNICIAN).distinct()
    if exclude_user_id:
        qs = qs.exclude(pk=exclude_user_id)
    return qs.order_by("first_name", "last_name")


class AssignForm(DecisionForm):
    technician = forms.ModelChoiceField(queryset=None, label="Technician", empty_label="Choose a technician")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technician"].queryset = technician_queryset(self.requisition.requester_id)
        if self.requisition.assigned_technician_id:
            self.initial.setdefault("technician", self.requisition.assigned_technician_id)
        self.order_fields(["technician", "comment", "password"])

    def workflow_data(self):
        data = super().workflow_data()
        data["technician"] = self.cleaned_data["technician"]
        return data


class RecommendForm(AssignForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technician"].label = "Technician to prepare the specification sheet"


class SpecificationsForm(DecisionForm):
    spec_files = MultipleFileField(
        label="Specification sheets",
        required=False,
        help_text="Required unless already attached. PDF, Word, Excel, PNG or JPEG.",
    )
    procurement_reference = forms.CharField(label="Procurement requisition number", max_length=60, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = list(self.requisition.items.all())
        for item in self.items:
            self.fields[f"spec_{item.pk}"] = forms.CharField(
                label=f"Recommended specification: {item.quantity} x {item.description}",
                widget=forms.Textarea(attrs={"rows": 3}),
                initial=item.recommended_specification,
            )
            self.fields[f"cost_{item.pk}"] = forms.DecimalField(
                label="Estimated unit cost",
                required=False,
                min_value=0,
                max_digits=12,
                decimal_places=2,
                initial=item.estimated_unit_cost,
            )
        if self.transition.code == "initiate_procurement":
            self.fields["procurement_reference"].required = True
        else:
            del self.fields["procurement_reference"]

    def item_fields(self):
        return [(item, self[f"spec_{item.pk}"], self[f"cost_{item.pk}"]) for item in self.items]

    def workflow_data(self):
        data = super().workflow_data()
        data["specifications"] = {
            item.pk: {
                "specification": self.cleaned_data[f"spec_{item.pk}"],
                "cost": self.cleaned_data.get(f"cost_{item.pk}"),
            }
            for item in self.items
        }
        data["files"] = self.cleaned_data.get("spec_files") or []
        if "procurement_reference" in self.cleaned_data:
            data["procurement_reference"] = self.cleaned_data["procurement_reference"]
        return data


class ProcurementForm(DecisionForm):
    procurement_reference = forms.CharField(label="Procurement requisition number", max_length=60)

    def workflow_data(self):
        data = super().workflow_data()
        data["procurement_reference"] = self.cleaned_data["procurement_reference"]
        return data


class FollowUpForm(DecisionForm):
    next_follow_up_on = forms.DateField(
        label="Next follow-up",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["comment"].label = "Who did you contact, and what did they say?"
        self.initial.setdefault(
            "next_follow_up_on", add_working_days(timezone.localdate(), settings.ACQ_FOLLOW_UP_INTERVAL_DAYS)
        )

    def workflow_data(self):
        data = super().workflow_data()
        data["next_follow_up_on"] = self.cleaned_data["next_follow_up_on"]
        return data


class ProcurementUpdateForm(DecisionForm):
    procurement_status = forms.ChoiceField(
        label="Status",
        required=False,
        choices=[
            ("", "No change"),
            ("Sourcing quotations", "Sourcing quotations"),
            ("Purchase order raised", "Purchase order raised"),
            ("Awaiting delivery from supplier", "Awaiting delivery from supplier"),
            ("Delivered to ICT", "Delivered to ICT"),
            ("On hold", "On hold"),
        ],
    )
    purchase_order = forms.CharField(label="Purchase order number", max_length=60, required=False)
    expected_delivery_on = forms.DateField(
        label="Expected delivery",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["procurement_status", "purchase_order", "expected_delivery_on", "comment"])

    def workflow_data(self):
        data = super().workflow_data()
        for name in ("procurement_status", "purchase_order", "expected_delivery_on"):
            data[name] = self.cleaned_data.get(name)
        return data


class DeliveredAssetForm(forms.Form):
    category = forms.ModelChoiceField(queryset=AssetCategory.objects.none(), label="Type")
    make = forms.CharField(max_length=80)
    model = forms.CharField(max_length=120)
    serial_number = forms.CharField(max_length=80, required=False, label="Serial number")
    asset_tag = forms.CharField(
        max_length=40, required=False, label="Asset tag", help_text="Leave blank to generate one."
    )
    specification = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    condition = forms.ChoiceField(choices=Condition.choices, initial=Condition.NEW)
    warranty_expires_on = forms.DateField(
        required=False,
        label="Warranty ends",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = AssetCategory.objects.filter(is_active=True)
        self.fields["category"].empty_label = "Choose a type"

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        if category and category.requires_serial and not (cleaned.get("serial_number") or "").strip():
            self.add_error("serial_number", f"{category} items need a serial number.")
        return cleaned


class BaseDeliveredFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        serials, tags = set(), set()
        for form in self.forms:
            if not form.has_changed() or not form.cleaned_data:
                continue
            serial = (form.cleaned_data.get("serial_number") or "").strip().upper()
            tag = (form.cleaned_data.get("asset_tag") or "").strip().upper()
            if serial:
                if serial in serials:
                    raise forms.ValidationError(f"Serial number {serial} appears twice.")
                serials.add(serial)
            if tag:
                if tag in tags:
                    raise forms.ValidationError(f"Asset tag {tag} appears twice.")
                tags.add(tag)


DeliveredAssetFormSet = forms.formset_factory(
    DeliveredAssetForm, formset=BaseDeliveredFormSet, extra=1, max_num=50, validate_max=True
)


class DeliveryForm(DecisionForm):
    delivery_complete = forms.BooleanField(
        label="The order is complete. Nothing more is expected from the supplier.", required=False
    )
    supplier = forms.CharField(max_length=160, required=False)
    purchase_order_ref = forms.CharField(label="Purchase order number", max_length=60, required=False)
    delivery_files = MultipleFileField(label="Delivery note", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.assets_formset = DeliveredAssetFormSet(
            self.data if self.is_bound else None, self.files if self.is_bound else None, prefix="asset"
        )

    def is_valid(self):
        return super().is_valid() and self.assets_formset.is_valid()

    def workflow_data(self):
        data = super().workflow_data()
        today = timezone.localdate()
        assets = []
        for form in self.assets_formset.forms:
            if not form.has_changed() or not form.cleaned_data:
                continue
            c = form.cleaned_data
            assets.append(
                {
                    "category": c["category"],
                    "make": c["make"].strip(),
                    "model": c["model"].strip(),
                    "serial_number": c.get("serial_number") or None,
                    "asset_tag": (c.get("asset_tag") or "").strip(),
                    "specification": c["specification"].strip(),
                    "condition": c["condition"],
                    "warranty_expires_on": c.get("warranty_expires_on"),
                    "supplier": (self.cleaned_data.get("supplier") or "").strip(),
                    "purchase_order_ref": (self.cleaned_data.get("purchase_order_ref") or "").strip(),
                    "acquired_on": today,
                    "campus": self.requisition.campus,
                }
            )
        data["assets"] = assets
        data["delivery_complete"] = self.cleaned_data.get("delivery_complete")
        data["files"] = self.cleaned_data.get("delivery_files") or []
        return data


class VoucherForm(DecisionForm):
    assets = forms.ModelMultipleChoiceField(
        queryset=Asset.objects.none(), widget=forms.CheckboxSelectMultiple, label="Assets to issue"
    )
    campus = forms.ModelChoiceField(queryset=Campus.objects.none())
    building = forms.CharField(max_length=120)
    office = forms.CharField(max_length=60, required=False, label="Office or room")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Notes on the voucher")
    replaced_collected = forms.TypedChoiceField(
        label="Was the old machine collected?",
        choices=[("", "Choose"), ("yes", "Yes, it is back with ICT"), ("no", "No")],
        coerce=lambda v: v == "yes",
        empty_value=None,
        required=False,
    )
    replaced_condition = forms.ChoiceField(
        label="Condition of the old machine", choices=[("", "Choose")] + list(Condition.choices), required=False
    )
    replaced_note = forms.CharField(label="Note about the old machine", max_length=255, required=False)

    def __init__(self, *args, show_all=False, **kwargs):
        super().__init__(*args, **kwargs)
        req = self.requisition
        stock = Asset.objects.filter(status=AssetStatus.IN_STOCK).select_related("category", "source_requisition")
        categories = list(req.items.values_list("category_id", flat=True))
        own = list(req.delivered_assets.filter(status=AssetStatus.IN_STOCK).values_list("pk", flat=True))
        if not show_all:
            stock = stock.filter(category_id__in=categories) | stock.filter(pk__in=own)
        self.fields["assets"].queryset = stock.distinct().order_by("category__name", "asset_tag")
        self.own_asset_ids = set(own)
        self.fields["campus"].queryset = Campus.objects.filter(is_active=True)
        self.fields["campus"].empty_label = "Choose a campus"
        self.initial.setdefault("campus", req.campus_id)
        self.initial.setdefault("building", req.building)
        self.initial.setdefault("office", req.office)
        if own:
            self.initial.setdefault("assets", own)
        self.replacement = req.kind == Kind.REPLACEMENT and req.asset_to_replace_id is not None
        if not self.replacement:
            for name in ("replaced_collected", "replaced_condition", "replaced_note"):
                del self.fields[name]
        else:
            self.fields["replaced_collected"].required = True

    def stock_rows(self):
        """(checkbox, asset, delivered for this request) for the stock table."""
        assets = {a.pk: a for a in self.fields["assets"].queryset}
        rows = []
        for widget in self["assets"]:
            value = widget.data["value"]
            asset = assets.get(int(getattr(value, "value", value)))
            if asset is not None:
                rows.append((widget, asset, asset.pk in self.own_asset_ids))
        return rows

    def workflow_data(self):
        data = super().workflow_data()
        data["asset_ids"] = [a.pk for a in self.cleaned_data["assets"]]
        for name in ("campus", "building", "office", "notes"):
            data[name] = self.cleaned_data.get(name)
        if self.replacement:
            data["replaced_collected"] = self.cleaned_data.get("replaced_collected")
            data["replaced_condition"] = self.cleaned_data.get("replaced_condition")
            data["replaced_note"] = self.cleaned_data.get("replaced_note")
        return data


class AcknowledgeForm(DecisionForm):
    confirm_serials = forms.BooleanField(label="The serial numbers match the items I received.")
    confirm_specification = forms.BooleanField(label="The specifications match what is on the voucher.")
    confirm_condition = forms.BooleanField(label="The condition of each item is as stated.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["confirm_serials", "confirm_specification", "confirm_condition", "comment", "password"])


class NoteForm(forms.Form):
    note = forms.CharField(
        label="Add a note",
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=4000,
        help_text="Everyone who can see this request can read notes.",
    )


class AttachmentForm(forms.Form):
    kind = forms.ChoiceField(choices=Attachment.Kind.choices, label="Type of document")
    files = MultipleFileField(label="Files")

    def clean_files(self):
        files = self.cleaned_data.get("files") or []
        if not files:
            raise forms.ValidationError("Choose at least one file.")
        return files


FORMS = {
    "decision": DecisionForm,
    "assign": AssignForm,
    "recommend": RecommendForm,
    "specifications": SpecificationsForm,
    "procurement": ProcurementForm,
    "follow_up": FollowUpForm,
    "procurement_update": ProcurementUpdateForm,
    "delivery": DeliveryForm,
    "voucher": VoucherForm,
    "acknowledge": AcknowledgeForm,
}


class RequestFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search", max_length=100)
    kind = forms.ChoiceField(required=False, choices=[("", "All types")] + list(Kind.choices), label="Type")
    state = forms.ChoiceField(required=False, label="Stage")

    def __init__(self, *args, **kwargs):
        from .models import State

        super().__init__(*args, **kwargs)
        self.fields["state"].choices = [("", "All stages")] + [(s.value, s.label) for s in State if s != State.DRAFT]
