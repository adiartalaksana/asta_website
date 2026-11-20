"""AI agents: one editable instruction per process, composed into the Gemini request.

The *contract* (JSON schema, amount/date formats, money in/out semantics) is fixed in code
because the server parses and re-checks the answer. Administrators edit only the
business instructions of each agent from the control center (/ledger/control/agents/).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.utils import DatabaseError

from .models import AgentInstructionVersion, AgentProcess, AgentProfile

READER = AgentProcess.STATEMENT_READER
MAPPER = AgentProcess.ACCOUNT_MAPPER
SUMMARIZER = AgentProcess.STATEMENT_SUMMARIZER

# Order in which agent instructions are placed in the prompt.
PIPELINE = [READER, MAPPER, SUMMARIZER]

DEFAULTS = {
    READER: {
        "name": "Pembaca rekening koran",
        "description": "Membaca PDF/gambar rekening koran dan menyalin setiap mutasi. Agen ini memegang pengaturan model Gemini.",
        "instruction": (
            "You are an accounting assistant reading an Indonesian bank statement (rekening koran / mutasi rekening).\n"
            "- Include EVERY transaction row on EVERY page, in the printed order. Do not merge, skip or invent rows.\n"
            "- Skip rows that are only carried-forward/opening/closing balance lines (e.g. \"SALDO AWAL\", \"SALDO AKHIR\"); "
            "put those values in opening_balance / closing_balance.\n"
            "- If the statement prints no opening balance, derive it from the first row's balance and amount.\n"
            "- Indonesian statements use day/month order.\n"
            "- warnings: list anything blurry, cut off, ambiguous or inconsistent, in Bahasa Indonesia. Never guess silently."
        ),
    },
    MAPPER: {
        "name": "Pemeta akun COA",
        "description": "Menyarankan akun Chart of Accounts untuk setiap mutasi. Saran tetap ditinjau manusia sebelum verifikasi.",
        "instruction": (
            "For each transaction choose the best matching account code from the chart of accounts below, "
            "based on the description and direction of the money. Leave the code empty when you are not confident; "
            "an empty suggestion is better than a wrong one. Never use the bank account itself as the counter account."
        ),
    },
    SUMMARIZER: {
        "name": "Perangkum & rekap",
        "description": "Menulis ringkasan rekening koran dalam Bahasa Indonesia untuk halaman review.",
        "instruction": (
            "Write the summary in Bahasa Indonesia, 3-6 sentences: the period, the number of transactions, "
            "total money in and out, the largest or notable transactions, and anything unusual that the reviewer should check."
        ),
    },
}

CONTRACT = """SYSTEM CONTRACT (fixed by the application; always follow it):
- Answer only with JSON that matches the response schema.
- Amounts: plain decimals, '.' as decimal separator, no thousands separators, no currency ("Rp 1.234.567,89" -> "1234567.89").
- money_in = money received by the account holder (CR / Kredit / Masuk); money_out = money leaving (DB / Debet / Keluar). Exactly one of them per row.
- Dates as YYYY-MM-DD; time as HH:MM or empty.
- If the document is not a bank statement, set is_bank_statement=false and explain in warnings."""

DISABLED_MAPPER = "suggested_account_code: always return an empty string (account mapping is switched off)."
DISABLED_SUMMARY = "summary: return an empty string (the summary agent is switched off)."


@dataclass(frozen=True)
class AgentConfig:
    process: str
    name: str
    instruction: str
    is_enabled: bool
    model_name: str
    temperature: Decimal
    version: int


def ensure_agents() -> dict[str, AgentProfile]:
    """Create any missing agent with its default instruction (version 1)."""
    agents = {agent.process: agent for agent in AgentProfile.objects.all()}
    for process, default in DEFAULTS.items():
        if process in agents:
            continue
        with transaction.atomic():
            agent = AgentProfile.objects.create(process=process, **default)
            AgentInstructionVersion.objects.create(
                agent=agent, version=1, instruction=agent.instruction, is_enabled=True,
                model_name="", temperature=agent.temperature, note="Instruksi bawaan",
            )
        agents[process] = agent
    return agents


def load_configs() -> dict[str, AgentConfig]:
    try:
        agents = ensure_agents()
    except DatabaseError:  # table not migrated yet: fall back to the defaults
        agents = {}
    configs = {}
    for process, default in DEFAULTS.items():
        agent = agents.get(process)
        configs[process] = AgentConfig(
            process=process,
            name=agent.name if agent else default["name"],
            instruction=agent.instruction if agent else default["instruction"],
            is_enabled=agent.is_enabled if agent else True,
            model_name=(agent.model_name if agent else "") or settings.GEMINI_MODEL,
            temperature=agent.temperature if agent else Decimal("0"),
            version=agent.version if agent else 1,
        )
    return configs


def compose_prompt(accounts_text: str, configs: dict[str, AgentConfig] | None = None) -> str:
    configs = configs or load_configs()
    sections = [CONTRACT]
    reader = configs[READER]
    sections.append(f"## Agent: {reader.name} (v{reader.version})\n{reader.instruction.strip()}")

    mapper = configs[MAPPER]
    if mapper.is_enabled:
        sections.append(
            f"## Agent: {mapper.name} (v{mapper.version})\n{mapper.instruction.strip()}\n"
            f"suggested_account_code must be one of these codes (code - name - type), or empty:\n"
            f"{accounts_text or '(belum ada akun)'}"
        )
    else:
        sections.append(DISABLED_MAPPER)

    summarizer = configs[SUMMARIZER]
    if summarizer.is_enabled:
        sections.append(f"## Agent: {summarizer.name} (v{summarizer.version})\n{summarizer.instruction.strip()}")
    else:
        sections.append(DISABLED_SUMMARY)
    return "\n\n".join(sections)


def save_agent(agent: AgentProfile, *, user, note: str = "") -> AgentProfile:
    """Persist edited fields as a new version (history is append-only)."""
    with transaction.atomic():
        locked = AgentProfile.objects.select_for_update().get(pk=agent.pk)
        agent.version = locked.version + 1
        agent.updated_by = user
        agent.save()
        AgentInstructionVersion.objects.create(
            agent=agent, version=agent.version, instruction=agent.instruction, is_enabled=agent.is_enabled,
            model_name=agent.model_name, temperature=agent.temperature, note=note[:255], created_by=user,
        )
    return agent


def restore_version(agent: AgentProfile, version: AgentInstructionVersion, *, user) -> AgentProfile:
    agent.instruction = version.instruction
    agent.is_enabled = version.is_enabled
    agent.model_name = version.model_name
    agent.temperature = version.temperature
    return save_agent(agent, user=user, note=f"Dipulihkan dari versi {version.version}")
