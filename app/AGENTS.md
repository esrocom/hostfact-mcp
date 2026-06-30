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

### 8. Duplicate product codes
`product/add` rejects a `ProductCode` that already exists in the catalog.
Always call `get_product` first to verify the code does not exist before attempting to create it.

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
