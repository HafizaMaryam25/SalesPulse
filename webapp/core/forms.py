from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm

from etl.validators import DATASETS

from .models import DATASET_CHOICES, InventoryItem


class SignInForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"class": "input", "placeholder": "e.g. analyst", "autocomplete": "username"})
        self.fields["password"].widget.attrs.update(
            {"class": "input", "placeholder": "Your password", "autocomplete": "current-password"})


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ["sku", "category", "name", "product_id", "unit_price", "unit_cost",
                  "stock_qty", "reorder_level", "is_active"]
        widgets = {
            "sku": forms.TextInput(attrs={"placeholder": "SP-000123"}),
            "name": forms.TextInput(attrs={"placeholder": "Product name customers see"}),
            "category": forms.TextInput(attrs={"list": "category-options", "placeholder": "health_beauty"}),
            "product_id": forms.TextInput(attrs={"placeholder": "32-character Olist ID (optional)"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "unit_cost": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "stock_qty": forms.NumberInput(attrs={"min": "0"}),
            "reorder_level": forms.NumberInput(attrs={"min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "is_active":
                field.widget.attrs.setdefault("class", "input")

    def clean_sku(self):
        return self.cleaned_data["sku"].strip().upper()

    def clean_category(self):
        return self.cleaned_data["category"].strip().lower().replace(" ", "_")

    def clean(self):
        data = super().clean()
        price, cost = data.get("unit_price"), data.get("unit_cost")
        if price is not None and cost is not None and cost > price:
            self.add_error("unit_cost", "Cost is higher than the selling price. "
                                        "Lower the cost or raise the price.")
        return data


class UploadForm(forms.Form):
    dataset = forms.ChoiceField(choices=DATASET_CHOICES,
                                widget=forms.Select(attrs={"class": "input"}))
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": ".csv", "class": "file-input"}))

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Choose a .csv file.")
        limit = settings.MAX_UPLOAD_MB * 1024 * 1024
        if f.size > limit:
            raise forms.ValidationError(f"The file is larger than {settings.MAX_UPLOAD_MB} MB.")
        return f

    @property
    def spec(self):
        return DATASETS[self.cleaned_data["dataset"]]
