# AGENTS.md — Hostfact MCP Server

Instructions and domain knowledge for AI systems working with this repository.

## Purpose
This MCP server connects Hostfact (invoicing/billing) to Claude.ai and other AI systems via the Model Context Protocol.

---

## Hostfact API — Important gotchas

### 1. Filtering invoices by debtor
The Hostfact API does **not** accept `DebtorCode` as a direct parameter in `invoice/list`.
Always use `searchat` + `searchfor`:

```
searchat=DebtorCode
searchfor=<DebtorCode>
```

Passing `DebtorCode=<value>` directly is silently ignored — the API returns all invoices.

### 2. Product → Service relationship
A **product** in the Hostfact catalog has a `ProductCode`.
A **service** (subscription) references a product via that same `ProductCode`.

**To correct an article code on a service:**
→ Edit the product in the catalog (`product/edit`), not the service.
→ The service automatically reflects the updated product code.

`edit_service` is for updating **quantity, price or billing period** — not for changing the article/product code.

### 3. Identifier required for edits
For `product/edit` and `service/edit`, the Hostfact API requires the internal numeric `Identifier` — not `ProductCode` or `DebtorCode`.

Workflow:
1. Call `product/show` or `service/show` first to retrieve the `Identifier`.
2. Use that `Identifier` in the edit call.

### 4. Invoice status codes
| Code | Label |
|------|-------|
| 0 | Concept |
| 1 | Te betalen |
| 2 | Verstuurd |
| 3 | Betaald |
| 4 | Voldaan |
| 5 | Geblokkeerd |
| 6 | Aanmaning |
| 7 | Deurwaarder |
| 8 | Creditfactuur |
| 9 | Gecrediteerd |

Concept (0) and Gecrediteerd (9) are excluded from revenue calculations.

### 5. Billing period codes
| Code | Meaning |
|------|---------|
| m | Monthly |
| k | Quarterly |
| j | Yearly |
| e | One-time |

### 6. product/add requires a Description (invoice line text)
`product/add` requires a separate `Description` field — this is the text that appears on invoice lines.
It is **not** the same as `ProductDescription` (the catalog description).

Omitting `Description` causes the error: *"De omschrijving voor op de factuur is niet juist ingevuld"*

Always supply `Description` (short invoice line text, e.g. "Microsoft Entra ID P2 per user/month") when calling `add_product`.

### 7. Services with no ProductCode
Some services exist without a linked product (empty `ProductCode`).
For these, `edit_product` cannot be used (there is no product to look up).
Use `edit_service` with `product_code` to directly set the ProductCode on the service subscription.

### 8. Subscription fields in service/edit use bracket notation
The `Subscription` parameter in `service/edit` must be passed as **flattened bracket-notation keys**, not as a nested dict or JSON string.

```python
params = {"Identifier": identifier}
for k, v in subscription.items():
    params[f"Subscription[{k}]"] = v
```

Supported fields: `ProductCode`, `Number`, `PriceExcl`, `Periodic`.

Passing a raw dict or JSON string causes: *"Invalid type for 'Subscription'"*

### 9. Duplicate product codes
`product/add` rejects a `ProductCode` that already exists in the catalog.
Always call `get_product` first to verify the code does not exist before attempting to create it.

### 10. Concept invoices DO have an InvoiceCode — just not an "F..." one, and NEVER guess `identifier` from it
Draft invoices (`Status = 0`, "Concept") are displayed with an `InvoiceCode` in the form
`"[concept]NNNN"` — they do have a code, it just isn't the final `F...`-style number assigned
once the invoice is sent.

`get_invoice`, `add_invoice_line`, `delete_invoice_line`, `delete_invoice` and `send_invoice`
all accept either `invoice_code` or `identifier`. For a concept invoice, always pass the
**entire string** `"[concept]NNNN"` as `invoice_code` — never extract `NNNN` and pass it as
`identifier`.

Confirmed by a live test: calling a tool with `identifier: "3874"` (the number taken from a
label `[concept]3874`) silently resolved to a completely unrelated, already-paid invoice from
2016 belonging to a different debtor — no error was raised, it just returned the wrong invoice.
The number after `[concept]` is part of the `InvoiceCode` string and is unrelated to Hostfact's
internal `Identifier` sequence; small integers coincidentally overlap between the two.

Only use `identifier` when it has been obtained directly from a prior API response's
`Identifier` field (e.g. `InvoiceLines[].Identifier`, or a service/debtor `Identifier`) — never
by guessing from a displayed label.

### 11. Merging concept invoices
Hostfact's own "merge draft invoices" UI action has no dedicated API endpoint — it's built
from the same primitives exposed here:
1. `get_invoice` (via `invoice_code`, full string incl. `[concept]` prefix if applicable) on
   each concept invoice to read its lines.
2. `add_invoice_line` to copy the lines from the invoice(s) being merged away onto the invoice
   that will remain ("master").
3. `delete_invoice` on the now-empty source invoice(s).

Always verify the copy succeeded (re-fetch the master with `get_invoice`) before deleting the
source — `delete_invoice` is irreversible for concept invoices.

Live-validated end-to-end (2026-07-01) across multiple concept-invoice pairs against the real
Hostfact API, not just mocked.

### 12. `delete_invoice` only deletes concept invoices — by design
`invoice/delete` in the raw Hostfact API already refuses non-concept invoices, but
`delete_invoice` in this server checks the status itself first (via `invoice/show`) and returns
a clear error rather than relying solely on the upstream error message. There is no way to
delete a sent/paid invoice through this tool — use `invoice/credit` (not yet exposed here) for
that.

### 13. `invoiceline/add` and `invoiceline/delete` require bracket notation, NOT a JSON string
Unlike `invoice/add` (where `InvoiceLines` as a `json.dumps(...)` string works fine),
`invoiceline/add` and `invoiceline/delete` reject a JSON-encoded `InvoiceLines` string with
*"Invalid type for 'InvoiceLines'"* — confirmed by a live test. They need the same flattened
bracket-notation form params used for `Subscription` (#8) and `CustomPrices`:

```python
params = dict(target)  # {"Identifier": ...} or {"InvoiceCode": ...}
for i, line in enumerate(lines_param):
    for k, v in line.items():
        params[f"InvoiceLines[{i}][{k}]"] = v
```

Hostfact's API is inconsistent across endpoints about whether nested arrays accept a JSON
string or require bracket notation — don't assume one endpoint's behavior applies to another;
test against the live API before trusting a new nested-array parameter.

### 14. `send_invoice` is irreversible and customer-facing — verify content first
`send_invoice` wraps `invoice/sendbyemail`. It only accepts `Identifier` or `InvoiceCode` — no
other parameters. Calling it actually emails the invoice PDF to the debtor's registered email
address and moves the invoice from Concept (0) to Verstuurd (2), assigning its final `F...`
invoice number in the process. There is no "undo" for this via the API (crediting afterwards is
a separate, visible transaction, not a silent rollback).

Because of this, always confirm the invoice content is correct (`get_invoice`) — and, if
relevant, finish any concept-invoice merge (#11) first — before calling `send_invoice`. Treat
every call to this tool as a real action with real customer and financial impact, not a
reversible test.

---

## Available tools

| Tool | Action |
|------|--------|
| `list_debtors` | List debtors |
| `get_debtor` | Debtor detail |
| `get_debtor_summary` | Full customer overview incl. subscriptions + invoices (supports `year_filter`) |
| `list_invoices` | Fetch invoices (filter via `debtor_code`, `date_from`, `status_filter`) |
| `get_invoice` | Invoice detail incl. lines and payment history |
| `list_creditinvoices` | Fetch credit invoices |
| `get_creditinvoice` | Credit invoice detail |
| `list_services` | Active subscriptions (filter via `debtor_code`) |
| `get_service` | Subscription detail via internal service ID |
| `edit_service` | Update subscription (quantity, price, period, or product_code) |
| `list_products` | Product catalog |
| `get_product` | Product detail |
| `edit_product` | Update product (including changing product code) |
| `add_product` | Create new product (requires product_code, product_name, description, price_excl, price_period) |
| `add_debtor` | Create new debtor |
| `add_service` | Add subscription |
| `add_invoice` | Create invoice |
| `add_invoice_line` | Add a line to an existing invoice (any status, incl. concept) |
| `delete_invoice_line` | Remove a single line from an existing invoice |
| `delete_invoice` | Delete a concept invoice (refuses non-concept invoices) |
| `send_invoice` | Send an invoice by email (Concept → Verstuurd, assigns final invoice number, irreversible) |
