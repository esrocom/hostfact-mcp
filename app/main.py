import os
import httpx
import json
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse

app = FastAPI(title="Hostfact MCP Server", version="1.0.0")

HOSTFACT_URL = os.getenv("HOSTFACT_URL", "https://administratie.esrocom.nl/Pro/apiv2/api.php")
HOSTFACT_API_KEY = os.getenv("HOSTFACT_API_KEY", "")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")

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

# ─────────────────────────────────────────────
# OAuth2 endpoints (minimal, for Claude.ai)
# ─────────────────────────────────────────────

@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    base = "https://hostfact.mcp.esrocom.nl"
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
    code = "esrocom-auth-code-2026"
    url = f"{redirect_uri}?code={code}"
    if state:
        url += f"&state={state}"
    return RedirectResponse(url=url, status_code=302)

@app.post("/token")
@app.post("/oauth/token")
async def oauth_token(request: Request):
    return {
        "access_token": MCP_AUTH_TOKEN,
        "token_type": "bearer",
        "expires_in": 86400
    }

# ─────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────

TOOLS = [
    {"name": "list_debtors", "description": "Haal een lijst van debiteuren op uit Hostfact.", "inputSchema": {"type": "object", "properties": {"search": {"type": "string"}, "limit": {"type": "integer", "default": 50}}}},
    {"name": "get_debtor", "description": "Haal één debiteur op via debiteurcode (bijv. DB10065).", "inputSchema": {"type": "object", "properties": {"debtor_code": {"type": "string"}, "identifier": {"type": "string"}}}},
    {"name": "list_services", "description": "Haal actieve abonnementen op, optioneel per debiteur.", "inputSchema": {"type": "object", "properties": {"debtor_code": {"type": "string"}, "status": {"type": "string", "default": "active"}}}},
    {"name": "list_products", "description": "Haal productcatalogus op uit Hostfact.", "inputSchema": {"type": "object", "properties": {"search": {"type": "string"}}}},
    {"name": "list_invoices", "description": "Haal facturen op, optioneel per debiteur.", "inputSchema": {"type": "object", "properties": {"debtor_code": {"type": "string"}, "limit": {"type": "integer", "default": 25}}}},
    {"name": "get_debtor_summary", "description": "Volledig klantoverzicht: gegevens, abonnementen en facturen.", "inputSchema": {"type": "object", "required": ["debtor_code"], "properties": {"debtor_code": {"type": "string"}}}},
    {"name": "add_debtor", "description": "Maak een nieuwe debiteur aan in Hostfact.", "inputSchema": {"type": "object", "required": ["company_name", "email"], "properties": {"company_name": {"type": "string"}, "email": {"type": "string"}, "initials": {"type": "string"}, "surname": {"type": "string"}, "phone": {"type": "string"}, "address": {"type": "string"}, "zipcode": {"type": "string"}, "city": {"type": "string"}}}},
    {"name": "add_service", "description": "Voeg een abonnement toe aan een debiteur.", "inputSchema": {"type": "object", "required": ["debtor_code", "description", "price_excl", "periodic"], "properties": {"debtor_code": {"type": "string"}, "product_code": {"type": "string"}, "description": {"type": "string"}, "number": {"type": "integer", "default": 1}, "price_excl": {"type": "number"}, "periodic": {"type": "string"}, "start_period": {"type": "string"}}}},
    {"name": "add_invoice", "description": "Maak een factuur aan voor een debiteur.", "inputSchema": {"type": "object", "required": ["debtor_code", "invoice_lines"], "properties": {"debtor_code": {"type": "string"}, "invoice_lines": {"type": "array", "items": {"type": "object", "properties": {"description": {"type": "string"}, "number": {"type": "integer"}, "price_excl": {"type": "number"}, "tax_percentage": {"type": "integer", "default": 21}}}}}}}
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
            if arguments.get("status", "active") != "all": params["status"] = arguments.get("status", "active")
            result = await hostfact_call("service", "list", params)
            services = result.get("services", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} abonnementen\n"]
            for s in services:
                sub = s.get("Subscription", {})
                lines.append(f"• [{s['DebtorCode']}] {s['CompanyName']}\n  {sub.get('ProductCode','(geen code)')} | {sub.get('Description','')[:60]}\n  Aantal: {sub.get('Number')} | €{sub.get('PriceExcl')} per {sub.get('Periodic')} | Totaal: €{sub.get('AmountExcl')}\n")
            return "\n".join(lines)
        elif name == "list_products":
            params = {}
            if arguments.get("search"): params["searchfor"] = arguments["search"]
            result = await hostfact_call("product", "list", params)
            products = result.get("products", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} producten\n"]
            for p in products:
                lines.append(f"• {p.get('ProductCode','(geen code)')} | {p['ProductName']} | €{p['PriceExcl']} per {p.get('PricePeriod','?')}")
            return "\n".join(lines)
        elif name == "list_invoices":
            params = {}
            if arguments.get("debtor_code"): params["DebtorCode"] = arguments["debtor_code"]
            if arguments.get("limit"): params["limit"] = arguments["limit"]
            result = await hostfact_call("invoice", "list", params)
            invoices = result.get("invoices", [])
            lines = [f"Totaal: {result.get('totalresults', 0)} facturen\n"]
            for inv in invoices:
                lines.append(f"• {inv.get('InvoiceCode','?')} | {inv.get('CompanyName','?')} | €{inv.get('AmountExcl','?')} | {inv.get('Status','?')} | {inv.get('Date','?')}")
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
            lines = [f"═══ KLANTOVERZICHT: {debtor.get('CompanyName', debtor_code)} ═══", f"Code: {debtor.get('DebtorCode')} | Email: {debtor.get('EmailAddress')}", f"Contactpersoon: {debtor.get('Initials','')} {debtor.get('SurName','')}", f"", f"── ACTIEVE ABONNEMENTEN ({len(services)}) ──"]
            for s in services:
                sub = s.get("Subscription", {})
                amount = float(sub.get("AmountExcl", 0))
                periodic = sub.get("Periodic", "?")
                monthly = amount/3 if periodic=="k" else amount/12 if periodic=="j" else amount if periodic=="m" else 0
                total_monthly += monthly
                lines.append(f"• {sub.get('ProductCode','(geen code)')} | {sub.get('Description','')[:50]} | #{sub.get('Number')} | €{amount} per {periodic}")
            lines += [f"", f"Geschat maandelijks: €{total_monthly:.2f} excl BTW", f"", f"── RECENTE FACTUREN ({len(invoices)}) ──"]
            for inv in invoices:
                lines.append(f"• {inv.get('InvoiceCode','?')} | €{inv.get('AmountExcl','?')} | {inv.get('Status','?')} | {inv.get('Date','?')}")
            return "\n".join(lines)
        elif name == "add_debtor":
            params = {"CompanyName": arguments["company_name"], "EmailAddress": arguments["email"]}
            for k, v in [("initials","Initials"),("surname","SurName"),("phone","Phone"),("address","Address"),("zipcode","ZipCode"),("city","City")]:
                if arguments.get(k): params[v] = arguments[k]
            result = await hostfact_call("debtor", "add", params)
            if result.get("status") == "success":
                return f"✅ Debiteur aangemaakt: {result.get('DebtorCode')} — {arguments['company_name']}"
            return f"❌ Fout: {result.get('errors', result)}"
        elif name == "add_service":
            params = {"DebtorCode": arguments["debtor_code"], "Description": arguments["description"], "Number": arguments.get("number", 1), "PriceExcl": arguments["price_excl"], "Periodic": arguments["periodic"]}
            if arguments.get("product_code"): params["ProductCode"] = arguments["product_code"]
            if arguments.get("start_period"): params["StartPeriod"] = arguments["start_period"]
            result = await hostfact_call("service", "add", params)
            if result.get("status") == "success":
                return f"✅ Abonnement aangemaakt voor {arguments['debtor_code']}: {arguments['description']}"
            return f"❌ Fout: {result.get('errors', result)}"
        elif name == "add_invoice":
            lines_param = [{"Description": l["description"], "Number": l.get("number", 1), "PriceExcl": l["price_excl"], "TaxPercentage": l.get("tax_percentage", 21)} for l in arguments["invoice_lines"]]
            result = await hostfact_call("invoice", "add", {"DebtorCode": arguments["debtor_code"], "InvoiceLines": json.dumps(lines_param)})
            if result.get("status") == "success":
                return f"✅ Factuur aangemaakt: {result.get('InvoiceCode')} voor {arguments['debtor_code']}"
            return f"❌ Fout: {result.get('errors', result)}"
        else:
            return f"Onbekende tool: {name}"
    except Exception as e:
        return f"Fout bij {name}: {str(e)}"

@app.get("/mcp")
async def mcp_get(request: Request):
    return {"jsonrpc":"2.0","id":0,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"hostfact-mcp","version":"1.0.0"}}}

@app.post("/mcp")
async def mcp_post(request: Request):
    check_auth(request)
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "hostfact-mcp", "version": "1.0.0"}}}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        result_text = await handle_tool(params.get("name"), params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": result_text}]}}
    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

@app.get("/health")
async def health():
    return {"status": "ok", "server": "hostfact-mcp", "version": "1.0.0"}
