import os
import httpx
import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Hostfact MCP Server", version="1.0.0")

HOSTFACT_URL = os.getenv("HOSTFACT_URL", "https://administratie.esrocom.nl/Pro/apiv2/api.php")
HOSTFACT_API_KEY = os.getenv("HOSTFACT_API_KEY", "")

async def hostfact_call(controller: str, action: str, params: dict = {}) -> dict:
    data = {"api_key": HOSTFACT_API_KEY, "controller": controller, "action": action, **params}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(HOSTFACT_URL, data=data)
        resp.raise_for_status()
        return resp.json()

TOOLS = [
    {
        "name": "list_debtors",
        "description": "Haal een lijst van debiteuren (klanten) op uit Hostfact. Optioneel te filteren op naam of debiteurcode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Zoekterm op bedrijfsnaam of debiteurcode"},
                "limit": {"type": "integer", "description": "Max aantal resultaten", "default": 50}
            }
        }
    },
    {
        "name": "get_debtor",
        "description": "Haal volledige gegevens op van één debiteur op basis van debiteurcode (bijv. DB10065) of intern Identifier.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "debtor_code": {"type": "string", "description": "Debiteurcode bijv. DB10065"},
                "identifier": {"type": "string", "description": "Intern Hostfact ID"}
            }
        }
    },
    {
        "name": "list_services",
        "description": "Haal abonnementen/diensten op uit Hostfact. Optioneel gefilterd op debiteur en status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "debtor_code": {"type": "string", "description": "Filter op debiteurcode"},
                "status": {"type": "string", "description": "active of terminated (standaard: active)", "default": "active"}
            }
        }
    },
    {
        "name": "list_products",
        "description": "Haal de volledige productcatalogus op uit Hostfact, inclusief prijzen en periodes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Zoekterm op productnaam of productcode"}
            }
        }
    },
    {
        "name": "list_invoices",
        "description": "Haal facturen op uit Hostfact. Optioneel gefilterd op debiteur.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "debtor_code": {"type": "string", "description": "Filter op debiteurcode"},
                "limit": {"type": "integer", "description": "Max aantal facturen", "default": 25}
            }
        }
    },
    {
        "name": "get_debtor_summary",
        "description": "Volledig klantoverzicht in één call: basisgegevens, alle actieve abonnementen inclusief geschat maandbedrag, en recente facturen.",
        "inputSchema": {
            "type": "object",
            "required": ["debtor_code"],
            "properties": {
                "debtor_code": {"type": "string", "description": "Debiteurcode bijv. DB10065"}
            }
        }
    },
    {
        "name": "add_debtor",
        "description": "Maak een nieuwe debiteur aan in Hostfact.",
        "inputSchema": {
            "type": "object",
            "required": ["company_name", "email"],
            "properties": {
                "company_name": {"type": "string", "description": "Bedrijfsnaam"},
                "email": {"type": "string", "description": "E-mailadres"},
                "initials": {"type": "string", "description": "Voorletters contactpersoon"},
                "surname": {"type": "string", "description": "Achternaam contactpersoon"},
                "phone": {"type": "string", "description": "Telefoonnummer"},
                "address": {"type": "string", "description": "Straat en huisnummer"},
                "zipcode": {"type": "string", "description": "Postcode"},
                "city": {"type": "string", "description": "Plaats"}
            }
        }
    },
    {
        "name": "add_service",
        "description": "Voeg een nieuw abonnement/dienst toe aan een debiteur in Hostfact.",
        "inputSchema": {
            "type": "object",
            "required": ["debtor_code", "description", "price_excl", "periodic"],
            "properties": {
                "debtor_code": {"type": "string", "description": "Debiteurcode"},
                "product_code": {"type": "string", "description": "Productcode uit de catalogus"},
                "description": {"type": "string", "description": "Omschrijving van de dienst"},
                "number": {"type": "integer", "description": "Aantal", "default": 1},
                "price_excl": {"type": "number", "description": "Prijs excl BTW per eenheid"},
                "periodic": {"type": "string", "description": "Facturatieperiode: m=maand, k=kwartaal, j=jaar"},
                "start_period": {"type": "string", "description": "Startdatum YYYY-MM-DD"}
            }
        }
    },
    {
        "name": "add_invoice",
        "description": "Maak een nieuwe factuur aan voor een debiteur in Hostfact.",
        "inputSchema": {
            "type": "object",
            "required": ["debtor_code", "invoice_lines"],
            "properties": {
                "debtor_code": {"type": "string", "description": "Debiteurcode"},
                "invoice_lines": {
                    "type": "array",
                    "description": "Factuurregels",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "number": {"type": "integer"},
                            "price_excl": {"type": "number"},
                            "tax_percentage": {"type": "integer", "default": 21}
                        }
                    }
                }
            }
        }
    }
]

async def handle_tool(name: str, arguments: dict) -> str:
    try:
        if name == "list_debtors":
            params = {}
            if arguments.get("search"): params["searchfor"] = arguments["search"]
            if arguments.get("limit"): params["limit"] = arguments["limit"]
            result = await hostfact_call("debtor", "list", params)
            debtors = result.get("debtors", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} debiteuren\n"]
            for d in debtors:
                lines.append(f"• {d['DebtorCode']} | {d['CompanyName']} | {d.get('EmailAddress','')}")
            return "\n".join(lines)

        elif name == "get_debtor":
            params = {}
            if arguments.get("debtor_code"): params["DebtorCode"] = arguments["debtor_code"]
            elif arguments.get("identifier"): params["Identifier"] = arguments["identifier"]
            result = await hostfact_call("debtor", "show", params)
            return json.dumps(result.get("debtor", {}), indent=2, ensure_ascii=False)

        elif name == "list_services":
            params = {}
            if arguments.get("debtor_code"): params["DebtorCode"] = arguments["debtor_code"]
            if arguments.get("status", "active") != "all":
                params["status"] = arguments.get("status", "active")
            result = await hostfact_call("service", "list", params)
            services = result.get("services", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} abonnementen\n"]
            for s in services:
                sub = s.get("Subscription", {})
                lines.append(
                    f"• [{s['DebtorCode']}] {s['CompanyName']}\n"
                    f"  {sub.get('ProductCode','(geen code)')} | {sub.get('Description','')[:60]}\n"
                    f"  Aantal: {sub.get('Number')} | €{sub.get('PriceExcl')} per {sub.get('Periodic')} | Totaal: €{sub.get('AmountExcl')}\n"
                )
            return "\n".join(lines)

        elif name == "list_products":
            params = {}
            if arguments.get("search"): params["searchfor"] = arguments["search"]
            result = await hostfact_call("product", "list", params)
            products = result.get("products", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} producten\n"]
            for p in products:
                lines.append(
                    f"• {p.get('ProductCode','(geen code)')} | {p['ProductName']} | "
                    f"€{p['PriceExcl']} per {p.get('PricePeriod','?')}"
                )
            return "\n".join(lines)

        elif name == "list_invoices":
            params = {}
            if arguments.get("debtor_code"): params["DebtorCode"] = arguments["debtor_code"]
            if arguments.get("limit"): params["limit"] = arguments["limit"]
            result = await hostfact_call("invoice", "list", params)
            invoices = result.get("invoices", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} facturen\n"]
            for inv in invoices:
                lines.append(
                    f"• {inv.get('InvoiceCode','?')} | {inv.get('CompanyName','?')} | "
                    f"€{inv.get('AmountExcl','?')} | {inv.get('Status','?')} | {inv.get('Date','?')}"
                )
            return "\n".join(lines)

        elif name == "get_debtor_summary":
            debtor_code = arguments["debtor_code"]
            debtor_result, services_result, invoices_result = await asyncio.gather(
                hostfact_call("debtor", "show", {"DebtorCode": debtor_code}),
                hostfact_call("service", "list", {"DebtorCode": debtor_code, "status": "active"}),
                hostfact_call("invoice", "list", {"DebtorCode": debtor_code, "limit": 10})
            )
            debtor = debtor_result.get("debtor", {})
            services = services_result.get("services", [])
            invoices = invoices_result.get("invoices", [])
            total_monthly = 0
            lines = [
                f"═══ KLANTOVERZICHT: {debtor.get('CompanyName', debtor_code)} ═══",
                f"Code: {debtor.get('DebtorCode')} | Email: {debtor.get('EmailAddress')}",
                f"Contactpersoon: {debtor.get('Initials','')} {debtor.get('SurName','')}",
                f"", f"── ACTIEVE ABONNEMENTEN ({len(services)}) ──"
            ]
            for s in services:
                sub = s.get("Subscription", {})
                amount = float(sub.get("AmountExcl", 0))
                periodic = sub.get("Periodic", "?")
                monthly = amount/3 if periodic=="k" else amount/12 if periodic=="j" else amount if periodic=="m" else 0
                total_monthly += monthly
                lines.append(
                    f"• {sub.get('ProductCode','(geen code)')} | "
                    f"{sub.get('Description','')[:50]} | "
                    f"#{sub.get('Number')} | €{amount} per {periodic}"
                )
            lines += [
                f"", f"Geschat maandelijks: €{total_monthly:.2f} excl BTW",
                f"", f"── RECENTE FACTUREN ({len(invoices)}) ──"
            ]
            for inv in invoices:
                lines.append(
                    f"• {inv.get('InvoiceCode','?')} | €{inv.get('AmountExcl','?')} | "
                    f"{inv.get('Status','?')} | {inv.get('Date','?')}"
                )
            return "\n".join(lines)

        elif name == "add_debtor":
            params = {
                "CompanyName": arguments["company_name"],
                "EmailAddress": arguments["email"],
            }
            if arguments.get("initials"): params["Initials"] = arguments["initials"]
            if arguments.get("surname"): params["SurName"] = arguments["surname"]
            if arguments.get("phone"): params["Phone"] = arguments["phone"]
            if arguments.get("address"): params["Address"] = arguments["address"]
            if arguments.get("zipcode"): params["ZipCode"] = arguments["zipcode"]
            if arguments.get("city"): params["City"] = arguments["city"]
            result = await hostfact_call("debtor", "add", params)
            if result.get("status") == "success":
                return f"✅ Debiteur aangemaakt: {result.get('DebtorCode')} — {arguments['company_name']}"
            return f"❌ Fout: {result.get('errors', result)}"

        elif name == "add_service":
            params = {
                "DebtorCode": arguments["debtor_code"],
                "Description": arguments["description"],
                "Number": arguments.get("number", 1),
                "PriceExcl": arguments["price_excl"],
                "Periodic": arguments["periodic"],
            }
            if arguments.get("product_code"): params["ProductCode"] = arguments["product_code"]
            if arguments.get("start_period"): params["StartPeriod"] = arguments["start_period"]
            result = await hostfact_call("service", "add", params)
            if result.get("status") == "success":
                return f"✅ Abonnement aangemaakt voor {arguments['debtor_code']}: {arguments['description']}"
            return f"❌ Fout: {result.get('errors', result)}"

        elif name == "add_invoice":
            lines_param = []
            for i, line in enumerate(arguments["invoice_lines"]):
                lines_param.append({
                    "Description": line["description"],
                    "Number": line.get("number", 1),
                    "PriceExcl": line["price_excl"],
                    "TaxPercentage": line.get("tax_percentage", 21)
                })
            params = {
                "DebtorCode": arguments["debtor_code"],
                "InvoiceLines": json.dumps(lines_param)
            }
            result = await hostfact_call("invoice", "add", params)
            if result.get("status") == "success":
                return f"✅ Factuur aangemaakt: {result.get('InvoiceCode')} voor {arguments['debtor_code']}"
            return f"❌ Fout: {result.get('errors', result)}"

        else:
            return f"Onbekende tool: {name}"

    except Exception as e:
        return f"Fout bij {name}: {str(e)}"


@app.post("/mcp")
async def mcp_post(request: Request):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hostfact-mcp", "version": "1.0.0"}
        }}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        result_text = await handle_tool(params.get("name"), params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": result_text}]
        }}
    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32601, "message": f"Method not found: {method}"
        }}


@app.get("/health")
async def health():
    return {"status": "ok", "server": "hostfact-mcp", "version": "1.0.0"}
