"""Control center (Pusat Kendali): one place for portal accounts, AI agents and activity."""

import re
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.ai_engine import gemini
from apps.ai_engine.agents import CONTRACT, DEFAULTS, READER, compose_prompt, ensure_agents, load_configs, restore_version, save_agent
from apps.ai_engine.models import AgentProcess, AgentProfile
from apps.audit.models import SecurityEventLog
from apps.ledger.models import BankStatementDocument

from .events import EVENT_GROUPS, EVENT_LABELS, cms_event
from .internal_gate import SESSION_KEY
from .roles import ADMINISTRATOR, ACCOUNTANT, SUPERUSER, role_of

User = get_user_model()
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,99}$")


def _context(request, tab, **extra):
    return {"control_tab": tab, **extra}


def _label_events(events):
    for event in events:
        event.label = EVENT_LABELS.get(event.event_type, event.event_type)
        event.group = next((key for key, (_, types) in EVENT_GROUPS.items() if event.event_type in types), "other")
        details = event.details if isinstance(event.details, dict) else {}
        if event.actor:
            event.who = event.actor.username
        elif details.get("via") == "cms" or event.event_type.startswith(("agent.", "internal.")):
            event.who = "CMS (token)"
        else:
            event.who = details.get("username") or "—"
        subject = details.get("username")
        event.subject = subject if subject and event.who != subject else ""
        event.agent = details.get("agent", "")
        event.version = details.get("version", "")
    return events


def overview(request):
    agents = list(ensure_agents().values())
    agents.sort(key=lambda agent: list(AgentProcess.values).index(agent.process))
    users = list(User.objects.prefetch_related("groups"))
    roles = [role_of(user) for user in users]
    documents = BankStatementDocument.objects.exclude(ai_result={})
    last_document = documents.order_by("-updated_at").first()
    reader = load_configs()[READER]
    return render(request, "control/overview.html", _context(
        request, "overview",
        user_stats={
            "total": len(users),
            "active": sum(1 for user in users if user.is_active),
            "staff": sum(1 for user, role in zip(users, roles) if user.is_active and role in (ADMINISTRATOR, ACCOUNTANT, SUPERUSER)),
            "inactive": sum(1 for user in users if not user.is_active),
        },
        agents=agents,
        enabled_agents=sum(1 for agent in agents if agent.is_enabled),
        ai_status={
            "configured": gemini.is_configured(),
            "model": reader.model_name,
            "reader_enabled": reader.is_enabled,
            "documents": documents.count(),
            "last_document": last_document,
        },
        events=_label_events(list(SecurityEventLog.objects.select_related("actor").order_by("-created_at")[:8])),
    ))


def activity(request):
    events = SecurityEventLog.objects.select_related("actor").order_by("-created_at")
    group = request.GET.get("group", "")
    query = request.GET.get("q", "").strip()
    if group in EVENT_GROUPS:
        events = events.filter(event_type__in=EVENT_GROUPS[group][1])
    if query:
        events = events.filter(Q(actor__username__icontains=query) | Q(details__username__icontains=query) | Q(ip_address__icontains=query))
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    _label_events(page.object_list)
    return render(request, "control/activity.html", _context(
        request, "activity", page=page, group=group, query=query,
        groups=[(key, label) for key, (label, _) in EVENT_GROUPS.items()],
    ))


def agent_list(request):
    agents = ensure_agents()
    ordered = [agents[process] for process in AgentProcess.values if process in agents]
    reader = load_configs()[READER]
    return render(request, "control/agents.html", _context(
        request, "agents", agents=ordered, configured=gemini.is_configured(), default_model=settings.GEMINI_MODEL,
        reader_model=reader.model_name,
    ))


class AgentForm(forms.Form):
    instruction = forms.CharField(label="Instruksi", widget=forms.Textarea(attrs={"rows": 14, "spellcheck": "false"}), max_length=8000)
    is_enabled = forms.BooleanField(label="Agen aktif", required=False)
    model_name = forms.CharField(label="Model Gemini", required=False, max_length=100,
                                 help_text="Kosongkan untuk memakai GEMINI_MODEL dari .env.")
    temperature = forms.DecimalField(label="Temperature", min_value=Decimal("0"), max_value=Decimal("1"), decimal_places=2,
                                     required=False, help_text="0 = paling konsisten (disarankan untuk angka keuangan).")
    note = forms.CharField(label="Catatan perubahan", required=False, max_length=255,
                           widget=forms.TextInput(attrs={"placeholder": "Contoh: tambah aturan untuk bank BCA"}))

    def __init__(self, *args, owns_call, **kwargs):
        super().__init__(*args, **kwargs)
        if not owns_call:
            del self.fields["model_name"]
            del self.fields["temperature"]

    def clean_instruction(self):
        instruction = self.cleaned_data["instruction"].replace("\r\n", "\n").strip()
        if len(instruction) < 20:
            raise forms.ValidationError("Instruksi terlalu singkat. Jelaskan tugas agen minimal satu kalimat.")
        return instruction

    def clean_model_name(self):
        name = self.cleaned_data.get("model_name", "").strip()
        if name and not MODEL_NAME.match(name):
            raise forms.ValidationError("Nama model tidak valid. Contoh: gemini-flash-latest atau gemini-2.5-pro.")
        return name


def agent_edit(request, process):
    ensure_agents()
    agent = get_object_or_404(AgentProfile, process=process)
    owns_call = agent.process == READER
    initial = {"instruction": agent.instruction, "is_enabled": agent.is_enabled, "model_name": agent.model_name, "temperature": agent.temperature}
    form = AgentForm(request.POST or None, initial=initial, owns_call=owns_call)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        changes = {}
        for field in ("instruction", "is_enabled", "model_name", "temperature"):
            if field not in data:
                continue
            value = data[field] if data[field] is not None else (Decimal("0") if field == "temperature" else data[field])
            if getattr(agent, field) != value:
                changes[field] = value
        if not changes:
            messages.info(request, "Tidak ada perubahan yang disimpan.")
            return redirect("portal_users:agent-edit", process=agent.process)
        for field, value in changes.items():
            setattr(agent, field, value)
        save_agent(agent, user=None, note=data.get("note", ""))
        cms_event(request, "agent.updated", agent=agent.process, version=agent.version, fields=sorted(changes))
        messages.success(request, f"{agent.name} disimpan sebagai versi {agent.version}. Dokumen berikutnya memakai instruksi ini.")
        return redirect("portal_users:agent-edit", process=agent.process)
    return render(request, "control/agent_edit.html", _context(
        request, "agents", agent=agent, form=form, owns_call=owns_call, versions=agent.versions.select_related("created_by")[:30],
        default_instruction=DEFAULTS[agent.process]["instruction"], default_model=settings.GEMINI_MODEL,
    ))


@require_POST
def agent_restore(request, process, version):
    agent = get_object_or_404(AgentProfile, process=process)
    snapshot = get_object_or_404(agent.versions, version=version)
    restore_version(agent, snapshot, user=None)
    cms_event(request, "agent.restored", agent=agent.process, version=agent.version, restored_from=version)
    messages.success(request, f"Versi {version} dipulihkan sebagai versi {agent.version}.")
    return redirect("portal_users:agent-edit", process=agent.process)


def agent_preview(request):
    from apps.ledger.document_reader import chart_of_accounts_text

    configs = load_configs()
    return render(request, "control/agent_preview.html", _context(
        request, "agents", prompt=compose_prompt(chart_of_accounts_text(), configs), reader=configs[READER], contract=CONTRACT,
    ))



@require_POST
def lock(request):
    """Close the CMS for this browser (removes the token unlock, keeps any portal login)."""
    request.session.pop(SESSION_KEY, None)
    cms_event(request, "internal.locked", area="control")
    return redirect("portal_users:control")
