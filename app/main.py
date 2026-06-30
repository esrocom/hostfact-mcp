import os
import httpx
import json
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI(title="Hostfact MCP Server", version="1.4.0")

# ─────────────────────────────────────────────
# Audit log
# ─────────────────────────────────────────────

AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "/data/audit.log")

# Write-actions: these mutate data in Hostfact
WRITE_TOOLS = {"edit_product", "edit_service", "add_product", "add_debtor", "add_service", "add_invoice"}

def _audit(tool: str, arguments: dict, result: str, error: bool = False):
    """Append one line to the audit log."""
    try:
        log_dir = Path(AUDIT_LOG_PATH).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        kind = "[WRITE]" if tool in WRITE_TOOLS else "[READ]"
        status = "ERROR" if error else "OK"
        # Redact api_key if accidentally included in arguments
        safe_args = {k: v for k, v in arguments.items() if "key" not in k.lower()}
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f'{ts} {kind} {status} tool={tool} args={json.dumps(safe_args, ensure_ascii=False)} result_preview={result[:120].replace(chr(10), " ")}\n'
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as ex:
        # Never crash the server because of a logging failure
        logging.warning(f"Audit log write failed: {ex}")

HOSTFACT_URL = os.getenv("HOSTFACT_URL", "")
HOSTFACT_API_KEY = os.getenv("HOSTFACT_API_KEY", "")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")

# Hostfact invoice status codes
INVOICE_STATUS = {
    "0": "Concept",
    "1": "Te betalen",
    "2": "Verstuurd",
    "3": "Betaald",
    "4": "Voldaan",
    "5": "Geblokkeerd",
    "6": "Aanmaning",
    "7": "Deurwaarder",
    "8": "Creditfactuur",
    "9": "Gecrediteerd",
}

PERIODIC_LABEL = {
    "m": "maand",
    "k": "kwartaal",
    "j": "jaar",
    "e": "eenmalig",
}

def check_auth(request: Request):
    if not MCP_AUTH_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {MCP_AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

async def hostfact_call(controller: str, action: str, params: dict = {}) -> dict:
    data = {"api_key": HOSTFACT_API_KEY, "controller": controller, "action": action, **params}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(HOSTFACT_URL, data=data)
        resp.raise_for_status()
        return resp.json()

def fmt_status(code) -> str:
    return INVOICE_STATUS.get(str(code), f"Status {code}")

def fmt_periodic(p) -> str:
    return PERIODIC_LABEL.get(str(p), str(p))

# ─────────────────────────────────────────────
# OAuth2 endpoints (minimal, for Claude.ai)
# ─────────────────────────────────────────────

@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    base = os.getenv("MCP_BASE_URL", "https://localhost")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"]
    }

@app.get("/authorize")
@app.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    params = dict(request.query_params)
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code = "hostfact-mcp-auth-code"
    url = f"{redirect_uri}?code={code}"
    if state:
        url += f"&state={state}"
    return RedirectResponse(url=url, status_code=302)

@app.post("/token")
@app.post("/oauth/token")
async def oauth_token(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
    return JSONResponse({
        "access_token": MCP_AUTH_TOKEN,
        "token_type": "bearer",
        "expires_in": 86400
    })

# ─────────────────────────────────────────────
# MCP Tool definitions
# ─────────────────────────────────────────────

TOOLS = [
    # ── DEBITEUREN ──
    {
        "name": "list_debtors",
        "description": "Haal een lijst van debiteuren op uit Hostfact. Optioneel te filteren op naam, e-mail of klantnummer.",
        "inputSchema": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Zoekterm op naam, e-mail of klantnummer"},
            "limit": {"type": "integer", "default": 50}
        }}
    },
    {
        "name": "get_debtor",
        "description": "Haal één debiteur op via debiteurcode (bijv. DB8595) of intern ID.",
        "inputSchema": {"type": "object", "properties": {
            "debtor_code": {"type": "string", "description": "Debiteurcode, bijv. DB8595"},
            "identifier": {"type": "string", "description": "Intern Hostfact ID (alternatief voor debtor_code)"}
        }}
    },

    # ── FACTUREN ──
    {
        "name": "list_invoices",
        "description": (
            "Haal facturen op. Gebruik debtor_code om te filteren op klant (bijv. DB8595). "
            "Gebruik date_from en/of date_to (YYYY-MM-DD) voor een datumperiode. "
            "Gebruik status_filter om op betaalstatus te filteren (bijv. 'Voldaan'). "
            "Geeft factuurnummer, bedrag excl. BTW, status en datum terug."
        ),
        "inputSchema": {"type": "object", "properties": {
            "debtor_code": {"type": "string", "description": "Filter op debiteurcode, bijv. DB8595"},
            "date_from": {"type": "string", "description": "Factuurdatum vanaf (YYYY-MM-DD)"},
            "date_to": {"type": "string", "description": "Factuurdatum t/m (YYYY-MM-DD)"},
            "status_filter": {"type": "string", "description": "Filter op status: Concept, Verstuurd, Voldaan, Aanmaning, etc."},
            "limit": {"type": "integer", "default": 50},
            "offset": {"type": "integer", "default": 0}
        }}
    },
    {
        "name": "get_invoice",
        "description": "Haal één factuur op inclusief alle factuurregels, bedragen en betaalstatus. Gebruik factuurnummer zoals F20261919.",
        "inputSchema": {"type": "object", "required": ["invoice_code"], "properties": {
            "invoice_code": {"type": "string", "description": "Factuurnummer, bijv. F20261919"}
        }}
    },

    # ── CREDITFACTUREN ──
    {
        "name": "list_creditinvoices",
        "description": "Haal creditfacturen op, optioneel gefilterd op debiteurcode of datum.",
        "inputSchema": {"type": "object", "properties": {
            "debtor_code": {"type": "string", "description": "Filter op debiteurcode"},
            "date_from": {"type": "string", "description": "Datum vanaf (YYYY-MM-DD)"},
            "limit": {"type": "integer", "default": 25},
            "offset": {"type": "integer", "default": 0}
        }}
    },
    {
        "name": "get_creditinvoice",
        "description": "Haal één creditfactuur op inclusief regels en bedragen. Gebruik creditfactuurnummer zoals CF0001.",
        "inputSchema": {"type": "object", "required": ["creditinvoice_code"], "properties": {
            "creditinvoice_code": {"type": "string", "description": "Creditfactuurnummer, bijv. CF0001"}
        }}
    },

    # ── ABONNEMENTEN ──
    {
        "name": "list_services",
        "description": "Haal actieve abonnementen op, optioneel gefilterd op debiteur. Toont productcode, omschrijving, aantal, prijs en facturatieperiode.",
        "inputSchema": {"type": "object", "properties": {
            "debtor_code": {"type": "string", "description": "Filter op debiteurcode"},
            "status": {"type": "string", "default": "active", "description": "active (default), inactive, all"},
            "limit": {"type": "integer", "default": 100}
        }}
    },
    {
        "name": "get_service",
        "description": "Haal één abonnement op via het interne Hostfact service-ID. Geeft volledige abonnementsdetails inclusief aantallen en prijzen.",
        "inputSchema": {"type": "object", "required": ["identifier"], "properties": {
            "identifier": {"type": "string", "description": "Intern Hostfact service-ID (numeriek)"}
        }}
    },
    {
        "name": "edit_service",
        "description": (
            "Pas een bestaand abonnement aan. "
            "Gebruik product_code om een artikelnummer te koppelen aan een service zonder code. "
            "Gebruik number, price_excl of periodic om facturatiegegevens bij te werken. "
            "Vereist het interne service-ID van get_service."
        ),
        "inputSchema": {"type": "object", "required": ["identifier"], "properties": {
            "identifier": {"type": "string", "description": "Intern Hostfact service-ID"},
            "product_code": {"type": "string", "description": "Productcode om aan de service te koppelen (bijv. MST-NCE-104-C100)"},
            "number": {"type": "integer", "description": "Nieuw aantal (bijv. aantal licenties)"},
            "price_excl": {"type": "number", "description": "Nieuwe prijs excl. BTW"},
            "periodic": {"type": "string", "description": "Facturatieperiode: m (maand), k (kwartaal), j (jaar)"}
        }}
    },

    # ── PRODUCTEN ──
    {
        "name": "list_products",
        "description": "Haal productcatalogus op uit Hostfact. Toont productcode, naam en prijs.",
        "inputSchema": {"type": "object", "properties": {
            "search": {"type": "string"}
        }}
    },
    {
        "name": "get_product",
        "description": "Haal één product op via productcode. Geeft volledige productdetails inclusief prijs, BTW en facturatieperiode.",
        "inputSchema": {"type": "object", "required": ["product_code"], "properties": {
            "product_code": {"type": "string", "description": "Productcode, bijv. P001 of ict2.0-desktop"}
        }}
    },
    {
        "name": "edit_product",
        "description": "Pas een product aan in de Hostfact productcatalogus. Gebruik dit om de productcode, naam, prijs of BTW-percentage bij te werken. Vereist de huidige productcode om het product te vinden.",
        "inputSchema": {"type": "object", "required": ["product_code"], "properties": {
            "product_code": {"type": "string", "description": "Huidige productcode waarmee het product gevonden wordt"},
            "new_product_code": {"type": "string", "description": "Nieuwe productcode (bijv. MST-NCE-181-C100)"},
            "product_name": {"type": "string", "description": "Nieuwe productnaam"},
            "price_excl": {"type": "number", "description": "Nieuwe prijs excl. BTW"},
            "tax_percentage": {"type": "integer", "description": "Nieuw BTW-percentage (bijv. 21)"},
            "price_period": {"type": "string", "description": "Facturatieperiode: m (maand), k (kwartaal), j (jaar), e (eenmalig)"}
        }}
    },

    # ── GECOMBINEERD OVERZICHT ──
    {
        "name": "get_debtor_summary",
        "description": (
            "Volledig klantoverzicht: klantgegevens, actieve abonnementen (met maandelijkse waarde) "
            "en recente facturen. Optioneel met year_filter (bijv. 2026) voor gefilterd factuuroverzicht."
        ),
        "inputSchema": {"type": "object", "required": ["debtor_code"], "properties": {
            "debtor_code": {"type": "string"},
            "year_filter": {"type": "integer", "description": "Filterjaar voor factuuroverzicht, bijv. 2026"}
        }}
    },

    # ── AANMAKEN ──
    {
        "name": "add_product",
        "description": "Maak een nieuw product aan in de Hostfact productcatalogus.",
        "inputSchema": {"type": "object", "required": ["product_code", "product_name"], "properties": {
            "product_code": {"type": "string", "description": "Productcode, bijv. MST-NCE-104-C100"},
            "product_name": {"type": "string", "description": "Productnaam"},
            "product_description": {"type": "string", "description": "Omschrijving van het product"},
            "price_excl": {"type": "number", "description": "Prijs excl. BTW"},
            "tax_percentage": {"type": "integer", "description": "BTW-percentage (bijv. 21)", "default": 21},
            "price_period": {"type": "string", "description": "Facturatieperiode: m (maand), k (kwartaal), j (jaar), e (eenmalig)"}
        }}
    },
    {
        "name": "add_debtor",
        "description": "Maak een nieuwe debiteur aan in Hostfact.",
        "inputSchema": {"type": "object", "required": ["company_name", "email"], "properties": {
            "company_name": {"type": "string"},
            "email": {"type": "string"},
            "initials": {"type": "string"},
            "surname": {"type": "string"},
            "phone": {"type": "string"},
            "address": {"type": "string"},
            "zipcode": {"type": "string"},
            "city": {"type": "string"}
        }}
    },
    {
        "name": "add_service",
        "description": "Voeg een abonnement toe aan een debiteur.",
        "inputSchema": {"type": "object", "required": ["debtor_code", "description", "price_excl", "periodic"], "properties": {
            "debtor_code": {"type": "string"},
            "product_code": {"type": "string"},
            "description": {"type": "string"},
            "number": {"type": "integer", "default": 1},
            "price_excl": {"type": "number"},
            "periodic": {"type": "string", "description": "m / k / j"},
            "start_period": {"type": "string", "description": "YYYY-MM (bijv. 2026-07)"}
        }}
    },
    {
        "name": "add_invoice",
        "description": "Maak een factuur aan voor een debiteur.",
        "inputSchema": {"type": "object", "required": ["debtor_code", "invoice_lines"], "properties": {
            "debtor_code": {"type": "string"},
            "invoice_lines": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "description": {"type": "string"},
                    "number": {"type": "integer"},
                    "price_excl": {"type": "number"},
                    "tax_percentage": {"type": "integer", "default": 21}
                }}
            }
        }}
    },
]

# ─────────────────────────────────────────────
# Tool handlers
# ─────────────────────────────────────────────

async def handle_tool(name: str, arguments: dict) -> str:
    result = await _handle_tool_inner(name, arguments)
    error = result.startswith("❌") or result.startswith("Fout") or result.startswith("Onbekende")
    _audit(name, arguments, result, error=error)
    return result


async def _handle_tool_inner(name: str, arguments: dict) -> str:
    try:

        # ── list_debtors ──
        if name == "list_debtors":
            params = {}
            if arguments.get("search"):
                params["searchfor"] = arguments["search"]
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            result = await hostfact_call("debtor", "list", params)
            debtors = result.get("debtors", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} debiteuren\n"]
            for d in debtors:
                lines.append(f"• {d['DebtorCode']} | {d['CompanyName']} | {d.get('EmailAddress', '')}")
            return "\n".join(lines)

        # ── get_debtor ──
        elif name == "get_debtor":
            params = {}
            if arguments.get("debtor_code"):
                params["DebtorCode"] = arguments["debtor_code"]
            elif arguments.get("identifier"):
                params["Identifier"] = arguments["identifier"]
            result = await hostfact_call("debtor", "show", params)
            return json.dumps(result.get("debtor", {}), indent=2, ensure_ascii=False)

        # ── list_invoices (FIXED) ──
        elif name == "list_invoices":
            params = {}
            # Filter op debiteurcode via searchat/searchfor
            if arguments.get("debtor_code"):
                params["searchat"] = "DebtorCode"
                params["searchfor"] = arguments["debtor_code"]
            # Datum filter: als beide aanwezig, gebruik date_from (Hostfact ondersteunt 1 filter tegelijk)
            elif arguments.get("date_from"):
                params["searchat"] = "Date"
                params["searchfor"] = arguments["date_from"]
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            if arguments.get("offset"):
                params["offset"] = arguments["offset"]
            result = await hostfact_call("invoice", "list", params)
            invoices = result.get("invoices", [])
            total = result.get("totalresults", 0)
            lines = [f"Totaal: {total} facturen\n"]
            # Optioneel: filter op datum range of status client-side
            for inv in invoices:
                inv_date = inv.get("Date", "")
                status_code = inv.get("Status", "")
                status_label = fmt_status(status_code)
                # Client-side datum range filter
                if arguments.get("date_to") and inv_date > arguments["date_to"]:
                    continue
                if arguments.get("date_from") and not arguments.get("debtor_code") and inv_date < arguments["date_from"]:
                    continue
                # Client-side status filter
                if arguments.get("status_filter"):
                    if arguments["status_filter"].lower() not in status_label.lower():
                        continue
                lines.append(
                    f"• {inv.get('InvoiceCode', '?')} | {inv.get('CompanyName', '?')} | "
                    f"€{inv.get('AmountExcl', '?')} excl. BTW | {status_label} | {inv_date}"
                )
            return "\n".join(lines)

        # ── get_invoice (NEW) ──
        elif name == "get_invoice":
            result = await hostfact_call("invoice", "show", {"InvoiceCode": arguments["invoice_code"]})
            inv = result.get("invoice", {})
            if not inv:
                return f"Factuur {arguments['invoice_code']} niet gevonden."
            lines = [
                f"Factuur: {inv.get('InvoiceCode')}",
                f"Debiteur: {inv.get('DebtorCode')} — {inv.get('CompanyName')}",
                f"Datum: {inv.get('Date')}",
                f"Status: {fmt_status(inv.get('Status', ''))}",
                f"Bedrag excl. BTW: €{inv.get('AmountExcl')}",
                f"BTW: €{inv.get('AmountVat')}",
                f"Bedrag incl. BTW: €{inv.get('AmountIncl')}",
                f"",
                f"── Factuurregels ──",
            ]
            for line in inv.get("InvoiceLines", []):
                lines.append(
                    f"• {line.get('Number', 1)}x {line.get('Description', '')} | "
                    f"€{line.get('PriceExcl')} | BTW {line.get('TaxPercentage', 21)}%"
                )
            history = inv.get("PaymentHistory", [])
            if history:
                lines += ["", "── Betaalhistorie ──"]
                for p in history:
                    lines.append(f"• {p.get('PaymentDate')} | €{p.get('AmountPaid')}")
            return "\n".join(lines)

        # ── list_creditinvoices (NEW) ──
        elif name == "list_creditinvoices":
            params = {}
            if arguments.get("debtor_code"):
                params["searchat"] = "DebtorCode"
                params["searchfor"] = arguments["debtor_code"]
            elif arguments.get("date_from"):
                params["searchat"] = "Date"
                params["searchfor"] = arguments["date_from"]
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            if arguments.get("offset"):
                params["offset"] = arguments["offset"]
            result = await hostfact_call("creditinvoice", "list", params)
            invoices = result.get("creditinvoices", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} creditfacturen\n"]
            for inv in invoices:
                lines.append(
                    f"• {inv.get('CreditInvoiceCode', '?')} | {inv.get('CompanyName', '?')} | "
                    f"€{inv.get('AmountExcl', '?')} excl. BTW | {fmt_status(inv.get('Status', ''))} | {inv.get('Date', '?')}"
                )
            return "\n".join(lines)

        # ── get_creditinvoice (NEW) ──
        elif name == "get_creditinvoice":
            result = await hostfact_call("creditinvoice", "show", {"CreditInvoiceCode": arguments["creditinvoice_code"]})
            inv = result.get("creditinvoice", {})
            if not inv:
                return f"Creditfactuur {arguments['creditinvoice_code']} niet gevonden."
            lines = [
                f"Creditfactuur: {inv.get('CreditInvoiceCode')}",
                f"Debiteur: {inv.get('DebtorCode')} — {inv.get('CompanyName')}",
                f"Datum: {inv.get('Date')}",
                f"Status: {fmt_status(inv.get('Status', ''))}",
                f"Bedrag excl. BTW: €{inv.get('AmountExcl')}",
                f"Bedrag incl. BTW: €{inv.get('AmountIncl')}",
                f"",
                f"── Creditfactuurregels ──",
            ]
            for line in inv.get("CreditInvoiceLines", []):
                lines.append(
                    f"• {line.get('Number', 1)}x {line.get('Description', '')} | "
                    f"€{line.get('PriceExcl')} | BTW {line.get('TaxPercentage', 21)}%"
                )
            return "\n".join(lines)

        # ── list_services ──
        elif name == "list_services":
            params = {}
            if arguments.get("debtor_code"):
                params["DebtorCode"] = arguments["debtor_code"]
            status = arguments.get("status", "active")
            if status != "all":
                params["status"] = status
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            result = await hostfact_call("service", "list", params)
            services = result.get("services", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} abonnementen\n"]
            for s in services:
                sub = s.get("Subscription", {})
                periodic = fmt_periodic(sub.get("Periodic", ""))
                lines.append(
                    f"• [{s['DebtorCode']}] {s['CompanyName']}\n"
                    f"  ID:{s.get('Identifier', '?')} | {sub.get('ProductCode', '(geen code)')} | {sub.get('Description', '')[:60]}\n"
                    f"  Aantal: {sub.get('Number')} | €{sub.get('PriceExcl')} per {periodic} | Totaal: €{sub.get('AmountExcl')}\n"
                )
            return "\n".join(lines)

        # ── get_service (NEW) ──
        elif name == "get_service":
            result = await hostfact_call("service", "show", {"Identifier": arguments["identifier"]})
            service = result.get("service", {})
            if not service:
                return f"Service {arguments['identifier']} niet gevonden."
            sub = service.get("Subscription", {})
            periodic = fmt_periodic(sub.get("Periodic", ""))
            lines = [
                f"Service ID: {service.get('Identifier')}",
                f"Debiteur: {service.get('DebtorCode')} — {service.get('CompanyName')}",
                f"",
                f"── Abonnement ──",
                f"Productcode: {sub.get('ProductCode', '(geen code)')}",
                f"Omschrijving: {sub.get('Description', '')}",
                f"Aantal: {sub.get('Number')}",
                f"Prijs excl. BTW: €{sub.get('PriceExcl')} per {periodic}",
                f"Totaal excl. BTW: €{sub.get('AmountExcl')}",
                f"Startdatum: {sub.get('StartDate', '?')}",
                f"Volgende factuurdatum: {sub.get('NextDate', '?')}",
            ]
            return "\n".join(lines)

        # ── edit_service ──
        elif name == "edit_service":
            subscription = {}
            if arguments.get("product_code") is not None:
                subscription["ProductCode"] = arguments["product_code"]
            if arguments.get("number") is not None:
                subscription["Number"] = arguments["number"]
            if arguments.get("price_excl") is not None:
                subscription["PriceExcl"] = arguments["price_excl"]
            if arguments.get("periodic"):
                subscription["Periodic"] = arguments["periodic"]
            params = {"Identifier": arguments["identifier"], "Subscription": subscription}
            result = await hostfact_call("service", "edit", params)
            if result.get("status") == "success":
                changes = []
                if arguments.get("product_code") is not None:
                    changes.append(f"productcode → {arguments['product_code']}")
                if arguments.get("number") is not None:
                    changes.append(f"aantal → {arguments['number']}")
                if arguments.get("price_excl") is not None:
                    changes.append(f"prijs → €{arguments['price_excl']}")
                if arguments.get("periodic"):
                    changes.append(f"periode → {fmt_periodic(arguments['periodic'])}")
                return f"✅ Service {arguments['identifier']} bijgewerkt: {', '.join(changes)}"
            return f"❌ Fout: {result.get('errors', result)}"

        # ── list_products ──
        elif name == "list_products":
            params = {}
            if arguments.get("search"):
                params["searchfor"] = arguments["search"]
            result = await hostfact_call("product", "list", params)
            products = result.get("products", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} producten\n"]
            for p in products:
                periodic = fmt_periodic(p.get("PricePeriod", ""))
                lines.append(
                    f"• {p.get('ProductCode', '(geen code)')} | {p['ProductName']} | "
                    f"€{p['PriceExcl']} per {periodic}"
                )
            return "\n".join(lines)

        # ── edit_product (NEW) ──
        elif name == "edit_product":
            # Stap 1: haal intern Identifier op via product/show
            show_result = await hostfact_call("product", "show", {"ProductCode": arguments["product_code"]})
            product = show_result.get("product", {})
            identifier = product.get("Identifier")
            if not identifier:
                return f"❌ Product '{arguments['product_code']}' niet gevonden (of geen Identifier teruggegeven)."
            # Stap 2: edit via Identifier
            params = {"Identifier": identifier}
            if arguments.get("new_product_code"):
                params["ProductCode"] = arguments["new_product_code"]
            if arguments.get("product_name"):
                params["ProductName"] = arguments["product_name"]
            if arguments.get("price_excl") is not None:
                params["PriceExcl"] = arguments["price_excl"]
            if arguments.get("tax_percentage") is not None:
                params["TaxPercentage"] = arguments["tax_percentage"]
            if arguments.get("price_period"):
                params["PricePeriod"] = arguments["price_period"]
            result = await hostfact_call("product", "edit", params)
            if result.get("status") == "success":
                changes = []
                if arguments.get("new_product_code"):
                    changes.append(f"code {arguments['product_code']} → {arguments['new_product_code']}")
                if arguments.get("product_name"):
                    changes.append(f"naam → {arguments['product_name']}")
                if arguments.get("price_excl") is not None:
                    changes.append(f"prijs → €{arguments['price_excl']}")
                return f"✅ Product bijgewerkt: {', '.join(changes)}"
            return f"❌ Fout: {result.get('errors', result)}"

        # ── get_product (NEW) ──
        elif name == "get_product":
            result = await hostfact_call("product", "show", {"ProductCode": arguments["product_code"]})
            product = result.get("product", {})
            if not product:
                return f"Product {arguments['product_code']} niet gevonden."
            periodic = fmt_periodic(product.get("PricePeriod", ""))
            lines = [
                f"Productcode: {product.get('ProductCode')}",
                f"Naam: {product.get('ProductName')}",
                f"Omschrijving: {product.get('ProductDescription', '')}",
                f"Prijs excl. BTW: €{product.get('PriceExcl')} per {periodic}",
                f"BTW-percentage: {product.get('TaxPercentage', 21)}%",
                f"Categorie: {product.get('Category', '?')}",
            ]
            return "\n".join(lines)

        # ── get_debtor_summary (FIXED) ──
        elif name == "get_debtor_summary":
            debtor_code = arguments["debtor_code"]
            year_filter = arguments.get("year_filter")

            # Bouw invoice params op basis van year_filter
            invoice_params = {
                "searchat": "DebtorCode",
                "searchfor": debtor_code,
                "limit": 100,
            }

            debtor_result, services_result, invoices_result = await asyncio.gather(
                hostfact_call("debtor", "show", {"DebtorCode": debtor_code}),
                hostfact_call("service", "list", {"DebtorCode": debtor_code, "status": "active", "limit": 500}),
                hostfact_call("invoice", "list", invoice_params),
            )

            debtor = debtor_result.get("debtor", {})
            services = services_result.get("services", [])
            services_total = services_result.get("totalresults", len(services))
            all_invoices = invoices_result.get("invoices", [])

            # Filter facturen op jaar indien opgegeven
            if year_filter:
                year_str = str(year_filter)
                invoices = [inv for inv in all_invoices if inv.get("Date", "").startswith(year_str)]
            else:
                invoices = all_invoices

            # Bereken maandelijkse waarde abonnementen
            total_monthly = 0.0
            for s in services:
                sub = s.get("Subscription", {})
                try:
                    amount = float(sub.get("AmountExcl", 0) or 0)
                    periodic = sub.get("Periodic", "m")
                    monthly = amount / 3 if periodic == "k" else amount / 12 if periodic == "j" else amount if periodic == "m" else 0
                    total_monthly += monthly
                except (ValueError, TypeError):
                    pass

            lines = [
                f"═══ KLANTOVERZICHT: {debtor.get('CompanyName', debtor_code)} ═══",
                f"Code: {debtor.get('DebtorCode')} | Email: {debtor.get('EmailAddress')}",
                f"Contactpersoon: {debtor.get('Initials', '')} {debtor.get('SurName', '')}".strip(),
                f"",
                f"── ACTIEVE ABONNEMENTEN ({services_total}) ──",
            ]
            for s in services[:50]:  # toon max 50 regels
                sub = s.get("Subscription", {})
                periodic = fmt_periodic(sub.get("Periodic", ""))
                lines.append(
                    f"• ID:{s.get('Identifier', '?')} | {sub.get('ProductCode', '(geen code)')} | "
                    f"{sub.get('Description', '')[:50]} | #{sub.get('Number')} | "
                    f"€{sub.get('AmountExcl')} per {periodic}"
                )
            if services_total > 50:
                lines.append(f"  ... en nog {services_total - 50} abonnementen (gebruik list_services voor volledig overzicht)")

            # Bereken omzet uit facturen
            invoice_total = sum(
                float(inv.get("AmountExcl", 0) or 0)
                for inv in invoices
                if str(inv.get("Status", "")) not in ("0", "9")  # Geen concept of gecrediteerd
            )

            year_label = f" ({year_filter})" if year_filter else ""
            lines += [
                f"",
                f"Geschat maandelijks (abonnementen): €{total_monthly:.2f} excl. BTW",
                f"",
                f"── FACTUREN{year_label} ({len(invoices)} weergegeven van {invoices_result.get('totalresults', '?')} totaal) ──",
                f"Omzet gefilterde periode: €{invoice_total:.2f} excl. BTW",
                f"",
            ]
            for inv in invoices:
                status_label = fmt_status(inv.get("Status", ""))
                lines.append(
                    f"• {inv.get('InvoiceCode', '?')} | €{inv.get('AmountExcl', '?')} excl. BTW | "
                    f"{status_label} | {inv.get('Date', '?')}"
                )

            return "\n".join(lines)

        # ── add_product (NEW) ──
        elif name == "add_product":
            params = {
                "ProductCode": arguments["product_code"],
                "ProductName": arguments["product_name"],
            }
            if arguments.get("product_description"):
                params["ProductDescription"] = arguments["product_description"]
            if arguments.get("price_excl") is not None:
                params["PriceExcl"] = arguments["price_excl"]
            if arguments.get("tax_percentage") is not None:
                params["TaxPercentage"] = arguments["tax_percentage"]
            if arguments.get("price_period"):
                params["PricePeriod"] = arguments["price_period"]
            result = await hostfact_call("product", "add", params)
            if result.get("status") == "success":
                return f"✅ Product aangemaakt: {arguments['product_code']} — {arguments['product_name']}"
            return f"❌ Fout: {result.get('errors', result)}"

        # ── add_debtor ──
        elif name == "add_debtor":
            params = {"CompanyName": arguments["company_name"], "EmailAddress": arguments["email"]}
            for k, v in [("initials", "Initials"), ("surname", "SurName"), ("phone", "Phone"),
                         ("address", "Address"), ("zipcode", "ZipCode"), ("city", "City")]:
                if arguments.get(k):
                    params[v] = arguments[k]
            result = await hostfact_call("debtor", "add", params)
            if result.get("status") == "success":
                return f"✅ Debiteur aangemaakt: {result.get('DebtorCode')} — {arguments['company_name']}"
            return f"❌ Fout: {result.get('errors', result)}"

        # ── add_service ──
        elif name == "add_service":
            params = {
                "DebtorCode": arguments["debtor_code"],
                "Description": arguments["description"],
                "Number": arguments.get("number", 1),
                "PriceExcl": arguments["price_excl"],
                "Periodic": arguments["periodic"],
            }
            if arguments.get("product_code"):
                params["ProductCode"] = arguments["product_code"]
            if arguments.get("start_period"):
                params["StartPeriod"] = arguments["start_period"]
            result = await hostfact_call("service", "add", params)
            if result.get("status") == "success":
                return f"✅ Abonnement aangemaakt voor {arguments['debtor_code']}: {arguments['description']}"
            return f"❌ Fout: {result.get('errors', result)}"

        # ── add_invoice ──
        elif name == "add_invoice":
            lines_param = [
                {
                    "Description": l["description"],
                    "Number": l.get("number", 1),
                    "PriceExcl": l["price_excl"],
                    "TaxPercentage": l.get("tax_percentage", 21),
                }
                for l in arguments["invoice_lines"]
            ]
            result = await hostfact_call("invoice", "add", {
                "DebtorCode": arguments["debtor_code"],
                "InvoiceLines": json.dumps(lines_param),
            })
            if result.get("status") == "success":
                return f"✅ Factuur aangemaakt: {result.get('InvoiceCode')} voor {arguments['debtor_code']}"
            return f"❌ Fout: {result.get('errors', result)}"

        else:
            return f"Onbekende tool: {name}"

    except Exception as e:
        return f"Fout bij {name}: {str(e)}"


# ─────────────────────────────────────────────
# MCP endpoints
# ─────────────────────────────────────────────

@app.get("/mcp")
async def mcp_get(request: Request):
    return {
        "jsonrpc": "2.0", "id": 0,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hostfact-mcp", "version": "1.4.0"}
        }
    }

@app.post("/mcp")
async def mcp_post(request: Request):
    check_auth(request)
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "hostfact-mcp", "version": "1.4.0"}
            }
        }
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        result_text = await handle_tool(params.get("name"), params.get("arguments", {}))
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": result_text}]}
        }
    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

@app.get("/health")
async def health():
    return {"status": "ok", "server": "hostfact-mcp", "version": "1.4.0"}

@app.post("/register")
async def oauth_register(request: Request):
    body = await request.json()
    return JSONResponse({
        "client_id": "hostfact-mcp-client",
        "client_id_issued_at": 1735000000,
        "redirect_uris": body.get("redirect_uris", []),
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"]
    })
